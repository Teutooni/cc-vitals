"""Prompt-cache TTL approximation + per-session usage aggregation.

TTL is approximated from the transcript file mtime — each assistant
response triggers a transcript write, so mtime closely tracks when the
cache was last refreshed. Precision good to a few hundred ms.

Cache hit ratio is rolled up across all assistant turns in the session,
not just the last turn. Per-turn ratios are misleading in Claude Code:
input_tokens is nearly always 1 (everything gets cache-controlled), so
a single turn that rebuilds half the prefix still reads ~99% on a per-
turn basis. Session totals smooth that out.

To keep parsing cheap with `refreshInterval: 1`, we persist
{file_size, totals, tier} per session_id and only read the new tail
(file_size_old → file_size_new) on each render.
"""
import json
import time
from pathlib import Path

from state import (
    DATA_DIR,
    MAX_SESSIONS,
    load_json,
    prune_sessions_lru,
    save_json_atomic,
)


# Claude Code currently writes to the 1-hour ephemeral cache tier. The 5-min
# tier is also possible for SDK callers; we detect from transcript and fall
# back to this default.
DEFAULT_TTL_SECONDS = 3600

STATE_FILE = DATA_DIR / 'cache-state.json'

# Idle ticks (no new transcript bytes) skip the rewrite, but bump last_seen
# at least this often so LRU pruning doesn't evict an active session.
_IDLE_TOUCH_SECONDS = 300


def get_cache_age_seconds(transcript_path):
    """Seconds since the transcript was last written, or None if unavailable."""
    if not transcript_path:
        return None
    try:
        mtime = Path(transcript_path).stat().st_mtime
    except OSError:
        return None
    return max(0.0, time.time() - mtime)


def get_cache_ttl_remaining(transcript_path, ttl_seconds=DEFAULT_TTL_SECONDS):
    """Seconds remaining on the prompt cache. Negative when expired, None if unknown."""
    age = get_cache_age_seconds(transcript_path)
    if age is None:
        return None
    return ttl_seconds - age


def _scan_chunk(text, seen_ids):
    """Walk JSONL text, yield (msg_id, usage_dict) for each unique assistant
    turn whose msg.id hasn't been seen yet. Mutates `seen_ids`."""
    for line in text.splitlines():
        if not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get('type') != 'assistant':
            continue
        msg = obj.get('message') or {}
        msg_id = msg.get('id')
        if not msg_id or msg_id in seen_ids:
            continue
        usage = msg.get('usage') or {}
        if not usage:
            continue
        seen_ids.add(msg_id)
        yield msg_id, usage


def _accumulate(totals, usage, tier_acc):
    totals['cache_read'] += int(usage.get('cache_read_input_tokens') or 0)
    totals['input_tokens'] += int(usage.get('input_tokens') or 0)
    totals['cache_creation'] += int(usage.get('cache_creation_input_tokens') or 0)
    totals['turns'] += 1
    cc = usage.get('cache_creation') or {}
    h1 = int(cc.get('ephemeral_1h_input_tokens') or 0)
    m5 = int(cc.get('ephemeral_5m_input_tokens') or 0)
    if h1 or m5:
        tier_acc['latest'] = '1h' if h1 >= m5 else '5m'


_EMPTY_TOTALS = {'cache_read': 0, 'input_tokens': 0, 'cache_creation': 0, 'turns': 0}


def get_session_cache_state(transcript_path, session_id):
    """Return aggregated cache stats for the current session.

    {
        'totals': {cache_read, input_tokens, cache_creation, turns},
        'tier_seconds': 3600 | 300 | None,
    }

    Uses an incremental on-disk cache keyed by session_id: stores file size,
    cumulative totals, latest tier, and the set of seen message ids so we
    only parse the file's new tail on each render.
    """
    if not transcript_path or not session_id:
        return None
    p = Path(transcript_path)
    try:
        size = p.stat().st_size
    except OSError:
        return None

    state = load_json(STATE_FILE) or {}
    sessions = state.setdefault('sessions', {})
    entry = sessions.get(session_id) or {}
    cached_size = int(entry.get('file_size') or 0)
    totals = dict(_EMPTY_TOTALS)
    totals.update({
        k: int(entry.get('totals', {}).get(k, 0)) for k in _EMPTY_TOTALS
    })
    seen_ids = set(entry.get('seen_ids') or [])
    tier_acc = {'latest': entry.get('tier')}
    last_seen = int(entry.get('last_seen') or 0)

    if size < cached_size:
        # Transcript shrunk (rotated/replaced) — recompute from scratch.
        totals = dict(_EMPTY_TOTALS)
        seen_ids = set()
        cached_size = 0
        tier_acc = {'latest': None}

    if size > cached_size:
        # Read just the new tail. Back up to the previous newline so we don't
        # split a line; dedup-by-msg-id handles any overlap re-parsed.
        start = max(0, cached_size - 1)
        try:
            with open(p, 'rb') as f:
                f.seek(start)
                new_bytes = f.read().decode('utf-8', errors='replace')
        except OSError:
            new_bytes = ''
        if new_bytes:
            for _msg_id, usage in _scan_chunk(new_bytes, seen_ids):
                _accumulate(totals, usage, tier_acc)

    out_totals = {k: int(totals[k]) for k in _EMPTY_TOTALS}
    now = int(time.time())
    changed = size != cached_size
    if changed or now - last_seen >= _IDLE_TOUCH_SECONDS:
        sessions[session_id] = {
            'file_size': size,
            'totals': out_totals,
            'tier': tier_acc['latest'],
            # Cap stored ids to keep state file from growing unbounded on huge
            # sessions. 5000 covers a very long session; older ids can fall off
            # because we only need them for dedup of the new tail.
            'seen_ids': list(seen_ids)[-5000:],
            'last_seen': now,
        }
        prune_sessions_lru(sessions, MAX_SESSIONS, key='last_seen')
        save_json_atomic(STATE_FILE, state)

    tier_seconds = None
    if tier_acc['latest'] == '1h':
        tier_seconds = 3600
    elif tier_acc['latest'] == '5m':
        tier_seconds = 300

    return {
        'totals': out_totals,
        'tier_seconds': tier_seconds,
    }
