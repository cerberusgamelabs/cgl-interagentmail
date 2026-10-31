import unittest
from unittest.mock import patch
from iam_desktop import DesktopBackend
from iam_service import IAMError

class DesktopFriendRequests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_roster_is_registered_and_read_only(self):
        backend = object.__new__(DesktopBackend)
        with patch.object(backend, 'projects', return_value={'Wren': {}}), patch('iam_messenger_friends.friend_operation', return_value={'relationships': []}) as operation:
            result = await backend.dispatch('agent-friend-roster', {'mailbox': 'Wren', 'operation': 'decide', 'action': 'accept'})
            self.assertEqual(result, {'relationships': []})
            self.assertEqual(operation.call_args.args[0].address, 'Wren')
            self.assertEqual(operation.call_args.args[1:], ('roster', {}))
            with self.assertRaises(IAMError):
                await backend.dispatch('agent-friend-roster', {'mailbox': 'Other'})
            self.assertEqual(operation.call_count, 1)

    async def test_config_projects_origin_only(self):
        backend = object.__new__(DesktopBackend)
        config = {'enabled': True, 'messenger': {'friendships_enabled': True}, 'server_url': 'wss://example.test/ws', 'node_token': 'SECRET'}
        with patch('iam_webconnect.load_config', return_value=config):
            self.assertEqual(await backend.dispatch('human-friend-config', {}), {'origin': 'https://example.test'})
            config['enabled'] = False
            with self.assertRaises(IAMError):
                await backend.dispatch('human-friend-config', {})
