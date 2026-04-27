"""Translate the ANSI vocabulary `colors.paint()` emits into tmux markup.

`segments.py` builds rendered lines using ANSI SGR escapes. tmux's status
bar interprets its own `#[fg=#xxx,bold]` markup and treats raw ANSI as
literal text — so the tmux render entrypoint runs the joined line through
`ansi_to_tmux()` as a final pass.

Vocabulary covered (everything `colors.paint()` ever emits):
  - `\\x1b[0m`              -> `#[default]`
  - `\\x1b[1m`              -> `#[bold]`
  - `\\x1b[2m`              -> `#[dim]`
  - `\\x1b[38;2;R;G;Bm`     -> `#[fg=##RRGGBB]`

The `#` in hex colors is doubled — tmux re-parses our output through
`#(…)` substitution, and a single `#` followed by a directive letter
(`#D` = pane_id, `#F` = pane flags, etc.) gets expanded mid-color, so
`#[fg=#DCDCAA]` would arrive as `#[fg=<pane_id>CDCAA]` and silently drop
the fg. `##` is the format-string escape for a literal `#`, applied both
to payload text and to the color values we emit. Unrecognized SGR
parameters and malformed escapes are dropped silently — better a
missing color than a broken status bar.
"""
import re


_SGR_RX = re.compile(r'\x1b\[([0-9;]*)m')


def _sgr_to_tmux(params):
    """Translate one SGR parameter list to a tmux `#[...]` block, or '' if
    it doesn't map to anything we emit."""
    if params == '' or params == '0':
        return '#[default]'
    parts = params.split(';')
    attrs = []
    fg = None
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in ('', '0'):
            attrs.append('default')
            i += 1
        elif p == '1':
            attrs.append('bold')
            i += 1
        elif p == '2':
            attrs.append('dim')
            i += 1
        elif p == '38' and i + 4 < len(parts) and parts[i + 1] == '2':
            try:
                r = int(parts[i + 2])
                g = int(parts[i + 3])
                b = int(parts[i + 4])
            except ValueError:
                i += 5
                continue
            if 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255:
                # Double the `#` so tmux's format parser treats it as a
                # literal rather than the start of a directive (e.g. `#D`).
                fg = '##{:02X}{:02X}{:02X}'.format(r, g, b)
            i += 5
        else:
            i += 1
    pieces = []
    if fg:
        pieces.append(f'fg={fg}')
    pieces.extend(attrs)
    if not pieces:
        return ''
    return '#[' + ','.join(pieces) + ']'


def ansi_to_tmux(text):
    """Final-pass translator. Replaces SGR escapes with tmux markup and
    doubles literal `#` characters in payload."""
    if not text:
        return text
    out = []
    pos = 0
    for m in _SGR_RX.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            out.append(chunk.replace('#', '##'))
        block = _sgr_to_tmux(m.group(1))
        if block:
            out.append(block)
        pos = m.end()
    tail = text[pos:]
    if tail:
        out.append(tail.replace('#', '##'))
    return ''.join(out)
