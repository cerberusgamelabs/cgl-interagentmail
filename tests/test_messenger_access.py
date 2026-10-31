import unittest
from dataclasses import replace

from iam_messenger_access import AccessDenied, OwnerGrant, Principal, authorize, authorize_history


class MessengerAccessTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(enabled=True,
            principal=Principal('home-a', 'owner', 'phone', 'messenger_device'),
            grant=OwnerGrant('home-a', 'owner', 'phone', 'node-a',
                frozenset({'ophelia-conversation'}), 200, 0),
            installation_id='node-a', conversation_id='ophelia-conversation', now=100)

    def test_paired_owner_can_read_explicitly_granted_existing_history(self):
        self.assertIs(authorize_history(message_created_at=1, **self.args), self.args['grant'])

    def test_default_disabled_and_unauthenticated(self):
        for changes in ({'enabled': False}, {'principal': None}, {'grant': None}):
            with self.subTest(changes=changes), self.assertRaises(AccessDenied):
                authorize(operation='send', **(self.args | changes))

    def test_identity_scope_and_revocation(self):
        for changes in (
            {'principal': replace(self.args['principal'], account_id='stranger')},
            {'principal': replace(self.args['principal'], home_server='other-home')},
            {'principal': replace(self.args['principal'], device_id='other-phone')},
            {'principal': replace(self.args['principal'], credential_kind='legacy_node')},
            {'installation_id': 'other-node'}, {'conversation_id': 'private-conversation'},
            {'grant': replace(self.args['grant'], revoked=True)}, {'now': 200},
        ):
            with self.subTest(changes=changes), self.assertRaises(AccessDenied):
                authorize(operation='send', **(self.args | changes))

    def test_admin_controls_never_authorized(self):
        for operation in ('restart', 'model', 'permissions', 'rpc', 'open', 'approval'):
            with self.subTest(operation=operation), self.assertRaises(AccessDenied):
                authorize(operation=operation, **self.args)

    def test_history_cutoff(self):
        args = self.args | {'grant': replace(self.args['grant'], history_from=50)}
        with self.assertRaises(AccessDenied):
            authorize_history(message_created_at=49, **args)
        authorize_history(message_created_at=50, **args)


if __name__ == '__main__':
    unittest.main()
