import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401
import session_discovery as sd


class IsolatedSessionsDir:
    """Context manager that swaps SESSIONS_DIR for a temp path."""
    def __enter__(self):
        self._tmp = TemporaryDirectory()
        self._patch = mock.patch.object(
            sd, 'SESSIONS_DIR', Path(self._tmp.name) / 'sessions',
        )
        self._patch.start()
        return Path(self._tmp.name) / 'sessions'

    def __exit__(self, *exc):
        self._patch.stop()
        self._tmp.cleanup()


class SafeSlot(unittest.TestCase):
    def test_accepts_alphanum(self):
        self.assertEqual(sd._safe_slot('repo-abc123'), 'repo-abc123')

    def test_accepts_dot_and_underscore(self):
        self.assertEqual(sd._safe_slot('my_repo.test'), 'my_repo.test')

    def test_rejects_slash(self):
        self.assertIsNone(sd._safe_slot('a/b'))

    def test_rejects_empty(self):
        self.assertIsNone(sd._safe_slot(''))
        self.assertIsNone(sd._safe_slot('   '))

    def test_rejects_unicode(self):
        self.assertIsNone(sd._safe_slot('résumé'))

    def test_rejects_too_long(self):
        self.assertIsNone(sd._safe_slot('x' * 200))

    def test_rejects_non_string(self):
        self.assertIsNone(sd._safe_slot(123))
        self.assertIsNone(sd._safe_slot(None))


class ResolveSlot(unittest.TestCase):
    def test_argv_wins_over_env(self):
        out = sd.resolve_slot(argv_slot='from-argv',
                              env={'CC_VITALS_SLOT': 'from-env'})
        self.assertEqual(out, 'from-argv')

    def test_env_used_when_no_argv(self):
        out = sd.resolve_slot(env={'CC_VITALS_SLOT': 'from-env'})
        self.assertEqual(out, 'from-env')

    def test_neither_returns_none(self):
        self.assertIsNone(sd.resolve_slot(env={}))

    def test_empty_argv_falls_back_to_env(self):
        # tmux passes #{session_name} which can render empty in odd setups;
        # treat that the same as no argv.
        out = sd.resolve_slot(argv_slot='', env={'CC_VITALS_SLOT': 'env-slot'})
        self.assertEqual(out, 'env-slot')

    def test_unsafe_argv_rejected(self):
        out = sd.resolve_slot(argv_slot='a/b', env={'CC_VITALS_SLOT': 'good'})
        self.assertEqual(out, 'good')


class SessionPath(unittest.TestCase):
    def test_path_includes_slot_and_json_extension(self):
        with IsolatedSessionsDir() as base:
            p = sd.session_path('myslot')
            self.assertEqual(p, base / 'myslot.json')

    def test_unsafe_returns_none(self):
        with IsolatedSessionsDir():
            self.assertIsNone(sd.session_path('a/b'))


class DiscoverLatest(unittest.TestCase):
    def test_empty_dir_returns_none(self):
        with IsolatedSessionsDir():
            self.assertIsNone(sd.discover_latest())

    def test_picks_most_recent(self):
        with IsolatedSessionsDir() as base:
            base.mkdir(parents=True)
            a = base / 'older.json'
            b = base / 'newer.json'
            a.write_text('{}')
            b.write_text('{}')
            now = time.time()
            os.utime(a, (now - 100, now - 100))
            os.utime(b, (now - 10, now - 10))
            self.assertEqual(sd.discover_latest(), b)

    def test_skips_stale_beyond_ttl(self):
        with IsolatedSessionsDir() as base:
            base.mkdir(parents=True)
            stale = base / 'stale.json'
            stale.write_text('{}')
            old = time.time() - (10 * 3600)
            os.utime(stale, (old, old))
            self.assertIsNone(sd.discover_latest(ttl_seconds=3600))

    def test_ignores_non_json_files(self):
        with IsolatedSessionsDir() as base:
            base.mkdir(parents=True)
            (base / 'note.txt').write_text('hi')
            self.assertIsNone(sd.discover_latest())


if __name__ == '__main__':
    unittest.main()
