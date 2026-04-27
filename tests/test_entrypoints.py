"""End-to-end tests for the tmux-mode entrypoints.

`scripts/ingest.py` (producer) and `scripts/tick.py` (host-side ticker)
are script files, not modules — we exercise them as subprocesses to
verify the contract CC and tmux actually see (stdin in, files mutated,
stdout out)."""
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
_INGEST = _SCRIPTS / 'ingest.py'
_TICK = _SCRIPTS / 'tick.py'


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


def _published_dir(home, env=None):
    """Resolve where ingest publishes its per-line manifests for this test
    home. Mirrors `publish.published_dir` precedence (env override beats
    the default)."""
    if env and env.get('CC_VITALS_DUMP_DIR'):
        return Path(env['CC_VITALS_DUMP_DIR'])
    return home / '.claude' / 'plugin-data' / 'cc-vitals' / 'published'


class IngestEntrypoint(unittest.TestCase):
    def test_publishes_manifest_per_line_and_no_stdout(self):
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

            published = _published_dir(home, env)
            line0 = published / 'myslot.line0.json'
            self.assertTrue(line0.exists(),
                            msg=f'expected manifest at {line0}')
            payload = json.loads(line0.read_text())
            self.assertIn('items', payload)
            self.assertIsInstance(payload['items'], list)
            # Manifest items always have a `type` discriminator.
            for it in payload['items']:
                self.assertIn(it.get('type'), {'static', 'live_ttl'})

    def test_falls_back_to_session_id_when_no_slot(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            stdin = json.dumps({'session_id': 'sess-fallback',
                                'cost': {'total_cost_usd': 0.0}})
            res = _run(_INGEST, _isolated_env(home), stdin=stdin)
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            published = _published_dir(home)
            self.assertTrue((published / 'sess-fallback.line0.json').exists())

    def test_dump_dir_env_override(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as bind:
            home = Path(d)
            stdin = json.dumps({
                'session_id': 'sess-bind',
                'cost': {'total_cost_usd': 0.0},
                'model': {'display_name': 'X'},
            })
            env = _isolated_env(home,
                                CC_VITALS_SLOT='bind-slot',
                                CC_VITALS_DUMP_DIR=str(bind))
            res = _run(_INGEST, env, stdin=stdin)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            # Manifest must land in the env-overridden dir, not under HOME.
            self.assertTrue((Path(bind) / 'bind-slot.line0.json').exists())
            default = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'published'
            self.assertFalse(
                (default / 'bind-slot.line0.json').exists(),
                msg='manifest must not fall through to default dir when env override is set',
            )

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


class TickEntrypoint(unittest.TestCase):
    """Drives `tick.py` end-to-end. We seed the published manifest dir
    directly so we can exercise tick.py in isolation from the producer —
    end-to-end ingest+tick is covered by the producer-side smoke tests."""

    def _seed_manifest(self, home, slot, line_index, items):
        pub = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'published'
        pub.mkdir(parents=True, exist_ok=True)
        (pub / f'{slot}.line{line_index}.json').write_text(
            json.dumps({'items': items}))

    def test_emits_tmux_markup_not_raw_ansi(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'myslot', 0, [
                {'type': 'static', 'ansi': '\x1b[38;2;100;200;50mClaude\x1b[0m'},
            ])
            env = _isolated_env(home, CC_VITALS_SLOT='myslot')
            res = _run(_TICK, env, argv=('myslot', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('#[fg=', res.stdout)
            self.assertNotIn('\x1b[', res.stdout)
            self.assertNotIn('\n', res.stdout)

    def test_line_index_selects_specific_row(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'sl', 0, [
                {'type': 'static', 'ansi': 'TopRowModel'},
            ])
            self._seed_manifest(home, 'sl', 1, [
                {'type': 'static', 'ansi': 'BottomRow'},
            ])
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            line0 = _run(_TICK, env, argv=('sl', '0'))
            line1 = _run(_TICK, env, argv=('sl', '1'))
            self.assertIn('TopRowModel', line0.stdout)
            self.assertNotIn('BottomRow', line0.stdout)
            self.assertIn('BottomRow', line1.stdout)
            self.assertNotIn('TopRowModel', line1.stdout)

    def test_live_ttl_renders_mm_ss(self):
        """A live_ttl item produces a `mm:ss` countdown that matches the
        wall-clock at tick time. This is the whole point of splitting
        producer and consumer."""
        with TemporaryDirectory() as d:
            home = Path(d)
            expiry = int(time.time()) + 125  # 2:05 from now
            self._seed_manifest(home, 'sl', 0, [
                {'type': 'live_ttl',
                 'expiry_epoch': expiry,
                 'style': 'countdown',
                 'ttl_seconds': 3600,
                 'alert_secs': 300,
                 'warn_secs': 60,
                 'glyphs': {'ok': 'O', 'alert': 'A', 'warn': 'W', 'expired': 'E'},
                 'ansi_prefix': {'ok': '', 'alert': '', 'warn': '', 'expired': ''},
                 'ansi_reset': ''},
            ])
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_TICK, env, argv=('sl', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            import re
            m = re.search(r'(\d+):(\d{2})', res.stdout)
            self.assertIsNotNone(m, msg=f'no mm:ss in {res.stdout!r}')
            secs = int(m.group(1)) * 60 + int(m.group(2))
            self.assertLessEqual(abs(secs - 125), 2,
                                 msg=f'countdown drift: {res.stdout!r}')

    def test_out_of_range_line_index_emits_nothing(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'sl', 0, [{'type': 'static', 'ansi': 'X'}])
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_TICK, env, argv=('sl', '99'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertEqual(res.stdout, '')

    def test_default_line_index_is_zero(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'sl', 0, [
                {'type': 'static', 'ansi': 'TopRowModel'},
            ])
            env = _isolated_env(home, CC_VITALS_SLOT='sl')
            res = _run(_TICK, env, argv=('sl',))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('TopRowModel', res.stdout)

    def test_argv_slot_takes_precedence(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'argv-slot', 0,
                                [{'type': 'static', 'ansi': 'FROM-ARGV'}])
            self._seed_manifest(home, 'env-slot', 0,
                                [{'type': 'static', 'ansi': 'FROM-ENV'}])
            env = _isolated_env(home, CC_VITALS_SLOT='env-slot')
            res = _run(_TICK, env, argv=('argv-slot', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('FROM-ARGV', res.stdout)
            self.assertNotIn('FROM-ENV', res.stdout)

    def test_no_manifest_prints_nothing(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            env = _isolated_env(home, CC_VITALS_SLOT='ghost')
            res = _run(_TICK, env, argv=('ghost', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertEqual(res.stdout, '')

    def test_dump_dir_env_override(self):
        """When CC_VITALS_DUMP_DIR is set, the ticker reads from there
        — the producer/consumer seam in the container scenario."""
        with TemporaryDirectory() as d, TemporaryDirectory() as bind:
            home = Path(d)
            # Seed manifest in the bind dir, NOT under HOME.
            (Path(bind) / 'cs.line0.json').write_text(
                json.dumps({'items': [{'type': 'static', 'ansi': 'cross-boundary'}]}))
            env = _isolated_env(home,
                                CC_VITALS_SLOT='cs',
                                CC_VITALS_DUMP_DIR=str(bind))
            res = _run(_TICK, env, argv=('cs', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('cross-boundary', res.stdout)

    def test_mtime_fallback_when_no_slot(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            self._seed_manifest(home, 'older', 0,
                                [{'type': 'static', 'ansi': 'OLD'}])
            self._seed_manifest(home, 'newer', 0,
                                [{'type': 'static', 'ansi': 'NEW'}])
            pub = home / '.claude' / 'plugin-data' / 'cc-vitals' / 'published'
            now = time.time()
            os.utime(pub / 'older.line0.json', (now - 100, now - 100))
            os.utime(pub / 'newer.line0.json', (now - 1, now - 1))
            env = _isolated_env(home)
            res = _run(_TICK, env, argv=('', '0'))
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn('NEW', res.stdout)
            self.assertNotIn('OLD', res.stdout)


if __name__ == '__main__':
    unittest.main()
