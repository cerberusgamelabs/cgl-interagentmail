import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from iam_desktop import DesktopStore
from iam_desktop_controls import SessionControls
from iam_service import IAMError


class ControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = 'idle'
        self.calls = []
        async def request(method, params):
            self.calls.append((method, params))
            if method == 'thread/read':
                return {'thread': {'id': 'thread', 'cwd': str(self.root), 'status': {'type': self.state}, 'model': 'test-model', 'reasoningEffort': 'high', 'preview': 'PRIVATE'}}
            if method == 'model/list':
                return {'data': [{'model': 'test-model', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
            if method == 'thread/turns/list':
                return {'data': [{'id': self.controls.turns.get('One', 'active-turn'), 'status': 'inProgress'}]}
            return {}
        self.client = SimpleNamespace(request=AsyncMock(side_effect=request))
        self.b = SimpleNamespace(store=DesktopStore(self.root/'desktop.sqlite3'), clients={'One': (self.client, 'thread')}, locks={},
            projects=lambda: {'One': {'project_root': str(self.root), 'sandbox': 'workspace-write', 'approval_policy': 'on-request'}},
            context_snapshot=lambda m: None, weekly=None)
        self.controls = SessionControls(self.b)
        self.owner = patch('iam_desktop.session_owner', side_effect=lambda m: m); self.owner.start(); self.addCleanup(self.owner.stop)
        binding = patch('iam_codex_bridge.DeliveryState.load', return_value=SimpleNamespace(thread_id='thread'));self.binding = binding.start();self.addCleanup(binding.stop)

    async def test_status_does_not_leak_preview_or_start_turn(self):
        result = await self.controls.handle('One', 'inspect', {})
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(['thread/read'], [m for m, _ in self.calls])

    async def test_unattached_active_stop_uses_metadata_without_resume(self):
        self.state = 'active'; self.b.clients.clear()
        self.client.connect = AsyncMock(); self.client.close = AsyncMock()
        with patch('iam_orchestrator.appserver_client', return_value=self.client), patch('iam_orchestrator.configured_endpoint', return_value='ws://127.0.0.1:4521'):
            info = await self.controls.handle('One', 'inspect', {})
            self.assertEqual(info['knownTurn'], 'active-turn')
            receipt = await self.controls.handle('One', 'prepare', {'kind': 'stop'})
            await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        methods = [m for m, _ in self.calls]
        self.assertNotIn('thread/resume', methods)
        self.assertNotIn('turn/start', methods)
        self.assertEqual(methods.count('turn/interrupt'), 1)
        self.assertEqual(self.b.clients, {})

    async def test_detailed_status_allowlists_account_data_and_stays_read_only(self):
        original = self.client.request.side_effect
        async def read(method, params):
            if method == 'account/read':
                self.calls.append((method, params))
                return {'account': {'type': 'chatgpt', 'email': 'test@example.test', 'planType': 'pro', 'token': 'SECRET'}}
            if method == 'account/rateLimits/read':
                self.calls.append((method, params))
                return {'rateLimits': {'limitId': 'codex', 'credits': {'balance': '123', 'secret': 'SECRET'}}}
            return await original(method, params)
        self.client.request.side_effect = read
        self.b.weekly_usage = lambda value: None
        with patch('iam_orchestrator.configured_endpoint', return_value='ws://127.0.0.1:4500'), patch('interagentmail.display_name', return_value='Test'):
            result = await self.controls.handle('One', 'inspect', {'details': True})
        self.assertEqual('123', result['credits']['balance'])
        self.assertNotIn('SECRET', str(result)); self.assertNotIn('PRIVATE', str(result))
        self.assertEqual({'refreshToken': False}, next(p for m,p in self.calls if m == 'account/read'))
        self.assertEqual({'thread/read', 'account/read', 'account/rateLimits/read'}, {m for m,p in self.calls})

    async def test_prepare_then_exactly_once_confirm(self):
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'compact'})
        self.assertNotIn('thread/compact/start', [m for m, _ in self.calls])
        await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        with self.assertRaises(IAMError): await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        self.assertEqual(1, len([m for m, _ in self.calls if m == 'thread/compact/start']))

    async def test_stop_does_not_interrupt_replacement_turn(self):
        self.state = 'active'; self.controls.turns['One'] = 'old'
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'stop'})
        self.controls.turns['One'] = 'new'
        with self.assertRaises(IAMError): await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        self.assertNotIn('turn/interrupt', [m for m, _ in self.calls])

    async def test_idle_action_rechecks_busy_and_binding(self):
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'compact'})
        self.state = 'active'
        with self.assertRaises(IAMError): await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        self.binding.return_value = SimpleNamespace(thread_id='different')
        with self.assertRaises(IAMError): await self.controls.handle('One', 'inspect', {})

    async def test_model_catalog_validation_and_plan_payload(self):
        with self.assertRaises(IAMError):
            await self.controls.handle('One', 'prepare', {'kind': 'model', 'model': 'invented', 'effort': 'high'})
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'plan', 'mode': 'plan'})
        await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        params = next(p for m,p in self.calls if m == 'thread/settings/update')
        self.assertEqual(None, params['collaborationMode']['settings']['developer_instructions'])
        self.assertEqual('test-model', params['collaborationMode']['settings']['model'])

    async def test_settings_drop_instructions(self):
        self.controls.observe('One', 'thread', {'method': 'thread/settings/updated', 'params': {'threadId': 'thread', 'threadSettings': {'collaborationMode': {'mode': 'plan', 'settings': {'developer_instructions': 'PRIVATE'}}}}})
        self.assertNotIn('PRIVATE', str(self.controls.settings))

    async def test_uncertainty_is_persistent_and_never_retried(self):
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'compact'})
        original = self.client.request.side_effect
        async def failing(method, params):
            if method == 'thread/compact/start': raise TimeoutError()
            return await original(method, params)
        self.client.request.side_effect = failing
        with self.assertRaises(IAMError): await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        self.client.request.side_effect = original
        controls = SessionControls(self.b)
        receipt = await controls.handle('One', 'prepare', {'kind': 'compact'})
        with self.assertRaises(IAMError): await controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        self.assertEqual('idle', (await controls.handle('One', 'inspect', {}))['state'])

    async def test_correlated_rejection_does_not_leave_uncertain_hold(self):
        from iam_codex_bridge import AppServerRpcError
        receipt = await self.controls.handle('One', 'prepare', {'kind': 'compact'})
        original = self.client.request.side_effect
        async def rejected(method, params):
            if method == 'thread/compact/start': raise AppServerRpcError('unsupported', -32601, 7)
            return await original(method, params)
        self.client.request.side_effect = rejected
        with self.assertRaises(IAMError): await self.controls.handle('One', 'confirm', {'ticket': receipt['ticket']})
        with self.b.store.connect() as db:
            self.assertEqual('rejected', db.execute('SELECT state FROM control_attempts').fetchone()[0])
