import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import tests  # noqa: F401
import state


class SaveAndLoad(unittest.TestCase):
    def test_round_trip(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'x.json'
            state.save_json_atomic(p, {'a': 1, 'b': [2, 3]})
            self.assertEqual(state.load_json(p), {'a': 1, 'b': [2, 3]})

    def test_load_missing_returns_empty(self):
        self.assertEqual(state.load_json(Path('/nonexistent/cc-vitals/x.json')), {})

    def test_load_corrupt_returns_empty(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'broken.json'
            p.write_text('{not json')
            self.assertEqual(state.load_json(p), {})

    def test_save_creates_parent_dir(self):
        with TemporaryDirectory() as d:
            nested = Path(d) / 'sub1' / 'sub2' / 'x.json'
            state.save_json_atomic(nested, {'ok': True})
            self.assertEqual(json.loads(nested.read_text()), {'ok': True})

    def test_atomic_write_no_partial_file_on_success(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'x.json'
            state.save_json_atomic(p, {'k': 'v'})
            # Tmp file should not linger after success.
            self.assertFalse((p.parent / (p.name + '.tmp')).exists())


class PruneSessionsLru(unittest.TestCase):
    def test_no_prune_under_max(self):
        sessions = {
            'a': {'last_seen': 1},
            'b': {'last_seen': 2},
        }
        state.prune_sessions_lru(sessions, max_items=5)
        self.assertEqual(set(sessions), {'a', 'b'})

    def test_prunes_oldest(self):
        sessions = {f's{i}': {'last_seen': i} for i in range(10)}
        state.prune_sessions_lru(sessions, max_items=3)
        # Survivors should be the 3 highest last_seen values.
        self.assertEqual(set(sessions), {'s7', 's8', 's9'})

    def test_missing_key_treated_as_zero(self):
        sessions = {
            'old': {},                 # no last_seen → 0
            'new': {'last_seen': 100},
        }
        state.prune_sessions_lru(sessions, max_items=1)
        self.assertEqual(set(sessions), {'new'})


if __name__ == '__main__':
    unittest.main()
