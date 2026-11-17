import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from iam_desktop import record_turn_failure


class DiagnosticsTests(unittest.TestCase):
    def test_only_allowlisted_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'run').mkdir()
            with patch('interagentmail.ROOT', root):
                record_turn_failure('thread', {'id': 'turn', 'status': 'failed',
                    'items': ['PRIVATE'], 'error': {'message': 'SECRET',
                    'additionalDetails': 'PRIVATE', 'codexErrorInfo': 'SECRET'}})
                record_turn_failure('thread', {'id': 'ok', 'status': 'completed'})
            data = (root / 'run' / 'turn-failures.jsonl').read_text()
            self.assertNotIn('SECRET', data)
            self.assertNotIn('PRIVATE', data)
            self.assertEqual(len(data.splitlines()), 1)
            self.assertEqual(json.loads(data)['category'], 'unclassified')
