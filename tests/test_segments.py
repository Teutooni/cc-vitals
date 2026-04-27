import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Ensure NO_COLOR doesn't leak in.
os.environ.pop('NO_COLOR', None)

import tests  # noqa: F401
import segments


def _strip_ansi(s):
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', s)


class RenderGitTimeout(unittest.TestCase):
    """When `get_git_info` returns the timeout sentinel (in a repo, no
    cache, git timed out), the segment must surface a visible warning
    rather than render empty."""

    def test_warning_marker_when_timeout(self):
        info = {
            'branch': None,
            'ahead': 0, 'behind': 0,
            'upstream': False,
            'added': 0, 'modified': 0, 'deleted': 0,
            'renamed': 0, 'untracked': 0,
            'op_state': None,
            'error': 'timeout',
        }
        with mock.patch.object(segments, 'get_git_info', return_value=info):
            out = segments.render_git(
                {'cwd': '/tmp/x'},
                {'icons': 'ascii'},
                {},
            )
        self.assertNotEqual(out, '')
        plain = _strip_ansi(out)
        self.assertIn('slow', plain)
        self.assertIn('?', plain)  # ascii fallback for the timeout glyph

    def test_no_repo_still_renders_empty(self):
        with mock.patch.object(segments, 'get_git_info', return_value=None):
            out = segments.render_git({'cwd': '/tmp/x'}, {}, {})
        self.assertEqual(out, '')


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


class FmtClock(unittest.TestCase):
    def test_renders_local_hh_mm(self):
        import time
        epoch = time.time() + 600  # 10 min from now
        out = segments._fmt_clock(epoch)
        self.assertRegex(out, r'^\d{2}:\d{2}$')

    def test_matches_localtime(self):
        import time
        epoch = 1_700_000_000
        expected = time.strftime('%H:%M', time.localtime(epoch))
        self.assertEqual(segments._fmt_clock(epoch), expected)

    def test_utc_tz_argument(self):
        from datetime import timezone
        # 2023-11-14 22:13:20 UTC.
        self.assertEqual(segments._fmt_clock(1_700_000_000, timezone.utc),
                         '22:13')

    def test_fixed_offset_argument(self):
        from datetime import timedelta, timezone
        # +05:30 of 22:13 UTC = 03:43 next day.
        tz = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(segments._fmt_clock(1_700_000_000, tz), '03:43')


