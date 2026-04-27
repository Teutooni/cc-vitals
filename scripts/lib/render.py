"""Pure render helpers shared by native CC mode and tmux mode.

The functions here are deliberately free of `config` / `theme` arguments and
side-effects: each takes the minimal runtime values needed to compute a
piece of the statusline. That makes them callable from two places:

  - `segments.render_cache` (native CC mode), which reads config + theme
    from the request and applies coloring inline.
  - `scripts/tick.py` (tmux host-side renderer), which receives parameters
    via the published manifest JSON and applies coloring at 1 Hz tick time.

Keeping the runtime-tickable bits here means the producer (in container)
publishes parameters and the consumer (host tmux) renders them live —
without sharing config, theme, or session state across the boundary.

`build_manifest` is the producer-side entry point: it walks the configured
lines/segments and emits a list of items per line, mixing pre-rendered
static ANSI runs with structured live items (currently just `live_ttl` for
the cache countdown). The host-side ticker is the only consumer that needs
to understand live items; everything else is opaque ANSI bytes."""
import time as _time


# Glyph tier defaults for the cache TTL segment. Picked so each event-driven
# CC re-render conveys urgency passively — no polling required.
TTL_GLYPH_DEFAULTS = {
    'ok':      '⏳',
    'alert':   '⏰',
    'warn':    '⚠',
    'expired': '⚠',
}


def ttl_tier(remaining, alert_secs, warn_secs):
    """Map seconds-remaining onto a tier name.

    Tiers in increasing urgency: 'ok' → 'alert' → 'warn' → 'expired'.
    `alert_secs` and `warn_secs` are seconds-remaining thresholds (warn is
    closer to expiry than alert)."""
    if remaining <= 0:
        return 'expired'
    if remaining < warn_secs:
        return 'warn'
    if remaining < alert_secs:
        return 'alert'
    return 'ok'


def fmt_countdown(remaining):
    """`mm:ss` for a countdown display. Used by the `countdown` cache style
    when a 1 Hz renderer (tmux) ticks the value smoothly. Native mode can't
    tick a countdown without polling, so it sticks with `expiry_clock`."""
    secs = max(0, int(remaining))
    return f'{secs // 60}:{secs % 60:02d}'


def fmt_clock(epoch_seconds, tz=None):
    """HH:mm at the given tzinfo (or system-local if ``tz is None``).

    Used to show *when* the cache expires rather than a live countdown
    — minute-grained clocks survive low-frequency statusline refreshes
    (CC's docs explicitly endorse this for time data). The tz hook lets
    a UTC container display the host's wall-clock time."""
    if tz is None:
        return _time.strftime('%H:%M', _time.localtime(epoch_seconds))
    from datetime import datetime
    return datetime.fromtimestamp(epoch_seconds, tz).strftime('%H:%M')


def render_ttl_label(remaining, expiry_epoch, style, alert_secs, warn_secs,
                     glyphs=None, tz=None):
    """Compute the cache TTL display label and its urgency tier.

    Returns ``(label, tier)`` so the caller can pick a color independently —
    native mode resolves color tokens via theme; tmux mode embeds resolved
    ANSI per-tier in the manifest and looks up by tier at tick time.

    Args:
        remaining: seconds until expiry (negative means expired).
        expiry_epoch: wall-clock epoch when the cache expires; consumed
            only by ``style='expiry_clock'``.
        style: ``'countdown'`` (mm:ss, ticks each second) or
            ``'expiry_clock'`` (HH:mm wall time, minute-grained).
        alert_secs, warn_secs: tier thresholds (seconds).
        glyphs: optional dict overriding ``TTL_GLYPH_DEFAULTS``. Missing
            keys fall back to the default glyph for that tier.
        tz: tzinfo for ``expiry_clock``, or ``None`` for system-local.
    """
    g = dict(TTL_GLYPH_DEFAULTS)
    if isinstance(glyphs, dict):
        g.update({k: v for k, v in glyphs.items() if isinstance(v, str)})

    tier = ttl_tier(remaining, alert_secs, warn_secs)
    if tier == 'expired':
        return f"{g['expired']} expired", tier
    if style == 'countdown':
        return f"{g[tier]} {fmt_countdown(remaining)}", tier
    clock = fmt_clock(expiry_epoch, tz) if expiry_epoch is not None else ''
    return f"{g[tier]} {clock}".rstrip(), tier


# Manifest item types used by the tmux producer/consumer split. tick.py
# pattern-matches on these `type` values, so the strings are part of the
# wire format — bump a version field if the shape ever changes.
ITEM_STATIC = 'static'
ITEM_LIVE_TTL = 'live_ttl'


def _append_static(items, ansi):
    """Coalesce consecutive static items so the manifest stays compact."""
    if not ansi:
        return
    if items and items[-1].get('type') == ITEM_STATIC:
        items[-1]['ansi'] += ansi
    else:
        items.append({'type': ITEM_STATIC, 'ansi': ansi})


