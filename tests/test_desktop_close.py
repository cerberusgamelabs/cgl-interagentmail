import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from iam_desktop import DesktopBackend, DesktopStore


class ClosedChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.b = DesktopBackend.__new__(DesktopBackend)
        self.b.store = DesktopStore(Path(temp.name) / 'desktop.sqlite3')
        self.b.locks = {}; self.b.attach_locks = {}
        self.b.chat_windows = {}; self.b.release_pending = set()
        self.b.statuses = {}; self.b.compactions = {}
        self.b.context_usage = {'Owner': {'remainingPercent': 69, 'threadId': 'thread'}}
        self.client = MagicMock(close=AsyncMock(), request=AsyncMock(return_value={
            'thread': {'id': 'thread', 'status': {'type': 'idle'}}}))
        self.client.closed.is_set.return_value = False
        self.b.clients = {'Owner': (self.client, 'thread')}
        self.b.projects = lambda: {'One': {}, 'Two': {}}
        p = patch('iam_desktop.session_owner', return_value='Owner')
        p.start(); self.addCleanup(p.stop)

    async def close_window(self):
        await self.b.chat_window('One', '1', True)
        await self.b.chat_window('One', '1', False)
        await self.b.release_closed_chats()

    async def test_idle_close_releases_connection_preserves_context(self):
        await self.close_window()
        self.client.close.assert_awaited_once()
        self.assertFalse(self.b.clients)
        self.assertEqual(self.b.context_snapshot('One')['remainingPercent'], 69)
        self.client.request.assert_awaited_once_with('thread/read', {'threadId': 'thread', 'includeTurns': False})

    async def test_busy_close_defers_until_idle(self):
        for flags in ([], ['waitingOnApproval']):
            self.client.request.return_value = {'thread': {'id': 'thread', 'status': {'type': 'active', 'activeFlags': flags}}}
            await self.close_window()
            self.client.close.assert_not_awaited()
        self.client.request.return_value = {'thread': {'id': 'thread', 'status': {'type': 'idle'}}}
        await self.b.release_closed_chats()
        self.client.close.assert_awaited_once()

    async def test_shared_window_keeps_connection(self):
        await self.b.chat_window('Two', '2', True)
        await self.close_window()
        self.client.close.assert_not_awaited()
        await self.b.chat_window('Two', '2', False)
        await self.b.release_closed_chats()
        self.client.close.assert_awaited_once()

    async def test_reopen_before_cleanup_keeps_connection(self):
        await self.b.chat_window('One', '1', True)
        await self.b.chat_window('One', '1', False)
        await self.b.chat_window('One', '2', True)
        await self.b.release_closed_chats()
        self.client.close.assert_not_awaited()

    async def test_queued_delivery_keeps_connection(self):
        self.b.store.create('One', 'test', '00000000-0000-4000-8000-000000000001')
        await self.close_window()
        self.client.close.assert_not_awaited()

    async def test_unknown_status_fails_closed(self):
        self.client.request.return_value = {'thread': {'id': 'thread', 'status': {'type': 'systemError'}}}
        await self.close_window()
        self.client.close.assert_not_awaited()

    async def test_unresolved_approval_keeps_connection_even_if_idle(self):
        with self.b.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES ('a','One','test','{}',0,'pending',NULL)")
        await self.close_window()
        self.client.close.assert_not_awaited()
        with self.b.store.connect() as db:
            db.execute("UPDATE approvals SET state='resolved'")
        await self.b.release_closed_chats()
        self.client.close.assert_awaited_once()

    async def test_close_waits_for_delivery_lock(self):
        lock = self.b.locks.setdefault('Owner', asyncio.Lock())
        await self.b.chat_window('One', '1', True)
        await lock.acquire()
        task = asyncio.create_task(self.b.chat_window('One', '1', False))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        lock.release()
        await task
