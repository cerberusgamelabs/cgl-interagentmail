import base64
import unittest
from tests.test_messenger_images import ImageTests
from iam_messenger_profiles import snapshot,publish_runtime
from unittest.mock import patch

class ProfileTests(unittest.TestCase):
    setUp=ImageTests.setUp
    transfer=ImageTests.transfer

    def test_runtime_is_metadata_only_and_expires_without_attaching(self):
        publish_runtime(self.desktop,[dict(mailbox='pilot',sessionConnected=True,availability='busy',
            context=dict(remainingPercent=60,threadId='private',usedTokens=1234))],now=100)
        with patch('iam_messenger_profiles.time.time',return_value=110):
            value=snapshot(self.desktop,'pilot')['runtime']
        self.assertEqual('busy',value['availability']);self.assertFalse(value['stale'])
        self.assertEqual({'remainingPercent':60,'stale':False},value['context'])
        with patch('iam_messenger_profiles.time.time',return_value=140):
            value=snapshot(self.desktop,'pilot')['runtime']
        self.assertEqual('unavailable',value['availability']);self.assertTrue(value['stale'])

    def test_avatar_scope_hash_and_profile_projection(self):
        raw=b'\xff\xd8\xfftest'
        self.desktop.profile('pilot',dict(status='Testing',presence='away',bio='not exported',avatar='data:image/jpeg;base64,'+base64.b64encode(raw).decode()))
        value=snapshot(self.desktop,'pilot')
        self.assertNotIn('bio',value)
        query=dict(action='read_avatar',sha256=value['avatar']['sha256'],offset=0)
        self.assertEqual(raw,base64.b64decode(self.transfer(query)['attachment']['data']))
        self.assertIn('error',self.transfer(query,conversation='other'))
        self.desktop.profile('pilot',dict(avatar=''))
        self.assertIn('error',self.transfer(query))
        self.store.revoke(self.grant)
        self.assertIn('error',self.transfer(query))
