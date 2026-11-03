import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.core import Simulator, load_trace
from backend.dataset import DEFAULT_PATH, SHA256, ensure_mooncake


class DatasetTests(unittest.TestCase):
    def test_bad_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversation_trace.jsonl'
            path.write_text('bad', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                ensure_mooncake(path)
            self.assertEqual(path.read_text(), 'bad')

    def test_bad_download_never_becomes_trace(self):
        from io import BytesIO
        with tempfile.TemporaryDirectory() as folder, patch('backend.dataset.urlopen', return_value=BytesIO(b'bad')):
            path = Path(folder) / 'trace.jsonl'
            with self.assertRaises(ValueError):
                ensure_mooncake(path)
            self.assertFalse(path.exists())

    def test_valid_download_is_cached_and_reused(self):
        import hashlib
        from io import BytesIO
        content = Path('data/demo.jsonl').read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as folder, patch('backend.dataset.SHA256', digest):
            path = Path(folder) / 'trace.jsonl'
            with patch('backend.dataset.urlopen', return_value=BytesIO(content)) as download:
                self.assertEqual(ensure_mooncake(path), path)
                self.assertEqual(ensure_mooncake(path), path)
                self.assertEqual(download.call_count, 1)

    @unittest.skipUnless(DEFAULT_PATH.exists(), 'real trace not downloaded; unit tests remain offline')
    def test_real_trace_first_requests_and_drain(self):
        rows = load_trace(ensure_mooncake())
        self.assertEqual(len(rows), 12031)
        self.assertEqual((rows[0].timestamp, rows[0].input_length, rows[0].output_length), (0, 6758, 500))
        self.assertEqual((rows[1].timestamp, rows[1].input_length, rows[1].output_length), (0, 7322, 490))
        sim = Simulator(rows)
        sim.advance(1)
        self.assertGreater(sim.cursor, 1)
        self.assertTrue(all(c['cached_tokens'] == 0 for c in sim.decisions[0]['candidates']))
        sim.advance(200000)
        state = sim.snapshot()
        self.assertTrue(state['finished'])
        self.assertEqual(state['completed'] + state['rejected'], 12031)