class ResolveTz(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(segments._resolve_tz(None))

    def test_local_returns_none(self):
        self.assertIsNone(segments._resolve_tz('local'))
        self.assertIsNone(segments._resolve_tz('SYSTEM'))
        self.assertIsNone(segments._resolve_tz(''))

    def test_utc(self):
        from datetime import timezone
        self.assertEqual(segments._resolve_tz('UTC'), timezone.utc)
        self.assertEqual(segments._resolve_tz('utc'), timezone.utc)

    def test_positive_offset_with_colon(self):
        from datetime import timedelta
        tz = segments._resolve_tz('+05:30')
        self.assertEqual(tz.utcoffset(None), timedelta(hours=5, minutes=30))

    def test_negative_offset_no_colon(self):
        from datetime import timedelta
        tz = segments._resolve_tz('-0800')
        self.assertEqual(tz.utcoffset(None), timedelta(hours=-8))

    def test_iana_name_resolves_when_available(self):
        try:
            from zoneinfo import ZoneInfo  # noqa: F401
        except ImportError:
            self.skipTest('zoneinfo unavailable on this Python')
        tz = segments._resolve_tz('America/Los_Angeles')
        self.assertIsNotNone(tz)

    def test_unknown_tz_falls_back_to_none(self):
        self.assertIsNone(segments._resolve_tz('Not/A/Real/Zone'))

    def test_non_string_returns_none(self):
        self.assertIsNone(segments._resolve_tz(123))
        self.assertIsNone(segments._resolve_tz(['UTC']))


class ResolveTierSecs(unittest.TestCase):
    def test_int_passes_through(self):
        self.assertEqual(segments._resolve_tier_secs(42, 3600, 99), 42)
        self.assertEqual(segments._resolve_tier_secs(42, 300, 99), 42)

    def test_dict_picks_1h_for_long_ttl(self):
        out = segments._resolve_tier_secs({'1h': 300, '5m': 60}, 3600, 0)
        self.assertEqual(out, 300)

    def test_dict_picks_5m_for_short_ttl(self):
        out = segments._resolve_tier_secs({'1h': 300, '5m': 60}, 300, 0)
        self.assertEqual(out, 60)

    def test_dict_falls_back_when_tier_missing(self):
        out = segments._resolve_tier_secs({'1h': 300}, 300, 99)
        self.assertEqual(out, 99)

    def test_none_returns_fallback(self):
        self.assertEqual(segments._resolve_tier_secs(None, 3600, 99), 99)

    def test_float_coerced_to_int(self):
        self.assertEqual(segments._resolve_tier_secs(60.5, 3600, 0), 60)


class TierKey(unittest.TestCase):
    def test_5m_for_300(self):
        self.assertEqual(segments._tier_key(300), '5m')

    def test_1h_for_3600(self):
        self.assertEqual(segments._tier_key(3600), '1h')

    def test_in_between_falls_back_to_1h(self):
        self.assertEqual(segments._tier_key(900), '1h')

    def test_below_5m_treated_as_5m(self):
        self.assertEqual(segments._tier_key(60), '5m')


class TtlTier(unittest.TestCase):
    def test_expired_at_zero(self):
        self.assertEqual(segments._ttl_tier(0, 300, 60), 'expired')

    def test_expired_negative(self):
        self.assertEqual(segments._ttl_tier(-10, 300, 60), 'expired')

    def test_warn_below_warn_secs(self):
        self.assertEqual(segments._ttl_tier(45, 300, 60), 'warn')

    def test_alert_below_alert_secs(self):
        self.assertEqual(segments._ttl_tier(120, 300, 60), 'alert')

    def test_ok_above_alert_secs(self):
        self.assertEqual(segments._ttl_tier(1800, 300, 60), 'ok')

    def test_warn_boundary(self):
        # exactly warn_secs => not warn, just alert
        self.assertEqual(segments._ttl_tier(60, 300, 60), 'alert')

    def test_alert_boundary(self):
        # exactly alert_secs => not alert, just ok
        self.assertEqual(segments._ttl_tier(300, 300, 60), 'ok')


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

    def test_basename_only_strips_path(self):
        cfg = {**self.cfg, 'segments': {'cwd': {'basename_only': True}}}
        out = segments.render_cwd(
            {'cwd': '/very/long/path/that/goes/forever/here'},
            cfg, self.theme,
        )
        self.assertIn('here', out)
        self.assertNotIn('forever', out)
        self.assertNotIn('…', out)

    def test_basename_only_keeps_tilde_for_home(self):
        home = os.path.expanduser('~')
        cfg = {**self.cfg, 'segments': {'cwd': {'basename_only': True}}}
        out = segments.render_cwd({'cwd': home}, cfg, self.theme)
        self.assertIn('~', out)


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


class FmtCountdown(unittest.TestCase):
    """`mm:ss` formatter used by the `countdown` cache style."""

    def test_zero(self):
        self.assertEqual(segments._fmt_countdown(0), '0:00')

    def test_negative_clamped(self):
        self.assertEqual(segments._fmt_countdown(-30), '0:00')

    def test_under_minute(self):
        self.assertEqual(segments._fmt_countdown(42), '0:42')

    def test_pads_seconds(self):
        self.assertEqual(segments._fmt_countdown(65), '1:05')

    def test_full_hour_one_h_tier(self):
        self.assertEqual(segments._fmt_countdown(3599), '59:59')

    def test_drops_fractional_seconds(self):
        self.assertEqual(segments._fmt_countdown(125.9), '2:05')


class RenderCacheTtlStyle(unittest.TestCase):
    """The TTL piece picks display style from `segments.cache.style`.
    Default is `expiry_clock` (HH:mm) for native; `countdown` (mm:ss) is
    for tmux mode where a 1 Hz renderer ticks the value."""

    def setUp(self):
        # Use ascii icons + claude-default theme so we get deterministic glyphs.
        self.theme = segments.__dict__.get('THEMES', None)  # not exposed; build minimal theme
        self.theme = {
            'primary': '#FFFFFF', 'secondary': '#FFFFFF', 'accent': '#FFFFFF',
            'muted': '#888888', 'warning': '#FFFF00', 'error': '#FF0000',
            'success': '#00FF00', 'dim': '#444444',
        }
        self._tmp = TemporaryDirectory()
        self.transcript = Path(self._tmp.name) / 't.jsonl'
        self.transcript.write_text('hi')

    def tearDown(self):
        self._tmp.cleanup()

    def _data(self):
        return {
            'session_id': 'sess-style',
            'transcript_path': str(self.transcript),
            # No assistant turns yet — `state.totals.turns` will be 0, hit
            # ratio shows '—%' and we focus on the TTL piece.
        }

    def _config(self, **cache_overrides):
        cache_seg = {
            'show_hit_ratio': False,
            'show_at_risk': False,
            'show_icon': False,
            'ttl_seconds': 3600,
        }
        cache_seg.update(cache_overrides)
        return {
            'icons': 'ascii',
            'segments': {'cache': cache_seg},
            'colors': {},
        }

    def test_default_style_is_expiry_clock(self):
        out = segments.render_cache(self._data(), self._config(), self.theme)
        plain = _strip_ansi(out)
        # `HH:MM` clock — exactly one colon, two digits each side.
        import re
        self.assertRegex(plain, r'\b\d{2}:\d{2}\b')
        # Countdown form would be `m:ss` with a single-digit minute on the
        # 1h cache; the strict 2-digit clock above wouldn't match `0:00`.

    def test_countdown_style_emits_mm_ss(self):
        out = segments.render_cache(
            self._data(),
            self._config(style='countdown'),
            self.theme,
        )
        plain = _strip_ansi(out)
        # mm:ss with 2-digit seconds, 1+-digit minutes (e.g. `59:59`).
        import re
        self.assertRegex(plain, r'\b\d{1,2}:\d{2}\b')

    def test_expired_label_independent_of_style(self):
        # Stale transcript so remaining ≤ 0.
        old = 1.0  # epoch=1 → very stale
        os.utime(self.transcript, (old, old))
        for style in (None, 'countdown'):
            cfg = self._config()
            if style is not None:
                cfg['segments']['cache']['style'] = style
            out = segments.render_cache(self._data(), cfg, self.theme)
            self.assertIn('expired', _strip_ansi(out))


class RenderCacheAtRisk(unittest.TestCase):
    """`at_risk` shows what's at stake if the prompt cache expires before
    the next turn. Per-turn each cached token lands in exactly one of
    cache_read (hit) or cache_creation (miss/rebuild), so the prefix size
    is `max(read, creation)` — using read alone blanks the segment on the
    rebuild turn (regression for issue #6)."""

    def setUp(self):
        self.theme = {
            'primary': '#FFFFFF', 'secondary': '#FFFFFF', 'accent': '#FFFFFF',
            'muted': '#888888', 'warning': '#FFFF00', 'error': '#FF0000',
            'success': '#00FF00', 'dim': '#444444',
        }
        self._tmp = TemporaryDirectory()
        self.transcript = Path(self._tmp.name) / 't.jsonl'
        self.transcript.write_text('hi')

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self):
        return {
            'icons': 'ascii',
            'segments': {'cache': {
                'show_hit_ratio': False,
                'show_ttl': False,
                'show_icon': False,
                'show_at_risk': True,
                'ttl_seconds': 3600,
            }},
            'colors': {},
        }

    def _data(self, current_usage):
        return {
            'session_id': 'sess-risk',
            'transcript_path': str(self.transcript),
            'model': {'id': 'claude-opus-4-7'},
            'context_window': {'current_usage': current_usage},
        }

    def test_shown_on_cache_hit_turn(self):
        # 100K cache_read on Opus 1h → ~$2.85.
        data = self._data({'cache_read_input_tokens': 100_000})
        out = _strip_ansi(segments.render_cache(data, self._config(), self.theme))
        self.assertIn('$2.85', out)

    def test_shown_on_rebuild_turn_with_only_cache_creation(self):
        # Right after expiry: read=0, but the rebuild stuffed 100K into
        # cache_creation. The estimate should still display — what's at
        # risk on the *next* miss is the freshly-built prefix.
        data = self._data({
            'cache_read_input_tokens': 0,
            'cache_creation_input_tokens': 100_000,
        })
        out = _strip_ansi(segments.render_cache(data, self._config(), self.theme))
        self.assertIn('$2.85', out)

    def test_uses_larger_bucket_when_both_present(self):
        # max(read, creation), not the sum — they describe the same prefix
        # split across hit/miss boundaries within one turn.
        data = self._data({
            'cache_read_input_tokens': 100_000,
            'cache_creation_input_tokens': 20_000,
        })
        out = _strip_ansi(segments.render_cache(data, self._config(), self.theme))
        self.assertIn('$2.85', out)
        self.assertNotIn('$3.42', out)  # would be the sum (120K * 28.5/M)


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
