"""Slot-based session routing for tmux mode.

In tmux mode the user can run several CC instances side by side — one per
repo, say — each in its own tmux session. Each tmux session has its own
status bar at the bottom, which has to render *that* session's CC state,
not whichever CC last produced output.

The mechanism is a per-slot dump file. The `cct` wrapper picks a slot id
when launching tmux and exports it as `CC_VITALS_SLOT`. Both the CC-side
ingest entrypoint and the tmux-side render entrypoint read the same env
var, so they line up on the same `sessions/<slot>.json` file.

Fallbacks:
  - No env slot, no argv slot → mtime-most-recent dump within
    `_FALLBACK_TTL_SECONDS` (covers raw-tmux usage without `cct` and
    single-session setups where the user doesn't care about routing).
  - Nothing dumped recently → return None (caller renders empty).
"""
import os
import re
from pathlib import Path

from state import DATA_DIR

SESSIONS_DIR = DATA_DIR / 'sessions'

_SLOT_RX = re.compile(r'^[A-Za-z0-9._\-]{1,128}$')

_FALLBACK_TTL_SECONDS = 4 * 3600


def _safe_slot(value):
    """Return value if it's a safe slot id, else None.

    Slot ids land in filesystem paths, so we restrict to a conservative
    charset. Anything funky (slashes, control chars, unicode) is rejected
    rather than sanitized — caller will fall back to mtime discovery."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or not _SLOT_RX.match(s):
        return None
    return s


def resolve_slot(argv_slot=None, env=None):
    """Pick the explicit slot for this caller, or None to mean 'discover'.

    Precedence: argv > CC_VITALS_SLOT env. The argv form is what tmux
    passes via `#{session_name}`; the env form is what `cct` exports for
    the CC-side ingest. Both lead to the same dump file."""
    e = env if env is not None else os.environ
    return _safe_slot(argv_slot) or _safe_slot(e.get('CC_VITALS_SLOT'))


def session_path(slot):
    """Filesystem path of a slot's dump. Caller is responsible for the
    parent directory existing on writes (use `ensure_sessions_dir`)."""
    s = _safe_slot(slot)
    if not s:
        return None
    return SESSIONS_DIR / f'{s}.json'


def ensure_sessions_dir():
    """Create the sessions directory if needed. Returns the path on
    success, None on filesystem error."""
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        return SESSIONS_DIR
    except OSError:
        return None


def discover_latest(ttl_seconds=_FALLBACK_TTL_SECONDS, now=None):
    """Most recently modified `sessions/*.json` within the TTL.

    Used by the tmux render path when no slot was passed. With one CC
    running this picks the right file; with several, it picks whichever
    last had a CC turn — which is the documented best-effort behavior
    for users not running through the `cct` wrapper."""
    import time as _time
    if now is None:
        now = _time.time()
    if not SESSIONS_DIR.exists():
        return None
    cutoff = now - ttl_seconds
    best_path = None
    best_mtime = -1.0
    try:
        for p in SESSIONS_DIR.iterdir():
            if not p.is_file() or p.suffix != '.json':
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m < cutoff:
                continue
            if m > best_mtime:
                best_mtime = m
                best_path = p
    except OSError:
        return None
    return best_path
