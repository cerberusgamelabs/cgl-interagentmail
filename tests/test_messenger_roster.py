import base64
import json
import unittest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from tests import test_messenger_images as images
from iam_messenger_connector import events_request
from iam_messenger_stream import ForegroundStreams
from unittest.mock import patch

class RosterTests(unittest.TestCase):
    setUp=images.ImageTests.setUp

    def test_signed_desktop_discovery_denies_legacy_and_returns_current_names_only(self):
        frame=self.frame | dict(type='messenger_events_challenge',conversation_id='@desktop',content=base64.b64encode(b'{"read":[]}').decode())
        self.assertIn('error',events_request(frame,self.config,self.store,self.path,now=120))
        # Synthetic explicit local upgrade, never mutate a production grant.
        with self.store.connection() as db:db.execute('UPDATE pairing_grants SET desktop_scope=1 WHERE id=?',(self.grant,))
        with patch.object(self.store,'desktop_conversations',return_value=['new-agent']), patch('iam_messenger_connector.conversation_name',return_value='New agent'):
            proof=events_request(frame,self.config,self.store,self.path,now=120)
            self.assertNotIn('error',proof)
            frame.update(type='messenger_events_sync',nonce=proof['nonce'],signature=base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode())
            result=events_request(frame,self.config,self.store,self.path,now=120)
            self.assertEqual(result['desktop_roster'],[{'conversation':'new-agent','display_name':'New agent'}])
            self.assertEqual(result['subscriptions'],[])
            self.assertNotIn('profile',result)
            self.assertIn('error',events_request(frame,self.config,self.store,self.path,now=120))

    def query(self,roster,listen=None,streams=None):
        value=dict(read=[],roster=roster)
        if listen:value['listen']=listen
        frame=self.frame | dict(type='messenger_events_challenge',content=base64.b64encode(json.dumps(value).encode()).decode())
        proof=events_request(frame,self.config,self.store,self.path,now=120,streams=streams)
        if 'error' in proof:return proof
        frame.update(type='messenger_events_sync',nonce=proof['nonce'],signature=base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),ec.ECDSA(hashes.SHA256()))).decode())
        return events_request(frame,self.config,self.store,self.path,now=120,streams=streams)

    def test_roster_requires_every_exact_grant_and_projects_only_metadata(self):
        self.desktop.profile('pilot',dict(status='chosen status',bio='private field'))
        scope=dict(conversation='pilot',grant=self.grant)
        result=self.query([scope])
        self.assertEqual('pilot',result['roster'][0]['conversation'])
        self.assertEqual({'status':'chosen status'},result['roster'][0]['profile'])
        self.assertIn('error',self.query([scope,dict(conversation='other',grant=self.grant)]))
        self.assertIn('error',self.query([scope,scope]))

    def test_roster_stream_detects_profile_change_and_revocation(self):
        from uuid import uuid4
        streams=ForegroundStreams();ident=str(uuid4())
        result=self.query([dict(conversation='pilot',grant=self.grant)],ident,streams)
        self.assertEqual(ident,result['stream']['id'])
        self.assertEqual(1,len(streams.updates(self.desktop,self.store,self.config,121)))
        self.assertEqual([],streams.updates(self.desktop,self.store,self.config,122))
        self.desktop.profile('pilot',dict(status='new status'))
        self.assertEqual('changed',streams.updates(self.desktop,self.store,self.config,123)[0]['state'])
        self.store.revoke(self.grant)
        self.assertEqual('ended',streams.updates(self.desktop,self.store,self.config,124)[0]['state'])
