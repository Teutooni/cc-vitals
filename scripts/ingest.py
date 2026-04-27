#!/usr/bin/env python3
"""CC `statusLine` entrypoint for tmux mode.

CC calls this with the same JSON-on-stdin contract as `statusline.py`. We:
  1. Apply the payload to persisted state (cost totals, cache aggregates)
     via the shared ingest module — same call statusline.py makes.
  2. Build a manifest of the configured statusline (per-line list of
     static ANSI runs + live items) and publish each line atomically to
     ``$CC_VITALS_DUMP_DIR/<slot>.line<n>.json``.

The manifest is what the host-side tmux ticker (``scripts/tick.py``) reads
every second. By rendering everything except live-tickable segments here,
we keep the seam narrow: the host never has to touch transcripts, theme,
or cache state — it just walks the published items and formats live ones.

We print *nothing* to stdout: in tmux mode, CC's own statusline area is
empty by design — tmux owns the bar.

Slot resolution: prefer ``CC_VITALS_SLOT`` env (set by the ``cct`` wrapper),
fall back to the CC ``session_id``. Either way, the matching tick entry
will find the manifest.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from colors import THEMES  # noqa: E402
from config import load_config  # noqa: E402
from ingest import ingest  # noqa: E402
from publish import publish_manifest  # noqa: E402
from render import build_manifest  # noqa: E402
from session_discovery import resolve_slot  # noqa: E402


def _slot_for(data):
    explicit = resolve_slot()
    if explicit:
        return explicit
    sid = data.get('session_id') if isinstance(data, dict) else None
    return resolve_slot(argv_slot=sid)


def _resolve_theme(config):
    t = config.get('theme', 'vs-dark-modern')
    if isinstance(t, dict):
        return t
    return THEMES.get(t, THEMES['vs-dark-modern'])


def _apply_tmux_defaults(config):
    """Tmux mode renders at 1 Hz, so the cache TTL gets a smooth countdown
    by default rather than the minute-grained expiry clock native mode has
    to use. Anything the user sets explicitly wins. ingest.py is the
    tmux-mode entrypoint exclusively, so applying these defaults here is
    safe — native mode runs through statusline.py and never sees this."""
    seg = config.setdefault('segments', {}).setdefault('cache', {})
    seg.setdefault('style', 'countdown')


def main():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ''
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    ingest(data)

    slot = _slot_for(data)
    if not slot:
        return

    config = load_config()
    _apply_tmux_defaults(config)
    theme = _resolve_theme(config)
    manifest = build_manifest(data, config, theme)
    publish_manifest(slot, manifest)


if __name__ == '__main__':
    main()
