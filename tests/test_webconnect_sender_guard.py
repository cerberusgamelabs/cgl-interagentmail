import copy
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from iam_webconnect import WebConnectClient


class SenderGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(WebConnectClient)
        self.client._invalid_sender_reported = set()

    def test_matches_relay_sender_contract(self):
        for value in ('Agent', 'Agent_2', 'agent.name-3'):
            self.assertTrue(self.client.relay_sender_allowed(value, 'id'))
        for value in ('Display Name', '', None, 42, 'Agent\n', 'a@b', 'é'):
            self.assertFalse(self.client.relay_sender_allowed(value, 'id'))

    def test_persisted_bad_frame_is_retained_while_valid_frame_can_send(self):
        frames = {'bad': {'id': 'bad', 'payload': {'from': 'Display Name', 'body': 'private'}},
                  'good': {'id': 'good', 'payload': {'from': 'Agent'}}}
        before = copy.deepcopy(frames)
        self.client.state = SimpleNamespace(outbound=frames, save=Mock())
        with patch('iam_webconnect.LOG.warning') as warning:
            self.assertEqual([frames['good']], self.client.relay_outbound())
            self.assertEqual([frames['good']], self.client.relay_outbound())
            warning.assert_called_once()
            self.assertNotIn('Display Name', str(warning.call_args))
            self.assertNotIn('private', str(warning.call_args))
        self.assertEqual(before, frames)
        self.client.state.save.assert_not_called()

    def test_new_bad_sender_is_not_observed_or_uploaded(self):
        human = Mock(address='Owner')
        human.list_messages.return_value = []
        human.inbox.return_value = [{'id': 'local-1', 'from': 'Display Name', 'to': ['Owner']}]
        self.client.humans = {'Owner': human}
        self.client.state = SimpleNamespace(_observed=set(), outbound={}, remember_local=Mock())
        self.client.outbound_attachments = Mock()
        self.client.queue_replies()
        self.client.outbound_attachments.assert_not_called()
        self.client.state.remember_local.assert_not_called()
        self.assertEqual({}, self.client.state.outbound)

    def test_warning_cannot_inject_lines(self):
        with patch('iam_webconnect.LOG.warning') as warning:
            self.client.relay_sender_allowed('bad sender', 'id\nsecret')
            self.assertEqual('invalid-id', warning.call_args.args[1])

    def test_initial_send_and_timeout_retry_both_hold_invalid_frame(self):
        bad = {'id': 'bad', 'payload': {'from': 'Display Name'}}
        good = {'id': 'good', 'payload': {'from': 'Agent'}}
        self.client.state = SimpleNamespace(outbound={'bad': bad, 'good': good})
        self.client.config = {'server_url': 'wss://example.invalid', 'node_id': 'test', 'node_token': 'fixture'}
        self.client.humans = {'Owner': Mock()}
        self.client.queue_replies = Mock()
        self.client.mailbox_directory = Mock(return_value={'mailboxes': []})
        socket = AsyncMock()
        socket.recv.side_effect = [json.dumps({'type': 'registered', 'human_mailboxes': ['Owner']}),
                                   asyncio.TimeoutError(), RuntimeError('end fixture')]
        connection = AsyncMock()
        connection.__aenter__.return_value = socket
        with patch('iam_webconnect.websockets.connect', return_value=connection), \
             patch('iam_webconnect.verify_relay', new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, 'end fixture'):
                asyncio.run(self.client.run_once())
        sent = [json.loads(call.args[0]) for call in socket.send.call_args_list]
        self.assertEqual(2, sum(frame.get('id') == 'good' for frame in sent))
        self.assertFalse(any(frame.get('id') == 'bad' for frame in sent))
        self.assertEqual({'bad': bad, 'good': good}, self.client.state.outbound)


if __name__ == '__main__':
    unittest.main()
