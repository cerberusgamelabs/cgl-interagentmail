import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import iam_desktop as desktop
from iam_orchestrator import parser
from iam_service import IAMError


class DesktopCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = self.root / 'reply.txt'
        self.image = self.root / 'proof.png'
        self.image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.doc = self.root / 'notes.md'
        self.doc.write_text('notes', encoding='utf-8')
        self.identity = MagicMock(address='Test', project_root=self.root)

    def args(self, *extra):
        return parser().parse_args(['desktop', 'reply', '--mailbox', 'Test', '--request-id', 'test', *extra])

    def publish(self, args):
        with patch('iam_service.mailbox_service', return_value=self.identity), \
             patch.object(desktop, 'agent_reply', return_value={'published': True}) as publish, \
             contextlib.redirect_stdout(io.StringIO()):
            desktop.command(args)
            return publish.call_args

    def test_body_file_preserves_multiline_unicode_and_both_attachment_categories(self):
        body = 'Hello, Sabine 😊\r\n\r\nEvidence follows.\n'
        self.body.write_bytes(b'\xef\xbb\xbf' + body.encode('utf-8'))
        result = self.publish(self.args('--body-file', str(self.body), '--image', str(self.image), '--file', str(self.doc)))
        self.assertEqual(result.args[2], body)
        self.assertEqual(result.kwargs['images'][0]['name'], 'proof.png')
        self.assertEqual(result.kwargs['files'][0]['name'], 'notes.md')

    def test_single_line_body_remains_supported(self):
        self.assertEqual(self.publish(self.args('--body', 'Hello')).args[2], 'Hello')

    def test_mutually_exclusive_body_sources(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args('--body', 'hello', '--body-file', str(self.body))

    def test_missing_invalid_and_oversize_body_files_never_publish(self):
        for data in (None, b'\xff', b' ', b'a' * 64001, b'a' * 256004):
            if data is not None:
                self.body.write_bytes(data)
            with patch.object(desktop, 'agent_reply') as publish, self.assertRaises(IAMError):
                desktop.command(self.args('--body-file', str(self.body)))
            publish.assert_not_called()

    def test_five_images_rejected_before_publication(self):
        args = self.args('--body', 'Evidence', *sum([['--image', str(self.image)]] * 5, []))
        with patch('iam_service.mailbox_service', return_value=self.identity), \
             patch.object(desktop, 'agent_reply') as publish, self.assertRaisesRegex(IAMError, 'four images'):
            desktop.command(args)
        publish.assert_not_called()

    def test_image_in_document_flag_rejected_not_silently_dropped(self):
        with patch('iam_service.mailbox_service', return_value=self.identity), \
             patch.object(desktop, 'agent_reply') as publish, self.assertRaises(IAMError):
            desktop.command(self.args('--body', 'Evidence', '--file', str(self.image)))
        publish.assert_not_called()
