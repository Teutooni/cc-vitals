import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401
import cache


class ScanChunk(unittest.TestCase):
    def _line(self, **kwargs):
        return json.dumps(kwargs)

    def test_yields_assistant_usage(self):
        text = '\n'.join([
            self._line(type='user', message={'content': 'hi'}),
            self._line(type='assistant',
                       message={'id': 'msg_1', 'usage': {'input_tokens': 5}}),
        ])
        seen = set()
        out = list(cache._scan_chunk(text, seen))
        self.assertEqual(len(out), 1)
        msg_id, usage = out[0]
        self.assertEqual(msg_id, 'msg_1')
        self.assertEqual(usage, {'input_tokens': 5})
        self.assertIn('msg_1', seen)

    def test_dedups_repeated_msg_ids(self):
        line = self._line(type='assistant',
                          message={'id': 'msg_x',
                                   'usage': {'input_tokens': 1}})
        seen = set()
        once = list(cache._scan_chunk(line + '\n' + line, seen))
        self.assertEqual(len(once), 1)
        # Second pass against the same `seen` should yield nothing.
        again = list(cache._scan_chunk(line, seen))
        self.assertEqual(again, [])

    def test_skips_non_object_lines(self):
        text = '\n'.join([
            'plain text',
            '[1,2,3]',
            self._line(type='assistant',
                       message={'id': 'm1', 'usage': {'input_tokens': 1}}),
        ])
        seen = set()
        out = list(cache._scan_chunk(text, seen))
        self.assertEqual(len(out), 1)

    def test_skips_assistant_without_usage(self):
        text = self._line(type='assistant', message={'id': 'm1'})
        seen = set()
        self.assertEqual(list(cache._scan_chunk(text, seen)), [])
        self.assertNotIn('m1', seen)

    def test_skips_assistant_without_id(self):
        text = self._line(type='assistant',
                          message={'usage': {'input_tokens': 1}})
        seen = set()
        self.assertEqual(list(cache._scan_chunk(text, seen)), [])

    def test_skips_invalid_json(self):
        seen = set()
        self.assertEqual(list(cache._scan_chunk('{not-json', seen)), [])


class Accumulate(unittest.TestCase):
    def test_basic_increment(self):
        totals = dict(cache._EMPTY_TOTALS)
        tier = {'latest': None}
        cache._accumulate(totals, {
            'cache_read_input_tokens': 100,
            'input_tokens': 5,
            'cache_creation_input_tokens': 25,
        }, tier)
        self.assertEqual(totals, {
            'cache_read': 100, 'input_tokens': 5,
            'cache_creation': 25, 'turns': 1,
        })

    def test_handles_missing_keys(self):
        totals = dict(cache._EMPTY_TOTALS)
        cache._accumulate(totals, {}, {'latest': None})
        self.assertEqual(totals['turns'], 1)
        self.assertEqual(totals['cache_read'], 0)

    def test_handles_none_values(self):
        totals = dict(cache._EMPTY_TOTALS)
        cache._accumulate(totals, {
            'cache_read_input_tokens': None,
            'input_tokens': None,
        }, {'latest': None})
        self.assertEqual(totals['cache_read'], 0)
        self.assertEqual(totals['input_tokens'], 0)

    def test_tier_detection_1h_dominates_5m(self):
        tier = {'latest': None}
        cache._accumulate({'cache_read': 0, 'input_tokens': 0,
                           'cache_creation': 0, 'turns': 0}, {
            'cache_creation': {
                'ephemeral_1h_input_tokens': 100,
                'ephemeral_5m_input_tokens': 50,
            }
        }, tier)
        self.assertEqual(tier['latest'], '1h')

    def test_tier_detection_5m(self):
        tier = {'latest': None}
        cache._accumulate({'cache_read': 0, 'input_tokens': 0,
                           'cache_creation': 0, 'turns': 0}, {
            'cache_creation': {
                'ephemeral_1h_input_tokens': 0,
                'ephemeral_5m_input_tokens': 50,
            }
        }, tier)
        self.assertEqual(tier['latest'], '5m')

    def test_tier_unchanged_when_no_breakdown(self):
        tier = {'latest': '5m'}
        cache._accumulate(dict(cache._EMPTY_TOTALS), {'input_tokens': 1}, tier)
        self.assertEqual(tier['latest'], '5m')


