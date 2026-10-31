import base64
import json
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from tests.test_messenger_history import HistoryTests
from iam_desktop import DesktopStore
from iam_messenger_connector import events_request
from iam_messenger_reads import unread, mark, push_states
from iam_messenger_access import AccessDenied


class ReadTests(HistoryTests):
    def setUp(self):
        super().setUp()
        self.desktop=DesktopStore(self.path)

    def event_frame(self, ids, push=None):
        frame=self.frame | {'type':'messenger_events_challenge','content':base64.b64encode(
            json.dumps({'read':ids,**({'push':push} if push else {})}).encode()).decode()}
        proof=events_request(frame,self.config,self.store,self.path,now=120)
        self.assertNotIn('error',proof)
        return frame | {'type':'messenger_events_sync','nonce':proof['nonce'],
            'signature':base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode()}

    def test_fetch_does_not_read_and_read_through_handles_notices(self):
        self.desktop.history('pilot')
        with self.desktop.connect() as db:
            self.assertEqual(unread(db,'pilot'),['1:reply','3:notice'])
            mark(db,'pilot',['3:notice'])
            self.assertEqual(unread(db,'pilot'),[])
            with self.assertRaises(AccessDenied):mark(db,'other',['1:reply'])
            with self.assertRaises(AccessDenied):mark(db,'pilot',['1:reply'],111)

    def test_read_through_respects_cutoff_future_and_atomic_validation(self):
        with self.desktop.connect() as db:
            db.execute("INSERT INTO notices VALUES('future','pilot','later',200,'agent')")
            before=unread(db,'pilot')
            with self.assertRaises(AccessDenied):mark(db,'pilot',['3:notice','2:reply'])
            self.assertEqual(unread(db,'pilot'),before)
            mark(db,'pilot',['3:notice'],111)
            self.assertEqual(unread(db,'pilot'),['1:reply','future:notice'])
            mark(db,'pilot',['3:notice'])
            self.assertEqual(unread(db,'pilot'),['future:notice'])
            mark(db,'pilot',['1:reply'])
            self.assertEqual(unread(db,'pilot'),['future:notice'])

    def test_signed_reads_shared_with_desktop_replay_rejected(self):
        frame=self.event_frame(['1:reply'])
        self.assertEqual(events_request(frame,self.config,self.store,self.path,now=121)['unread'],1)
        self.assertIn('error',events_request(frame,self.config,self.store,self.path,now=122))
        with self.desktop.connect() as db:self.assertEqual(unread(db,'pilot'),['3:notice'])

    def test_push_versions_change_on_desktop_read_and_revocation_clears(self):
        push={'id':'00000000-0000-4000-8000-000000000001','token':'a'*50}
        result=events_request(self.event_frame([],push),self.config,self.store,self.path,now=121)
        self.assertEqual(result['unread'],2)
        self.assertEqual(result['subscriptions'][0]['unread'],0)
        with self.desktop.connect() as db:
            db.execute("INSERT INTO notices(id,mailbox,body,created,kind) VALUES('new','pilot','new',122,'agent')")
        first=push_states(self.desktop,self.store,self.config,122)[0]
        self.assertEqual(first['unread'],1)
        with self.desktop.connect() as db:mark(db,'pilot',['new:notice'])
        second=push_states(self.desktop,self.store,self.config,123)[0]
        self.assertGreater(second['revision'],first['revision']);self.assertEqual(second['unread'],0)
        self.assertEqual(second['revision'],push_states(self.desktop,self.store,self.config,124)[0]['revision'])
        self.assertNotIn('body',second)
        # Reaching the newest notice also acknowledges earlier permitted history.
        with self.desktop.connect() as db:self.assertEqual(len(unread(db,'pilot')),0)
        again=events_request(self.event_frame([],push),self.config,self.store,self.path,now=123)
        self.assertEqual(again['subscriptions'][0]['unread'],0)
        with self.desktop.connect() as db:
            self.assertEqual(db.execute('SELECT notify_from FROM push_subscriptions').fetchone()[0],121)
        self.store.revoke(self.frame['grant_id'])
        self.assertEqual(push_states(self.desktop,self.store,self.config,125)[0]['unread'],0)

    def test_legacy_subscription_migration_preserves_reads_and_starts_now(self):
        from iam_messenger_reads import initialize
        from unittest.mock import patch
        with self.desktop.connect() as db:
            db.execute('DROP TABLE push_subscriptions')
            db.execute("CREATE TABLE push_subscriptions(id TEXT PRIMARY KEY,grant_id TEXT,principal TEXT,mailbox TEXT,token TEXT,fingerprint TEXT,revision INTEGER)")
            db.execute("INSERT INTO push_subscriptions VALUES('old','grant','{}','pilot','token','old',7)")
            with patch('iam_messenger_reads.time.time',return_value=150):initialize(db)
            self.assertEqual(db.execute('SELECT notify_from FROM push_subscriptions').fetchone()[0],150)
            self.assertEqual(len(unread(db,'pilot')),2)
            with patch('iam_messenger_reads.time.time',return_value=200):initialize(db)
            self.assertEqual(db.execute('SELECT notify_from FROM push_subscriptions').fetchone()[0],150)

    def test_signed_wrong_conversation_cutoff_and_revoked_proof_fail(self):
        frame=self.event_frame(['2:reply'])
        self.assertIn('error',events_request(frame,self.config,self.store,self.path,now=121))
        frame=self.event_frame(['1:reply'])
        self.store.revoke(self.frame['grant_id'])
        self.assertIn('error',events_request(frame,self.config,self.store,self.path,now=121))
