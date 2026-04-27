#!/usr/bin/env python3
"""CC `statusLine` entrypoint for tmux mode.

CC calls this with the same JSON-on-stdin contract as `statusline.py`.
We:
  1. Apply the payload to persisted state (cost totals, cache aggregates)
     via the shared ingest module — same call statusline.py makes.
  2. Dump the raw stdin to `sessions/<slot>.json` so the tmux-side
     render entrypoint has fresh data on its next 1 Hz tick.

We print *nothing*: in tmux mode, CC's own statusline area is empty by
design — tmux owns the bar.

Slot resolution: prefer `CC_VITALS_SLOT` env (set by the `cct` wrapper),
fall back to the CC `session_id`. Either way, the matching render entry
will find the dump.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from ingest import ingest  # noqa: E402
from session_discovery import (  # noqa: E402
    ensure_sessions_dir,
    resolve_slot,
    session_path,
)


def _slot_for(data):
    explicit = resolve_slot()
    if explicit:
        return explicit
    sid = data.get('session_id') if isinstance(data, dict) else None
    return resolve_slot(argv_slot=sid)


def _dump(data, slot):
    if not slot:
        return
    if not ensure_sessions_dir():
        return
    p = session_path(slot)
    if p is None:
        return
    try:
        tmp = p.with_suffix(p.suffix + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, p)
    except OSError:
        pass


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
    _dump(data, _slot_for(data))


if __name__ == '__main__':
    main()
