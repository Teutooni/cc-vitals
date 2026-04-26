import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import tests  # noqa: F401
import context


class ContextSizeFor(unittest.TestCase):
    def test_default_is_200k(self):
        self.assertEqual(context._context_size_for(None), 200000)
        self.assertEqual(context._context_size_for(''), 200000)
        self.assertEqual(context._context_size_for('claude-sonnet-4-6'), 200_000)

    def test_1m_marker_promotes(self):
        self.assertEqual(context._context_size_for('claude-sonnet-4-6-1m'), 1_000_000)
        self.assertEqual(context._context_size_for('SONNET-1M-CONTEXT'), 1_000_000)


class GetContextUsage(unittest.TestCase):
    def _write_jsonl(self, path, lines):
        with open(path, 'w') as f:
            for obj in lines:
                f.write(json.dumps(obj) + '\n')

    def test_no_path(self):
        self.assertIsNone(context.get_context_usage(None))

    def test_missing_file(self):
        self.assertIsNone(context.get_context_usage('/no/such/file.jsonl'))

    def test_walks_back_to_last_usage(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_jsonl(p, [
                {'type': 'user'},
                {'type': 'assistant',
                 'message': {'usage': {'input_tokens': 100,
                                       'cache_read_input_tokens': 1000,
                                       'cache_creation_input_tokens': 50}}},
                {'type': 'system'},
            ])
            frac = context.get_context_usage(str(p), 'claude-sonnet-4-6')
            # 1150 / 200_000 = 0.00575
            self.assertAlmostEqual(frac, 1150 / 200_000, places=5)

    def test_clamps_to_one(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_jsonl(p, [
                {'type': 'assistant',
                 'message': {'usage': {'input_tokens': 999_999_999}}},
            ])
            frac = context.get_context_usage(str(p), 'claude-sonnet-4-6')
            self.assertEqual(frac, 1.0)

    def test_no_usage_returns_none(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            self._write_jsonl(p, [{'type': 'user', 'message': 'hi'}])
            self.assertIsNone(context.get_context_usage(str(p)))

    def test_garbage_lines_skipped(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 't.jsonl'
            with open(p, 'w') as f:
                f.write('not-json\n')
                f.write('\n')
                f.write(json.dumps({
                    'type': 'assistant',
                    'message': {'usage': {'input_tokens': 50}},
                }) + '\n')
            frac = context.get_context_usage(str(p), 'claude-sonnet-4-6')
            self.assertAlmostEqual(frac, 50 / 200_000, places=6)


if __name__ == '__main__':
    unittest.main()
