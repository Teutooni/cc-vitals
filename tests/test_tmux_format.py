import unittest

import tests  # noqa: F401
from tmux_format import ansi_to_tmux


class AnsiToTmux(unittest.TestCase):
    def test_plain_text_unchanged(self):
        self.assertEqual(ansi_to_tmux('hello world'), 'hello world')

    def test_empty(self):
        self.assertEqual(ansi_to_tmux(''), '')

    def test_reset(self):
        self.assertEqual(ansi_to_tmux('\x1b[0m'), '#[default]')

    def test_bold(self):
        self.assertEqual(ansi_to_tmux('\x1b[1mhello\x1b[0m'),
                         '#[bold]hello#[default]')

    def test_dim(self):
        self.assertEqual(ansi_to_tmux('\x1b[2m...\x1b[0m'),
                         '#[dim]...#[default]')

    def test_truecolor_fg(self):
        # 0xD4A464 = 212,164,100
        self.assertEqual(
            ansi_to_tmux('\x1b[38;2;212;164;100mwarn\x1b[0m'),
            '#[fg=#D4A464]warn#[default]',
        )

    def test_combined_bold_then_color(self):
        self.assertEqual(
            ansi_to_tmux('\x1b[1m\x1b[38;2;255;0;0mERR\x1b[0m'),
            '#[bold]#[fg=#FF0000]ERR#[default]',
        )

    def test_hex_uppercase(self):
        # ensure low values still emit zero-padded uppercase hex
        self.assertEqual(
            ansi_to_tmux('\x1b[38;2;1;2;3mx\x1b[0m'),
            '#[fg=#010203]x#[default]',
        )

    def test_literal_hash_doubled(self):
        self.assertEqual(ansi_to_tmux('# 12'), '## 12')

    def test_literal_hash_inside_painted_text(self):
        self.assertEqual(
            ansi_to_tmux('\x1b[1m#main\x1b[0m'),
            '#[bold]##main#[default]',
        )

    def test_separator_only(self):
        # The default separator " │ " painted dim.
        self.assertEqual(
            ansi_to_tmux('\x1b[2m │ \x1b[0m'),
            '#[dim] │ #[default]',
        )

    def test_unknown_sgr_dropped(self):
        # 256-color FG (\x1b[38;5;Nm) is outside our vocabulary; drop it.
        self.assertEqual(ansi_to_tmux('\x1b[38;5;208mx\x1b[0m'),
                         'x#[default]')

    def test_malformed_escape_dropped(self):
        # No `m` terminator anywhere — non-SGR sequence is left as literal
        # text but not emitted as tmux markup.
        self.assertEqual(
            ansi_to_tmux('before\x1b[1cafter'),
            'before\x1b[1cafter',
        )

    def test_back_to_back_painted_segments(self):
        # Roughly what `colored_sep.join([...])` produces in statusline.py.
        rendered = (
            '\x1b[38;2;86;156;214mClaude\x1b[0m'
            '\x1b[2m │ \x1b[0m'
            '\x1b[38;2;204;204;204m~/proj\x1b[0m'
        )
        out = ansi_to_tmux(rendered)
        self.assertIn('#[fg=#569CD6]Claude#[default]', out)
        self.assertIn('#[dim] │ #[default]', out)
        self.assertIn('#[fg=#CCCCCC]~/proj#[default]', out)


if __name__ == '__main__':
    unittest.main()
