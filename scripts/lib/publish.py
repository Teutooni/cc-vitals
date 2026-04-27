"""Manifest publishing: producer-side write of `<slot>.line<n>.json`.

The published directory is the only seam between the producer (CC's
ingest entrypoint, which may be running inside a container) and the
consumer (the host-side tick script that tmux invokes every second).
Keeping it a separate, narrow seam — distinct from the rest of
`plugin-data/` — avoids accidentally entangling host and container caches:
the bind mount only covers what's actually needed across the boundary.

Resolution precedence for the dump dir:
  1. ``CC_VITALS_DUMP_DIR`` env var. Set this on the producer side when
     the consumer reads from a different filesystem location (typical
     container case: bind-mount a host path into the container under
     this env).
  2. ``~/.claude/plugin-data/cc-vitals/published/``. Default for the
     all-on-one-host case where there is no boundary to bridge.
"""
import json
import os
from pathlib import Path

from state import DATA_DIR

DEFAULT_PUBLISHED_DIR = DATA_DIR / 'published'

ENV_VAR = 'CC_VITALS_DUMP_DIR'


def published_dir(env=None):
    """Resolve the dump directory. Returns a Path; does not create it."""
    e = env if env is not None else os.environ
    override = e.get(ENV_VAR)
    if isinstance(override, str) and override.strip():
        return Path(override.strip()).expanduser()
    return DEFAULT_PUBLISHED_DIR


def manifest_path(slot, line_index, env=None):
    """Filesystem path of a slot's per-line manifest.

    `slot` should already have been validated by `session_discovery._safe_slot`
    or its public `resolve_slot`; we don't sanitize here so a bug in the
    caller shows up as a clear write failure rather than a silent path
    rewrite."""
    return published_dir(env) / f'{slot}.line{int(line_index)}.json'


def publish_line(slot, line_index, manifest_line, env=None):
    """Atomically write one line's manifest. Best-effort: filesystem
    errors are swallowed (statusline rendering is non-critical)."""
    if not slot:
        return False
    path = manifest_path(slot, line_index, env=env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(manifest_line, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def publish_manifest(slot, manifest, env=None):
    """Publish all lines of a manifest. Returns the list of indices written.

    The manifest format is whatever `render.build_manifest` returns:
    ``{"lines": [[item, ...], ...]}``. Each line is published as its own
    file so the host-side ticker can render lines independently and
    incrementally."""
    if not slot:
        return []
    lines = (manifest or {}).get('lines') or []
    written = []
    for i, line_items in enumerate(lines):
        if publish_line(slot, i, {'items': line_items}, env=env):
            written.append(i)
    return written


def read_line(slot, line_index, env=None):
    """Read one line's published manifest. Returns the parsed dict (with
    ``items`` key) or None on missing file / parse error."""
    if not slot:
        return None
    path = manifest_path(slot, line_index, env=env)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# Mtime-fallback TTL — same value the legacy session_discovery used. Past
# this we assume no CC has ingested recently and the dump is stale.
DISCOVERY_TTL_SECONDS = 4 * 3600


def discover_latest_slot(line_index=0, ttl_seconds=DISCOVERY_TTL_SECONDS,
                         env=None, now=None):
    """Slot whose `<slot>.line<line_index>.json` was last modified within
    the TTL, or None. Used by the ticker when no slot was passed (raw-tmux
    usage without `cct`, single-session setups). With one CC running this
    picks the right slot; with several, whichever last had a CC turn —
    documented best-effort."""
    import time as _time
    if now is None:
        now = _time.time()
    d = published_dir(env)
    if not d.exists():
        return None
    cutoff = now - ttl_seconds
    suffix = f'.line{int(line_index)}.json'
    best_slot = None
    best_mtime = -1.0
    try:
        for p in d.iterdir():
            if not p.is_file() or not p.name.endswith(suffix):
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m < cutoff or m <= best_mtime:
                continue
            best_mtime = m
            best_slot = p.name[:-len(suffix)]
    except OSError:
        return None
    return best_slot
