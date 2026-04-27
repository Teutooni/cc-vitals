#!/usr/bin/env python3
"""Tmux `status-format` entrypoint — host-side per-tick renderer.

Tmux re-runs this every `status-interval` seconds (1 by default). We:
  1. Resolve the slot for this tmux session — argv[1] is the canonical
     form (`#{session_name}`), with `CC_VITALS_SLOT` and an mtime fallback
     for users not running through the `cct` wrapper.
  2. Read the producer-published per-line manifest from
     ``$CC_VITALS_DUMP_DIR/<slot>.line<N>.json``.
  3. Walk the items list. ``static`` items emit their pre-rendered ANSI
     verbatim; ``live_ttl`` items get formatted *now* using the current
     wall-clock time so the cache countdown ticks smoothly.
  4. Translate the joined ANSI output to tmux markup so the status bar
     interprets colors instead of printing literal escapes.

Crucially, we do not need transcript paths, theme machinery, or cache
state on this side of the boundary — the producer rendered everything
that doesn't tick and resolved per-tier ANSI prefixes for the live items.
That keeps the host install minimal and the producer/consumer cross-boundary
seam to a single bind-mounted directory of small JSON files.

When no manifest is available (no CC running, or the dump is stale beyond
the discovery TTL), print nothing — tmux shows an empty bar slot rather
than a stale-but-loud one.
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from publish import discover_latest_slot, read_line  # noqa: E402
from render import ITEM_LIVE_TTL, ITEM_STATIC, render_ttl_label  # noqa: E402
from session_discovery import resolve_slot  # noqa: E402
from tmux_format import ansi_to_tmux  # noqa: E402


def _format_item(item, now):
    """Convert one manifest item to a raw-ANSI fragment. Unknown types are
    skipped silently — better a missing piece than a broken status bar."""
    t = item.get('type')
    if t == ITEM_STATIC:
        return item.get('ansi') or ''
    if t == ITEM_LIVE_TTL:
        expiry = item.get('expiry_epoch')
        if expiry is None:
            return ''
        remaining = expiry - now
        label, tier = render_ttl_label(
            remaining,
            expiry,
            item.get('style', 'countdown'),
            int(item.get('alert_secs', 300)),
            int(item.get('warn_secs', 60)),
            item.get('glyphs') or {},
        )
        prefix = (item.get('ansi_prefix') or {}).get(tier, '')
        reset = item.get('ansi_reset', '')
        return f'{prefix}{label}{reset}' if prefix else label
    return ''


def _render_manifest_line(manifest_line, now=None):
    """Walk a single line's items and return the joined ANSI string."""
    if now is None:
        now = time.time()
    items = (manifest_line or {}).get('items') or []
    return ''.join(_format_item(it, now) for it in items)


def main():
    """Emit one cc-vitals line at the requested index.

    Tmux's `#(...)` substitution flattens newlines into the same status
    row, so we can't ship multi-line output through a single
    `status-format[N]` directive. The conf wires one `status-format` row
    per cc-vitals line and passes the line index as argv[2] —
    `tick.py <slot> <line-index>`."""
    argv_slot = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        line_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    except ValueError:
        line_index = 0

    slot = resolve_slot(argv_slot=argv_slot)
    if not slot:
        slot = discover_latest_slot(line_index=line_index)
    if not slot:
        return

    manifest_line = read_line(slot, line_index)
    if not manifest_line:
        return

    ansi = _render_manifest_line(manifest_line)
    if ansi:
        sys.stdout.write(ansi_to_tmux(ansi))


if __name__ == '__main__':
    main()
