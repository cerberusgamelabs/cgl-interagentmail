import asyncio
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import iam_desktop as desktop


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    def test_connection_signal_uses_live_client_not_busy_status(self):
        backend = desktop.DesktopBackend.__new__(desktop.DesktopBackend)
        backend.clients = {}
        self.assertFalse(backend.session_connected('Owner'))
        client = MagicMock()
        client.closed.is_set.return_value = False
        backend.clients['Owner'] = (client, 'thread')
        self.assertTrue(backend.session_connected('Owner'))
        client.closed.is_set.return_value = True
        self.assertFalse(backend.session_connected('Owner'))

    async def test_linked_windows_serialize_attachment_without_blocking_delivery_lock(self):
        backend = desktop.DesktopBackend.__new__(desktop.DesktopBackend)
        backend.attach_locks = {}
        active = 0
        high = 0
        async def attach(mailbox):
            nonlocal active, high
            active += 1
            high = max(high, active)
            await asyncio.sleep(0)
            active -= 1
            return {'state':'attached'}
        with patch.object(desktop, 'session_owner', return_value='Owner'), patch.object(backend, '_open', side_effect=attach):
            await asyncio.gather(backend.open('One'), backend.open('Two'))
        self.assertEqual(high, 1)

    async def test_busy_session_is_only_read_never_resumed_or_steered(self):
        backend = desktop.DesktopBackend.__new__(desktop.DesktopBackend)
        backend.attach_locks = {}; backend.clients = {}; backend.context_usage = {}
        backend.compactions = {}; backend.statuses = {}
        with tempfile.TemporaryDirectory() as root:
            client = MagicMock(connect=AsyncMock(), close=AsyncMock(), request=AsyncMock(return_value={
                'thread': {'id':'thread', 'cwd':root, 'status':{'type':'active'}}}))
            with patch.object(backend, 'projects', return_value={'One':{'project_root':root}}), \
                 patch.object(desktop, 'session_owner', return_value='One'), \
                 patch('iam_codex_bridge.DeliveryState.load', return_value=MagicMock(thread_id='thread')), \
                 patch('iam_orchestrator.configured_endpoint', return_value='ws://127.0.0.1:4500'), \
                 patch('iam_orchestrator.appserver_client', return_value=client):
                self.assertEqual(await backend.open('One'), {'state':'busy'})
            client.request.assert_awaited_once_with('thread/read', {'threadId':'thread', 'includeTurns':False})
            client.close.assert_awaited_once()
            self.assertFalse(backend.clients)
