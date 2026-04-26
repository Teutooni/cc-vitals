"""Shared persistence helpers for plugin-data JSON state files.

Both `cache.py` and `cost.py` keep small JSON state under
`~/.claude/plugin-data/cc-vitals/`. They share the same atomic-write
pattern and LRU pruning, hoisted here so a future fix (fsync, locking,
schema versioning) lands once.
"""
import json
import os
from pathlib import Path

DATA_DIR = Path.home() / '.claude' / 'plugin-data' / 'cc-vitals'

MAX_SESSIONS = 200


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_atomic(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def prune_sessions_lru(sessions, max_items=MAX_SESSIONS, key='last_seen'):
    if len(sessions) <= max_items:
        return
    ordered = sorted(sessions.items(), key=lambda kv: kv[1].get(key, 0))
    for k, _ in ordered[:-max_items]:
        del sessions[k]
