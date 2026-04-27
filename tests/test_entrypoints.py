"""End-to-end tests for the tmux-mode entrypoints.

`scripts/ingest.py` and `scripts/render-tmux.py` are script files, not
modules — we exercise them as subprocesses to verify the contract CC and
tmux actually see (stdin in, files mutated, stdout out)."""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
_INGEST = _SCRIPTS / 'ingest.py'
_RENDER = _SCRIPTS / 'render-tmux.py'


def _run(script, env, stdin='', argv=()):
    return subprocess.run(
        ['python3', str(script), *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _isolated_env(home, **extra):
    env = dict(os.environ)
    env['HOME'] = str(home)
    env.pop('CC_VITALS_SLOT', None)
    env.pop('CC_VITALS_THEME', None)
    env.pop('CC_VITALS_DEBUG', None)
    env.pop('NO_COLOR', None)
    env.update(extra)
    return env


class IngestEntrypoint(unittest.TestCase):
    def test_writes_per_slot_dump_and_no_stdout(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            stdin = json.dumps({
                'session_id': 'sess-A',
                'cost': {'total_cost_usd': 0.42},
                'cwd': '/tmp',
                'model': {'display_name': 'X'},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='myslot')
            res = _run(_INGEST, env, stdin=stdin)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertEqual(res.stdout, '')

            dump = home / '.claude' / 'plugin-data' / 'cc-vitals' \
                       / 'sessions' / 'myslot.json'
            self.assertTrue(dump.exists())
            data = json.loads(dump.read_text())
            self.assertEqual(data['session_id'], 'sess-A')
            self.assertAlmostEqual(data['cost']['total_cost_usd'], 0.42)

    def test_falls_back_to_session_id_when_no_slot(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            stdin = json.dumps({'session_id': 'sess-fallback',
                                'cost': {'total_cost_usd': 0.0}})
            res = _run(_INGEST, _isolated_env(home), stdin=stdin)
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            dump = home / '.claude' / 'plugin-data' / 'cc-vitals' \
                       / 'sessions' / 'sess-fallback.json'
            self.assertTrue(dump.exists())

    def test_advances_cost_state(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            stdin = json.dumps({'session_id': 'sess-C',
                                'cost': {'total_cost_usd': 1.50}})
            res = _run(_INGEST, _isolated_env(home), stdin=stdin)
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            costs = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'costs.json'
            self.assertTrue(costs.exists())
            data = json.loads(costs.read_text())
            self.assertIn('sess-C', data.get('sessions', {}))
            self.assertAlmostEqual(
                data['sessions']['sess-C']['last_cost'], 1.50,
            )

    def test_invalid_stdin_does_not_crash(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            res = _run(_INGEST, _isolated_env(home), stdin='not json')
            self.assertEqual(res.returncode, 0, msg=res.stderr)


class RenderTmuxEntrypoint(unittest.TestCase):
    def _seed_dump(self, home, slot, payload):
        sessions = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'sessions'
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / f'{slot}.json').write_text(json.dumps(payload))

    def test_emits_tmux_markup_not_raw_ansi(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            transcript = home / 't.jsonl'
            transcript.write_text('hi')
            self._seed_dump(home, 'myslot', {
                'session_id': 'sess',
                'transcript_path': str(transcript),
                'cwd': '/tmp',
                'model': {'display_name': 'Claude'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='myslot')
            res = _run(_RENDER, env)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            # tmux markup, not raw ANSI escapes.
            self.assertIn('#[fg=', res.stdout)
            self.assertNotIn('\x1b[', res.stdout)

    def test_argv_slot_takes_precedence(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_dump(home, 'argv-slot', {
                'session_id': 'sess',
                'model': {'display_name': 'FROM-ARGV'},
                'cost': {'total_cost_usd': 0.0},
            })
            self._seed_dump(home, 'env-slot', {
                'session_id': 'sess',
                'model': {'display_name': 'FROM-ENV'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='env-slot')
            res = _run(_RENDER, env, argv=('argv-slot',))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('FROM-ARGV', res.stdout)
            self.assertNotIn('FROM-ENV', res.stdout)

    def test_no_dump_prints_nothing(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            env = _isolated_env(home, CC_VITALS_SLOT='ghost')
            res = _run(_RENDER, env)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertEqual(res.stdout, '')

    def test_mtime_fallback_when_no_slot(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_dump(home, 'older', {
                'session_id': 's',
                'model': {'display_name': 'OLD'},
                'cost': {'total_cost_usd': 0.0},
            })
            self._seed_dump(home, 'newer', {
                'session_id': 's',
                'model': {'display_name': 'NEW'},
                'cost': {'total_cost_usd': 0.0},
            })
            sessions = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'sessions'
            now = time.time()
            os.utime(sessions / 'older.json', (now - 100, now - 100))
            os.utime(sessions / 'newer.json', (now - 1, now - 1))
            env = _isolated_env(home)
            res = _run(_RENDER, env)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('NEW', res.stdout)
            self.assertNotIn('OLD', res.stdout)

    def test_default_cache_style_is_countdown(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            transcript = home / 't.jsonl'
            transcript.write_text('hi')
            # Default (1h tier) — TTL display should be `mm:ss` form.
            self._seed_dump(home, 'sl', {
                'session_id': 'sess',
                'transcript_path': str(transcript),
                'model': {'display_name': 'C'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_RENDER, env)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            # Countdown shows the seconds with a colon and 2-digit pad
            # (`mm:ss`). The expiry-clock variant would use `HH:MM` with a
            # 2-digit minute. With a fresh transcript on the 1h tier the
            # countdown reads ~59:5x — both forms include `:`, so we look
            # specifically for the `:NN ` pattern at the end of a TTL piece.
            import re
            # strip tmux markup blocks for easier regex matching
            plain = re.sub(r'#\[[^\]]*\]', '', res.stdout)
            self.assertRegex(plain, r'\d{1,2}:\d{2}')


if __name__ == '__main__':
    unittest.main()
