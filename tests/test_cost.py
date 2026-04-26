import unittest
from calendar import monthrange
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401
import cost


class NormalizeDay(unittest.TestCase):
    def test_legacy_float(self):
        out = cost._normalize_day(1.5)
        self.assertEqual(out, {'total': 1.5, 'hours': {}})

    def test_dict_form(self):
        out = cost._normalize_day({'total': 2.0, 'hours': {'10': 0.5, '14': 1.5}})
        self.assertEqual(out['total'], 2.0)
        self.assertEqual(out['hours'], {10: 0.5, 14: 1.5})

    def test_none_default(self):
        self.assertEqual(cost._normalize_day(None), {'total': 0.0, 'hours': {}})


class IsolatedCost:
    """Context manager that swaps cost.COSTS_FILE for a temp path and resets
    the per-process data cache, so each test starts from a clean slate."""
    def __enter__(self):
        self._tmp = TemporaryDirectory()
        self._patch_path = mock.patch.object(
            cost, 'COSTS_FILE', Path(self._tmp.name) / 'costs.json'
        )
        self._patch_path.start()
        self._original_cache = cost._DATA_CACHE
        cost._DATA_CACHE = None
        return self._tmp

    def __exit__(self, *exc):
        self._patch_path.stop()
        cost._DATA_CACHE = self._original_cache
        self._tmp.cleanup()


class UpdateAndGet(unittest.TestCase):
    def test_first_call_records_zero_delta(self):
        with IsolatedCost():
            session, day, month = cost.update_and_get('sess-1', 1.50)
            self.assertEqual(session, 1.50)
            # First-ever record establishes baseline; delta = 1.50 - 0 = 1.50.
            self.assertAlmostEqual(day, 1.50, places=4)
            self.assertAlmostEqual(month, 1.50, places=4)

    def test_delta_only_added_on_increase(self):
        with IsolatedCost():
            cost.update_and_get('sess-1', 1.00)
            _, day_after, _ = cost.update_and_get('sess-1', 1.30)
            # Delta = 0.30 added on top of the original 1.00.
            self.assertAlmostEqual(day_after, 1.30, places=4)

    def test_session_cost_decrease_does_not_subtract(self):
        with IsolatedCost():
            cost.update_and_get('sess-1', 5.00)
            _, day, _ = cost.update_and_get('sess-1', 3.00)
            # Negative delta is clamped to 0 — day stays at 5.0.
            self.assertAlmostEqual(day, 5.00, places=4)

    def test_multiple_sessions_aggregate(self):
        with IsolatedCost():
            cost.update_and_get('sess-1', 2.00)
            _, day, _ = cost.update_and_get('sess-2', 3.00)
            self.assertAlmostEqual(day, 5.00, places=4)

    def test_idempotent_no_change_does_not_grow_day(self):
        with IsolatedCost():
            cost.update_and_get('sess-1', 1.00)
            _, day_a, _ = cost.update_and_get('sess-1', 1.00)
            _, day_b, _ = cost.update_and_get('sess-1', 1.00)
            self.assertEqual(day_a, day_b)


class GetProjection(unittest.TestCase):
    def test_no_history_returns_none(self):
        with IsolatedCost():
            self.assertIsNone(cost.get_projection())

    def test_avg_uses_past_window_excluding_today(self):
        with IsolatedCost():
            now = datetime.now()
            today_key = now.strftime('%Y-%m-%d')
            data = cost._load()
            data.setdefault('days', {})
            # Three past days at $1, $2, $3 → avg $2.
            for offset, total in zip((1, 2, 3), (3.0, 2.0, 1.0)):
                d = datetime(now.year, now.month, now.day).replace(day=max(1, now.day - offset))
                data['days'][d.strftime('%Y-%m-%d')] = {'total': total, 'hours': {}}
            data['days'][today_key] = {'total': 0.0, 'hours': {}}
            cost._save(data)
            cost._DATA_CACHE = None
            proj = cost.get_projection(window=7)
            self.assertIsNotNone(proj)
            self.assertAlmostEqual(proj['avg'], 2.0, places=4)


class GetMonthProjection(unittest.TestCase):
    def _seed_history(self, days_avg):
        """Seed `days_avg` ($/day) for several past days so get_projection has
        an average to work with. Returns (now, today_key)."""
        now = datetime.now()
        today_key = now.strftime('%Y-%m-%d')
        data = cost._load()
        days = data.setdefault('days', {})
        for offset in range(1, 4):
            day_num = now.day - offset
            if day_num < 1:
                continue
            d = datetime(now.year, now.month, day_num)
            days[d.strftime('%Y-%m-%d')] = {'total': days_avg, 'hours': {}}
        data['days'][today_key] = {'total': 0.0, 'hours': {}}
        cost._save(data)
        cost._DATA_CACHE = None
        return now, today_key

    def test_no_history_returns_none(self):
        with IsolatedCost():
            self.assertIsNone(cost.get_month_projection())

    def test_forecast_uses_month_to_date_plus_avg_remaining(self):
        with IsolatedCost():
            now, _ = self._seed_history(days_avg=2.0)
            month_key = now.strftime('%Y-%m')
            # Month-to-date $10; future days project at avg $2.
            data = cost._load()
            data.setdefault('months', {})[month_key] = 10.0
            cost._save(data)
            cost._DATA_CACHE = None

            proj = cost.get_month_projection(window=7)
            self.assertIsNotNone(proj)
            days_in_month = monthrange(now.year, now.month)[1]
            expected = 10.0 + 2.0 * max(0, days_in_month - now.day)
            self.assertAlmostEqual(proj['forecast'], expected, places=4)
            self.assertEqual(proj['days_in_month'], days_in_month)
            self.assertEqual(proj['day_of_month'], now.day)
            self.assertAlmostEqual(proj['avg_daily'], 2.0, places=4)
            self.assertAlmostEqual(proj['month_so_far'], 10.0, places=4)

    def test_forecast_handles_missing_month_entry(self):
        with IsolatedCost():
            now, _ = self._seed_history(days_avg=3.0)
            # No `months` entry yet — month_so_far should default to 0.
            proj = cost.get_month_projection(window=7)
            self.assertIsNotNone(proj)
            self.assertAlmostEqual(proj['month_so_far'], 0.0, places=4)
            days_in_month = monthrange(now.year, now.month)[1]
            self.assertAlmostEqual(
                proj['forecast'],
                3.0 * max(0, days_in_month - now.day),
                places=4,
            )

    def test_ratio_compares_mtd_to_expected(self):
        with IsolatedCost():
            now, _ = self._seed_history(days_avg=2.0)
            month_key = now.strftime('%Y-%m')
            data = cost._load()
            # Spend exactly avg × (day_of_month - 1) → ratio ≈ 1.0 if no
            # hourly history (expected_by_now = 0 for today's partial).
            data.setdefault('months', {})[month_key] = 2.0 * max(0, now.day - 1)
            cost._save(data)
            cost._DATA_CACHE = None

            proj = cost.get_month_projection(window=7)
            self.assertIsNotNone(proj)
            if now.day == 1:
                # First of the month: nothing past → ratio undefined.
                self.assertIsNone(proj['ratio'])
            else:
                self.assertAlmostEqual(proj['ratio'], 1.0, places=4)


if __name__ == '__main__':
    unittest.main()
