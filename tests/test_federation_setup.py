import argparse
import copy
import unittest
from unittest.mock import patch
from iam_federation_setup import run, FederationError, NoRedirect


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.config={'schema_version':1,'enabled':True,'node_id':'a','node_token':'synthetic',
                     'server_url':'wss://example.invalid','human_mailbox':'Human'}
        self.info={'node':'a','owner_account_id':'user:owner','messenger_owner_account_id':'owner',
                   'home_server':'home','messenger_enabled':True,'peers':['b']}

    def configure(self,peer='b',chat=False):
        saved=[]
        with patch('iam_webconnect.load_config',return_value=copy.deepcopy(self.config)), \
             patch('iam_webconnect.save_config',side_effect=lambda value:saved.append(copy.deepcopy(value))), \
             patch('iam_orchestrator.registered_projects',return_value={'Agent':{}}), \
             patch('iam_federation_setup.relay_operation',return_value=self.info):
            run(argparse.Namespace(operation='configure',peer=[peer],mailbox=['Agent'],allow_owner_chat=chat))
        return saved

    def test_configure_derives_owner_from_authenticated_relay(self):
        result=self.configure()[0]
        self.assertEqual(result['federation']['owner_account_id'],'user:owner')
        self.assertNotIn('messenger',result)

    def test_foreign_peer_rejected(self):
        with self.assertRaises(FederationError):self.configure('foreign')

    def test_owner_chat_requires_separate_explicit_flag(self):
        result=self.configure(chat=True)[0]
        self.assertTrue(result['messenger']['send_enabled'])
        self.assertEqual(result['messenger']['owner_account_id'],'owner')

    def test_existing_other_messenger_identity_not_overwritten(self):
        self.config['messenger']={'owner_account_id':'other','home_server':'home'}
        with self.assertRaises(FederationError):self.configure(chat=True)

    def test_disable_saves_local_revocation_even_when_relay_unavailable(self):
        saved=[]
        with patch('iam_webconnect.load_config',return_value=copy.deepcopy(self.config)), \
             patch('iam_webconnect.save_config',side_effect=lambda value:saved.append(copy.deepcopy(value))), \
             patch('iam_federation_setup.relay_operation',side_effect=FederationError('offline')):
            with self.assertRaises(FederationError):run(argparse.Namespace(operation='disable'))
        self.assertFalse(saved[0]['federation']['enabled'])

    def test_credentials_never_follow_redirect(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://foreign.invalid'))
