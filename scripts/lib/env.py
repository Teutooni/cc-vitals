"""Host environment detection: native OS, WSL, Docker, VM, k8s.

The slow probes (WSL via /proc/version, systemd-detect-virt subprocess,
/etc/os-release parse) are cached to disk for 24h keyed on hostname so we
don't fork ~daily-once-detected work on every render. Container indicators
(/.dockerenv, KUBERNETES_SERVICE_HOST) are checked live every call — those
can change between processes that share a $HOME mount.
"""
import os
import platform
import subprocess
import time
from pathlib import Path

from state import DATA_DIR, load_json, save_json_atomic

_HOST_CACHE_FILE = DATA_DIR / 'host.json'
_HOST_CACHE_TTL_SECONDS = 86400  # 24h


def _read_os_release():
    p = Path('/etc/os-release')
    if not p.exists():
        return {}
    out = {}
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            out[k] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def _detect_distro_uncached():
    info = _read_os_release()
    distro_id = info.get('ID', '').lower()
    pretty = info.get('PRETTY_NAME') or info.get('NAME') or distro_id
    id_like = (info.get('ID_LIKE') or '').lower().split()
    return distro_id, pretty, id_like


def _detect_env_slow_uncached():
    """WSL → systemd-detect-virt → native OS. Skips fast container checks."""
    try:
        with open('/proc/version') as f:
            contents = f.read().lower()
            if 'microsoft' in contents or 'wsl' in contents:
                return 'wsl'
    except OSError:
        pass

    if 'microsoft' in platform.release().lower():
        return 'wsl'

    try:
        r = subprocess.run(
            ['systemd-detect-virt'],
            capture_output=True, text=True, timeout=0.3
        )
        v = (r.stdout or '').strip()
        if v and v != 'none':
            return v
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    system = platform.system()
    if system == 'Darwin':
        return 'macos'
    if system == 'Windows':
        return 'windows'
    if system == 'Linux':
        return 'linux'
    return system.lower() or 'unknown'


def _read_host_cache():
    data = load_json(_HOST_CACHE_FILE)
    if not isinstance(data, dict) or not data:
        return None
    if data.get('host') != platform.node():
        return None
    try:
        ts = float(data.get('ts') or 0)
    except (TypeError, ValueError):
        return None
    if time.time() - ts > _HOST_CACHE_TTL_SECONDS:
        return None
    return data


def _write_host_cache(env_slow, distro):
    save_json_atomic(_HOST_CACHE_FILE, {
        'host': platform.node(),
        'env_slow': env_slow,
        'distro_id': distro[0],
        'pretty': distro[1],
        'id_like': list(distro[2]),
        'ts': int(time.time()),
    })


# Per-process memoization: a single render may call detect_environment and
# get_linux_distro back-to-back; one disk read is enough.
_HOST_STATE_MEMO = None


def _ensure_host_state():
    global _HOST_STATE_MEMO
    if _HOST_STATE_MEMO is not None:
        return _HOST_STATE_MEMO
    cache = _read_host_cache()
    if cache:
        _HOST_STATE_MEMO = (
            cache.get('env_slow') or 'unknown',
            cache.get('distro_id') or '',
            cache.get('pretty') or '',
            list(cache.get('id_like') or []),
        )
        return _HOST_STATE_MEMO
    env_slow = _detect_env_slow_uncached()
    distro = _detect_distro_uncached()
    _write_host_cache(env_slow, distro)
    _HOST_STATE_MEMO = (env_slow, distro[0], distro[1], distro[2])
    return _HOST_STATE_MEMO


def get_linux_distro():
    """Return (id, pretty_name, id_like_list) for the current Linux distro.

    id is the canonical short id (e.g. 'opensuse-tumbleweed', 'ubuntu').
    pretty_name is e.g. 'openSUSE Tumbleweed'. id_like_list is a list of
    parent-distro ids from ID_LIKE (e.g. ['opensuse', 'suse']).
    """
    _, distro_id, pretty, id_like = _ensure_host_state()
    return distro_id, pretty, id_like


def detect_environment():
    # Container indicators always run live: a process with the same $HOME
    # might be inside a container while the cache reflects the host.
    if Path('/.dockerenv').exists():
        return 'docker'
    if os.environ.get('KUBERNETES_SERVICE_HOST'):
        return 'k8s'
    env_slow, _, _, _ = _ensure_host_state()
    return env_slow
