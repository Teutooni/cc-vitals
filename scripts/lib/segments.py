"""Segment renderers. Each takes (stdin_data, config, theme) -> str."""
import os
import re
import subprocess
import time as _time

from cache import (
    get_cache_expiry_epoch,
    get_cache_ttl_remaining,
    get_session_cache_state,
    DEFAULT_TTL_SECONDS,
)
from colors import paint, gradient_hex
from cost import update_and_get, get_projection, get_month_projection
from context import get_context_usage
from env import detect_environment, get_linux_distro
from git import get_git_info
from pricing import at_risk_cost


# Nerd-font glyphs by codepoint. See https://www.nerdfonts.com/cheat-sheet
_ICONS = {
    'nerd': {
        'model':       '',  # nf-fa-bolt
        'effort':      '',  # nf-fa-lightbulb
        'cwd':         '',  # nf-fa-folder
        'git':         '',  # nf-fa-code_branch
        'cost':        '',  # nf-fa-dollar
        'context':     '',  # nf-fa-bar_chart
        'limits':      '',  # nf-fa-tachometer
        'tokens':      '',  # nf-fa-exchange
        'tokens_session': 'Σ',  # Greek capital sigma
        'cache':       '',  # nf-fa-database
        'duration':    '',  # nf-fa-clock
        'runtime':     '',  # nf-fa-rocket
        'cc_version':  '',  # nf-fa-tag
        'env_linux':   '',  # tux (generic)
        'env_wsl':     '',  # nf-fa-windows
        'env_macos':   '',  # nf-fa-apple
        'env_windows': '',  # nf-fa-windows
        'env_docker':  '',  # nf-linux-docker
        'env_k8s':     '󰀝',  # nf-md-kubernetes (just a placeholder; see ascii)
        'env_unknown': '',  # nf-fa-server
        # Linux distro logos (nf-linux-*)
        'distro_opensuse':           '',
        'distro_opensuse_tumbleweed':'',
        'distro_opensuse_leap':      '',
        'distro_suse':               '',
        'distro_sles':               '',
        'distro_ubuntu':             '',
        'distro_debian':             '',
        'distro_fedora':             '',
        'distro_rhel':               '',
        'distro_redhat':             '',
        'distro_centos':             '',
        'distro_arch':               '',
        'distro_manjaro':            '',
        'distro_endeavouros':        '',
        'distro_alpine':             '',
        'distro_gentoo':             '',
        'distro_nixos':              '',
        'distro_mint':               '',
        'distro_linuxmint':          '',
        'distro_pop':                '',
        'distro_void':               '',
        'distro_raspbian':           '',
        'distro_kali':               '',
        'distro_almalinux':          '',
        'distro_rocky':              '',
        'distro_zorin':              '',
    },
    'ascii': {
        'model':      '[M]',
        'effort':     '[*]',
        'cwd':        '[DIR]',
        'git':        'git:',
        'cost':       '$',
        'context':    '%',
        'limits':     '%%',
        'tokens':     'T:',
        'tokens_session': 'Σ',
        'cache':      'cache:',
        'duration':   't:',
        'runtime':    'rt:',
        'cc_version': 'cc',
        'env_linux':  '[linux]',
        'env_wsl':    '[wsl]',
        'env_macos':  '[mac]',
        'env_windows':'[win]',
        'env_docker': '[docker]',
        'env_k8s':    '[k8s]',
        'env_unknown':'[?]',
    },
}


def _icon(config, key):
    style = config.get('icons', 'nerd')
    custom = config.get('segments', {}).get(key, {}).get('icon')
    if custom is not None:
        return custom
    return _ICONS.get(style, _ICONS['ascii']).get(key, '')


def _colors(config):
    return config.get('colors', {})


_MODEL_PAREN_RX = re.compile(r'\s*\(\s*([^)]+?)\s*\)\s*$')
_TOKEN_RX = re.compile(r'\b(\d+(?:\.\d+)?[KMG])\b', re.IGNORECASE)


def _shorten_model_name(name, enabled=True):
    """Compress trailing parenthetical: 'Opus 4.7 (1M context)' -> 'Opus 4.7 [1M]'."""
    if not name or not enabled:
        return name
    m = _MODEL_PAREN_RX.search(name)
    if not m:
        return name
    inside = m.group(1)
    tok = _TOKEN_RX.search(inside)
    short = tok.group(1).upper() if tok else inside
    return name[:m.start()] + f' [{short}]'


def _detect_effort(data):
    # Preferred: top-level `effort.level` (officially exposed by Claude Code).
    eff = data.get('effort')
    if isinstance(eff, dict):
        v = eff.get('level')
        if v:
            return v if isinstance(v, str) else str(v)
    # Legacy fallbacks for older CC versions / unusual payloads.
    model = data.get('model') or {}
    for k in ('effort', 'thinking_budget', 'reasoning_effort', 'reasoning'):
        v = model.get(k)
        if v:
            return v if isinstance(v, str) else str(v)
    out_style = data.get('output_style')
    if isinstance(out_style, dict):
        v = out_style.get('effort')
        if v:
            return v if isinstance(v, str) else str(v)
    try:
        import json
        from pathlib import Path
        for p in (
            Path.home() / '.claude' / 'settings.json',
            Path.home() / '.claude' / 'settings.local.json',
        ):
            if p.exists():
                v = json.loads(p.read_text()).get('effortLevel')
                if v:
                    return v if isinstance(v, str) else str(v)
    except (OSError, ValueError):
        pass
    return None


