"""Approval recovery regression tests: temporary store and fake transport only."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import iam_desktop as desktop
from iam_service import IAMError


class ApprovalLifecycleAudit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = desktop.DesktopStore(Path(temp.name) / 'desktop.sqlite3')
        factory = patch.object(desktop, 'DesktopStore', return_value=self.store)
        factory.start()
        self.addCleanup(factory.stop)
        self.backend = desktop.DesktopBackend()

    async def snapshot(self, backend):
        with patch.object(backend, 'roster_config', new=AsyncMock(return_value={'projects': {}})):
            return await backend.dispatch('snapshot', {})

    async def test_reopening_backend_keeps_live_leased_prompt(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/commandExecution/requestApproval','{}',0,'pending',NULL)")
            db.execute("INSERT INTO approval_leases VALUES('a',?)", (time.time() + 30,))
        reopened = desktop.DesktopBackend()
        self.assertEqual(['a'], [r['id'] for r in (await self.snapshot(reopened))['approvals']])

    async def test_expired_lease_keeps_nonactionable_recovery_notice(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/commandExecution/requestApproval','{}',0,'pending',NULL)")
            db.execute("INSERT INTO approval_leases VALUES('a',0)")
        reopened = desktop.DesktopBackend()
        snapshot = await self.snapshot(reopened)
        self.assertEqual([], snapshot['approvals'])
        self.assertEqual(1, len(snapshot['notices']))
        self.assertEqual('infrastructure', snapshot['notices'][0]['kind'])
        self.assertNotIn('echo fixture', snapshot['notices'][0]['body'])
        with self.store.connect() as db:
            self.assertEqual('pending', db.execute("SELECT state FROM approvals WHERE id='a'").fetchone()[0])
        with self.assertRaises(IAMError):
            await reopened.dispatch('decide', {'id': 'a', 'decision': 'accept'})
        await reopened.dispatch('dismiss-notice', {'id': snapshot['notices'][0]['id']})
        self.assertEqual([], (await self.snapshot(reopened))['notices'])
        with self.store.connect() as db:
            db.execute('UPDATE approval_leases SET expires=?', (time.time()+30,))
        self.assertEqual(1, len((await self.snapshot(reopened))['approvals']))
        with self.store.connect() as db:
            db.execute('UPDATE approval_leases SET expires=0')
        self.assertEqual(1, len((await self.snapshot(reopened))['notices']))

    async def offer(self, client):
        with patch.object(desktop, 'enabled', return_value=True), \
                patch.object(desktop.iam, 'load', return_value={'projects': {'One': {}}}), \
                patch.object(desktop, 'session_owner', return_value='One'), \
                patch('iam_codex_bridge.DeliveryState.load', return_value=MagicMock(thread_id='thread')):
            self.assertTrue(await desktop.offer_approval(client, {
                'id': 7, 'method': 'item/commandExecution/requestApproval',
                'params': {'threadId': 'thread', 'turnId': 'turn', 'command': 'echo fixture'}}))
        with self.store.connect() as db:
            return db.execute('SELECT id FROM approvals').fetchone()[0]

    def client(self):
        client = MagicMock(closed=asyncio.Event(), _write=AsyncMock())
        client._desktop_resolved = set()
        client._desktop_approval_tasks = set()
        return client

    async def test_write_waits_for_exact_server_confirmation_without_resend(self):
        client = self.client()
        written = asyncio.Event()
        client._write.side_effect = lambda message: written.set()
        approval_id = await self.offer(client)
        await self.backend.dispatch('decide', {'id': approval_id, 'decision': 'decline'})
        await asyncio.wait_for(written.wait(), 5)
        self.assertEqual(set(), client._desktop_resolved)
        with self.store.connect() as db:
            self.assertEqual('sent', db.execute('SELECT state FROM approvals').fetchone()[0])
        self.assertEqual(1, len((await self.snapshot(self.backend))['notices']))
        client._desktop_resolved.add(99)
        await asyncio.sleep(.4)
        self.assertTrue(client._desktop_approval_tasks)
        client._desktop_resolved.add(7)
        await asyncio.wait_for(asyncio.gather(*client._desktop_approval_tasks), 5)
        client._write.assert_awaited_once_with({'id': 7, 'result': {'decision': 'decline'}})
        with self.store.connect() as db:
            self.assertEqual('resolved', db.execute('SELECT state FROM approvals').fetchone()[0])
        self.assertEqual([], (await self.snapshot(self.backend))['notices'])

    async def test_disconnect_marks_unavailable_and_gives_notice(self):
        client = self.client()
        await self.offer(client)
        client.closed.set()
        await asyncio.wait_for(asyncio.gather(*client._desktop_approval_tasks), 2)
        client._write.assert_not_awaited()
        with self.store.connect() as db:
            self.assertEqual('unavailable', db.execute('SELECT state FROM approvals').fetchone()[0])
        snapshot = await self.snapshot(self.backend)
        self.assertEqual([], snapshot['approvals'])
        self.assertEqual(1, len(snapshot['notices']))

    async def test_write_failure_is_not_retried_or_reported_as_confirmed(self):
        client = self.client()
        client._write.side_effect = OSError('private payload')
        approval_id = await self.offer(client)
        await self.backend.dispatch('decide', {'id': approval_id, 'decision': 'decline'})
        with patch.object(desktop, 'record_failure') as record:
            await asyncio.wait_for(asyncio.gather(*client._desktop_approval_tasks), 5)
        record.assert_called_once()
        client._write.assert_awaited_once()
        with self.store.connect() as db:
            self.assertEqual('unavailable', db.execute('SELECT state FROM approvals').fetchone()[0])
        self.assertNotIn('private payload', str(await self.snapshot(self.backend)))

    async def test_resolution_before_decision_sends_nothing(self):
        client = self.client()
        await self.offer(client)
        client._desktop_resolved.add(7)
        await asyncio.wait_for(asyncio.gather(*client._desktop_approval_tasks), 5)
        client._write.assert_not_awaited()
        with self.store.connect() as db:
            self.assertEqual('resolved', db.execute('SELECT state FROM approvals').fetchone()[0])
        self.assertEqual([], (await self.snapshot(self.backend))['notices'])

    async def test_restart_cannot_replay_sent_decision(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/commandExecution/requestApproval','{}',0,'sent','accept')")
        reopened = desktop.DesktopBackend()
        snapshot = await self.snapshot(reopened)
        self.assertEqual([], snapshot['approvals'])
        self.assertEqual(1, len(snapshot['notices']))
        with self.assertRaises(IAMError):
            await reopened.dispatch('decide', {'id': 'a', 'decision': 'accept'})
        self.assertEqual({}, reopened.clients)
