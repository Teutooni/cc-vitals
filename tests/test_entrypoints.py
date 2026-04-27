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
            res = _run(_RENDER, env, argv=('myslot', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            # tmux markup, not raw ANSI escapes.
            self.assertIn('#[fg=', res.stdout)
            self.assertNotIn('\x1b[', res.stdout)
            # Single line — no newlines (tmux flattens them anyway).
            self.assertNotIn('\n', res.stdout)

    def test_line_index_selects_specific_row(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            transcript = home / 't.jsonl'
            transcript.write_text('hi')
            self._seed_dump(home, 'sl', {
                'session_id': 'sess',
                'transcript_path': str(transcript),
                'model': {'display_name': 'TopRowModel'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            # Default config: line 0 has model+cwd+..., line 1 has
            # context+limits+tokens+cache. Pick by index.
            line0 = _run(_RENDER, env, argv=('sl', '0'))
            line1 = _run(_RENDER, env, argv=('sl', '1'))
            self.assertIn('TopRowModel', line0.stdout)
            self.assertNotIn('TopRowModel', line1.stdout)
            # Cache TTL piece only on line 1 in the default config.
            import re
            self.assertRegex(re.sub(r'#\[[^\]]*\]', '', line1.stdout),
                             r'\d{1,2}:\d{2}')
            self.assertNotRegex(re.sub(r'#\[[^\]]*\]', '', line0.stdout),
                                r'\d{1,2}:\d{2}')

    def test_out_of_range_line_index_emits_nothing(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_dump(home, 'sl', {
                'session_id': 'sess',
                'model': {'display_name': 'X'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_RENDER, env, argv=('sl', '99'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertEqual(res.stdout, '')

    def test_default_line_index_is_zero(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_dump(home, 'sl', {
                'session_id': 'sess',
                'model': {'display_name': 'TopRowModel'},
                'cost': {'total_cost_usd': 0.0},
            })
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_RENDER, env, argv=('sl',))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('TopRowModel', res.stdout)

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
            res = _run(_RENDER, env, argv=('argv-slot', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('FROM-ARGV', res.stdout)
            self.assertNotIn('FROM-ENV', res.stdout)

    def test_no_dump_prints_nothing(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            env = _isolated_env(home, CC_VITALS_SLOT='ghost')
            res = _run(_RENDER, env, argv=('ghost', '0'))
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
            res = _run(_RENDER, env, argv=('', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('NEW', res.stdout)
            self.assertNotIn('OLD', res.stdout)


if __name__ == '__main__':
    unittest.main()
