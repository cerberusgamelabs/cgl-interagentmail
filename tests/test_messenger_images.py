import base64
import hashlib
import json
import unittest
from uuid import uuid4
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from tests import test_messenger_send as send_tests
from iam_messenger_connector import attachment_request, history_request
from iam_messenger_images import scope_key, uploaded_image, validate_query, upload_chunk
from iam_messenger_access import AccessDenied


class ImageTests(unittest.TestCase):
    setUp = send_tests.SendTests.setUp
    call = send_tests.SendTests.call
    signed = send_tests.SendTests.signed
    lookup = send_tests.SendTests.lookup

    def transfer(self, query, conversation='pilot', execute=True):
        frame = self.frame | dict(type='messenger_attachment_challenge',conversation_id=conversation,
            content=base64.b64encode(json.dumps(query).encode()).decode())
        result = attachment_request(frame,self.config,self.store,self.path,now=120)
        if 'error' in result or not execute:return result
        frame |= dict(type='messenger_attachment_transfer',nonce=result['nonce'],signature=base64.b64encode(
            self.key.sign(base64.b64decode(result['payload']),ec.ECDSA(hashes.SHA256()))).decode())
        return attachment_request(frame,self.config,self.store,self.path,now=120)

    def upload(self):
        raw=b'\xff\xd8\xff'+b'x'*40000
        ident=str(uuid4());digest=hashlib.sha256(raw).hexdigest()
        for part in range(2):
            query=dict(action='upload',upload=ident,part=part,total=len(raw),sha256=digest,
                       data=base64.b64encode(raw[part*32768:(part+1)*32768]).decode())
            self.assertTrue(self.transfer(query)['attachment']['stored'])
        return ident,digest,raw,query

    def test_upload_not_a_message_and_idempotent_chunks(self):
        ident,digest,raw,query=self.upload()
        self.assertTrue(self.transfer(query)['attachment']['stored'])
        with self.desktop.connect() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM requests').fetchone()[0])
            image=uploaded_image(db,scope_key(self.principal,'node','pilot',self.grant),ident,digest,121)
            self.assertEqual(raw,base64.b64decode(image['data'].split(',')[1]))
        changed=query | dict(data=base64.b64encode(b'y'*(len(raw)-32768)).decode())
        self.assertIn('error',self.transfer(changed))

    def test_document_download_is_scoped_and_original(self):
        raw=('Original Unicode document: ♡\n'*4000).encode()
        self.desktop.create('pilot','document',self.request,files=[dict(name='notes.txt',data=base64.b64encode(raw).decode())])
        query=dict(action='read_file',event_id=self.request+':request',index=0,offset=0)
        chunks=[]
        for offset in range(0,len(raw),65536):
            value=self.transfer(query | dict(offset=offset))['attachment']
            self.assertEqual('notes.txt',value['name'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),value['sha256'])
            chunks.append(base64.b64decode(value['data']))
        self.assertEqual(raw,b''.join(chunks))
        self.assertIn('error',self.transfer(query,conversation='other'))
        with self.desktop.connect() as db:db.execute('UPDATE requests SET created=50')
        with self.store.connection() as db:db.execute('UPDATE pairing_grants SET history_from=100 WHERE id=?',(self.grant,))
        self.assertIn('error',self.transfer(query))

    def test_empty_document_and_revocation(self):
        self.desktop.create('pilot','empty',self.request,files=[dict(name='empty.txt',data='')])
        query=dict(action='read_file',event_id=self.request+':request',index=0,offset=0)
        value=self.transfer(query)['attachment']
        self.assertEqual(0,value['total']);self.assertEqual('',value['data'])
        self.store.revoke(self.grant)
        self.assertIn('error',self.transfer(query))

    def test_manifest_enqueues_once_and_conflicting_image_is_rejected(self):
        ident,digest,raw,_=self.upload()
        manifest=dict(request_id=self.request,body='hello',image_upload=ident,image_sha256=digest)
        frame=self.frame | dict(content=base64.b64encode(json.dumps(manifest).encode()).decode())
        self.assertEqual('queued',self.call(self.signed(frame))['state'])
        self.assertEqual('queued',self.call(self.signed(frame))['state'])
        with self.desktop.connect() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM images').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT count(*) FROM mobile_image_chunks').fetchone()[0])
        self.assertIn('error',self.call(self.signed())) # text-only cannot masquerade as same send
        self.assertIn('error',self.lookup()) # recovery must include image digest
        image=self.transfer(dict(action='read',event_id=self.request+':request',index=0,offset=0))['attachment']
        self.assertEqual(digest,image['sha256']);self.assertEqual(raw,base64.b64decode(image['data']))

    def test_download_requires_conversation_and_history_grant(self):
        image={'name':'test.png','data':'data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\nexample').decode()}
        self.desktop.create('other','private',self.request,images=[image])
        query=dict(action='read',event_id=self.request+':request',index=0,offset=0)
        self.assertIn('error',self.transfer(query))
        self.assertIn('error',self.transfer(query,conversation='other'))
        with self.desktop.connect() as db:db.execute('UPDATE requests SET mailbox=?,created=?',('pilot',50))
        with self.store.connection() as db:db.execute('UPDATE pairing_grants SET history_from=100 WHERE id=?',(self.grant,))
        self.assertIn('error',self.transfer(query))
        with self.desktop.connect() as db:db.execute('UPDATE requests SET created=110')
        self.assertIn('attachment',self.transfer(query))
        self.store.revoke(self.grant);self.assertIn('error',self.transfer(query))

    def test_upload_scope_and_incomplete_manifest_fail_closed(self):
        ident,digest,_,query=self.upload()
        with self.desktop.connect() as db:
            with self.assertRaises(AccessDenied):uploaded_image(db,'different',ident,digest,121)
            with self.assertRaises(AccessDenied):uploaded_image(db,scope_key(self.principal,'node','pilot',self.grant),ident,digest,4000)
        self.config['messenger']['send_enabled']=False
        self.assertIn('error',self.transfer(query))
        self.config['messenger']['send_enabled']=True
        manifest=dict(request_id=self.request,body='hello',image_upload=str(uuid4()),image_sha256=digest)
        frame=self.frame | dict(content=base64.b64encode(json.dumps(manifest).encode()).decode())
        self.assertIn('error',self.call(self.signed(frame)))
        with self.desktop.connect() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM requests').fetchone()[0])

    def test_path_indices_offsets_and_oversized_uploads_denied(self):
        for query in [dict(action='read',path='C:/secret'),dict(action='read',event_id='x',index=4,offset=0),
                      dict(action='read',event_id='x',index=0,offset=1),
                      dict(action='upload',upload=str(uuid4()),part=0,total=524289,sha256='0'*64,data='AA==')]:
            with self.assertRaises((AccessDenied,ValueError)):validate_query(query)

    def test_global_chunk_limit_also_applies_to_existing_uploads(self):
        query=dict(action='upload',upload=str(uuid4()),part=0,total=65536,sha256='a'*64,
                   data=base64.b64encode(b'a'*32768).decode())
        upload_chunk(self.desktop,'scope',query,120)
        with self.desktop.connect() as db:
            db.executemany('INSERT INTO mobile_image_chunks VALUES(?,?,?,?,?,?,?)',
                [('filler',str(i),0,1,'a'*64,b'a',1000) for i in range(2047)])
        self.assertTrue(upload_chunk(self.desktop,'scope',query,120)['stored'])
        with self.assertRaises(AccessDenied):upload_chunk(self.desktop,'scope',query | {'part':1},120)
