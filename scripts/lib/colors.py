"""ANSI color helpers and themed palettes."""
import os
import re

_NO_COLOR = bool(os.environ.get('NO_COLOR'))

RESET = '' if _NO_COLOR else '\033[0m'
BOLD = '' if _NO_COLOR else '\033[1m'
DIM = '' if _NO_COLOR else '\033[2m'

_HEX3_RX = re.compile(r'^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$')
_HEX6_RX = re.compile(r'^#[0-9a-fA-F]{6}$')


def _normalize_hex(value):
    """Return canonical '#RRGGBB' for a valid hex color, else None.

    Accepts 6-digit and 3-digit shorthand ('#FFF' -> '#FFFFFF'). Anything
    that isn't a string starting with '#' followed by 3 or 6 hex digits
    returns None — callers fall back to default colors instead of raising
    deep in render."""
    if not isinstance(value, str):
        return None
    if _HEX6_RX.match(value):
        return value.upper()
    m = _HEX3_RX.match(value)
    if m:
        a, b, c = m.groups()
        return f'#{a}{a}{b}{b}{c}{c}'.upper()
    return None


def _hex_to_rgb(h):
    norm = _normalize_hex(h)
    if not norm:
        return None
    h = norm.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fg(hex_color):
    if _NO_COLOR or not hex_color:
        return ''
    rgb = _hex_to_rgb(hex_color)
    if not rgb:
        return ''
    r, g, b = rgb
    return f'\033[38;2;{r};{g};{b}m'


THEMES = {
    "vs-dark-modern": {
        "primary":   "#CCCCCC",
        "secondary": "#9CDCFE",
        "accent":    "#569CD6",
        "muted":     "#858585",
        "warning":   "#DCDCAA",
        "error":     "#F44747",
        "success":   "#6A9955",
        "dim":       "#5A5A5A"
    },
    "high-contrast": {
        "primary":   "#FFFFFF",
        "secondary": "#00FFFF",
        "accent":    "#FFFF00",
        "muted":     "#BBBBBB",
        "warning":   "#FFB000",
        "error":     "#FF4040",
        "success":   "#00FF00",
        "dim":       "#888888"
    },
    "claude-default": {
        "primary":   "#E5E5E5",
        "secondary": "#C3A995",
        "accent":    "#D97757",
        "muted":     "#8F8F8F",
        "warning":   "#D4A464",
        "error":     "#E06C75",
        "success":   "#98C379",
        "dim":       "#5F5F5F"
    }
}


def resolve_color(token, theme):
    if not token:
        return None
    if isinstance(token, str) and token.startswith('#'):
        return _normalize_hex(token)
    if isinstance(theme, dict):
        val = theme.get(token)
        if isinstance(val, str) and val.startswith('#'):
            return _normalize_hex(val)
        return val
    return None


def _lerp(a, b, t):
    return int(round(a + (b - a) * t))


def _mix_hex(hex_a, hex_b, t):
    a = _hex_to_rgb(hex_a)
    b = _hex_to_rgb(hex_b)
    if not a or not b:
        return a and _normalize_hex(hex_a) or b and _normalize_hex(hex_b)
    return '#{:02X}{:02X}{:02X}'.format(
        _lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t)
    )


def gradient_hex(frac, theme, stops=('success', 'warning', 'error')):
    """Pick a hex color along a gradient through theme palette stops.

    frac is clamped to [0, 1]. With three stops, 0.0 -> first, 0.5 -> middle,
    1.0 -> last; linearly interpolated in RGB between adjacent stops.
    """
    if not isinstance(theme, dict) or not stops:
        return None
    f = max(0.0, min(1.0, float(frac)))
    if len(stops) == 1:
        return resolve_color(stops[0], theme)
    n = len(stops) - 1
    pos = f * n
    i = min(int(pos), n - 1)
    local = pos - i
    a = resolve_color(stops[i], theme)
    b = resolve_color(stops[i + 1], theme)
    if not a or not b:
        return a or b
    return _mix_hex(a, b, local)


def paint(text, color_token, theme, bold=False, dim=False):
    if not text:
        return ''
    color = resolve_color(color_token, theme)
    prefix = ''
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    if color:
        prefix += _fg(color)
    if not prefix:
        return text
    return f'{prefix}{text}{RESET}'
