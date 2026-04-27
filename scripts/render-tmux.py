#!/usr/bin/env python3
"""Tmux `status-format` entrypoint.

Tmux re-runs this every `status-interval` seconds (1 by default). We:
  1. Resolve the slot for this tmux session — argv[1] is the canonical
     form (`#{session_name}`), with `CC_VITALS_SLOT` and an mtime fallback
     for users not running through the `cct` wrapper.
  2. Read the dumped CC stdin from `sessions/<slot>.json`.
  3. Run the same segment pipeline as native `statusline.py` *minus* the
     ingest step — render is a pure read; mutation already happened in
     `scripts/ingest.py` on the originating CC event.
  4. Translate the joined ANSI output to tmux markup so the status bar
     interprets colors instead of printing literal escapes.

When no dump is available (no CC running, or the dump is stale beyond
the discovery TTL), print nothing — tmux shows an empty bar slot rather
than a stale-but-loud one.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from colors import THEMES, paint  # noqa: E402
from config import load_config  # noqa: E402
from segments import render_segment  # noqa: E402
from session_discovery import (  # noqa: E402
    discover_latest,
    resolve_slot,
    session_path,
)
from tmux_format import ansi_to_tmux  # noqa: E402


def _resolve_theme(config):
    t = config.get('theme', 'vs-dark-modern')
    if isinstance(t, dict):
        return t
    return THEMES.get(t, THEMES['vs-dark-modern'])


def _load_dump(argv_slot):
    """Return parsed CC stdin dump or None if nothing usable was found.

    Slot precedence: argv[1] (tmux passes `#{session_name}`) > env var >
    mtime fallback over `sessions/*.json`."""
    slot = resolve_slot(argv_slot=argv_slot)
    path = session_path(slot) if slot else None
    if path is None or not path.exists():
        path = discover_latest()
    if path is None:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _apply_tmux_defaults(config):
    """Tmux mode renders at 1 Hz, so the cache TTL gets a smooth countdown
    by default rather than the minute-grained expiry clock the native mode
    has to use. Anything the user sets explicitly wins."""
    seg = config.setdefault('segments', {}).setdefault('cache', {})
    seg.setdefault('style', 'countdown')


def _render_lines(data, config, theme):
    separator = config.get('separator', ' │ ')
    sep_col = config.get('colors', {}).get('separator', 'dim')
    colored_sep = paint(separator, sep_col, theme, dim=True)

    lines_cfg = config.get('lines')
    if not lines_cfg:
        lines_cfg = [['model', 'cwd', 'git', 'env', 'cost', 'context']]
    if lines_cfg and isinstance(lines_cfg[0], str):
        lines_cfg = [lines_cfg]

    out_lines = []
    for line_segments in lines_cfg:
        rendered = []
        for seg in line_segments:
            s = render_segment(seg, data, config, theme)
            if s:
                rendered.append(s)
        if rendered:
            out_lines.append(colored_sep.join(rendered))
    return out_lines


def main():
    """Emit one cc-vitals line at the requested index.

    Tmux's `#(...)` substitution flattens newlines into the same status
    row, so we can't ship multi-line output through a single
    `status-format[N]` directive. Instead, the conf wires one
    `status-format` row per cc-vitals line and passes the line index as
    argv[2] — `render-tmux.py <slot> <line-index>`."""
    argv_slot = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        line_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    except ValueError:
        line_index = 0

    data = _load_dump(argv_slot)
    if not isinstance(data, dict):
        return

    config = load_config()
    _apply_tmux_defaults(config)
    theme = _resolve_theme(config)
    lines = _render_lines(data, config, theme)
    if line_index < 0 or line_index >= len(lines):
        return
    sys.stdout.write(ansi_to_tmux(lines[line_index]))


if __name__ == '__main__':
    main()
