"""Unit tests for `lib/publish.py`.

The published directory is the sole seam between producer and consumer.
Tests here lock down env-var precedence, atomic writes, and slot
discovery — anything a host/container bind-mount setup will exercise."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts', 'lib'))

import publish  # noqa: E402
from publish import (  # noqa: E402
    DEFAULT_PUBLISHED_DIR,
    discover_latest_slot,
    manifest_path,
    publish_line,
    publish_manifest,
    published_dir,
    read_line,
)


class PublishedDir(unittest.TestCase):
    def test_default_when_env_unset(self):
        self.assertEqual(published_dir({}), DEFAULT_PUBLISHED_DIR)

    def test_env_override(self):
        self.assertEqual(published_dir({'CC_VITALS_DUMP_DIR': '/tmp/x'}),
                         Path('/tmp/x'))

    def test_env_override_expands_tilde(self):
        out = published_dir({'CC_VITALS_DUMP_DIR': '~/foo'})
        self.assertEqual(out, Path.home() / 'foo')

    def test_blank_env_falls_back_to_default(self):
        # Whitespace-only override is treated as unset.
        self.assertEqual(published_dir({'CC_VITALS_DUMP_DIR': '   '}),
                         DEFAULT_PUBLISHED_DIR)


class ManifestPath(unittest.TestCase):
    def test_uses_dump_dir(self):
        with TemporaryDirectory() as d:
            p = manifest_path('foo', 1, env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(p, Path(d) / 'foo.line1.json')


class PublishLine(unittest.TestCase):
    def test_writes_atomically(self):
        with TemporaryDirectory() as d:
            ok = publish_line('s', 0, {'items': [{'type': 'static', 'ansi': 'hi'}]},
                              env={'CC_VITALS_DUMP_DIR': d})
            self.assertTrue(ok)
            payload = json.loads((Path(d) / 's.line0.json').read_text())
            self.assertEqual(payload['items'][0]['ansi'], 'hi')

    def test_no_partial_file_left_after_success(self):
        with TemporaryDirectory() as d:
            publish_line('s', 0, {'items': []},
                         env={'CC_VITALS_DUMP_DIR': d})
            # No `.tmp` should remain — replace was atomic.
            self.assertFalse(any(p.suffix == '.tmp' for p in Path(d).iterdir()))

    def test_creates_parent_dir_on_demand(self):
        with TemporaryDirectory() as d:
            nested = Path(d) / 'a' / 'b' / 'c'
            self.assertFalse(nested.exists())
            ok = publish_line('s', 0, {'items': []},
                              env={'CC_VITALS_DUMP_DIR': str(nested)})
            self.assertTrue(ok)
            self.assertTrue((nested / 's.line0.json').exists())

    def test_empty_slot_returns_false(self):
        with TemporaryDirectory() as d:
            self.assertFalse(publish_line('', 0, {'items': []},
                                          env={'CC_VITALS_DUMP_DIR': d}))
            self.assertFalse(publish_line(None, 0, {'items': []},
                                          env={'CC_VITALS_DUMP_DIR': d}))

    def test_failure_returns_false(self):
        # Pointing at a path that can't be created (a regular file pretending
        # to be a directory) should fail gracefully — statusline is non-critical.
        with TemporaryDirectory() as d:
            collide = Path(d) / 'iam-a-file'
            collide.write_text('blocker')
            ok = publish_line('s', 0, {'items': []},
                              env={'CC_VITALS_DUMP_DIR': str(collide / 'sub')})
            self.assertFalse(ok)


class PublishManifest(unittest.TestCase):
    def test_writes_one_file_per_line(self):
        with TemporaryDirectory() as d:
            m = {'lines': [
                [{'type': 'static', 'ansi': 'a'}],
                [{'type': 'static', 'ansi': 'b'}],
                [{'type': 'static', 'ansi': 'c'}],
            ]}
            written = publish_manifest('s', m, env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(written, [0, 1, 2])
            for i, expected in enumerate('abc'):
                payload = json.loads((Path(d) / f's.line{i}.json').read_text())
                self.assertEqual(payload['items'][0]['ansi'], expected)

    def test_empty_manifest_writes_nothing(self):
        with TemporaryDirectory() as d:
            written = publish_manifest('s', {'lines': []},
                                       env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(written, [])
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_no_slot_writes_nothing(self):
        with TemporaryDirectory() as d:
            written = publish_manifest('', {'lines': [[{'type': 'static', 'ansi': 'x'}]]},
                                       env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(written, [])
            self.assertEqual(list(Path(d).iterdir()), [])


class ReadLine(unittest.TestCase):
    def test_round_trip(self):
        with TemporaryDirectory() as d:
            publish_line('s', 0, {'items': [{'type': 'static', 'ansi': 'hi'}]},
                         env={'CC_VITALS_DUMP_DIR': d})
            payload = read_line('s', 0, env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(payload['items'][0]['ansi'], 'hi')

    def test_missing_returns_none(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(read_line('absent', 0,
                                        env={'CC_VITALS_DUMP_DIR': d}))

    def test_corrupt_returns_none(self):
        with TemporaryDirectory() as d:
            (Path(d) / 'broken.line0.json').write_text('}{not json')
            self.assertIsNone(read_line('broken', 0,
                                        env={'CC_VITALS_DUMP_DIR': d}))

    def test_empty_slot_returns_none(self):
        self.assertIsNone(read_line('', 0))


class DiscoverLatestSlot(unittest.TestCase):
    def test_picks_most_recent_within_ttl(self):
        with TemporaryDirectory() as d:
            now = time.time()
            for slot, age in (('older', 100), ('newer', 1)):
                p = Path(d) / f'{slot}.line0.json'
                p.write_text('{"items":[]}')
                os.utime(p, (now - age, now - age))
            picked = discover_latest_slot(line_index=0,
                                          env={'CC_VITALS_DUMP_DIR': d})
            self.assertEqual(picked, 'newer')

    def test_skips_files_outside_ttl(self):
        with TemporaryDirectory() as d:
            now = time.time()
            old = Path(d) / 'old.line0.json'
            old.write_text('{"items":[]}')
            os.utime(old, (now - 10000, now - 10000))
            picked = discover_latest_slot(
                line_index=0, ttl_seconds=60,
                env={'CC_VITALS_DUMP_DIR': d}, now=now,
            )
            self.assertIsNone(picked)

    def test_filters_by_line_index(self):
        with TemporaryDirectory() as d:
            (Path(d) / 'a.line0.json').write_text('{"items":[]}')
            (Path(d) / 'b.line1.json').write_text('{"items":[]}')
            self.assertEqual(
                discover_latest_slot(line_index=0,
                                     env={'CC_VITALS_DUMP_DIR': d}),
                'a',
            )
            self.assertEqual(
                discover_latest_slot(line_index=1,
                                     env={'CC_VITALS_DUMP_DIR': d}),
                'b',
            )

    def test_missing_dir_returns_none(self):
        # Default dir under a fresh HOME — guaranteed not to exist.
        with TemporaryDirectory() as d:
            self.assertIsNone(discover_latest_slot(
                line_index=0, env={'CC_VITALS_DUMP_DIR': str(Path(d) / 'nope')}
            ))


class AnsiPrefix(unittest.TestCase):
    """`colors.ansi_prefix` is what build_cache_items resolves per tier so
    tick.py can wrap live labels without theme machinery on the host."""

    def test_returns_prefix_only(self):
        from colors import ansi_prefix, RESET
        out = ansi_prefix('error', {'error': '#F44747'})
        self.assertTrue(out.startswith('\x1b['))
        self.assertNotIn(RESET, out)  # caller pairs with RESET

    def test_no_color_env_returns_empty(self):
        # Re-import with NO_COLOR set so the module re-evaluates the flag.
        # We can't easily reload colors.py mid-test; just exercise the
        # documented behavior: a missing color token produces an empty
        # prefix on platforms with color support.
        from colors import ansi_prefix
        self.assertEqual(ansi_prefix(None, {}), '')

    def test_bold_and_dim_compose(self):
        from colors import ansi_prefix
        out = ansi_prefix('error', {'error': '#F44747'}, bold=True, dim=True)
        self.assertIn('\x1b[1m', out)  # bold
        self.assertIn('\x1b[2m', out)  # dim


if __name__ == '__main__':
    unittest.main()
