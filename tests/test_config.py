import json
import unittest
from pathlib import Path

import tests  # noqa: F401
import config


class DeepMerge(unittest.TestCase):
    def test_flat_override(self):
        out = config._deep_merge({'a': 1, 'b': 2}, {'b': 3, 'c': 4})
        self.assertEqual(out, {'a': 1, 'b': 3, 'c': 4})

    def test_nested_merge(self):
        base = {'segments': {'cwd': {'max_length': 40}, 'git': {'show': True}}}
        override = {'segments': {'cwd': {'max_length': 60}}}
        out = config._deep_merge(base, override)
        self.assertEqual(out['segments']['cwd']['max_length'], 60)
        self.assertEqual(out['segments']['git'], {'show': True})

    def test_dict_replaces_non_dict(self):
        out = config._deep_merge({'x': 1}, {'x': {'y': 2}})
        self.assertEqual(out, {'x': {'y': 2}})

    def test_non_dict_short_circuits(self):
        # If either side isn't a dict, override wins outright.
        self.assertEqual(config._deep_merge({'a': 1}, [1, 2]), [1, 2])
        self.assertEqual(config._deep_merge('hi', {'a': 1}), {'a': 1})

    def test_does_not_mutate_inputs(self):
        base = {'a': {'b': 1}}
        override = {'a': {'c': 2}}
        config._deep_merge(base, override)
        self.assertEqual(base, {'a': {'b': 1}})
        self.assertEqual(override, {'a': {'c': 2}})


class LoadDefaultConfig(unittest.TestCase):
    def test_default_config_is_valid_json_with_required_keys(self):
        cfg = config.load_default_config()
        self.assertIn('theme', cfg)
        self.assertIn('lines', cfg)
        self.assertIsInstance(cfg['lines'], list)
        self.assertIn('colors', cfg)
        self.assertIn('segments', cfg)


class LoadConfigUserOverride(unittest.TestCase):
    def setUp(self):
        # Re-point USER_CONFIG_PATH at a temp file so we don't touch real config.
        self._original = config.USER_CONFIG_PATH

    def tearDown(self):
        config.USER_CONFIG_PATH = self._original

    def test_missing_user_config_returns_defaults(self):
        config.USER_CONFIG_PATH = Path('/nonexistent/cc-vitals-test/no.json')
        cfg = config.load_config()
        defaults = config.load_default_config()
        self.assertEqual(cfg['theme'], defaults['theme'])

    def test_user_config_deep_merges(self):
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            json.dump({'theme': 'high-contrast',
                       'segments': {'cwd': {'max_length': 99}}}, f)
            path = f.name
        try:
            config.USER_CONFIG_PATH = Path(path)
            cfg = config.load_config()
            self.assertEqual(cfg['theme'], 'high-contrast')
            self.assertEqual(cfg['segments']['cwd']['max_length'], 99)
            # Other segments survive the deep merge.
            self.assertIn('git', cfg['segments'])
        finally:
            Path(path).unlink()

    def test_malformed_user_config_falls_back_to_defaults(self):
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        ) as f:
            f.write('{not: valid json')
            path = f.name
        try:
            config.USER_CONFIG_PATH = Path(path)
            cfg = config.load_config()
            self.assertIn('theme', cfg)
        finally:
            Path(path).unlink()

    def test_env_theme_override(self):
        import os
        os.environ['CC_VITALS_THEME'] = 'claude-default'
        config.USER_CONFIG_PATH = Path('/nonexistent/x.json')
        try:
            cfg = config.load_config()
            self.assertEqual(cfg['theme'], 'claude-default')
        finally:
            del os.environ['CC_VITALS_THEME']


if __name__ == '__main__':
    unittest.main()
