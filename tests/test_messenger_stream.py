import base64
import json
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from tests.test_messenger_history import HistoryTests
from iam_desktop import DesktopStore
from iam_messenger_stream import ForegroundStreams
from iam_messenger_connector import events_request
from iam_messenger_reads import mark


class StreamTests(HistoryTests):
    def test_signed_subscription_change_only_read_and_revocation(self):
        streams=ForegroundStreams();desktop=DesktopStore(self.path)
        stream_id='00000000-0000-4000-8000-000000000001'
        frame=self.frame | {'type':'messenger_events_challenge','content':base64.b64encode(json.dumps({'read':[],'listen':stream_id}).encode()).decode()}
        proof=events_request(frame,self.config,self.store,self.path,now=120,streams=streams)
        self.assertNotIn('error',proof)
        signed=frame | {'type':'messenger_events_sync','nonce':proof['nonce'],
            'signature':base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode()}
        result=events_request(signed,self.config,self.store,self.path,now=121,streams=streams)
        self.assertEqual(result['stream']['id'],stream_id)
        self.assertIn('error',events_request(signed,self.config,self.store,self.path,now=122,streams=streams))
        first=streams.updates(desktop,self.store,self.config,122)
        self.assertEqual(first[0]['state'],'changed');self.assertNotIn('body',first[0])
        self.assertEqual(streams.updates(desktop,self.store,self.config,123),[])
        with desktop.connect() as db:mark(db,'pilot',['1:reply'])
        self.assertGreater(streams.updates(desktop,self.store,self.config,124)[0]['revision'],first[0]['revision'])
        self.store.revoke(self.frame['grant_id'])
        self.assertEqual(streams.updates(desktop,self.store,self.config,125)[0]['state'],'ended')
        self.assertEqual(streams.leases,{})
