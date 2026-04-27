"""Unit tests for the producer-side render helpers.

`render_ttl_label`, `build_manifest`, and `build_cache_items` form the
producer→consumer interface — they decide what crosses the seam to the
host-side ticker. Locking the shape down here means future refactors
that rewire the rendering pipeline don't silently change the wire
format."""
import os
import sys
import unittest
from datetime import timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'lib'))

import render  # noqa: E402
from render import (  # noqa: E402
    ITEM_LIVE_TTL,
    ITEM_STATIC,
    TTL_GLYPH_DEFAULTS,
    build_cache_items,
    build_manifest,
    fmt_clock,
    fmt_countdown,
    render_ttl_label,
    ttl_tier,
)


class TtlTier(unittest.TestCase):
    def test_expired_at_zero(self):
        self.assertEqual(ttl_tier(0, 300, 60), 'expired')

    def test_expired_negative(self):
        self.assertEqual(ttl_tier(-50, 300, 60), 'expired')

    def test_warn_just_inside_window(self):
        self.assertEqual(ttl_tier(45, 300, 60), 'warn')

    def test_alert_between_thresholds(self):
        self.assertEqual(ttl_tier(120, 300, 60), 'alert')

    def test_ok_above_alert_threshold(self):
        self.assertEqual(ttl_tier(1800, 300, 60), 'ok')

    def test_alert_threshold_is_exclusive(self):
        # remaining == warn_secs is not "warn" (exclusive boundary)
        self.assertEqual(ttl_tier(60, 300, 60), 'alert')

    def test_ok_threshold_is_exclusive(self):
        self.assertEqual(ttl_tier(300, 300, 60), 'ok')


