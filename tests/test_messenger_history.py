import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4
from unittest.mock import patch
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from iam_messenger_pairing import Pairings
from iam_messenger_access import Principal
from iam_messenger_connector import history_request


class HistoryTests(unittest.TestCase):
    def test_delivery_markers_do_not_expose_error_details(self):
        from iam_messenger_history import history_page
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET reply=NULL,state='accepted',error='Turn finished; no reply published.' WHERE id='1'")
        page=history_page(self.path,'pilot',0,json.dumps({'after':[0,''],'limit':50}))
        event=next(e for e in page['events'] if e['event_id']=='1:request')
        self.assertEqual('no_reply',event['delivery']);self.assertNotIn('error',event)

    def test_history_documents_are_metadata_only(self):
        from iam_messenger_history import history_page
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS documents(kind TEXT,message_id TEXT,data TEXT)')
            db.execute('INSERT INTO documents VALUES(?,?,?)',('reply','1',json.dumps([{'name':'notes.txt','data':'YWJj'}])))
        db.close()
        page=history_page(self.path,'pilot',105,json.dumps({'after':[0,''],'limit':50}))
        event=next(e for e in page['events'] if e['event_id']=='1:reply')
        self.assertEqual([{'name':'notes.txt','size':3}],event['documents'])
        self.assertNotIn('YWJj',json.dumps(page))

    def text_read(self, event_id, offset=0):
        content=base64.b64encode(json.dumps({'text':{'event_id':event_id,'offset':offset}}).encode()).decode()
        return history_request(self.signed(content=content),self.config,self.store,self.path,now=121)

    def test_full_text_preserves_unicode_and_cutoff_and_excludes_private_events(self):
        import hashlib
        text=('hello'+chr(0x1f600))*20000
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO notices VALUES(?,?,?,?,?)',('large','pilot',text,114,'agent'))
        raw=text.encode();parts=[]
        for offset in range(0,len(raw),65536):
            result=self.text_read('large:notice',offset)
            value=result['text']
            self.assertEqual(value['total'],len(raw))
            self.assertEqual(value['sha256'],hashlib.sha256(raw).hexdigest())
            parts.append(base64.b64decode(value['data']))
            self.assertLess(len(json.dumps(result).encode()),100000)
        self.assertEqual(b''.join(parts).decode(),text)
        self.assertEqual(base64.b64decode(self.text_read('1:reply')['text']['data']),b'new reply')
        for event in ('1:request','2:request','4:notice','missing:reply','../secret'):
            self.assertIn('error',self.text_read(event))
        for offset in (-1,True,1.5,len(raw)+1):
            self.assertIn('error',self.text_read('large:notice',offset))

    def test_full_text_revalidation_and_empty_and_size_limit(self):
        from iam_messenger_history import text_chunk
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO notices VALUES(?,?,?,?,?)',('empty','pilot','',114,'agent'))
            db.execute('INSERT INTO notices VALUES(?,?,?,?,?)',('huge','pilot','a'*1000001,114,'agent'))
        self.assertEqual(self.text_read('empty:notice')['text']['total'],0)
        self.assertIn('error',self.text_read('huge:notice'))
        def revoked(*args):
            value=text_chunk(*args)
            self.store.revoke(self.frame['grant_id'])
            return value
        with patch('iam_messenger_history.text_chunk',side_effect=revoked):
            result=self.text_read('3:notice')
        self.assertIn('error',result)
        self.assertNotIn('text',result)

    def test_maximum_full_text_fits_existing_proof_budget(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO notices VALUES(?,?,?,?,?)',('maximum','pilot','a'*1000000,114,'agent'))
        size=0
        for offset in range(0,1000000,65536):
            result=self.text_read('maximum:notice',offset)
            self.assertNotIn('error',result)
            size+=len(base64.b64decode(result['text']['data']))
        self.assertEqual(size,1000000)

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'desktop.db'
        self.store=Pairings(Path(tmp.name)/'pair.db')
        self.key=ec.generate_private_key(ec.SECP256R1())
        public=base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        principal=Principal('home','owner','phone','messenger_device')
        challenge=self.store.request(principal,'node',now=100,owner_key=public)
        grant=self.store.approve(challenge,principal,'node',{'pilot'},now=106,expires_at=1000,
            history_from=105,expected_owner_key=public)
        self.config=dict(node_id='node',messenger=dict(enabled=True,home_server='home',owner_account_id='owner'))
        self.frame=dict(protocol='iam-webconnect',version=1,type='messenger_history_challenge',id=str(uuid4()),
            principal=asdict(principal),installation_id='node',conversation_id='pilot',grant_id=grant,
            content=base64.b64encode(b'{"after":[0,""],"limit":1}').decode())
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE requests(id TEXT,mailbox TEXT,body TEXT,created REAL,state TEXT,reply TEXT,error TEXT);
              CREATE TABLE reply_times(request_id TEXT,created REAL);
              CREATE TABLE notices(id TEXT,mailbox TEXT,body TEXT,created REAL,kind TEXT);
              INSERT INTO requests VALUES('1','pilot','old',100,'replied','new reply','private error');
              INSERT INTO reply_times VALUES('1',110);
              INSERT INTO requests VALUES('2','other','private body',111,'queued',NULL,NULL);
              INSERT INTO notices VALUES('3','pilot','hello',112,'agent');
              INSERT INTO notices VALUES('4','pilot','internal',113,'infrastructure');''')

    def signed(self, **changes):
        frame=self.frame | changes
        proof=history_request(frame,self.config,self.store,self.path,now=120)
        self.assertNotIn('error',proof)
        return frame | dict(type='messenger_history_read',nonce=proof['nonce'],
            signature=base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode())

    def test_signed_projection_cutoff_pagination_and_replay(self):
        signed=self.signed()
        result=history_request(signed,self.config,self.store,self.path,now=121)
        self.assertNotIn('error',result)
        self.assertEqual([e['body'] for e in result['events']],['new reply'])
        self.assertNotIn('private',json.dumps(result));self.assertTrue(result['more'])
        content=base64.b64encode(json.dumps(dict(after=result['after'],limit=10)).encode()).decode()
        second=history_request(self.signed(content=content),self.config,self.store,self.path,now=121)
        self.assertEqual([e['body'] for e in second['events']],['hello'])
        self.assertFalse(second['more'])
        self.assertIn('error',history_request(signed,self.config,self.store,self.path,now=122))

    def test_revoke_and_substitution_never_read(self):
        signed=self.signed()
        for changes in ({'conversation_id':'other'}, {'content':base64.b64encode(b'{}').decode()},
                        {'path':'arbitrary'}, {'principal':signed['principal'] | {'device_id':'other'}}):
            self.assertIn('error',history_request(signed | changes,self.config,self.store,self.path,now=121))
        self.store.revoke(self.frame['grant_id'])
        self.assertIn('error',history_request(signed,self.config,self.store,self.path,now=121))

    def test_disabled_and_missing_database_do_not_create_files(self):
        self.assertIn('error',history_request(self.frame,{},None,self.path,now=120))
        missing=self.path.parent/'missing.db'
        self.assertIn('error',history_request(self.signed(),self.config,self.store,missing,now=121))
        self.assertFalse(missing.exists())

    def test_revocation_during_read_prevents_publication(self):
        from iam_messenger_history import history_page
        signed=self.signed()
        def revoke_during_read(*args):
            page=history_page(*args)
            self.store.revoke(self.frame['grant_id'])
            return page
        with patch('iam_messenger_history.history_page',side_effect=revoke_during_read):
            result=history_request(signed,self.config,self.store,self.path,now=121)
        self.assertIn('error',result)
        self.assertNotIn('events',result)

    def test_large_unicode_output_is_bounded_and_explicit(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',
                ('large','pilot',chr(0x1f600)*32000,114,'queued',None,'private detail'))
        content=base64.b64encode(b'{"after":[113,""],"limit":50}').decode()
        result=history_request(self.signed(content=content),self.config,self.store,self.path,now=121)
        self.assertEqual(len(result['events'][0]['body']),16000)
        self.assertTrue(result['events'][0]['truncated'])
        self.assertLess(len(json.dumps(result,ensure_ascii=False).encode()),150000)

    def test_reverse_pages_start_newest_and_respect_ties_and_cutoff(self):
        from iam_messenger_history import history_page
        with sqlite3.connect(self.path) as db:
            for i in range(120):
                db.execute('INSERT INTO notices VALUES(?,?,?,?,?)',(f'n{i:03}','pilot','notice',200+i//3,'agent'))
        first=history_page(self.path,'pilot',105,json.dumps({'before':None,'limit':50}))
        self.assertEqual(len(first['events']),50);self.assertTrue(first['more'])
        self.assertEqual(first['events'][-1]['event_id'],'n119:notice')
        self.assertEqual(first['after'],[first['events'][0]['created'],first['events'][0]['event_id']])
        second=history_page(self.path,'pilot',105,json.dumps({'before':first['after'],'limit':50}))
        third=history_page(self.path,'pilot',105,json.dumps({'before':second['after'],'limit':50}))
        ids=[e['event_id'] for page in (third,second,first) for e in page['events']]
        self.assertEqual(len(ids),122);self.assertEqual(len(set(ids)),122)
        self.assertNotIn('1:request',ids);self.assertNotIn('4:notice',ids)
        self.assertFalse(third['more'])
        content=base64.b64encode(b'{"before":null,"limit":50}').decode()
        signed=self.signed(content=content)
        projected=history_request(signed,self.config,self.store,self.path,now=121)
        self.assertEqual(projected['events'][-1]['event_id'],'n119:notice')
        self.assertIn('error',history_request(signed,self.config,self.store,self.path,now=122))