def render_model(data, config, theme):
    model = data.get('model') or {}
    name = model.get('display_name') or model.get('id')
    if not name:
        return ''
    seg = config.get('segments', {}).get('model', {})
    name = _shorten_model_name(name, enabled=seg.get('shorten', True))
    icon = _icon(config, 'model')
    label = f'{icon} {name}' if icon else name
    # Inline effort kept for back-compat unless an explicit 'effort' segment is in lines.
    if seg.get('inline_effort', False):
        effort = _detect_effort(data)
        if effort:
            label += f' · {effort}'
    return paint(label, _colors(config).get('model'), theme)


# Discrete per-level colors matching the /effort UI.
_EFFORT_COLORS = {
    'none':       '#888888',
    'off':        '#888888',
    'minimal':    '#FFC108',
    'low':        '#FFC108',   # amber
    'medium':     '#4EBA65',   # green
    'standard':   '#4EBA65',
    'std':        '#4EBA65',
    'normal':     '#4EBA65',
    'high':       '#B1B9F7',   # soft blue
    'xhigh':      '#AF87FF',   # purple
    'extra_high': '#AF87FF',
    'extra-high': '#AF87FF',
    'very_high':  '#AF87FF',
    # 'max' handled specially — rainbow per-character
}

# Rainbow hues (cycled per-character) for the 'max' level. Same set rotates
# for any string length.
_RAINBOW = (
    '#FF5555', '#FF9944', '#FFCC33', '#66D94F',
    '#4FB8D9', '#6B6BE0', '#B876D3',
)


def _paint_rainbow(text, theme):
    out = []
    i = 0
    for ch in text:
        if ch.isspace():
            out.append(ch)
            continue
        out.append(paint(ch, _RAINBOW[i % len(_RAINBOW)], theme))
        i += 1
    return ''.join(out)


def render_effort(data, config, theme):
    effort = _detect_effort(data)
    seg = config.get('segments', {}).get('effort', {})
    icon = _icon(config, 'effort')
    show_icon = icon and seg.get('show_icon', True)
    color_override = _colors(config).get('effort')

    if not effort:
        label = f'{icon} —' if show_icon else '—'
        return paint(label, color_override or '#888888', theme)

    label = f'{icon} {effort}' if show_icon else effort

    if not seg.get('adaptive_color', True) or color_override:
        return paint(label, color_override, theme)

    key = str(effort).strip().lower()
    if key in ('max', 'extreme', 'ultra'):
        return _paint_rainbow(label, theme)
    color = _EFFORT_COLORS.get(key)
    if color:
        return paint(label, color, theme)
    # Unknown effort token (e.g. numeric thinking_budget) — fall back to gray.
    return paint(label, '#888888', theme)


def render_cwd(data, config, theme):
    cwd = data.get('cwd') or (data.get('workspace') or {}).get('current_dir') or os.getcwd()
    home = os.path.expanduser('~')
    if cwd == home:
        cwd = '~'
    elif home and cwd.startswith(home + os.sep):
        cwd = '~' + cwd[len(home):]
    cfg = config.get('segments', {}).get('cwd', {})
    if cfg.get('basename_only'):
        cwd = os.path.basename(cwd.rstrip('/')) or cwd
    else:
        max_len = cfg.get('max_length', 40)
        if len(cwd) > max_len and '/' in cwd:
            parts = cwd.split('/')
            if len(parts) > 3:
                cwd = '/'.join([parts[0] or '', '…'] + parts[-2:])
    icon = _icon(config, 'cwd')
    label = f'{icon} {cwd}' if icon else cwd
    return paint(label, _colors(config).get('cwd'), theme)


_STATUS_CATS = (
    # key,        default nerd glyph, default ascii glyph, color key
    ('added',     '+', '+', 'git.added'),
    ('modified',  '~', '~', 'git.modified'),
    ('deleted',   '-', '-', 'git.deleted'),
    ('renamed',   '»', 'R', 'git.renamed'),
    ('untracked', '?', '?', 'git.untracked'),
)


_OP_LABELS = {
    'merge':       'MERGING',
    'rebase':      'REBASE',
    'cherry-pick': 'CHERRY-PICK',
    'revert':      'REVERT',
    'bisect':      'BISECT',
}