class FmtCountdown(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(fmt_countdown(0), '0:00')

    def test_negative_clamps_to_zero(self):
        self.assertEqual(fmt_countdown(-30), '0:00')

    def test_under_a_minute(self):
        self.assertEqual(fmt_countdown(42), '0:42')

    def test_with_minutes(self):
        self.assertEqual(fmt_countdown(125), '2:05')

    def test_truncates_subsecond(self):
        self.assertEqual(fmt_countdown(125.9), '2:05')


class FmtClock(unittest.TestCase):
    def test_utc(self):
        self.assertEqual(fmt_clock(1_700_000_000, timezone.utc), '22:13')

    def test_no_tz_uses_local(self):
        # Don't assert the value (depends on system tz); just that it's HH:MM.
        out = fmt_clock(1_700_000_000)
        self.assertRegex(out, r'^\d{2}:\d{2}$')


class RenderTtlLabel(unittest.TestCase):
    def _glyphs(self):
        # Use distinct sentinels so we can read the tier off the label.
        return {'ok': 'O', 'alert': 'A', 'warn': 'W', 'expired': 'E'}

    def test_expired_uses_expired_glyph_and_word(self):
        label, tier = render_ttl_label(
            -10, 1_700_000_000, 'countdown', 300, 60, self._glyphs(),
        )
        self.assertEqual(tier, 'expired')
        self.assertEqual(label, 'E expired')

    def test_countdown_emits_mm_ss(self):
        label, tier = render_ttl_label(
            125, 1_700_000_000, 'countdown', 300, 60, self._glyphs(),
        )
        self.assertEqual(tier, 'alert')
        self.assertEqual(label, 'A 2:05')

    def test_expiry_clock_uses_clock_format(self):
        label, tier = render_ttl_label(
            1800, 1_700_000_000, 'expiry_clock', 300, 60, self._glyphs(),
            tz=timezone.utc,
        )
        self.assertEqual(tier, 'ok')
        self.assertEqual(label, 'O 22:13')

    def test_glyphs_default_when_none_passed(self):
        label, tier = render_ttl_label(
            1800, 1_700_000_000, 'countdown', 300, 60, None,
        )
        self.assertEqual(tier, 'ok')
        self.assertTrue(label.startswith(TTL_GLYPH_DEFAULTS['ok'] + ' '))

    def test_partial_glyphs_fall_back_to_defaults(self):
        label, _ = render_ttl_label(
            1800, 1_700_000_000, 'countdown', 300, 60, {'ok': 'X'},
        )
        # Custom 'ok' wins; other tiers stay default — confirm by passing
        # `remaining` that maps to a tier we didn't override.
        self.assertTrue(label.startswith('X '))
        label_warn, tier = render_ttl_label(
            30, 1_700_000_000, 'countdown', 300, 60, {'ok': 'X'},
        )
        self.assertEqual(tier, 'warn')
        self.assertTrue(label_warn.startswith(TTL_GLYPH_DEFAULTS['warn'] + ' '))


class BuildCacheItems(unittest.TestCase):
    """`build_cache_items` is the only place that emits live_ttl into the
    manifest. The rules: countdown style + remaining > 0 → live_ttl;
    everything else → flat static fallback identical to native render."""

    def _data(self, transcript=None, model_id='claude-opus-4-7', cu=None):
        return {
            'session_id': 'unit',
            'transcript_path': transcript,
            'model': {'id': model_id},
            'context_window': {'current_usage': cu or {}},
        }

    def _theme(self):
        return {
            'primary': '#FFFFFF', 'secondary': '#CCCCCC', 'accent': '#5588FF',
            'muted': '#888888', 'warning': '#DCDCAA', 'error': '#F44747',
            'success': '#6A9955', 'dim': '#5A5A5A',
        }

    def test_static_fallback_when_no_transcript(self):
        # No transcript → no remaining → pure static items.
        items = build_cache_items(
            self._data(transcript=None),
            {'segments': {'cache': {'style': 'countdown'}}},
            self._theme(),
        )
        types = [it.get('type') for it in items]
        self.assertNotIn(ITEM_LIVE_TTL, types)

    def test_static_fallback_for_expiry_clock_style(self):
        import cache as cache_mod
        orig = cache_mod.get_cache_ttl_remaining
        cache_mod.get_cache_ttl_remaining = lambda *a, **k: 1500
        try:
            items = build_cache_items(
                self._data(transcript='/nope'),
                {'segments': {'cache': {'style': 'expiry_clock'}}},
                self._theme(),
            )
        finally:
            cache_mod.get_cache_ttl_remaining = orig
        types = [it.get('type') for it in items]
        # Expiry clock is minute-grained, doesn't need a tick.
        self.assertNotIn(ITEM_LIVE_TTL, types)

    def test_emits_live_ttl_for_countdown_with_remaining(self):
        import cache as cache_mod
        orig_remaining = cache_mod.get_cache_ttl_remaining
        orig_expiry = cache_mod.get_cache_expiry_epoch
        cache_mod.get_cache_ttl_remaining = lambda *a, **k: 1500
        cache_mod.get_cache_expiry_epoch = lambda *a, **k: 1714000000
        try:
            items = build_cache_items(
                self._data(transcript='/nope',
                           cu={'cache_read_input_tokens': 50000}),
                {'segments': {'cache': {'style': 'countdown'}}},
                self._theme(),
            )
        finally:
            cache_mod.get_cache_ttl_remaining = orig_remaining
            cache_mod.get_cache_expiry_epoch = orig_expiry

        live = [it for it in items if it.get('type') == ITEM_LIVE_TTL]
        self.assertEqual(len(live), 1, msg=items)
        ttl = live[0]
        # Wire-format keys the host-side ticker depends on.
        for k in ('expiry_epoch', 'style', 'ttl_seconds', 'alert_secs',
                  'warn_secs', 'glyphs', 'ansi_prefix', 'ansi_reset'):
            self.assertIn(k, ttl, msg=f'missing {k}: {ttl}')
        self.assertEqual(ttl['style'], 'countdown')
        # Per-tier ANSI prefixes resolved at publish time so tick.py never
        # needs theme machinery.
        for tier in ('ok', 'alert', 'warn', 'expired'):
            self.assertIn(tier, ttl['ansi_prefix'])

    def test_returns_empty_list_when_segment_disabled(self):
        # All sub-pieces off → nothing to render.
        items = build_cache_items(
            self._data(transcript=None),
            {'segments': {'cache': {
                'show_hit_ratio': False,
                'show_ttl': False,
                'show_at_risk': False,
                'style': 'countdown',
            }}},
            self._theme(),
        )
        self.assertEqual(items, [])


class BuildManifest(unittest.TestCase):
    def _theme(self):
        return {
            'primary': '#FFFFFF', 'secondary': '#CCCCCC', 'accent': '#5588FF',
            'muted': '#888888', 'warning': '#DCDCAA', 'error': '#F44747',
            'success': '#6A9955', 'dim': '#5A5A5A',
        }

    def test_returns_lines_dict(self):
        m = build_manifest({'model': {'display_name': 'X'}},
                           {'lines': [['model']]}, self._theme())
        self.assertIn('lines', m)
        self.assertIsInstance(m['lines'], list)

    def test_empty_segment_drops_line(self):
        # No data for any segment → empty rendered string → line dropped.
        m = build_manifest({},
                           {'lines': [['model']]}, self._theme())
        self.assertEqual(m['lines'], [])

    def test_separator_between_segments(self):
        m = build_manifest(
            {'model': {'display_name': 'M1'}, 'cwd': '/x'},
            {'lines': [['model', 'cwd']], 'separator': ' | '},
            self._theme(),
        )
        # Single coalesced static item carrying both segments + separator.
        self.assertEqual(len(m['lines']), 1)
        items = m['lines'][0]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['type'], ITEM_STATIC)
        self.assertIn('|', items[0]['ansi'])

    def test_lines_string_form_promoted_to_list_of_lists(self):
        # Back-compat: `lines: ["model","cwd"]` (single line) should work.
        m = build_manifest(
            {'model': {'display_name': 'M1'}, 'cwd': '/x'},
            {'lines': ['model', 'cwd']},
            self._theme(),
        )
        self.assertEqual(len(m['lines']), 1)


class AppendStaticCoalesces(unittest.TestCase):
    """Internal helper, but the manifest's compactness depends on it."""

    def test_consecutive_static_merge(self):
        items = []
        render._append_static(items, 'A')
        render._append_static(items, 'B')
        self.assertEqual(items, [{'type': ITEM_STATIC, 'ansi': 'AB'}])

    def test_empty_append_is_noop(self):
        items = [{'type': ITEM_STATIC, 'ansi': 'X'}]
        render._append_static(items, '')
        self.assertEqual(items, [{'type': ITEM_STATIC, 'ansi': 'X'}])

    def test_non_static_breaks_run(self):
        items = [{'type': ITEM_STATIC, 'ansi': 'X'}]
        items.append({'type': ITEM_LIVE_TTL, 'expiry_epoch': 0})
        render._append_static(items, 'Y')
        self.assertEqual(len(items), 3)
        self.assertEqual(items[-1]['ansi'], 'Y')


if __name__ == '__main__':
    unittest.main()
