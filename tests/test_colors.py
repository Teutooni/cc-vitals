import os
import unittest

# Force NO_COLOR off for these tests so paint() actually emits escapes.
os.environ.pop('NO_COLOR', None)

import tests  # noqa: F401 -- sets sys.path
import colors


class HexNormalization(unittest.TestCase):
    def test_six_digit_uppercased(self):
        self.assertEqual(colors._normalize_hex('#abcdef'), '#ABCDEF')

    def test_three_digit_expanded(self):
        self.assertEqual(colors._normalize_hex('#FFF'), '#FFFFFF')
        self.assertEqual(colors._normalize_hex('#0a3'), '#00AA33')

    def test_invalid_returns_none(self):
        for bad in ('', '#', '#ZZZ', '#FFFF', '#1234567', 'red', None, 123, '#GGG'):
            self.assertIsNone(colors._normalize_hex(bad))


class HexToRgb(unittest.TestCase):
    def test_six_digit(self):
        self.assertEqual(colors._hex_to_rgb('#FF8040'), (255, 128, 64))

    def test_three_digit_expansion(self):
        self.assertEqual(colors._hex_to_rgb('#F84'), (255, 136, 68))

    def test_invalid_returns_none(self):
        self.assertIsNone(colors._hex_to_rgb('not-a-color'))


class ResolveColor(unittest.TestCase):
    def setUp(self):
        self.theme = {'accent': '#569CD6', 'muted': '#858585'}

    def test_palette_token(self):
        self.assertEqual(colors.resolve_color('accent', self.theme), '#569CD6')

    def test_hex_passthrough_normalized(self):
        self.assertEqual(colors.resolve_color('#abc', self.theme), '#AABBCC')

    def test_invalid_hex_falls_through(self):
        self.assertIsNone(colors.resolve_color('#zzz', self.theme))

    def test_unknown_token(self):
        self.assertIsNone(colors.resolve_color('not-in-theme', self.theme))

    def test_empty_token(self):
        self.assertIsNone(colors.resolve_color('', self.theme))
        self.assertIsNone(colors.resolve_color(None, self.theme))


class GradientHex(unittest.TestCase):
    def setUp(self):
        # success → warning → error
        self.theme = {
            'success': '#00FF00',
            'warning': '#FFFF00',
            'error':   '#FF0000',
        }

    def test_endpoints(self):
        self.assertEqual(colors.gradient_hex(0.0, self.theme), '#00FF00')
        self.assertEqual(colors.gradient_hex(1.0, self.theme), '#FF0000')

    def test_midpoint_is_warning(self):
        self.assertEqual(colors.gradient_hex(0.5, self.theme), '#FFFF00')

    def test_clamps_out_of_range(self):
        self.assertEqual(colors.gradient_hex(-1.0, self.theme), '#00FF00')
        self.assertEqual(colors.gradient_hex(2.0, self.theme), '#FF0000')

    def test_single_stop(self):
        self.assertEqual(
            colors.gradient_hex(0.7, self.theme, stops=('warning',)),
            '#FFFF00',
        )

    def test_no_theme(self):
        self.assertIsNone(colors.gradient_hex(0.5, None))


class Paint(unittest.TestCase):
    def setUp(self):
        self.theme = {'accent': '#569CD6'}

    def test_empty_text_short_circuits(self):
        self.assertEqual(colors.paint('', 'accent', self.theme), '')

    def test_palette_token_emits_escape(self):
        out = colors.paint('hi', 'accent', self.theme)
        self.assertIn('\033[38;2;86;156;214m', out)
        self.assertTrue(out.endswith('\033[0m'))
        self.assertIn('hi', out)

    def test_no_color_no_escape(self):
        out = colors.paint('hi', None, self.theme)
        self.assertEqual(out, 'hi')

    def test_bold_dim_modifiers(self):
        out = colors.paint('x', 'accent', self.theme, bold=True, dim=True)
        self.assertIn('\033[1m', out)
        self.assertIn('\033[2m', out)


if __name__ == '__main__':
    unittest.main()
