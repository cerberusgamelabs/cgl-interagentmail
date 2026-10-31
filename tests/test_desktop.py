import asyncio
import json
import tempfile
import unittest
import uuid
import time
import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import iam_desktop as desktop
from iam_service import IAMError


class DesktopStoreTests(unittest.TestCase):
    def test_image_only_message_is_persisted_and_retry_is_bound_to_image(self):
        raw = b'\x89PNG\r\n\x1a\nfixture'
        images = [{'name': '../../outside.png', 'data': 'data:image/png;base64,' + base64.b64encode(raw).decode()}]
        self.store.create('One', '', self.request, images)
        self.store.create('One', '', self.request, images)
        with self.assertRaises(IAMError): self.store.create('One', '', self.request, [])
        inputs = self.store.image_inputs(self.request)
        self.assertEqual('localImage', inputs[0]['type'])
        saved = Path(inputs[0]['path'])
        self.assertEqual(self.store.path.parent.resolve() / 'desktop-attachments', saved.parent)
        self.assertEqual(raw, saved.read_bytes())
        history = self.store.history('One')
        self.assertEqual([{'name': '../../outside.png'}], history[0]['images'])
        self.assertNotIn('base64', json.dumps(history))

    def test_invalid_and_excessive_images_are_rejected_before_queueing(self):
        for images in ([{'data':'https://example.com/image.png'}], [{'data':'data:image/svg+xml;base64,AAAA'}],
                       [{'data':'data:image/png;base64,YmFk'}], [{}]*5,
                       [{'data':'data:image/png;base64,' + 'A'*5500001}]):
            with self.assertRaises(IAMError): self.store.create('One', 'hello', self.request, images)
        self.assertEqual([], self.store.history('One'))
    def test_weekly_requires_actual_weekly_codex_window(self):
        parse = desktop.DesktopBackend.weekly_usage
        self.assertIsNone(parse({'rateLimits': {'primary': {'usedPercent': 30, 'windowDurationMins': 300}}}))
        result = parse({'rateLimits': {'limitId': 'codex', 'secondary': {'usedPercent': 23, 'windowDurationMins': 10080}}})
        self.assertEqual(77, result['remainingPercent'])
        self.assertIsNone(parse({'rateLimits': {'limitId': 'other', 'secondary': {'usedPercent': 23, 'windowDurationMins': 10080}}}))

    def test_credit_projection_is_codex_only_and_preserves_fraction(self):
        parse = desktop.DesktopBackend.credit_usage
        self.assertEqual({'balance':'62497.55342','unlimited':False,'hasCredits':True},parse({'rateLimitsByLimitId':{'codex':{'credits':{'balance':'62497.55342','unlimited':False,'hasCredits':True,'secret':'excluded'}}}}))
        self.assertIsNone(parse({'rateLimits':{'limitId':'other','credits':{'balance':'42'}}}))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = desktop.DesktopStore(Path(self.temp.name) / 'desktop.sqlite3')
        self.request = str(uuid.uuid4())

    def test_reply_is_explicit_mailbox_scoped_and_immutable(self):
        self.store.create('One', 'Hello', self.request)
        with self.assertRaises(IAMError): self.store.reply('One', self.request, 'Not submitted')
        self.store.change(self.request, 'accepted')
        with self.assertRaises(IAMError): self.store.reply('Two', self.request, 'Wrong identity')
        self.assertEqual([], self.store.history('Two'))
        self.store.reply('One', self.request, 'Intended reply')
        first_time = self.store.history('One')[0]['replied_at']
        self.assertIsInstance(first_time, float)
        self.store.reply('One', self.request, 'Intended reply')
        self.assertEqual(first_time, self.store.history('One')[0]['replied_at'])
        with self.assertRaises(IAMError): self.store.reply('One', self.request, 'Replacement')
        self.assertEqual('Intended reply', self.store.history('One')[0]['reply'])

    def test_create_is_idempotent_and_rejects_id_reuse(self):
        self.store.create('One', 'Hello', self.request)
        self.store.create('One', 'Hello', self.request)
        self.assertEqual(1, len(self.store.history('One')))
        with self.assertRaises(IAMError): self.store.create('Two', 'Hello', self.request)
        with self.assertRaises(IAMError): self.store.create('One', 'Changed', self.request)

    def test_late_reply_can_resolve_held_but_cannot_be_overwritten_by_receipt(self):
        self.store.create('One', 'Hello', self.request)
        self.store.change(self.request, 'held')
        self.store.reply('One', self.request, 'Late reply')
        self.store.change(self.request, 'accepted', turn_id='turn')
        self.assertEqual('replied', self.store.history('One')[0]['state'])

    def test_completion_before_acceptance_and_late_reply(self):
        self.store.create('One', 'Hello', self.request)
        self.store.change(self.request, 'submitting', thread_id='thread')
        self.store.turn_completed('thread', 'turn')
        self.store.change(self.request, 'accepted', turn_id='turn')
        row = self.store.history('One')[0]
        self.assertEqual('accepted', row['state'])
        self.assertEqual('Turn finished; no reply published.', row['error'])
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM requests WHERE state IN ('queued','mobile_queued')").fetchone()[0])
        self.store.reply('One', self.request, 'Delayed legitimate reply')
        self.store.turn_completed('thread', 'turn')
        row = self.store.history('One')[0]
        self.assertEqual('replied', row['state'])
        self.assertIsNone(row['error'])

    def test_completion_matches_exact_thread_and_turn(self):
        self.store.create('One', 'Hello', self.request)
        self.store.change(self.request, 'accepted', thread_id='thread', turn_id='turn')
        for thread, turn in [('other', 'turn'), ('thread', 'other'), ('thread', None)]:
            self.store.turn_completed(thread, turn)
            self.assertIsNone(self.store.history('One')[0]['error'])
        self.store.turn_completed('thread', 'turn')
        self.store.turn_completed('thread', 'turn')
        self.assertEqual('Turn finished; no reply published.', self.store.history('One')[0]['error'])

    def test_reply_before_completion_remains_replied(self):
        self.store.create('One', 'Hello', self.request)
        self.store.change(self.request, 'accepted', thread_id='thread', turn_id='turn')
        self.store.reply('One', self.request, 'Published')
        self.store.turn_completed('thread', 'turn')
        self.assertIsNone(self.store.history('One')[0]['error'])
        self.assertEqual('replied', self.store.history('One')[0]['state'])

    def test_away_messages_are_scoped_bounded_and_clearable(self):
        note = 'Drawing for a while.\n\nBack after tea.'
        self.store.profile('One', {'presence': 'away', 'away_message': note})
        self.assertEqual(note, self.store.profile('One')['away_message'])
        self.assertEqual({}, self.store.profile('Two'))
        for fields in ({'presence': 'offline'}, {'away_message': 'x' * 2001}, {'presence': True}):
            with self.assertRaises(IAMError):
                self.store.profile('One', fields)
        self.store.profile('One', {'presence': '', 'away_message': ''})
        self.assertEqual('', self.store.profile('One')['away_message'])

    def test_profiles_cannot_change_authority_or_load_remote_images(self):
        self.store.profile('One', {'status': 'Drawing', 'color': '#112233'})
        self.assertEqual({}, self.store.profile('Two'))
        for fields in ({'permissions':'full'}, {'avatar':'https://example.com/tracker.png'}, {'avatar':'data:image/svg+xml;base64,AAAA'}, {'color':'red; background:url(x)'}):
            with self.assertRaises(IAMError): self.store.profile('One', fields)

    def test_lifecycle_is_opt_in_and_stops_only_companion(self):
        import argparse
        import iam_orchestrator as orchestrator
        with patch.object(orchestrator,'registered_projects',return_value={'One':{}}), patch.object(orchestrator,'ensure_appserver'), patch.object(orchestrator,'ensure_daemon'), patch.object(desktop,'enabled',return_value=False), patch.object(desktop,'start_desktop') as start:
            orchestrator.cmd_start(argparse.Namespace(url='ws://127.0.0.1:4500'))
            start.assert_not_called()
        with patch.object(orchestrator,'registered_projects',return_value={'One':{}}), patch.object(orchestrator,'ensure_appserver'), patch.object(orchestrator,'ensure_daemon'), patch.object(desktop,'enabled',return_value=True), patch.object(desktop,'start_desktop',return_value='started') as start:
            orchestrator.cmd_start(argparse.Namespace(url='ws://127.0.0.1:4500'))
            start.assert_called_once()
        with patch.object(desktop,'control',return_value=True) as control, patch.object(orchestrator,'read_pid',return_value=None), patch.object(orchestrator,'stop_process') as stop:
            orchestrator.cmd_stop(argparse.Namespace(all=False))
            control.assert_called_once_with('stop')
            stop.assert_not_called()


class DesktopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_replaces_context_until_completion_and_fresh_report(self):
        from iam_codex_bridge import AppServerClient
        client = AppServerClient(url='ws://localhost:4500')
        client._desktop_usage_callback = lambda message: self.backend.token_event('One', 'thread', message)
        self.backend.clients['One'] = (client, 'thread')
        start = {'method':'item/started','params':{'threadId':'thread','turnId':'turn','item':{'id':'compact','type':'contextCompaction','text':'PRIVATE'}}}
        with patch.object(desktop,'session_owner',return_value='One'):
            await client._handle_message(start)
            self.assertEqual({'state':'compacting'}, self.backend.context_snapshot('One'))
            token = {'method':'thread/tokenUsage/updated','params':{'threadId':'thread','tokenUsage':{'modelContextWindow':1000,'last':{'totalTokens':100}}}}
            await client._handle_message(token)
            self.assertEqual({'state':'compacting'}, self.backend.context_snapshot('One'))
            await client._handle_message({**start,'method':'item/completed'})
            self.assertIsNone(self.backend.context_snapshot('One'))
            await client._handle_message(token)
            self.assertEqual(90, self.backend.context_snapshot('One')['remainingPercent'])
            await client._handle_message(start)
            client.closed.set()
            self.assertIsNone(self.backend.context_snapshot('One'))
        self.assertNotIn('PRIVATE', json.dumps(self.backend.compactions))

    async def test_context_uses_last_usage_not_lifetime_and_filters_thread(self):
        event = {'params': {'threadId': 't', 'tokenUsage': {'modelContextWindow': 1000,
                  'last': {'totalTokens': 250}, 'total': {'totalTokens': 999999}}}}
        self.backend.token_event('One', 't', event)
        self.assertEqual(75, self.backend.context_usage['One']['remainingPercent'])
        self.backend.token_event('Two', 'different', event)
        self.assertNotIn('Two', self.backend.context_usage)
        self.backend.token_event('One', 't', {'params': {'threadId': 't', 'tokenUsage': {}}})
        self.assertNotIn('One', self.backend.context_usage)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = desktop.DesktopStore(Path(self.temp.name) / 'desktop.sqlite3')
        factory = patch.object(desktop, 'DesktopStore', return_value=self.store)
        factory.start(); self.addCleanup(factory.stop)
        self.backend = desktop.DesktopBackend()

    async def test_snapshot_has_no_approval_details_or_messages(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/commandExecution/requestApproval',?,0,'pending',NULL)", (json.dumps({'command':'PRIVATE'}),))
            db.execute("INSERT INTO approval_leases VALUES('a',?)", (time.time()+30,))
        with patch.object(self.backend, 'roster_config', return_value={'projects': {'One':{}}}), patch.object(desktop, 'session_owner', side_effect=lambda x, config=None:x), patch.object(desktop.iam,'display_name',return_value='Friend'):
            result = await self.backend.dispatch('snapshot', {})
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual('PRIVATE', (await self.backend.dispatch('approval', {'id':'a'}))['details']['command'])

    async def test_completion_callback_records_exact_turn_without_output(self):
        request = str(uuid.uuid4())
        self.store.create('One', 'Hello', request)
        self.store.change(request, 'accepted', thread_id='thread', turn_id='turn')
        client = MagicMock()
        self.backend.adopt_client('One', 'thread', client, {})
        client._desktop_status_callback({'method': 'turn/completed', 'params': {
            'threadId': 'other', 'turn': {'id': 'turn'}}})
        self.assertIsNone(self.store.history('One')[0]['error'])
        client._desktop_status_callback({'method': 'turn/completed', 'params': {
            'threadId': 'thread', 'turn': {'id': 'turn', 'items': [{'text': 'PRIVATE OUTPUT'}]}}})
        history = self.store.history('One')
        self.assertEqual('Turn finished; no reply published.', history[0]['error'])
        self.assertNotIn('PRIVATE OUTPUT', json.dumps(history))

    async def test_approval_accept_missing_details_fails_closed(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/fileChange/requestApproval','{}',0,'pending',NULL)")
            db.execute("INSERT INTO approval_leases VALUES('a',?)", (time.time()+30,))
        with self.assertRaises(IAMError): await self.backend.dispatch('decide', {'id':'a','decision':'accept'})
        await self.backend.dispatch('decide', {'id':'a','decision':'decline'})
        with self.assertRaises(IAMError): await self.backend.dispatch('decide', {'id':'a','decision':'accept'})

    async def test_open_never_creates_a_replacement_thread(self):
        with patch.object(self.backend,'projects',return_value={'One':{'project_root':self.temp.name}}), patch.object(desktop,'session_owner',return_value='One'), patch('iam_codex_bridge.DeliveryState.load',return_value=MagicMock(thread_id=None)), patch('iam_orchestrator.appserver_client') as factory:
            with self.assertRaises(IAMError): await self.backend.open('One')
            factory.assert_not_called()

    async def test_transport_does_not_publish_any_agent_output(self):
        from iam_codex_bridge import AppServerClient
        client=AppServerClient(url='ws://localhost:4500')
        for kind in ('item/agentMessage/delta','item/reasoning/textDelta','item/commandExecution/outputDelta','turn/completed'):
            await client._handle_message({'method':kind,'params':{'delta':'PRIVATE'}})
        with self.store.connect() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM requests').fetchone()[0])

    async def test_open_restores_registered_policy_after_appserver_restart(self):
        for actual in ('dangerFullAccess', 'workspaceWrite'):
            client = MagicMock(closed=asyncio.Event(), connect=AsyncMock(), close=AsyncMock())
            thread = {'id': 'thread', 'cwd': self.temp.name, 'status': {'type': 'idle'}}
            responses = [{'thread': thread}, {'thread': thread,
                'approvalPolicy': 'never', 'sandbox': {'type': actual}, 'activePermissionProfile': None}]
            if actual != 'dangerFullAccess':
                responses.extend([{}, {'thread': thread, 'approvalPolicy': 'never',
                    'sandbox': {'type': 'dangerFullAccess'}, 'activePermissionProfile': None}])
            client.request = AsyncMock(side_effect=responses)
            self.backend.clients.clear()
            with patch.object(self.backend, 'projects', return_value={'One': {'project_root': self.temp.name,
                    'sandbox': 'danger-full-access', 'approval_policy': 'never'}}), \
                    patch.object(desktop, 'session_owner', return_value='One'), \
                    patch('iam_codex_bridge.DeliveryState.load', return_value=MagicMock(thread_id='thread')), \
                    patch('iam_orchestrator.configured_endpoint', return_value='ws://127.0.0.1:4500'), \
                    patch('iam_orchestrator.appserver_client', return_value=client):
                self.assertEqual({'state': 'attached'}, await self.backend.open('One'))
                self.assertEqual({'threadId': 'thread', 'excludeTurns': True}, client.request.call_args_list[1].args[1])
                methods = [call.args[0] for call in client.request.call_args_list]
                self.assertEqual(actual != 'dangerFullAccess', 'thread/settings/update' in methods)

    async def test_disabled_desktop_never_takes_approvals(self):
        with patch.object(desktop,'enabled',return_value=False):
            self.assertFalse(await desktop.offer_approval(MagicMock(),{'method':'item/commandExecution/requestApproval'}))

    async def test_delivery_targets_exact_session_and_uncertainty_never_replays(self):
        request_id=str(uuid.uuid4())
        self.store.create('One','Human conversation',request_id)
        row={'id':request_id,'mailbox':'One','body':'Human conversation'}
        client=MagicMock()
        client.request=AsyncMock(side_effect=[{'thread':{'status':{'type':'idle'}}}, TimeoutError('PRIVATE transport diagnostic')])
        self.backend.clients['One']=(client,'exact-thread')
        with patch.object(desktop,'session_owner',return_value='One'), patch.object(self.backend,'open',new=AsyncMock(return_value={'state':'attached'})), patch.object(self.backend,'projects',return_value={'One':{'project_root':self.temp.name,'approval_policy':'on-request','sandbox':'workspace-write'}}):
            await self.backend.deliver_once(row)
            await self.backend.deliver_once(row)
        calls=client.request.call_args_list
        self.assertEqual(2,len(calls))
        self.assertEqual('exact-thread',calls[1].args[1]['threadId'])
        self.assertEqual('on-request',calls[1].args[1]['approvalPolicy'])
        history=self.store.history('One')
        self.assertEqual('held',history[0]['state'])
        self.assertNotIn('PRIVATE',json.dumps(history))

    async def test_busy_session_queues_without_steering(self):
        request_id=str(uuid.uuid4())
        self.store.create('One','Hello',request_id)
        with patch.object(desktop,'session_owner',return_value='One'), patch.object(self.backend,'open',new=AsyncMock(return_value={'state':'busy'})):
            await self.backend.deliver_once({'id':request_id,'mailbox':'One','body':'Hello'})
        self.assertEqual('queued',self.store.history('One')[0]['state'])

    async def test_image_sent_as_local_image_to_exact_thread(self):
        rid = str(uuid.uuid4())
        images = [{'name':'test.png','data':'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]
        self.store.create('One', '', rid, images)
        client = MagicMock()
        client.request = AsyncMock(side_effect=[{'thread':{'status':{'type':'idle'}}}, {'turn':{'id':'turn'}}])
        self.backend.clients['One'] = (client, 'exact-thread')
        with patch.object(desktop,'session_owner',return_value='One'), patch.object(self.backend,'open',new=AsyncMock(return_value={'state':'attached'})), patch.object(self.backend,'projects',return_value={'One':{'project_root':self.temp.name}}):
            await self.backend.deliver_once({'id':rid,'mailbox':'One','body':''})
        params = client.request.call_args_list[1].args[1]
        self.assertEqual('exact-thread', params['threadId'])
        self.assertEqual('localImage', params['input'][1]['type'])
        self.assertIn('actual newline characters', params['input'][0]['text'])
        self.assertEqual('accepted', self.store.history('One')[0]['state'])

    async def test_approval_is_not_sent_until_human_decides(self):
        client=MagicMock(closed=asyncio.Event(),_write=AsyncMock())
        client._desktop_approval_tasks=set()
        client._desktop_resolved=set()
        client._write.side_effect = lambda message: client._desktop_resolved.add(message['id'])
        with patch.object(desktop,'enabled',return_value=True), patch.object(desktop.iam,'load',return_value={'projects':{'One':{}}}), patch.object(desktop,'session_owner',return_value='One'), patch('iam_codex_bridge.DeliveryState.load',return_value=MagicMock(thread_id='thread')):
            accepted=await desktop.offer_approval(client,{'id':7,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread','turnId':'turn','command':'echo harmless'}})
        self.assertTrue(accepted)
        client._write.assert_not_awaited()
        with self.store.connect() as db: approval_id=db.execute('SELECT id FROM approvals').fetchone()[0]
        await self.backend.dispatch('decide',{'id':approval_id,'decision':'decline'})
        await asyncio.gather(*client._desktop_approval_tasks)
        client._write.assert_awaited_once_with({'id':7,'result':{'decision':'decline'}})

    async def test_expired_approval_cannot_be_accepted(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO approvals VALUES('a','One','item/commandExecution/requestApproval','{}',0,'pending',NULL)")
            db.execute("INSERT INTO approval_leases VALUES('a',0)")
        with self.assertRaises(IAMError): await self.backend.dispatch('decide',{'id':'a','decision':'accept'})

    async def test_mcp_confirmation_waits_for_human_and_uses_mcp_response(self):
        client = MagicMock(closed=asyncio.Event(), _write=AsyncMock())
        client._desktop_approval_tasks = set()
        client._desktop_resolved = set()
        client._write.side_effect = lambda message: client._desktop_resolved.add(message['id'])
        message = {'id': 8, 'method': 'mcpServer/elicitation/request', 'params': {
            'threadId': 'thread', 'serverName': 'interagentmail', 'mode': 'form', 'message': 'Allow profile update?',
            'requestedSchema': {'type': 'object', 'properties': {'approve': {'type': 'boolean'}}, 'required': ['approve']}}}
        with patch.object(desktop, 'enabled', return_value=True), patch.object(desktop.iam, 'load', return_value={'projects': {'One': {}}}), patch.object(desktop, 'session_owner', return_value='One'), patch('iam_codex_bridge.DeliveryState.load', return_value=MagicMock(thread_id='thread')):
            self.assertTrue(await desktop.offer_approval(client, message))
        client._write.assert_not_awaited()
        with self.store.connect() as db: approval_id = db.execute('SELECT id FROM approvals').fetchone()[0]
        with self.assertRaises(IAMError):
            await self.backend.dispatch('decide', {'id': approval_id, 'decision': 'accept', 'content': {}})
        await self.backend.dispatch('decide', {'id': approval_id, 'decision': 'accept', 'content': {'approve': True}})
        await asyncio.gather(*client._desktop_approval_tasks)
        client._write.assert_awaited_once_with({'id': 8, 'result': {'action': 'accept', 'content': {'approve': True}}})

    async def test_unsupported_mcp_creates_notice_not_approval(self):
        client = MagicMock(_write=AsyncMock())
        with patch.object(desktop, 'enabled', return_value=True), patch.object(desktop.iam, 'load', return_value={'projects': {'One': {}}}), patch.object(desktop, 'session_owner', return_value='One'), patch('iam_codex_bridge.DeliveryState.load', return_value=MagicMock(thread_id='thread')):
            self.assertFalse(await desktop.offer_approval(client, {'id': 9, 'method': 'mcpServer/elicitation/request',
                'params': {'threadId': 'thread', 'mode': 'url', 'url': 'https://secret.invalid/token'}}))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM approvals').fetchone()[0], 0)
            notice = db.execute('SELECT kind,body FROM notices').fetchone()
            self.assertEqual(notice['kind'], 'infrastructure')
            self.assertNotIn('secret', notice['body'])
        client._write.assert_not_awaited()