def build_cache_items(data, config, theme):
    """Cache segment as a list of manifest items.

    For 'countdown' style with a known expiry, the TTL portion becomes a
    `live_ttl` item carrying the parameters needed for the host-side ticker
    to compute mm:ss + tier color every second; surrounding portions (hit
    ratio, at_risk) and the inter-part ' · ' separators are emitted as
    static ANSI runs. For all other cases the entire segment renders to a
    single static item identical to native `render_cache` output."""
    # Local imports — render.py is imported by segments.py at module load,
    # so segments-side helpers must be resolved lazily.
    from cache import get_cache_expiry_epoch, get_cache_ttl_remaining
    from colors import RESET, ansi_prefix
    from segments import (
        cache_at_risk_part,
        cache_context,
        cache_hit_part,
        cache_ttl_part,
        _ttl_color,
        _ttl_glyphs,
        _ttl_thresholds,
    )

    ctx = cache_context(data, config)
    seg = ctx['seg']
    style = seg.get('style', 'expiry_clock')
    transcript = ctx['transcript']
    session_id = ctx['session_id']
    ttl_seconds = ctx['ttl_seconds']
    remaining = (
        get_cache_ttl_remaining(transcript, ttl_seconds, session_id)
        if seg.get('show_ttl', True) else None
    )

    # Static fallback: any case that doesn't actually need a live tick.
    # That's every style other than 'countdown', plus countdown when there's
    # no remaining-seconds yet (no transcript), plus the already-expired
    # state (label is just "⚠ expired", which doesn't tick).
    if style != 'countdown' or remaining is None or remaining <= 0:
        s = ' · '.join(
            p for p in (
                cache_hit_part(ctx, theme),
                cache_ttl_part(ctx, theme),
                cache_at_risk_part(ctx, theme),
            ) if p
        )
        return [{'type': ITEM_STATIC, 'ansi': s}] if s else []

    # Countdown with a live TTL: split into static-pre, live_ttl, static-post.
    hit = cache_hit_part(ctx, theme)
    at_risk = cache_at_risk_part(ctx, theme)
    alert_secs, warn_secs = _ttl_thresholds(seg, ttl_seconds)
    glyphs = _ttl_glyphs(seg)
    expiry = get_cache_expiry_epoch(transcript, ttl_seconds, session_id)
    c = ctx['c']
    # Resolve theme colors to raw ANSI prefixes once at publish time so the
    # host-side ticker doesn't need theme machinery — it just looks up the
    # tier and wraps the live label.
    ansi_per_tier = {
        tier: ansi_prefix(_ttl_color(tier, c), theme)
        for tier in ('ok', 'alert', 'warn', 'expired')
    }

    items = []
    if hit:
        _append_static(items, hit + ' · ')
    items.append({
        'type': ITEM_LIVE_TTL,
        'expiry_epoch': expiry,
        'style': 'countdown',
        'ttl_seconds': ttl_seconds,
        'alert_secs': alert_secs,
        'warn_secs': warn_secs,
        'glyphs': glyphs,
        'ansi_prefix': ansi_per_tier,
        'ansi_reset': RESET,
    })
    if at_risk:
        _append_static(items, ' · ' + at_risk)
    return items


def build_manifest(data, config, theme):
    """Build the per-line manifest the tmux ticker consumes.

    Walks `config['lines']` (same shape statusline.py uses), renders each
    segment, and stitches the results together with the configured separator.
    Most segments produce a single static item; the cache segment can split
    into static + live_ttl + static when its countdown style is active.

    Returns ``{"lines": [[item, ...], ...]}``. Empty lines (every segment
    rendered empty) are dropped, mirroring native statusline.py behavior."""
    from colors import paint
    from segments import render_segment

    sep = config.get('separator', ' │ ')
    sep_col = config.get('colors', {}).get('separator', 'dim')
    sep_ansi = paint(sep, sep_col, theme, dim=True)

    lines_cfg = config.get('lines')
    if not lines_cfg:
        lines_cfg = [['model', 'cwd', 'git', 'env', 'cost', 'context']]
    if lines_cfg and isinstance(lines_cfg[0], str):
        lines_cfg = [lines_cfg]

    out_lines = []
    for line_segments in lines_cfg:
        # First collect non-empty per-segment item lists, *then* interleave
        # the separator. Doing it in two passes avoids leaving a trailing or
        # leading separator when a segment renders empty.
        segs_items = []
        for seg_name in line_segments:
            if seg_name == 'cache':
                items = build_cache_items(data, config, theme)
            else:
                s = render_segment(seg_name, data, config, theme)
                items = [{'type': ITEM_STATIC, 'ansi': s}] if s else []
            if items:
                segs_items.append(items)

        if not segs_items:
            continue

        line_items = []
        for i, items in enumerate(segs_items):
            if i > 0:
                _append_static(line_items, sep_ansi)
            for it in items:
                if it.get('type') == ITEM_STATIC:
                    _append_static(line_items, it['ansi'])
                else:
                    line_items.append(it)
        out_lines.append(line_items)

    return {'lines': out_lines}