def render_git(data, config, theme):
    cwd = data.get('cwd') or (data.get('workspace') or {}).get('current_dir')
    seg = config.get('segments', {}).get('git', {})
    info = get_git_info(
        cwd,
        timeout=seg.get('timeout'),
        cache_ttl=seg.get('cache_ttl'),
    )
    if not info:
        return ''
    style = config.get('icons', 'nerd')
    c = _colors(config)
    icon = _icon(config, 'git')

    if info.get('error') == 'timeout':
        # In a repo, but `git status` timed out and there's no prior
        # cache to fall back to. Show a warning marker rather than
        # blanking the segment — silent disappearance hides a real
        # filesystem problem the user should know about.
        glyph = seg.get('timeout_glyph', '⏳' if style == 'nerd' else '?')
        label_text = seg.get('timeout_label', 'slow')
        col = c.get('git.timeout', 'warning')
        return paint(f'{icon} {glyph} {label_text}'.strip(), col, theme)

    ahead_glyph = seg.get('ahead_glyph', '↑' if style == 'nerd' else '^')
    behind_glyph = seg.get('behind_glyph', '↓' if style == 'nerd' else 'v')
    glyphs = seg.get('status_glyphs', {})
    branch_col = c.get('git.branch')
    no_up_col = c.get('git.no_upstream')
    op_col = c.get('git.op', 'error')
    wt_col = c.get('git.worktree', 'accent')

    parts = []
    op = info.get('op_state')
    if op:
        op_name, progress = op
        label = _OP_LABELS.get(op_name, op_name.upper())
        if progress:
            label += f' {progress}'
        parts.append(paint(label, op_col, theme, bold=True))

    branch_label = f'{icon} {info["branch"]}'.strip()
    parts.append(paint(branch_label, branch_col, theme))

    workspace = data.get('workspace') or {}
    worktree_name = workspace.get('git_worktree')
    if worktree_name and seg.get('show_worktree', True):
        wt_glyph = seg.get('worktree_glyph', '⎇' if style == 'nerd' else 'wt:')
        parts.append(paint(f'{wt_glyph} {worktree_name}'.strip(), wt_col, theme))

    out = ' '.join(parts)
    for key, nerd_g, ascii_g, col_key in _STATUS_CATS:
        n = info.get(key, 0)
        if not n:
            continue
        g = glyphs.get(key, nerd_g if style == 'nerd' else ascii_g)
        out += ' ' + paint(f'{g}{n}', c.get(col_key), theme)
    if info['ahead']:
        out += ' ' + paint(f'{ahead_glyph}{info["ahead"]}', branch_col, theme)
    if info['behind']:
        out += ' ' + paint(f'{behind_glyph}{info["behind"]}', branch_col, theme)
    if not info['upstream']:
        out += ' ' + paint('(no upstream)', no_up_col, theme)
    return out


def _resolve_distro_icon(distro_id, id_like):
    """Pick the most specific available distro icon, falling back through
    ID_LIKE parents and finally to the generic Linux penguin."""
    nerd = _ICONS.get('nerd', {})
    for src in [distro_id] + list(id_like):
        if not src:
            continue
        for key in {'distro_' + src.replace('-', '_'), 'distro_' + src}:
            if key in nerd:
                return key
    return None


_DISTRO_COLORS = {
    'opensuse':            '#73BA25',
    'opensuse-tumbleweed': '#73BA25',
    'opensuse-leap':       '#73BA25',
    'suse':                '#73BA25',
    'sles':                '#73BA25',
    'ubuntu':              '#E95420',
    'debian':              '#A81D33',
    'fedora':              '#51A2DA',
    'rhel':                '#EE0000',
    'redhat':              '#EE0000',
    'centos':              '#9CCD2A',
    'arch':                '#1793D1',
    'manjaro':             '#34BE5B',
    'endeavouros':         '#7F3FBF',
    'alpine':              '#0D597F',
    'gentoo':              '#54487A',
    'nixos':               '#5277C3',
    'mint':                '#87CF3E',
    'linuxmint':           '#87CF3E',
    'pop':                 '#48B9C7',
    'void':                '#478061',
    'raspbian':            '#A22846',
    'kali':                '#557C94',
    'almalinux':           '#0D597F',
    'rocky':               '#10B981',
    'zorin':               '#15A6F0',
}

# Non-distro environments. Native Linux falls back to an olive green to match
# context's "0%" hue. Virtualized contexts share a purple spectrum.
_ENV_COLORS = {
    'linux':   '#7BA05B',  # olive green
    'macos':   '#A8A8A8',  # silver
    'windows': '#0078D4',  # win blue
    'wsl':     '#7B61FF',  # purple
    'docker':  '#9B6CC2',  # mid purple
    'k8s':     '#5B4B8A',  # deep indigo
    'unknown': '#888888',
}


def _env_color(env, distro_id, id_like, prefer_distro=True):
    """Pick an adaptive color for the env segment.

    Prefers brand colors for recognized Linux distros (and their ID_LIKE
    parents); falls back to the non-distro env palette.
    """
    if env == 'linux' and prefer_distro:
        if distro_id and distro_id in _DISTRO_COLORS:
            return _DISTRO_COLORS[distro_id]
        for parent in id_like:
            if parent in _DISTRO_COLORS:
                return _DISTRO_COLORS[parent]
    return _ENV_COLORS.get(env, _ENV_COLORS['unknown'])


_VIRTUAL_ENVS = ('docker', 'wsl', 'k8s')

# Nerd Font icon keys whose glyphs render at ~2 cells wide and need an extra
# trailing space before the label so the text doesn't crowd the icon. Most
# nf-linux-* glyphs are 1-cell — the SUSE/openSUSE family is the notable
# exception. Add to this set if other distros appear cramped in your terminal.
_WIDE_ICON_KEYS = frozenset({
    'distro_opensuse',
    'distro_opensuse_tumbleweed',
    'distro_opensuse_leap',
    'distro_suse',
    'distro_sles',
})


