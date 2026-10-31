import base64
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from iam_messenger_access import Principal
from iam_messenger_pairing import Pairings
from iam_messenger_connector import pairing_request


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Pairings(Path(temp.name) / 'pairing.db')
        key = ec.generate_private_key(ec.SECP256R1())
        public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        self.config = dict(node_id='node', messenger=dict(enabled=True, home_server='home',owner_account_id='owner'))
        self.frame = dict(protocol='iam-webconnect', version=1,type='messenger_pairing_request',
            id=str(uuid4()), principal=asdict(Principal('home','owner','phone','messenger_device')),
            installation_id='node', owner_key=public)

    def test_pairing_only_does_not_grant_authority(self):
        result = pairing_request(self.frame,self.config,self.store,now=100)
        self.assertNotIn('error',result)
        pending = self.store.inspect(result['challenge'],now=101)
        self.assertEqual(pending['owner_key'],self.frame['owner_key'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pairing_grants').fetchone()[0],0)

    def test_fail_closed(self):
        for changes in ({'installation_id':'other'}, {'owner_key':'bad'}, {'type':'rpc'},
                        {'principal':self.frame['principal'] | {'account_id':'other'}},
                        {'principal':self.frame['principal'] | {'home_server':'other'}},
                        {'extra':'ignored?'}):
            with self.subTest(changes=changes):
                self.assertEqual(pairing_request(self.frame | changes,self.config,self.store,now=100)['error'],'pairing_unavailable')
        self.assertIn('error',pairing_request(self.frame,{},None,now=100))

    def test_status_exact_device_approval_revoke_and_expiry(self):
        result = pairing_request(self.frame,self.config,self.store,now=100)
        frame = {k:v for k,v in self.frame.items() if k != 'owner_key'}
        frame.update(type='messenger_pairing_status',challenge=result['challenge'])
        self.assertEqual(pairing_request(frame,self.config,self.store,now=101)['state'],
                         'awaiting_local_confirmation')
        person = Principal(**self.frame['principal'])
        grant = self.store.approve(result['challenge'],person,'node',{'ophelia'},
            now=102,expires_at=1000,history_from=0,expected_owner_key=self.frame['owner_key'])
        # A new request must not remove a consumed challenge before retrieval.
        self.store.request(person,'node',now=103,owner_key=self.frame['owner_key'])
        fresh = Pairings(self.store.path)
        approved = pairing_request(frame,self.config,fresh,now=104)
        self.assertEqual(approved['grant_id'],grant)
        self.assertNotIn('owner_key',approved)
        for field in ('account_id','device_id','home_server'):
            other=frame | {'principal':frame['principal'] | {field:'other'}}
            self.assertIn('error',pairing_request(other,self.config,fresh,now=104))
        self.assertIn('error',pairing_request(frame,self.config,fresh,now=400))
        fresh.revoke(grant)
        self.assertIn('error',pairing_request(frame,self.config,fresh,now=105))
