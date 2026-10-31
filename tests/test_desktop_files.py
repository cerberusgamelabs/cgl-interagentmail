import base64
import tempfile
import unittest
import uuid
from pathlib import Path
from iam_desktop import DesktopStore
from iam_desktop_files import validate_files, local_publish_files
from iam_service import IAMError


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DesktopStore(self.root / 'test.sqlite3')
        self.file = self.root / 'PLAN_ONE.md'
        self.file.write_text('# Plan\n\nHello', encoding='utf-8')
        self.files = local_publish_files([str(self.file)], self.root)
        self.rid = str(uuid.uuid4())

    def test_request_snapshot_scoped_and_durable(self):
        self.store.create('One', '', self.rid, files=self.files)
        self.store.create('One', '', self.rid, files=self.files)
        self.file.unlink()
        refs = self.store.history('One')[0]['files']
        self.assertNotIn('data', refs[0])
        self.assertEqual(self.store.document('One', 'request', self.rid, 0), self.files[0])
        with self.assertRaises(IAMError): self.store.document('Two', 'request', self.rid, 0)
        with self.assertRaises(IAMError): self.store.create('One', '', self.rid)
        inputs = self.store.document_inputs(self.rid, 'One')
        self.assertEqual(Path(inputs[0]['path']).read_text(), '# Plan\n\nHello')

    def test_reply_and_notice_immutable(self):
        self.store.create('One', 'Hi', self.rid)
        self.store.change(self.rid, 'accepted')
        self.store.reply('One', self.rid, 'Plan', files=self.files)
        self.store.reply('One', self.rid, 'Plan', files=self.files)
        with self.assertRaises(IAMError): self.store.reply('One', self.rid, 'Plan')
        self.assertEqual(len(self.store.history('One')[0]['reply_files']), 1)
        result = self.store.notify('One', 'Plan', 'stable', files=self.files)
        with self.assertRaises(IAMError): self.store.notify('One', 'Plan', 'stable')
        self.assertEqual(self.store.document('One', 'notice', result['id'], 0), self.files[0])

    def test_rejections(self):
        for name in ['../x.txt', 'a.exe', 'a.html', 'a.svg', 'x.txt:evil', 'x\n.md']:
            with self.assertRaises(IAMError): validate_files([{'name': name, 'data': 'aGk='}])
        for raw in [b'\xff', b'a\0b', b'x' * 4000001]:
            with self.assertRaises(IAMError): validate_files([{'name':'x.txt', 'data':base64.b64encode(raw).decode()}])
        with self.assertRaises(IAMError): validate_files(self.files * 5)
        with self.assertRaises(IAMError): validate_files([{'name':'x.pdf', 'data':'aGk='}])
        with self.assertRaises(IAMError): self.store.document('One', 'request', self.rid, True)

    def test_failed_reply_does_not_publish_files(self):
        self.store.create('One', 'Hi', self.rid)
        with self.assertRaises(IAMError): self.store.reply('One', self.rid, 'Plan', files=self.files)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0], 0)
