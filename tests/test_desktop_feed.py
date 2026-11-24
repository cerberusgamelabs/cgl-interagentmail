import sqlite3
import tempfile
import unittest
from pathlib import Path
from iam_desktop import DesktopStore
from iam_messenger_reads import unread

class FeedTests(unittest.TestCase):
    def test_external_writer_updates_revision_and_read_receipts_remain_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            store=DesktopStore(Path(folder)/'desktop.sqlite3')
            with sqlite3.connect(store.path) as other:
                other.execute("INSERT INTO requests(id,mailbox,body,created,state,reply) VALUES('one','A','question',1,'replied','answer')")
            other.close()
            with store.connect() as db:
                self.assertEqual(['one:reply'],unread(db,'A'))
                self.assertEqual([],unread(db,'B'))
                revision=db.execute('SELECT revision FROM desktop_revisions WHERE mailbox=?',('A',)).fetchone()[0]
            store.history('A',50)
            with sqlite3.connect(store.path) as other:
                other.execute("UPDATE requests SET reply='updated' WHERE id='one'")
            other.close()
            with store.connect() as db:
                self.assertGreater(db.execute('SELECT revision FROM desktop_revisions WHERE mailbox=?',('A',)).fetchone()[0],revision)
                self.assertEqual(['one:reply'],unread(db,'A'))
                db.execute("INSERT INTO read_events VALUES('one:reply')")
                self.assertEqual([],unread(db,'A'))
                plan=db.execute('EXPLAIN QUERY PLAN SELECT id FROM requests WHERE mailbox=?',('A',)).fetchall()
                self.assertTrue(any('requests_mailbox_created' in r[3] for r in plan))
