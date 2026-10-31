import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from iam_messenger_connector import conversation_name


class NameTests(unittest.TestCase):
    def test_only_public_name_and_no_mailbox_creation(self):
        with tempfile.TemporaryDirectory() as tmp, patch('interagentmail.MAILBOXES', Path(tmp)):
            folder = Path(tmp) / 'project'
            folder.mkdir()
            profile = folder / 'profile.json'
            profile.write_text(json.dumps({'display_name': 'Rowan', 'private': 'not exported'}))
            self.assertEqual(conversation_name('project'), 'Rowan')
            self.assertEqual(conversation_name('missing'), 'missing')
            self.assertFalse((Path(tmp) / 'missing').exists())
            self.assertEqual(conversation_name('../project'), '../project')
            profile.write_text(json.dumps({'display_name': ['bad']}))
            self.assertEqual(conversation_name('project'), 'project')
