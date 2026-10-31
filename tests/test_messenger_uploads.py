import base64
import hashlib
import json
import unittest
from uuid import uuid4
from tests.test_messenger_images import ImageTests
from iam_messenger_uploads import fingerprint,manifest_digest


class OriginalUploadTests(unittest.TestCase):
    setUp=ImageTests.setUp
    transfer=ImageTests.transfer
    call=ImageTests.call
    signed=ImageTests.signed

    def stage(self,raw=b'original document',name='notes.txt',kind='document'):
        meta=dict(upload=str(uuid4()),name=name,kind=kind,size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        status=self.transfer(dict(action='upload_status',**meta))['attachment']
        self.assertEqual(0,status['next_part'])
        for part in range(max(1,(len(raw)+34999)//35000)):
            result=self.transfer(dict(action='upload_blob',**meta,part=part,data=base64.b64encode(raw[part*35000:(part+1)*35000]).decode()))['attachment']
            self.assertEqual(part+1,result['next_part'])
        return meta,dict(upload=meta['upload'],digest=fingerprint(meta))

    def submit(self,items):
        frame=self.frame | dict(content=base64.b64encode(json.dumps(dict(request_id=self.request,body='files',attachments=items)).encode()).decode())
        return self.call(self.signed(frame))

    def test_original_documents_atomic_send_and_exact_retry(self):
        raw=('♡ original\n'*4000).encode();meta,item=self.stage(raw)
        self.assertEqual('queued',self.submit([item])['state'])
        self.assertEqual('queued',self.submit([item])['state'])
        with self.desktop.connect() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM requests').fetchone()[0])
            file=json.loads(db.execute('SELECT data FROM documents').fetchone()[0])[0]
            self.assertEqual(raw,base64.b64decode(file['data']))
            self.assertEqual(0,db.execute('SELECT count(*) FROM mobile_uploads').fetchone()[0])
        self.assertIn('error',self.submit([item | dict(digest='a'*64)]))
        from iam_messenger_send import lookup_request
        result=lookup_request(self.path,self.principal,'node','pilot',self.grant,0,
            dict(request_id=self.request,body_sha256=hashlib.sha256(b'files').hexdigest(),attachments_sha256=manifest_digest([item])))
        self.assertTrue(result['found'])

    def test_partial_resumes_and_metadata_conflict_denies(self):
        meta=dict(upload=str(uuid4()),kind='document',name='notes.txt',size=40000,sha256=hashlib.sha256(b'x'*40000).hexdigest())
        first=dict(action='upload_blob',**meta,part=0,data=base64.b64encode(b'x'*35000).decode())
        self.assertEqual(1,self.transfer(first)['attachment']['next_part'])
        self.assertEqual(1,self.transfer(dict(action='upload_status',**meta))['attachment']['next_part'])
        self.assertEqual(1,self.transfer(first)['attachment']['next_part'])
        self.assertIn('error',self.transfer(first | dict(name='changed.txt')))
        self.assertIn('error',self.submit([dict(upload=meta['upload'],digest=fingerprint(meta))]))
        with self.desktop.connect() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM requests').fetchone()[0])
        self.store.revoke(self.grant)
        self.assertIn('error',self.transfer(first))

    def test_empty_file_and_invalid_content(self):
        _,empty=self.stage(b'')
        self.assertEqual('queued',self.submit([empty])['state'])
        self.request=str(uuid4())
        _,bad=self.stage(b'not a pdf','bad.pdf')
        self.assertIn('error',self.submit([bad]))
        with self.desktop.connect() as db:self.assertEqual(1,db.execute('SELECT count(*) FROM requests').fetchone()[0])

    def test_category_limit_is_atomic_and_expired_parts_are_not_sent(self):
        items=[self.stage(b'x',f'file{i}.txt')[1] for i in range(5)]
        self.assertIn('error',self.submit(items))
        with self.desktop.connect() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM requests').fetchone()[0])
            db.execute('UPDATE mobile_uploads SET expires=0')
        self.assertIn('error',self.submit(items[:1]))

    def test_chunk_changes_and_bad_metadata_fail_closed(self):
        meta=dict(upload=str(uuid4()),kind='document',name='notes.txt',size=3,sha256=hashlib.sha256(b'abc').hexdigest())
        query=dict(action='upload_blob',**meta,part=0,data='YWJj')
        self.assertIn('attachment',self.transfer(query))
        self.assertIn('error',self.transfer(query | dict(data='eHl6')))
        for change in [dict(size=4000001),dict(name='../notes.txt'),dict(part=True),dict(data='YWJj='),dict(kind='unknown')]:
            self.assertIn('error',self.transfer(query | change))
