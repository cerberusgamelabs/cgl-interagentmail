import asyncio
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import interagentmail as iam
import iam_desktop_onboarding as onboarding
from iam_codex_bridge import DeliveryState
from iam_service import IAMError


@contextmanager
def guard(*args):
    yield True


class FirstSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.box = self.root / 'mailbox'
        self.box.mkdir()
        self.config = self.root / 'config.json'
        self.entry = {'project_root': str(self.root), 'sandbox': 'workspace-write', 'approval_policy': 'on-request'}
        iam.write_atomic(self.config, {'projects': {'New': self.entry}})
        for target, value in [('CONFIG', self.config), ('mailbox', MagicMock(return_value=self.box)), ('display_name', MagicMock(return_value='New Agent'))]:
            p = patch.object(iam, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(onboarding, 'session_guard', guard)
        p.start()
        self.addCleanup(p.stop)
        self.backend = MagicMock(locks={})
        self.backend.store.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        self.flow = onboarding.FirstSession(self.backend)
        self.client = MagicMock(connect=AsyncMock(), close=AsyncMock(), request=AsyncMock(side_effect=self.request))
        self.flow.client = lambda: self.client
        self.calls = []
        self.threads = []
        self.start_error = None

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/list':
            return {'data': self.threads, 'nextCursor': None}
        if self.start_error:
            raise self.start_error
        return {'thread': {'id': 'new-thread', 'cwd': str(self.root)},
                'sandbox': {'type': 'workspaceWrite'}, 'approvalPolicy': 'on-request'}

    async def test_confirm_creates_once_pins_and_adopts_without_sending(self):
        review = await self.flow.review('New')
        self.assertEqual(review['state'], 'confirm')
        self.assertFalse(any(m == 'thread/start' for m, _ in self.calls))
        self.assertEqual(await self.flow.confirm('New', review['token']), {'state': 'bound'})
        self.assertEqual(DeliveryState.load(self.box / '.codex-bridge-state.json').thread_id, 'new-thread')
        self.assertEqual(iam.load(self.flow.journal('New'))['state'], 'bound')
        self.backend.adopt_client.assert_called_once()
        self.assertEqual(await self.flow.review('New'), {'state': 'bound'})
        self.assertEqual(sum(m == 'thread/start' for m, _ in self.calls), 1)
        self.assertFalse(any(m == 'turn/start' for m, _ in self.calls))
        params = next(p for m, p in self.calls if m == 'thread/start')
        self.assertEqual(params['approvalPolicy'], 'on-request')

    async def test_existing_archived_or_live_thread_blocks_creation(self):
        self.threads = [{'id': 'existing'}]
        with self.assertRaises(IAMError):
            await self.flow.review('New')
        self.assertFalse(self.flow.journal('New').exists())
        self.assertFalse(any(m == 'thread/start' for m, _ in self.calls))

    async def test_uncertain_start_blocks_retry_even_after_restart(self):
        review = await self.flow.review('New')
        self.start_error = TimeoutError('unknown outcome')
        with patch('iam_desktop.record_failure'), self.assertRaises(IAMError):
            await self.flow.confirm('New', review['token'])
        self.flow = onboarding.FirstSession(self.backend)
        with self.assertRaises(IAMError):
            await self.flow.review('New')
        self.assertIsNone(DeliveryState.load(self.box / '.codex-bridge-state.json').thread_id)
        self.assertEqual(sum(m == 'thread/start' for m, _ in self.calls), 1)

    async def test_recheck_catches_new_thread_after_confirmation_review(self):
        review = await self.flow.review('New')
        self.threads = [{'id': 'created-elsewhere'}]
        with self.assertRaises(IAMError):
            await self.flow.confirm('New', review['token'])
        self.assertFalse(any(m == 'thread/start' for m, _ in self.calls))

    async def test_archived_only_session_blocks_creation(self):
        self.client.request.side_effect = [
            {'data': [], 'nextCursor': None}, {'data': [{'id': 'archived'}], 'nextCursor': None}]
        with self.assertRaises(IAMError):
            await self.flow.review('New')
        self.assertTrue(self.client.request.call_args.args[1]['archived'])

    async def test_bound_session_never_calls_start_or_lists(self):
        state = DeliveryState.load(self.box / '.codex-bridge-state.json')
        state.thread_id = 'existing'
        state.save()
        self.assertEqual(await self.flow.review('New'), {'state': 'bound'})
        self.assertFalse(self.calls)

    async def test_binding_lost_after_creation_is_not_treated_as_new(self):
        review = await self.flow.review('New')
        await self.flow.confirm('New', review['token'])
        state = DeliveryState.load(self.box / '.codex-bridge-state.json')
        state.thread_id = None
        state.save()
        with self.assertRaises(IAMError):
            await self.flow.review('New')
        self.assertEqual(sum(m == 'thread/start' for m, _ in self.calls), 1)

    async def test_policy_change_invalidates_confirmation(self):
        review = await self.flow.review('New')
        self.entry['sandbox'] = 'danger-full-access'
        iam.write_atomic(self.config, {'projects': {'New': self.entry}})
        with self.assertRaises(IAMError):
            await self.flow.confirm('New', review['token'])
        self.assertFalse(any(m == 'thread/start' for m, _ in self.calls))

    async def test_expired_and_replayed_confirmations_rejected(self):
        review = await self.flow.review('New')
        self.flow.reviews[review['token']]['expires'] = 0
        for _ in range(2):
            with self.assertRaises(IAMError):
                await self.flow.confirm('New', review['token'])

    async def test_missing_binding_with_history_is_not_new_agent(self):
        self.backend.store.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
        with self.assertRaises(IAMError):
            await self.flow.review('New')
        self.assertFalse(self.calls)

    async def test_bad_server_policy_preserves_receipt_but_does_not_bind(self):
        review = await self.flow.review('New')
        self.client.request.side_effect = None
        self.client.request.return_value = {'data': [], 'thread': {'id': 'new-thread', 'cwd': str(self.root)},
                                           'sandbox': {'type':'dangerFullAccess'}, 'approvalPolicy':'never'}
        with self.assertRaises(IAMError):
            await self.flow.confirm('New', review['token'])
        self.assertEqual(iam.load(self.flow.journal('New'))['thread_id'], 'new-thread')
        self.assertIsNone(DeliveryState.load(self.box / '.codex-bridge-state.json').thread_id)
        self.backend.adopt_client.assert_not_called()