def _icon_pad(config, icon_key):
    """Single space by default; double for known-wide glyphs. Users can extend
    via segments.env.wide_icon_keys (list of icon keys to also treat as wide)."""
    extra = set((config.get('segments', {}).get('env', {}) or {}).get('wide_icon_keys') or [])
    if icon_key and (icon_key in _WIDE_ICON_KEYS or icon_key in extra):
        return '  '
    return ' '


def render_env(data, config, theme):
    env = detect_environment()
    seg = config.get('segments', {}).get('env', {})
    style = config.get('icons', 'nerd')
    color_override = _colors(config).get('env')
    adaptive = seg.get('adaptive_color', True) and not color_override

    distro_id, pretty, id_like = get_linux_distro()
    show_host = seg.get('show_container_host', True)

    if env in _VIRTUAL_ENVS and pretty and show_host and style == 'nerd':
        distro_icon_key = _resolve_distro_icon(distro_id, id_like)
        distro_icon = _icon(config, distro_icon_key) if distro_icon_key else _icon(config, 'env_linux')
        distro_name = pretty if seg.get('show_distro', True) else (distro_id or 'linux')
        host_icon_key = f'env_{env}' if f'env_{env}' in _ICONS.get('nerd', {}) else 'env_unknown'
        host_icon = _icon(config, host_icon_key)

        if adaptive:
            distro_col = _env_color('linux', distro_id, id_like, True)
            host_col = _ENV_COLORS.get(env, _ENV_COLORS['unknown'])
        else:
            distro_col = host_col = color_override

        pad = _icon_pad(config, distro_icon_key)
        distro_part = paint(f'{distro_icon}{pad}{distro_name}'.strip(), distro_col, theme)
        host_part = paint(f'({host_icon} {env})', host_col, theme)
        return f'{distro_part} {host_part}'

    if env == 'linux':
        label_text = pretty if (seg.get('show_distro', True) and pretty) else 'linux'
        icon_key = _resolve_distro_icon(distro_id, id_like) if style == 'nerd' else None
        icon = _icon(config, icon_key) if icon_key else _icon(config, 'env_linux')
    else:
        icon_key = f'env_{env}' if f'env_{env}' in _ICONS.get('nerd', {}) else 'env_unknown'
        icon = _icon(config, icon_key)
        label_text = env

    if style == 'nerd':
        pad = _icon_pad(config, icon_key)
        label = f'{icon}{pad}{label_text}' if icon else label_text
    else:
        label = icon or f'[{label_text}]'

    if adaptive:
        color = _env_color(env, distro_id, id_like, seg.get('prefer_distro_color', True))
        return paint(label, color, theme)
    return paint(label, color_override, theme)


def render_cost(data, config, theme):
    cost_info = data.get('cost') or {}
    session_cost = cost_info.get('total_cost_usd', 0.0) or 0.0
    session_id = data.get('session_id') or 'unknown'
    session, day, month = update_and_get(session_id, session_cost)
    seg = config.get('segments', {}).get('cost', {})
    c = _colors(config)
    icon = _icon(config, 'cost')
    parts = []
    if seg.get('show_session', True):
        parts.append(paint(f'{icon}{session:.2f}', c.get('cost.session'), theme))
    if seg.get('show_day', True):
        parts.append(paint(f'{day:.2f}/d', c.get('cost.day'), theme))
    if seg.get('show_month', True):
        parts.append(paint(f'{month:.2f}/mo', c.get('cost.month'), theme))
    return ' '.join(p for p in parts if p)


def _forecast_config(config, *namespaces):
    """First non-empty `segments.<ns>` block wins; same precedence for color
    keys via the returned `col` lookup. Lets new canonical names coexist
    with the old `cost_avg` config block."""
    segs = config.get('segments', {})
    seg = next((segs[n] for n in namespaces if segs.get(n)), {})
    colors = _colors(config)

    def col(key, default):
        for ns in namespaces:
            val = colors.get(f'{ns}.{key}')
            if val is not None:
                return val
        return default

    return seg, col


def _ratio_color_arrow(ratio, col):
    if ratio < 0.85:
        return col('under', 'success'), '↓'
    if ratio < 1.00:
        return col('ok', 'warning'), '→'
    if ratio < 1.15:
        return col('warn', '#D97757'), '↑'
    return col('over', 'error'), '↑'


def render_cost_day_forecast(data, config, theme):
    seg, col = _forecast_config(config, 'cost_day_forecast', 'cost_avg')
    window = int(seg.get('window', 7))
    proj = get_projection(window=window)
    if not proj:
        return paint('—/d', col('avg', 'muted'), theme)
    avg_part = paint(f'${proj["avg"]:.2f}/d', col('avg', 'muted'), theme)

    if not proj['enough'] or proj['ratio'] is None:
        return avg_part

    color, arrow = _ratio_color_arrow(proj['ratio'], col)
    projected = proj['ratio'] * proj['avg']
    parts = [f'${projected:.2f}']
    if seg.get('show_arrow', True):
        parts.append(arrow)
    status = paint(' '.join(parts), color, theme)
    return f'{status} {avg_part}' if seg.get('show_avg', True) else status


