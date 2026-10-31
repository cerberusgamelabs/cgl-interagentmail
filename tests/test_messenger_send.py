import base64
import json
import tempfile
import unittest
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from iam_desktop import DesktopStore
from iam_messenger_access import Principal, AccessDenied
from iam_messenger_pairing import Pairings
from iam_messenger_connector import send_request, history_request
from iam_messenger_send import authorize_dispatch
from iam_desktop import DesktopBackend


class SendTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'desktop.db'
        self.desktop=DesktopStore(self.path)
        self.store=Pairings(Path(tmp.name)/'pair.db')
        self.key=ec.generate_private_key(ec.SECP256R1())
        public=base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        self.principal=Principal('home','owner','phone','messenger_device')
        challenge=self.store.request(self.principal,'node',now=100,owner_key=public)
        self.grant=self.store.approve(challenge,self.principal,'node',{'pilot'},now=106,expires_at=1000,
            history_from=0,expected_owner_key=public)
        self.config=dict(node_id='node',messenger=dict(enabled=True,send_enabled=True,home_server='home',owner_account_id='owner'))
        self.request=str(uuid4())
        self.frame=dict(protocol='iam-webconnect',version=1,type='messenger_send_challenge',id=str(uuid4()),
            principal=asdict(self.principal),installation_id='node',conversation_id='pilot',grant_id=self.grant,
            content=base64.b64encode(json.dumps(dict(request_id=self.request,body='hello')).encode()).decode())

    def call(self, frame, now=120):
        return send_request(frame,self.config,self.store,self.path,now=now,thread_lookup=lambda _: 'pinned')

    def signed(self, frame=None):
        frame=frame or self.frame
        proof=self.call(frame)
        self.assertNotIn('error',proof)
        return frame | dict(type='messenger_send_submit',nonce=proof['nonce'],signature=base64.b64encode(
            self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode())

    def test_durable_deduplication_and_conflicts(self):
        signed=self.signed()
        self.assertEqual('queued',self.call(signed)['state'])
        self.assertIn('error',self.call(signed)) # replayed proof
        self.assertEqual('queued',self.call(self.signed())['state']) # fresh proof, same identity
        with self.desktop.connect() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM requests').fetchone()[0])
            self.assertEqual('mobile_queued',db.execute('SELECT state FROM requests').fetchone()[0])
        changed=self.frame | {'content':base64.b64encode(json.dumps(dict(request_id=self.request,body='changed')).encode()).decode()}
        self.assertIn('error',self.call(self.signed(changed)))

    def lookup(self, body='hello'):
        query=dict(lookup=dict(request_id=self.request,body_sha256=hashlib.sha256(body.encode()).hexdigest()))
        frame=self.frame | dict(type='messenger_history_challenge',content=base64.b64encode(json.dumps(query).encode()).decode())
        proof=history_request(frame,self.config,self.store,self.path,now=120)
        if 'error' in proof:return proof
        frame |= dict(type='messenger_history_read',nonce=proof['nonce'],signature=base64.b64encode(
            self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode())
        return history_request(frame,self.config,self.store,self.path,now=120)

    def test_exact_lookup_missing_found_and_scope(self):
        self.assertEqual(dict(request_id=self.request,found=False),self.lookup()['lookup'])
        self.call(self.signed())
        self.assertEqual(dict(request_id=self.request,found=True,state='queued'),self.lookup()['lookup'])
        self.assertIn('error',self.lookup(body='different'))
        for state in ('held','blocked','failed','accepted','replied'):
            with self.desktop.connect() as db:db.execute('UPDATE requests SET state=? WHERE id=?',(state,self.request))
            self.assertEqual(state,self.lookup()['lookup']['state'])
        self.store.revoke(self.grant)
        self.assertIn('error',self.lookup())

    def test_lookup_conflicting_local_id_is_not_absence(self):
        self.desktop.create('pilot','hello',self.request)
        self.assertIn('error',self.lookup())

    def test_missing_lookup_then_racing_submissions_enqueue_once(self):
        self.assertFalse(self.lookup()['lookup']['found'])
        frames=[self.signed(),self.signed()]
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(self.call,frames))
        self.assertEqual(['queued','queued'],[result.get('state') for result in results])
        with self.desktop.connect() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM requests WHERE id=?',(self.request,)).fetchone()[0])

    def test_revoke_after_enqueue_expiry_thread_and_flags(self):
        self.call(self.signed())
        def authorize(config=None,thread='pinned',now=121):
            return authorize_dispatch(self.desktop,self.store,self.request,config or self.config,thread,now=now)
        self.assertEqual('phone',authorize()['principal']['device_id'])
        for kwargs in ({'thread':'replacement'},{'now':181},
                       {'config':{'node_id':'node','messenger':{'enabled':True}}}):
            with self.assertRaises(AccessDenied):authorize(**kwargs)
        self.store.revoke(self.grant)
        with self.assertRaises(AccessDenied):authorize()

    def test_disabled_bad_scope_changed_body_and_local_id_collision(self):
        signed=self.signed()
        for changes in ({'conversation_id':'elsewhere'},{'content':base64.b64encode(b'{}').decode()},
                        {'principal':asdict(self.principal)|{'device_id':'other'}},{'path':'anything'}):
            self.assertIn('error',self.call(signed|changes))
        self.desktop.create('pilot','hello',self.request)
        self.assertIn('error',self.call(signed))
        self.config['messenger']['send_enabled']=False
        self.assertIn('error',self.call(self.frame))

    def test_dispatch_uses_same_thread_and_rechecks_after_await(self):
        for revoke in (False,True):
            self.request=str(uuid4())
            self.frame['content']=base64.b64encode(json.dumps(dict(request_id=self.request,body='hello')).encode()).decode()
            self.assertNotIn('error',self.call(self.signed()))
            with patch('iam_desktop.DesktopStore',return_value=self.desktop):
                backend=DesktopBackend()
            async def rpc(method,params):
                if method=='thread/read':
                    if revoke:self.store.revoke(self.grant)
                    return {'thread':{'status':{'type':'idle'}}}
                return {'turn':{'id':'accepted-turn'}}
            client=MagicMock(request=AsyncMock(side_effect=rpc))
            backend.clients['pilot']=(client,'pinned')
            def authorize(request,thread):
                return authorize_dispatch(self.desktop,self.store,request,self.config,thread,now=121)
            with self.desktop.connect() as db:
                row=dict(db.execute('SELECT * FROM requests WHERE id=?',(self.request,)).fetchone())
            with patch('iam_desktop.session_owner',return_value='pilot'), \
                 patch.object(backend,'open',new=AsyncMock(return_value={'state':'attached'})), \
                 patch.object(backend,'authorize_mobile',side_effect=authorize), \
                 patch.object(backend,'projects',return_value={'pilot':{'project_root':str(self.path.parent)}}):
                asyncio.run(backend.deliver_once(row))
                asyncio.run(backend.deliver_once(row))
            starts=[c for c in client.request.call_args_list if c.args[0]=='turn/start']
            self.assertEqual(0 if revoke else 1,len(starts))
            if starts:
                self.assertEqual('pinned',starts[0].args[1]['threadId'])
                self.assertIn('Origin: mobile',starts[0].args[1]['input'][0]['text'])

    def test_lost_runtime_receipt_is_held_without_second_turn(self):
        self.call(self.signed())
        with patch('iam_desktop.DesktopStore',return_value=self.desktop):
            backend=DesktopBackend()
        client=MagicMock(request=AsyncMock(side_effect=[{'thread':{'status':{'type':'idle'}}},TimeoutError()]))
        backend.clients['pilot']=(client,'pinned')
        with self.desktop.connect() as db:
            row=dict(db.execute('SELECT * FROM requests WHERE id=?',(self.request,)).fetchone())
        with patch('iam_desktop.session_owner',return_value='pilot'), \
             patch.object(backend,'open',new=AsyncMock(return_value={'state':'attached'})), \
             patch.object(backend,'authorize_mobile',return_value={'verified':True}), \
             patch.object(backend,'projects',return_value={'pilot':{'project_root':str(self.path.parent)}}):
            asyncio.run(backend.deliver_once(row));asyncio.run(backend.deliver_once(row))
        self.assertEqual(1,len([c for c in client.request.call_args_list if c.args[0]=='turn/start']))
        with self.desktop.connect() as db:
            self.assertEqual('held',db.execute('SELECT state FROM requests WHERE id=?',(self.request,)).fetchone()[0])
