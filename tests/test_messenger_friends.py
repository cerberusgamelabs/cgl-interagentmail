import copy
import tempfile
import unittest
from pathlib import Path
from iam_desktop import DesktopStore
from iam_messenger_consent import ExternalContactReview,DISCLOSURE_VERSION
from iam_messenger_friends import policy_request


class FriendPolicyTests(unittest.TestCase):
    def test_socket_policy_is_fresh_scoped_and_never_enables_permission(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DesktopStore(Path(temp)/'desktop.db')
            config=dict(enabled=True,node_id='node',messenger=dict(enabled=True,friendships_enabled=True,home_server='home',owner_account_id='owner'))
            frame=dict(protocol='iam-webconnect',version=1,type='messenger_friend_policy',id='test',installation_id='node',mailbox='Agent',home_server='home',owner_account_id='owner')
            review=ExternalContactReview(store)
            self.assertFalse(policy_request(frame,config,store,{'Agent':{}})['permitted'])
            ticket=review.prepare('Agent',config,'owner-enabled',now=1)['ticket']
            policy=review.confirm('Agent',config,ticket,acknowledged=True,version=DISCLOSURE_VERSION,now=2)
            result=policy_request(frame,config,store,{'Agent':{}})
            self.assertTrue(result['permitted']);self.assertEqual(result['revision'],policy['revision'])
            self.assertEqual(set(result),{'protocol','version','type','id','permitted','revision','scope'})
            for key in ('installation_id','mailbox','home_server','owner_account_id'):
                forged=dict(frame);forged[key]='other'
                self.assertIn('error',policy_request(forged,config,store,{'Agent':{}}))
            for section,key in ((None,'enabled'),('messenger','friendships_enabled')):
                disabled=copy.deepcopy(config)
                (disabled if section is None else disabled[section])[key]=False
                self.assertIn('error',policy_request(frame,disabled,store,{'Agent':{}}))
            review.disable('Agent',config,now=3)
            self.assertFalse(policy_request(frame,config,store,{'Agent':{}})['permitted'])
