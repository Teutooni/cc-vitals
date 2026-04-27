"""Smoke tests for hooks/cache-refresh.py.

Runs the hook as a subprocess with HOME pointed at a tempdir so the
marker lands somewhere we can inspect — same model the script will use
in the wild via `Path.home()`.
"""
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_HOOK = Path(__file__).resolve().parent.parent / 'hooks' / 'cache-refresh.py'


def _run(stdin, home):
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=stdin,
        text=True,
        capture_output=True,
        env={'HOME': str(home), 'PATH': '/usr/bin:/bin'},
        timeout=10,
    )


class HookCacheRefresh(unittest.TestCase):
    def test_creates_marker_for_valid_session(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            r = _run(json.dumps({'session_id': 'abc-123_XYZ'}), home)
            self.assertEqual(r.returncode, 0, r.stderr)
            marker = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'cache-refresh' / 'abc-123_XYZ'
            self.assertTrue(marker.exists())
            self.assertLess(time.time() - marker.stat().st_mtime, 5.0)

    def test_bumps_existing_marker(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            d_marker = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'cache-refresh'
            d_marker.mkdir(parents=True)
            marker = d_marker / 'sess1'
            marker.touch()
            import os
            old = time.time() - 1000
            os.utime(marker, (old, old))
            r = _run(json.dumps({'session_id': 'sess1'}), home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.time() - marker.stat().st_mtime, 5.0)

    def test_silent_on_invalid_json(self):
        with TemporaryDirectory() as d:
            r = _run('not json', Path(d))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, '')

    def test_silent_on_missing_session_id(self):
        with TemporaryDirectory() as d:
            r = _run(json.dumps({}), Path(d))
            self.assertEqual(r.returncode, 0)

    def test_rejects_unsafe_session_id(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            r = _run(json.dumps({'session_id': '../escape'}), home)
            self.assertEqual(r.returncode, 0)
            # No marker dir should appear at all because we never touched.
            self.assertFalse(
                (home / '.claude' / 'plugin-data' / 'cc-vitals' / 'cache-refresh' / 'escape').exists()
            )

    def test_rejects_non_string_session_id(self):
        with TemporaryDirectory() as d:
            r = _run(json.dumps({'session_id': 123}), Path(d))
            self.assertEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