def render_cost_month_forecast(data, config, theme):
    seg, col = _forecast_config(config, 'cost_month_forecast')
    window = int(seg.get('window', 7))
    proj = get_month_projection(window=window)
    if not proj:
        return paint('—/mo', col('forecast', 'muted'), theme)

    decimals = int(seg.get('decimals', 0))
    forecast_str = f'${proj["forecast"]:.{decimals}f}/mo'

    if proj['ratio'] is None or not proj['enough']:
        return paint(forecast_str, col('forecast', 'muted'), theme)

    color, arrow = _ratio_color_arrow(proj['ratio'], col)
    parts = [forecast_str]
    if seg.get('show_arrow', True):
        parts.append(arrow)
    status = paint(' '.join(parts), color, theme)
    if seg.get('show_so_far', False):
        so_far = paint(f'${proj["month_so_far"]:.2f} so far', col('so_far', 'muted'), theme)
        return f'{status} {so_far}'
    return status


def _context_threshold_color(pct, c):
    if pct >= 90:
        return c.get('context.crit', c.get('context.normal'))
    if pct >= 75:
        return c.get('context.warn', c.get('context.normal'))
    return c.get('context.normal')


def render_context(data, config, theme):
    cw = data.get('context_window') or {}
    pct_raw = cw.get('used_percentage')
    if isinstance(pct_raw, (int, float)):
        frac = max(0.0, min(1.0, pct_raw / 100.0))
    else:
        model = data.get('model') or {}
        frac = get_context_usage(data.get('transcript_path'), model.get('id'))
        if frac is None:
            frac = 0.0
    pct = int(round(frac * 100))
    seg = config.get('segments', {}).get('context', {})
    c = _colors(config)
    style = seg.get('style', 'text')  # 'text' | 'bar' | 'both'
    icon = _icon(config, 'context')

    if style in ('bar', 'both'):
        width = int(seg.get('bar_width', 10))
        filled_glyph = seg.get('filled_glyph', '█')  # full block
        empty_glyph = seg.get('empty_glyph', '▒')   # medium shade
        filled = max(0, min(width, int(round(frac * width))))
        bar_col = gradient_hex(frac, theme) or c.get('context.normal')
        empty_col = c.get('context.empty', 'dim')
        bar = (
            paint(filled_glyph * filled, bar_col, theme)
            + paint(empty_glyph * (width - filled), empty_col, theme)
        )
        if style == 'bar':
            head = paint(icon + ' ', bar_col, theme) if icon else ''
            return head + bar + paint(f' {pct}%', bar_col, theme)
        text = paint(f'{icon} {pct}%' if icon else f'{pct}%', _context_threshold_color(pct, c), theme)
        return f'{text} {bar}'

    label = f'{icon} {pct}%' if icon else f'{pct}%'
    return paint(label, _context_threshold_color(pct, c), theme)


def render_limits(data, config, theme):
    """Subscription rate-limit usage: 5-hour and 7-day windows.

    Renders an overlaid bar — the 7-day fill is the underlay; the 5-hour
    fill overrides it where they overlap. Each window has its own color
    scheme so you can tell at a glance which limit is climbing."""
    rl = data.get('rate_limits') or {}
    five_raw = (rl.get('five_hour') or {}).get('used_percentage')
    seven_raw = (rl.get('seven_day') or {}).get('used_percentage')
    have_data = isinstance(five_raw, (int, float)) or isinstance(seven_raw, (int, float))
    five = float(five_raw) if isinstance(five_raw, (int, float)) else 0.0
    seven = float(seven_raw) if isinstance(seven_raw, (int, float)) else 0.0

    seg = config.get('segments', {}).get('limits', {})
    c = _colors(config)
    style = seg.get('style', 'bar')
    width = max(1, int(seg.get('bar_width', 10)))
    filled_g = seg.get('filled_glyph', '█')
    empty_g = seg.get('empty_glyph', '▒')

    d_fill = max(0, min(width, int(round(min(five, 100.0) / 100.0 * width))))
    w_fill = max(0, min(width, int(round(min(seven, 100.0) / 100.0 * width))))

    # Each window's color gradients on its own usage. Daily uses the warm
    # success→warning→error path; weekly uses a cooler secondary→accent→error
    # path so overlap regions are visually distinguishable.
    daily_col = (
        c.get('limits.daily')
        or gradient_hex(min(five / 100.0, 1.0), theme, ('success', 'warning', 'error'))
    )
    weekly_col = (
        c.get('limits.weekly')
        or gradient_hex(min(seven / 100.0, 1.0), theme, ('secondary', 'accent', 'error'))
    )
    empty_col = c.get('limits.empty', 'dim')
    text_col = c.get('limits.text', 'muted')

    cells = []
    for i in range(width):
        if i < d_fill:
            cells.append(paint(filled_g, daily_col, theme))
        elif i < w_fill:
            cells.append(paint(filled_g, weekly_col, theme))
        else:
            cells.append(paint(empty_g, empty_col, theme))
    bar = ''.join(cells)

    icon = _icon(config, 'limits')
    head = paint(icon + ' ', text_col, theme) if icon else ''
    if have_data:
        daily_txt = paint(f'{int(round(five))}% 5h', daily_col, theme)
        weekly_txt = paint(f'{int(round(seven))}% 7d', weekly_col, theme)
    else:
        daily_txt = paint('—% 5h', text_col, theme)
        weekly_txt = paint('—% 7d', text_col, theme)
    sep = paint(' · ', text_col, theme)
    text = f'{daily_txt}{sep}{weekly_txt}'

    if style == 'text':
        return f'{head}{text}'
    if style == 'both':
        return f'{head}{text} {bar}'
    if seg.get('show_text', True):
        return f'{head}{bar} {text}'
    return f'{head}{bar}'


