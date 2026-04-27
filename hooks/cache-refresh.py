#!/usr/bin/env python3
"""PostToolUse hook: bump per-session cache-refresh marker.

Each tool result triggers a fresh API call that hits (and extends) the
prompt cache. Touching a per-session marker file lets the statusline's
cache segment show an accurate expiry time during long agent turns —
without it, the TTL would falsely tick down while Claude is still
calling tools and refreshing the cache on every request.

The marker is just an empty file whose mtime is the signal; reading
it back is a single stat() call. Errors are swallowed so the hook
never blocks tool execution.
"""
import json
import re
import sys
from pathlib import Path


_SAFE_ID = re.compile(r'^[A-Za-z0-9_\-]{1,128}$')


def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    sid = data.get('session_id')
    if not isinstance(sid, str) or not _SAFE_ID.match(sid):
        return
    try:
        d = Path.home() / '.claude' / 'plugin-data' / 'cc-vitals' / 'cache-refresh'
        d.mkdir(parents=True, exist_ok=True)
        (d / sid).touch()
    except OSError:
        pass


if __name__ == '__main__':
    main()
