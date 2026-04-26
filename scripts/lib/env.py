"""Host environment detection: native OS, WSL, Docker, VM, k8s."""
import os
import platform
import subprocess
from pathlib import Path


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


def get_linux_distro():
    """Return (id, pretty_name, id_like_list) for the current Linux distro.

    id is the canonical short id (e.g. 'opensuse-tumbleweed', 'ubuntu').
    pretty_name is e.g. 'openSUSE Tumbleweed'. id_like_list is a list of
    parent-distro ids from ID_LIKE (e.g. ['opensuse', 'suse']).
    """
    info = _read_os_release()
    distro_id = info.get('ID', '').lower()
    pretty = info.get('PRETTY_NAME') or info.get('NAME') or distro_id
    id_like = (info.get('ID_LIKE') or '').lower().split()
    return distro_id, pretty, id_like


def detect_environment():
    if Path('/.dockerenv').exists():
        return 'docker'
    if os.environ.get('KUBERNETES_SERVICE_HOST'):
        return 'k8s'

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
