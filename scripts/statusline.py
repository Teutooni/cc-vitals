#!/usr/bin/env python3
"""Claude Code customizable statusline.

Reads session JSON on stdin, prints one or more colored status lines
composed from configured segments (model, cwd, git, env, cost, context,
duration, runtime, cc-version).
"""
import json
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'lib'))

from colors import THEMES, paint  # noqa: E402
from config import load_config  # noqa: E402
from segments import render_segment  # noqa: E402


def _maybe_dump(raw):
    """When CC_VITALS_DUMP=1, persist raw stdin so the user can
    inspect undocumented fields (e.g. permission_mode)."""
    if not os.environ.get('CC_VITALS_DUMP'):
        return
    try:
        d = Path.home() / '.claude' / 'plugin-data' / 'cc-vitals'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'last-stdin.json').write_text(raw)
    except OSError:
        pass


def _resolve_theme(config):
    t = config.get('theme', 'vs-dark-modern')
    if isinstance(t, dict):
        return t
    return THEMES.get(t, THEMES['vs-dark-modern'])


def main():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ''
    _maybe_dump(raw)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    config = load_config()
    theme = _resolve_theme(config)
    separator = config.get('separator', ' │ ')
    sep_col = config.get('colors', {}).get('separator', 'dim')
    colored_sep = paint(separator, sep_col, theme, dim=True)

    lines_cfg = config.get('lines')
    if not lines_cfg:
        lines_cfg = [['model', 'cwd', 'git', 'env', 'cost', 'context']]
    if lines_cfg and isinstance(lines_cfg[0], str):
        lines_cfg = [lines_cfg]

    out_lines = []
    for line_segments in lines_cfg:
        rendered = []
        for seg in line_segments:
            s = render_segment(seg, data, config, theme)
            if s:
                rendered.append(s)
        if rendered:
            out_lines.append(colored_sep.join(rendered))

    sys.stdout.write('\n'.join(out_lines))
    sys.stdout.write('\n' if out_lines else '')


if __name__ == '__main__':
    main()