def render_duration(data, config, theme):
    cost_info = data.get('cost') or {}
    ms = cost_info.get('total_duration_ms') or 0
    s = max(0, int(ms // 1000))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        t = f'{h}h{m:02d}m'
    elif m:
        t = f'{m}m{sec:02d}s'
    else:
        t = f'{sec}s'
    icon = _icon(config, 'duration')
    label = f'{icon} {t}' if icon else t
    return paint(label, _colors(config).get('duration'), theme)


_RUNTIME_DETECTORS = [
    ('package.json',     'node',    ['--version']),
    ('pyproject.toml',   'python3', ['--version']),
    ('requirements.txt', 'python3', ['--version']),
    ('Cargo.toml',       'rustc',   ['--version']),
    ('go.mod',           'go',      ['version']),
    ('pom.xml',          'java',    ['-version']),
    ('Gemfile',          'ruby',    ['--version']),
]


def render_runtime(data, config, theme):
    cwd = data.get('cwd')
    if not cwd or not os.path.isdir(cwd):
        return ''
    try:
        files = set(os.listdir(cwd))
    except OSError:
        return ''
    for marker, cmd, args in _RUNTIME_DETECTORS:
        if marker not in files:
            continue
        try:
            r = subprocess.run(
                [cmd] + args, capture_output=True, text=True, timeout=0.5
            )
            raw = (r.stdout or r.stderr or '').strip().splitlines()
            if not raw:
                continue
            version = raw[0].strip()
            label = f'{cmd} {version}' if cmd not in version else version
            icon = _icon(config, 'runtime')
            return paint(f'{icon} {label}'.strip(), _colors(config).get('runtime'), theme)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return ''


def render_cc_version(data, config, theme):
    v = data.get('version') or '—'
    icon = _icon(config, 'cc_version')
    label = f'{icon} {v}' if icon else f'cc {v}'
    return paint(label, _colors(config).get('cc_version'), theme)


def _fmt_tokens(n):
    """Compact token count: 523, 1.2K, 12K, 1.2M."""
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f'{n / 1000:.1f}K'
    if n < 1_000_000:
        return f'{n // 1000}K'
    return f'{n / 1_000_000:.1f}M'


def render_tokens(data, config, theme):
    """Last-turn token activity: ↑fresh-input ↓output +cache-creation.

    `current_usage.input_tokens` is the *uncached* input portion — the new
    content the model actually had to read fresh. Cache reads are excluded
    from the up-arrow because they're served from cache (see `cache` segment
    for hit-ratio context). `+` only renders when cache_creation is non-zero,
    which signals the cache prefix changed this turn.
    """
    cw = data.get('context_window') or {}
    cu = cw.get('current_usage') or {}
    inp = int(cu.get('input_tokens') or 0)
    out = int(cu.get('output_tokens') or 0)
    cc = int(cu.get('cache_creation_input_tokens') or 0)

    seg = config.get('segments', {}).get('tokens', {})
    c = _colors(config)
    show_icon = seg.get('show_icon', False)
    icon = _icon(config, 'tokens') if show_icon else ''

    parts = []
    if icon:
        parts.append(paint(icon, c.get('tokens.icon', 'muted'), theme))
    if seg.get('show_input', True):
        parts.append(paint(f'↑{_fmt_tokens(inp)}', c.get('tokens.input', 'muted'), theme))
    if seg.get('show_output', True):
        out_warn = int(seg.get('output_warn_threshold', 6000))
        col = c.get('tokens.output_high', 'warning') if out >= out_warn else c.get('tokens.output', 'muted')
        parts.append(paint(f'↓{_fmt_tokens(out)}', col, theme))
    if seg.get('show_cache_creation', True) and cc > 0:
        parts.append(paint(f'+{_fmt_tokens(cc)}', c.get('tokens.cache_creation', 'warning'), theme))
    return ' '.join(parts)


def render_tokens_session(data, config, theme):
    """Cumulative session tokens: Σ ↑total-input ↓total-output."""
    cw = data.get('context_window') or {}
    inp = int(cw.get('total_input_tokens') or 0)
    out = int(cw.get('total_output_tokens') or 0)
    seg = config.get('segments', {}).get('tokens-session', {})
    c = _colors(config)
    icon = _icon(config, 'tokens_session')
    parts = []
    if icon:
        parts.append(paint(icon, c.get('tokens_session.icon', 'muted'), theme))
    if seg.get('show_input', True):
        parts.append(paint(f'↑{_fmt_tokens(inp)}', c.get('tokens_session.input', 'muted'), theme))
    if seg.get('show_output', True):
        parts.append(paint(f'↓{_fmt_tokens(out)}', c.get('tokens_session.output', 'muted'), theme))
    return ' '.join(parts)


_TZ_OFFSET_RX = re.compile(r'^([+-])(\d{2}):?(\d{2})$')


def _resolve_tz(tz_config):
    """Return a tzinfo (or None for system-local).

    Accepts: ``None`` / ``'local'`` / ``'system'`` (system local — None);
    ``'UTC'``; offset strings (``'+05:30'`` / ``'-0800'``); or any IANA
    name (``'America/Los_Angeles'``). IANA names need ``zoneinfo``
    (Python 3.9+); on older Pythons or when the tz database is missing,
    we silently fall back to system local rather than blanking the segment.
    """
    if tz_config is None:
        return None
    if not isinstance(tz_config, str):
        return None
    s = tz_config.strip()
    if not s or s.lower() in ('local', 'system'):
        return None
    if s.upper() == 'UTC':
        from datetime import timezone
        return timezone.utc
    m = _TZ_OFFSET_RX.match(s)
    if m:
        from datetime import timedelta, timezone
        sign, hh, mm = m.groups()
        offset = timedelta(hours=int(hh), minutes=int(mm))
        if sign == '-':
            offset = -offset
        return timezone(offset)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        return ZoneInfo(s)
    except ImportError:
        return None
    except Exception:
        # ZoneInfoNotFoundError or any IO error reading the tz database.
        return None


def _fmt_clock(epoch_seconds, tz=None):
    """HH:mm at the given tzinfo (or system-local if ``tz is None``).

    Used to show *when* the cache expires rather than a live countdown
    — minute-grained clocks survive low-frequency statusline refreshes
    (CC's docs explicitly endorse this for time data). The tz hook lets
    a UTC container display the host's wall-clock time."""
    if tz is None:
        return _time.strftime('%H:%M', _time.localtime(epoch_seconds))
    from datetime import datetime
    return datetime.fromtimestamp(epoch_seconds, tz).strftime('%H:%M')


# Glyph tier thresholds (seconds remaining). Picked so that each event-driven
# CC re-render conveys urgency passively — no polling required.
_TTL_GLYPH_DEFAULTS = {
    'ok':      '⏳',
    'alert':   '⏰',
    'warn':    '⚠',
    'expired': '⚠',
}


def _ttl_tier(remaining, alert_secs, warn_secs):
    if remaining <= 0:
        return 'expired'
    if remaining < warn_secs:
        return 'warn'
    if remaining < alert_secs:
        return 'alert'
    return 'ok'


def _tier_key(ttl_seconds):
    """Map a TTL window to a config key. Anything ≥1h is treated as '1h',
    anything ≤5m as '5m'; values in between fall back to the larger key."""
    if ttl_seconds >= 3600:
        return '1h'
    if ttl_seconds <= 300:
        return '5m'
    return '1h'


def _resolve_tier_secs(value, ttl_seconds, fallback):
    """Accept int (use as-is) or {'1h': N, '5m': M} (lookup by current tier).
    Falls through to `fallback` if a dict lacks an entry for the active tier."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        v = value.get(_tier_key(ttl_seconds))
        if isinstance(v, (int, float)):
            return int(v)
    return int(fallback)


def render_cache(data, config, theme):
    """Prompt-cache health: hit ratio · expiry clock · $ at risk on miss.

    Hit ratio is rolled up across every assistant turn in the current
    session — `Σcache_read / Σ(cache_read + input_tokens + cache_creation)`
    — with totals incrementally aggregated from the transcript and persisted
    per-session. Per-turn ratios are misleading in Claude Code: input_tokens
    is nearly always 1, so a single turn that rebuilds half the prefix still
    reads ~99% on a per-turn basis. The session view shows true efficiency.

    TTL is shown as the wall-clock expiry time (HH:mm), not a countdown —
    a sub-second `refreshInterval` corrupts CC's TUI re-renders, but a
    minute-grained clock works fine at the docs-recommended 60s interval.
    The PostToolUse hook bumps a per-session marker so long agent turns
    keep the expiry accurate. Glyph tiers (⏳ → ⏰ <5m → ⚠ <1m → ⚠ expired)
    convey urgency on each event-driven re-render without any polling.

    The cache tier (5m vs 1h) is auto-detected from the latest assistant
    turn's usage breakdown. `at_risk` uses the most recent turn's
    cache_read since that represents the value at risk on the next miss.
    """
    cw = data.get('context_window') or {}
    cu = cw.get('current_usage') or {}
    transcript = data.get('transcript_path')
    session_id = data.get('session_id')

    state = get_session_cache_state(transcript, session_id)
    if state and state['totals']['turns'] > 0:
        totals = state['totals']
        detected_tier = state['tier_seconds']
    elif cu:
        # No transcript yet (very early in session) — fall back to current_usage.
        totals = {
            'cache_read': int(cu.get('cache_read_input_tokens') or 0),
            'input_tokens': int(cu.get('input_tokens') or 0),
            'cache_creation': int(cu.get('cache_creation_input_tokens') or 0),
            'turns': 1,
        }
        detected_tier = None
    else:
        totals = None
        detected_tier = None

    seg = config.get('segments', {}).get('cache', {})
    c = _colors(config)
    icon = _icon(config, 'cache') if seg.get('show_icon', True) else ''

    # TTL resolution: explicit user override → auto-detect → 1h fallback.
    ttl_override = seg.get('ttl_seconds')
    if ttl_override is not None:
        ttl_seconds = int(ttl_override)
    else:
        ttl_seconds = detected_tier or DEFAULT_TTL_SECONDS

    parts = []

    if seg.get('show_hit_ratio', True):
        denom = 0
        if totals:
            denom = totals['cache_read'] + totals['input_tokens'] + totals['cache_creation']
        if denom > 0:
            hit = totals['cache_read'] / denom
            hit_pct = int(round(hit * 100))
            warn_below = int(seg.get('hit_warn_below', 70))
            crit_below = int(seg.get('hit_crit_below', 30))
            if hit_pct < crit_below:
                col = c.get('cache.hit_crit', 'error')
            elif hit_pct < warn_below:
                col = c.get('cache.hit_low', 'warning')
            else:
                col = c.get('cache.hit_high', 'success')
            label = f'{icon} {hit_pct}%' if icon else f'{hit_pct}%'
        else:
            col = 'muted'
            label = f'{icon} —%' if icon else '—%'
        parts.append(paint(label, col, theme))
        icon = ''  # only attach to first piece

    if seg.get('show_ttl', True):
        remaining = get_cache_ttl_remaining(transcript, ttl_seconds, session_id)
        if remaining is not None:
            # 5m cache shrinks the warning windows proportionally — alerting at
            # "5 min remaining" on a 5-min cache means always alert.
            alert_default = 60 if ttl_seconds <= 300 else 300
            warn_default = 15 if ttl_seconds <= 300 else 60
            alert_secs = _resolve_tier_secs(
                seg.get('ttl_alert_seconds'), ttl_seconds, alert_default,
            )
            warn_secs = _resolve_tier_secs(
                seg.get('ttl_warn_seconds'), ttl_seconds, warn_default,
            )
            tier = _ttl_tier(remaining, alert_secs, warn_secs)
            glyphs = dict(_TTL_GLYPH_DEFAULTS)
            user_glyphs = seg.get('ttl_glyphs')
            if isinstance(user_glyphs, dict):
                glyphs.update({k: v for k, v in user_glyphs.items() if isinstance(v, str)})
            # Single-glyph back-compat: a string `ttl_glyph` overrides the
            # 'ok' tier without forcing users to expand the full map.
            single = seg.get('ttl_glyph')
            if isinstance(single, str):
                glyphs['ok'] = single

            if tier == 'expired':
                label = f"{glyphs['expired']} expired"
                col = c.get('cache.expired', 'error')
            else:
                expiry = get_cache_expiry_epoch(transcript, ttl_seconds, session_id)
                tz = _resolve_tz(seg.get('timezone'))
                clock = _fmt_clock(expiry, tz) if expiry is not None else ''
                col_keys = {
                    'warn':  ('cache.ttl_warn', 'warning'),
                    'alert': ('cache.ttl_alert', c.get('cache.ttl_warn', 'warning')),
                    'ok':    ('cache.ttl', 'muted'),
                }
                key, default = col_keys[tier]
                col = c.get(key, default)
                label = f'{glyphs[tier]} {clock}'.strip()
            parts.append(paint(label, col, theme))

    # at_risk reflects what's at stake on the *next* miss, so it uses the
    # most recent turn's cache_read (from current_usage), not the session sum.
    last_cache_read = int((cu or {}).get('cache_read_input_tokens') or 0)
    if seg.get('show_at_risk', True) and last_cache_read > 0:
        model_id = (data.get('model') or {}).get('id')
        ttl_kind = '1h' if ttl_seconds >= 3600 else '5m'
        risk = at_risk_cost(last_cache_read, model_id, ttl=ttl_kind)
        min_show = float(seg.get('at_risk_min', 0.01))
        if risk >= min_show:
            parts.append(paint(f'${risk:.2f}', c.get('cache.at_risk', 'muted'), theme))

    return ' · '.join(parts)


RENDERERS = {
    'model':          render_model,
    'effort':         render_effort,
    'cwd':            render_cwd,
    'git':            render_git,
    'env':            render_env,
    'cost':           render_cost,
    'cost-day-forecast': render_cost_day_forecast,
    'cost-month-forecast': render_cost_month_forecast,
    'cost-avg':       render_cost_day_forecast,  # legacy alias
    'context':        render_context,
    'limits':         render_limits,
    'tokens':         render_tokens,
    'tokens-session': render_tokens_session,
    'cache':          render_cache,
    'duration':       render_duration,
    'runtime':        render_runtime,
    'cc-version':     render_cc_version,
}


def render_segment(name, data, config, theme):
    fn = RENDERERS.get(name)
    if not fn:
        return ''
    try:
        return fn(data, config, theme) or ''
    except Exception:
        # Swallowed by default so a single broken segment can't blank the
        # whole statusline. Set CC_VITALS_DEBUG=1 to surface the traceback.
        if os.environ.get('CC_VITALS_DEBUG'):
            raise
        return ''
