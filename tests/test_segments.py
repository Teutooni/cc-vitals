import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Ensure NO_COLOR doesn't leak in.
os.environ.pop('NO_COLOR', None)

import tests  # noqa: F401
import segments


class ShortenModelName(unittest.TestCase):
    def test_extracts_token(self):
        self.assertEqual(
            segments._shorten_model_name('Opus 4.7 (1M context)'),
            'Opus 4.7 [1M]',
        )

    def test_extracts_uppercase(self):
        self.assertEqual(
            segments._shorten_model_name('Sonnet (200k input)'),
            'Sonnet [200K]',
        )

    def test_no_paren_unchanged(self):
        self.assertEqual(
            segments._shorten_model_name('Claude Opus 4.7'),
            'Claude Opus 4.7',
        )

    def test_paren_without_token_uses_inside(self):
        self.assertEqual(
            segments._shorten_model_name('Claude (beta)'),
            'Claude [beta]',
        )

    def test_disabled_returns_input(self):
        self.assertEqual(
            segments._shorten_model_name('Opus 4.7 (1M context)', enabled=False),
            'Opus 4.7 (1M context)',
        )

    def test_empty_input(self):
        self.assertEqual(segments._shorten_model_name('', enabled=True), '')
        self.assertEqual(segments._shorten_model_name(None, enabled=True), None)


class FmtTokens(unittest.TestCase):
    def test_under_1k_raw(self):
        self.assertEqual(segments._fmt_tokens(0), '0')
        self.assertEqual(segments._fmt_tokens(523), '523')
        self.assertEqual(segments._fmt_tokens(999), '999')

    def test_kilo_one_decimal_under_10k(self):
        self.assertEqual(segments._fmt_tokens(1234), '1.2K')
        self.assertEqual(segments._fmt_tokens(9999), '10.0K')

    def test_kilo_no_decimal_at_10k(self):
        self.assertEqual(segments._fmt_tokens(12_000), '12K')
        self.assertEqual(segments._fmt_tokens(523_000), '523K')

    def test_mega_one_decimal(self):
        self.assertEqual(segments._fmt_tokens(1_200_000), '1.2M')

    def test_handles_none(self):
        self.assertEqual(segments._fmt_tokens(None), '0')


