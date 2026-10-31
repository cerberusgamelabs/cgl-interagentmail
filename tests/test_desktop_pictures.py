import base64
import tempfile
import unittest
import uuid
from pathlib import Path
from iam_desktop import DesktopStore, local_publish_images
from iam_service import IAMError


class PublishedPicturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DesktopStore(self.root / 'test.sqlite3')
        self.file = self.root / 'test.png'
        self.file.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='))
        self.images = local_publish_images(['test.png'], self.root)

    def test_reply_images_immutable_and_scoped(self):
        rid = str(uuid.uuid4())
        self.store.create('One', 'Show me', rid)
        self.store.change(rid, 'accepted')
        self.store.reply('One', rid, 'Here', self.images)
        self.store.reply('One', rid, 'Here', self.images)
        with self.assertRaises(IAMError): self.store.reply('One', rid, 'Here', [])
        with self.assertRaises(IAMError): self.store.published_image('Two', 'reply', rid, 0)
        with self.assertRaises(IAMError): self.store.published_image('One', 'reply', rid, -1)
        with self.assertRaises(IAMError): self.store.published_image('One', 'reply', rid, True)
        ref = self.store.history('One')[0]['reply_images'][0]
        self.assertNotIn('data', ref)
        self.assertEqual(self.store.published_image('One', 'reply', rid, 0), self.images[0])

    def test_unsolicited_images_are_explicit_and_survive_source_removal(self):
        result = self.store.notify('One', 'A picture for you', 'pic-1', images=self.images)
        self.file.unlink()
        self.assertEqual(self.store.published_image('One', 'notice', result['id'], 0), self.images[0])
        with self.assertRaises(IAMError): self.store.notify('One', 'A picture for you', 'pic-1')
        row = self.store.history('One')[0]
        self.assertEqual(row['notice_kind'], 'agent')
        self.assertIsNone(row['body'])
        self.assertEqual(row['reply'], 'A picture for you')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    def test_limits_and_non_raster_rejected(self):
        with self.assertRaises(IAMError): local_publish_images(['test.png'] * 5, self.root)
        with self.assertRaises(IAMError): local_publish_images(['private.svg'], self.root)
        self.file.write_bytes(b'not an image')
        with self.assertRaises(IAMError): local_publish_images(['test.png'], self.root)
        self.file.write_bytes(b'X' * 4000001)
        with self.assertRaises(IAMError): local_publish_images(['test.png'], self.root)
