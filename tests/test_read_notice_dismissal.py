import tempfile
import unittest
from pathlib import Path

from iam_desktop import DesktopStore
from iam_messenger_access import AccessDenied
from iam_messenger_reads import mark


class NoticeDismissalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = DesktopStore(Path(temp.name) / 'desktop.sqlite3')
        with self.store.connect() as db:
            db.executemany('INSERT INTO notices VALUES(?,?,?,?,?,0)', [
                ('old', 'one', 'agent', 'old', 1),
                ('visible', 'one', 'agent', 'visible', 2),
                ('future', 'one', 'agent', 'future', 3),
                ('other', 'two', 'agent', 'other', 1),
                ('infra', 'one', 'infrastructure', 'alert', 1),
            ])

    def dismissed(self, db):
        return {r[0] for r in db.execute('SELECT id FROM notices WHERE dismissed=1')}

    def test_read_through_dismisses_only_acknowledged_agent_notices(self):
        with self.store.connect() as db:
            self.store.history('one')
            self.assertEqual(self.dismissed(db), set())
            mark(db, 'one', ['visible:notice'])
            self.assertEqual(self.dismissed(db), {'old', 'visible'})
            mark(db, 'one', ['visible:notice'])
            self.assertEqual(self.dismissed(db), {'old', 'visible'})
            self.assertEqual(db.execute('SELECT count(*) FROM notices').fetchone()[0], 5)

    def test_cutoff_and_invalid_batch_do_not_dismiss_other_events(self):
        with self.store.connect() as db:
            with self.assertRaises(AccessDenied):
                mark(db, 'one', ['visible:notice', 'other:notice'])
            self.assertEqual(self.dismissed(db), set())
            mark(db, 'one', ['visible:notice'], history_from=2)
            self.assertEqual(self.dismissed(db), {'visible'})

    def test_reply_anchor_also_dismisses_preceding_notices(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO requests(id,mailbox,body,created,state,reply) VALUES('reply','one','question',2.5,'accepted','answer')")
            mark(db, 'one', ['reply:reply'])
            self.assertEqual(self.dismissed(db), {'old', 'visible'})