class FmtTtl(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(segments._fmt_ttl(45), '0:45')

    def test_minutes(self):
        self.assertEqual(segments._fmt_ttl(125), '2:05')

    def test_hours(self):
        self.assertEqual(segments._fmt_ttl(3725), '1:02:05')

    def test_negative_clamps(self):
        self.assertEqual(segments._fmt_ttl(-5), '0:00')


class DetectEffort(unittest.TestCase):
    """Isolate the home-directory fallback path with a temp HOME — otherwise
    the real ~/.claude/settings.json on the test host can leak in."""
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._patch = mock.patch.object(Path, 'home',
                                        return_value=Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_top_level_effort_dict(self):
        data = {'effort': {'level': 'high'}}
        self.assertEqual(segments._detect_effort(data), 'high')

    def test_legacy_model_effort(self):
        data = {'model': {'effort': 'medium'}}
        self.assertEqual(segments._detect_effort(data), 'medium')

    def test_thinking_budget_fallback(self):
        data = {'model': {'thinking_budget': 8000}}
        self.assertEqual(segments._detect_effort(data), '8000')

    def test_output_style_effort(self):
        data = {'output_style': {'effort': 'low'}}
        self.assertEqual(segments._detect_effort(data), 'low')

    def test_no_effort_returns_none(self):
        self.assertIsNone(segments._detect_effort({}))

    def test_settings_file_fallback(self):
        claude_dir = Path(self._tmp.name) / '.claude'
        claude_dir.mkdir()
        (claude_dir / 'settings.json').write_text('{"effortLevel": "high"}')
        self.assertEqual(segments._detect_effort({}), 'high')


class ContextThresholdColor(unittest.TestCase):
    def test_normal_under_75(self):
        c = {'context.normal': 'muted', 'context.warn': 'warning',
             'context.crit': 'error'}
        self.assertEqual(segments._context_threshold_color(50, c), 'muted')

    def test_warn_at_75(self):
        c = {'context.normal': 'muted', 'context.warn': 'warning',
             'context.crit': 'error'}
        self.assertEqual(segments._context_threshold_color(80, c), 'warning')

    def test_crit_at_90(self):
        c = {'context.normal': 'muted', 'context.warn': 'warning',
             'context.crit': 'error'}
        self.assertEqual(segments._context_threshold_color(95, c), 'error')


class RenderCwd(unittest.TestCase):
    def setUp(self):
        self.theme = {'primary': '#FFFFFF'}
        self.cfg = {'colors': {'cwd': 'primary'}, 'icons': 'ascii',
                    'segments': {'cwd': {'max_length': 40}}}

    def test_home_path_rendered_as_tilde(self):
        home = os.path.expanduser('~')
        out = segments.render_cwd({'cwd': home}, self.cfg, self.theme)
        self.assertIn('~', out)
        # Crucially: not '~/extra' for plain home.
        self.assertNotIn('~/', out)

    def test_substring_match_does_not_strip(self):
        # If home is /home/teu and cwd is /home/teutooni, the old buggy
        # code would render '~tooni'. The fix keeps the full path.
        home = '/tmp/cc-vitals-fake-home'
        cwd = '/tmp/cc-vitals-fake-home2/sub'
        original = os.environ.get('HOME')
        os.environ['HOME'] = home
        try:
            out = segments.render_cwd({'cwd': cwd}, self.cfg, self.theme)
        finally:
            if original is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = original
        self.assertIn('cc-vitals-fake-home2', out)
        self.assertNotIn('~tooni', out)
        self.assertNotIn('~2', out)

    def test_truncation_for_deep_path(self):
        cfg = {**self.cfg, 'segments': {'cwd': {'max_length': 20}}}
        out = segments.render_cwd(
            {'cwd': '/very/long/path/that/goes/forever/here'},
            cfg, self.theme,
        )
        self.assertIn('…', out)


class RenderDuration(unittest.TestCase):
    def test_seconds_only(self):
        out = segments.render_duration(
            {'cost': {'total_duration_ms': 42_000}},
            {'colors': {}, 'icons': 'ascii', 'segments': {}},
            {},
        )
        self.assertIn('42s', out)

    def test_minutes(self):
        out = segments.render_duration(
            {'cost': {'total_duration_ms': 90_500}},
            {'colors': {}, 'icons': 'ascii', 'segments': {}},
            {},
        )
        self.assertIn('1m30s', out)

    def test_hours(self):
        out = segments.render_duration(
            {'cost': {'total_duration_ms': 3_660_000}},
            {'colors': {}, 'icons': 'ascii', 'segments': {}},
            {},
        )
        self.assertIn('1h01m', out)


class CostForecastSegments(unittest.TestCase):
    """Renderers consult cost data, so swap in a temp store and stub the
    projection helpers with deterministic shapes."""

    def setUp(self):
        self.theme = {
            'primary': '#FFFFFF', 'muted': '#888888', 'success': '#0F0',
            'warning': '#FF0', 'error': '#F00',
        }
        self.cfg = {
            'colors': {
                'cost_day_forecast.avg': 'muted', 'cost_day_forecast.over': 'error',
                'cost_month_forecast.forecast': 'muted',
                'cost_month_forecast.over': 'error',
            },
            'icons': 'ascii',
            'segments': {
                'cost_day_forecast': {'window': 7, 'show_arrow': True, 'show_avg': True},
                'cost_month_forecast': {'window': 7, 'show_arrow': True, 'decimals': 0},
            },
        }

    def test_day_forecast_no_history_renders_dash(self):
        with mock.patch.object(segments, 'get_projection', return_value=None):
            out = segments.render_cost_day_forecast({}, self.cfg, self.theme)
        self.assertIn('—/d', out)

    def test_day_forecast_with_pace_includes_arrow(self):
        proj = {'avg': 2.0, 'today_so_far': 4.0, 'expected_by_now': 1.0,
                'ratio': 1.5, 'enough': True,
                'days_sampled': 5, 'hour_days_sampled': 5}
        with mock.patch.object(segments, 'get_projection', return_value=proj):
            out = segments.render_cost_day_forecast({}, self.cfg, self.theme)
        self.assertIn('$3.00', out)  # 1.5 × 2.0
        self.assertIn('↑', out)
        self.assertIn('$2.00/d', out)

    def test_month_forecast_no_history_renders_dash(self):
        with mock.patch.object(segments, 'get_month_projection', return_value=None):
            out = segments.render_cost_month_forecast({}, self.cfg, self.theme)
        self.assertIn('—/mo', out)

    def test_month_forecast_renders_dollar_amount(self):
        proj = {'forecast': 137.4, 'month_so_far': 50.0, 'avg_daily': 4.0,
                'days_in_month': 30, 'day_of_month': 10, 'days_remaining': 20,
                'ratio': 1.2, 'enough': True}
        with mock.patch.object(segments, 'get_month_projection', return_value=proj):
            out = segments.render_cost_month_forecast({}, self.cfg, self.theme)
        self.assertIn('$137/mo', out)  # decimals=0
        self.assertIn('↑', out)

    def test_legacy_cost_avg_alias_routes_to_day_forecast(self):
        self.assertIs(
            segments.RENDERERS['cost-avg'],
            segments.RENDERERS['cost-day-forecast'],
        )

    def test_legacy_cost_avg_config_block_still_honored(self):
        cfg = {
            'colors': {'cost_avg.avg': '#ABCDEF'},
            'icons': 'ascii',
            'segments': {'cost_avg': {'window': 14, 'show_arrow': False, 'show_avg': True}},
        }
        captured = {}
        def fake_proj(window):
            captured['window'] = window
            return None
        with mock.patch.object(segments, 'get_projection', side_effect=fake_proj):
            segments.render_cost_day_forecast({}, cfg, self.theme)
        self.assertEqual(captured['window'], 14)


class RenderSegmentSwallowsErrors(unittest.TestCase):
    def test_unknown_segment_returns_empty(self):
        self.assertEqual(segments.render_segment('nonexistent', {}, {}, {}), '')

    def test_renderer_exception_swallowed(self):
        original = segments.RENDERERS['model']
        segments.RENDERERS['boom'] = lambda *a, **k: 1 / 0
        try:
            self.assertEqual(segments.render_segment('boom', {}, {}, {}), '')
        finally:
            del segments.RENDERERS['boom']
            segments.RENDERERS['model'] = original

    def test_debug_env_var_re_raises(self):
        original = segments.RENDERERS['model']
        segments.RENDERERS['boom'] = lambda *a, **k: 1 / 0
        os.environ['CC_VITALS_DEBUG'] = '1'
        try:
            with self.assertRaises(ZeroDivisionError):
                segments.render_segment('boom', {}, {}, {})
        finally:
            os.environ.pop('CC_VITALS_DEBUG', None)
            del segments.RENDERERS['boom']
            segments.RENDERERS['model'] = original


if __name__ == '__main__':
    unittest.main()
