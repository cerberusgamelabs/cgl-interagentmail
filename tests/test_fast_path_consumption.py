import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch
import iam_fast_path as fp


class ConsumptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = dict(integrationId='integration-1', registrationGeneration='registration-1',
                        residentId='resident-1', threadBinding='thread-1', root=str(Path.cwd()))
        self.request = dict(version=1, **{k: v for k, v in self.cfg.items() if k != 'root'},
                            deliveryKind='text', deliveryId='delivery-1', sourceId='subscription-1',
                            attemptId='attempt-1', operation='start', policyGeneration=1)
        self.current = dict(self.request, state='runtime_accepted', accelerationMode='idle_wake_only',
            committedAt='2026-10-17T12:00:00Z', dispatchBefore='2026-10-17T12:00:05Z',
            outcomeAt='2026-10-17T12:00:00Z', failureCode=None,
            runtimeAcceptedAt='2026-10-17T12:00:00Z', consumptionGraceUntil='2026-10-17T12:15:00Z',
            acceptedTurnId='turn-1', terminalTurnState=None)
        self.row = dict(key='key', attempt='attempt-1', request=json.dumps(self.request),
            candidate=json.dumps(dict(self.request, accelerationMode='idle_wake_only')),
            receipt=json.dumps(dict(kind='runtime_acceptance', id='turn-1')))
        self.store = Mock()
        self.verifier = AsyncMock()
        self.verifier.request.return_value = self.current
        self.client = AsyncMock()
        self.thread = dict(id='thread-1', cwd=self.cfg['root'], turns=[dict(id='turn-1', status='completed')])
        self.client.request.return_value = {'thread': self.thread}
        self.factory = Mock(return_value=self.client)
        self.now = fp.epoch('2026-10-17T12:16:00Z')

    async def run_check(self):
        with patch.object(fp.time, 'time', return_value=self.now):
            await fp.reconcile_consumption(self.store, self.row, self.cfg, self.factory, 'local', self.verifier)

    def reports(self):
        return [json.loads(c.kwargs['report']) for c in self.store.change.call_args_list if 'report' in c.kwargs]

    async def test_exact_completed_turn_creates_identifier_only_report(self):
        await self.run_check()
        report, = self.reports()
        self.assertEqual(set(report), set('version integrationId registrationGeneration attemptId residentId threadBinding deliveryKind deliveryId outcome receipt acceptedTurnId acceptanceReceipt terminalState failureCode'.split()))
        self.assertEqual(report['acceptedTurnId'], report['receipt']['id'])
        self.assertEqual(report['acceptanceReceipt'], json.loads(self.row['receipt']))
        self.assertEqual('runtime_accepted_unconsumed', report['outcome'])
        self.client.request.assert_awaited_once_with('thread/read', {'threadId': 'thread-1', 'includeTurns': True})
        self.verifier.request.assert_awaited_once()  # No live outcome in evidence phase.

    async def test_missing_running_or_unknown_turn_never_terminalizes(self):
        for turns in ([], [dict(id='other', status='completed')], [dict(id='turn-1', status='inProgress')], [dict(id='turn-1', status='unknown')]):
            self.thread['turns'] = turns
            await self.run_check()
        self.assertEqual([], self.reports())

    async def test_explicit_interruption_is_normalized(self):
        self.thread['turns'][0]['status'] = 'interrupted'
        await self.run_check()
        report, = self.reports()
        self.assertEqual('cancelled', report['terminalState'])
        self.assertEqual('runtime_turn_interrupted', report['failureCode'])

    async def test_grace_is_not_renewed_and_no_runtime_read_before_it(self):
        self.now = fp.epoch('2026-10-17T12:05:00Z')
        await self.run_check()
        self.factory.assert_not_called()
        self.assertEqual(fp.epoch(self.current['consumptionGraceUntil']), self.store.change.call_args.kwargs['retry_at'])

    async def test_legacy_or_missing_acceptance_timestamp_stays_held(self):
        self.current['runtimeAcceptedAt'] = self.current['consumptionGraceUntil'] = None
        await self.run_check()
        for key in fp.CONSUMPTION_FIELDS:
            self.current.pop(key)
        await self.run_check()
        self.factory.assert_not_called()

    async def test_wrong_thread_or_receipt_is_rejected(self):
        self.thread['id'] = 'other'
        with self.assertRaises(fp.FastPathError):
            await self.run_check()
        self.current['acceptedTurnId'] = 'wrong'
        with self.assertRaises(fp.FastPathError):
            await self.run_check()
        self.assertEqual([], self.reports())

    async def test_consumed_status_is_retired_without_runtime_read(self):
        self.current['state'] = 'resident_consumed'
        await self.run_check()
        self.factory.assert_not_called()
        self.assertEqual('resident_consumed', self.store.change.call_args.args[1])

    def test_unknown_keys_and_partial_extension_rejected(self):
        for value in (dict(self.current, extra=True), {k:v for k,v in self.current.items() if k != 'terminalTurnState'}):
            with self.assertRaises(fp.FastPathError):
                fp.validate_reservation(value, self.request, 'idle_wake_only')

    async def dispatch_report(self, error=None):
        await self.run_check()
        report = self.reports()[-1]
        self.store.reset_mock()
        self.row.update(state='runtime_accepted', report=fp.canonical(report))
        self.store.next.return_value = self.row
        response = dict(self.current, state='runtime_accepted_unconsumed', terminalTurnState='completed')
        self.verifier.request.side_effect = error
        self.verifier.request.return_value = response
        with patch.object(fp, 'binding', return_value=(Path.cwd(), {})), patch.object(fp, 'validate_candidate'):
            await fp.dispatch(self.store, self.cfg, self.factory, 'local', self.verifier)
        return report

    async def test_report_success_preserves_original_acceptance_receipt(self):
        report = await self.dispatch_report()
        self.assertEqual('runtime_accepted_unconsumed', self.store.change.call_args.args[1])
        self.assertIsNone(self.store.change.call_args.kwargs['report'])
        self.assertNotIn('receipt', self.store.change.call_args.kwargs)
        self.verifier.request.assert_awaited_with('outcome', report)

    async def test_retrieval_conflict_stops_polling_without_runtime_retry(self):
        await self.dispatch_report(fp.FastPathError('runtime_delivery_already_retrieved', 409))
        self.assertEqual('consumption_review', self.store.change.call_args.args[1])
        self.assertIsNone(self.store.change.call_args.kwargs['report'])
        self.assertEqual(1, self.client.request.await_count)  # Only original evidence read.

    async def test_uncertain_outcome_preserves_identical_report(self):
        original = await self.dispatch_report(TimeoutError())
        self.assertEqual(original, json.loads(self.row['report']))
        self.assertNotIn('report', self.store.change.call_args.kwargs)
        self.assertEqual('runtime_accepted', self.store.change.call_args.args[1])
        self.assertEqual(1, self.client.request.await_count)