class TtlHelpers(unittest.TestCase):
    def test_age_none_for_missing(self):
        self.assertIsNone(cache.get_cache_age_seconds(None))
        self.assertIsNone(cache.get_cache_age_seconds('/nope/missing.jsonl'))

    def test_age_recent_file_close_to_zero(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'x.jsonl'
            p.write_text('hi')
            age = cache.get_cache_age_seconds(str(p))
            self.assertIsNotNone(age)
            self.assertLess(age, 5.0)

    def test_ttl_expired_returns_negative(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'x.jsonl'
            p.write_text('hi')
            old = time.time() - 5000
            import os
            os.utime(p, (old, old))
            remaining = cache.get_cache_ttl_remaining(str(p), ttl_seconds=3600)
            self.assertLess(remaining, 0)


class GetSessionCacheState(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._patches = [
            mock.patch.object(cache, 'STATE_FILE',
                              Path(self._tmp.name) / 'cache-state.json'),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _write_transcript(self, path, msgs):
        with open(path, 'w') as f:
            for m in msgs:
                f.write(json.dumps(m) + '\n')

    def test_empty_transcript_returns_zero_totals(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            p.write_text('')
            out = cache.get_session_cache_state(str(p), 'sess1')
            self.assertEqual(out['totals']['turns'], 0)

    def test_aggregates_across_turns(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_transcript(p, [
                {'type': 'assistant',
                 'message': {'id': 'm1',
                             'usage': {'input_tokens': 10,
                                       'cache_read_input_tokens': 100,
                                       'cache_creation_input_tokens': 5}}},
                {'type': 'assistant',
                 'message': {'id': 'm2',
                             'usage': {'input_tokens': 2,
                                       'cache_read_input_tokens': 200}}},
            ])
            out = cache.get_session_cache_state(str(p), 'sess1')
            self.assertEqual(out['totals']['turns'], 2)
            self.assertEqual(out['totals']['cache_read'], 300)
            self.assertEqual(out['totals']['input_tokens'], 12)
            self.assertEqual(out['totals']['cache_creation'], 5)

    def test_incremental_only_reads_new_tail(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_transcript(p, [
                {'type': 'assistant',
                 'message': {'id': 'm1',
                             'usage': {'input_tokens': 1}}},
            ])
            cache.get_session_cache_state(str(p), 'sess1')
            # Append another turn.
            with open(p, 'a') as f:
                f.write(json.dumps({
                    'type': 'assistant',
                    'message': {'id': 'm2',
                                'usage': {'input_tokens': 7}},
                }) + '\n')
            out = cache.get_session_cache_state(str(p), 'sess1')
            self.assertEqual(out['totals']['turns'], 2)
            self.assertEqual(out['totals']['input_tokens'], 8)

    def test_truncated_transcript_recomputes(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_transcript(p, [
                {'type': 'assistant',
                 'message': {'id': 'm1',
                             'usage': {'input_tokens': 100}}},
            ])
            cache.get_session_cache_state(str(p), 'sess1')
            # Replace with a smaller file.
            self._write_transcript(p, [
                {'type': 'assistant',
                 'message': {'id': 'm9',
                             'usage': {'input_tokens': 3}}},
            ])
            out = cache.get_session_cache_state(str(p), 'sess1')
            self.assertEqual(out['totals']['input_tokens'], 3)
            self.assertEqual(out['totals']['turns'], 1)

    def test_tier_seconds_set_from_breakdown(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_transcript(p, [
                {'type': 'assistant',
                 'message': {'id': 'm1',
                             'usage': {'input_tokens': 1,
                                       'cache_creation': {
                                           'ephemeral_1h_input_tokens': 10,
                                           'ephemeral_5m_input_tokens': 0,
                                       }}}},
            ])
            out = cache.get_session_cache_state(str(p), 'sess1')
            self.assertEqual(out['tier_seconds'], 3600)


if __name__ == '__main__':
    unittest.main()
