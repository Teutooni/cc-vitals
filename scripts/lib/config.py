"""Config loader: shipped defaults merged with user overrides."""
import json
import os
from pathlib import Path

USER_CONFIG_PATH = Path.home() / '.claude' / 'statusline.json'
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / 'default-config.json'


def _deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_default_config():
    with open(_DEFAULT_PATH) as f:
        return json.load(f)


def load_config():
    config = load_default_config()
    try:
        with open(USER_CONFIG_PATH) as f:
            config = _deep_merge(config, json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    env_theme = os.environ.get('CC_VITALS_THEME')
    if env_theme:
        config['theme'] = env_theme
    return config
