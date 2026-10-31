import concurrent.futures
import json
import tempfile
import threading
import unittest
import contextlib
import io
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from iam_hooks import Broker, HookError, HookServer, utc


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1800000000.
        self.broker = Broker(Path(self.temp.name) / 'hooks', clock=lambda: self.now)
        self.producer = self.broker.create_producer('adapter')
        self.policy = {'schema_version': '1.0', 'runtime_id': 'worker',
                       'capabilities': ['conversation.read', 'conversation.reply'],
                       'isolation': 'Test fixture only; no actual runtime approved', 'public_docs': []}
        self.consumer, self.policy_hash = self.broker.create_target('worker', self.policy)
        self.broker.enable('producer', 'adapter', True)
        self.broker.enable('consumer', 'consumer-worker', True)
        self.broker.enable('target', 'worker', True, reviewed=True)
        self.broker.grant('adapter', 'worker', 'conversation.activity', True)

    def tearDown(self):
        self.temp.cleanup()

    def event(self, name='event-1'):
        return {'schema_version': '1.0', 'event_id': name, 'target': 'worker',
                'type': 'conversation.activity', 'occurred_at': utc(self.now),
                'expires_at': utc(self.now + 300), 'refs': {'stream_id': 'room-1', 'cursor': 'c-1'}}

    def error(self, code, call, *args):
        with self.assertRaises(HookError) as caught:
            call(*args)
        self.assertEqual(code, caught.exception.code)

    def test_submit_dedup_conflict_and_restart(self):
        event = self.event()
        first = self.broker.submit(self.producer, event)
        self.assertEqual(first, self.broker.submit(self.producer, event))
        event['refs']['cursor'] = 'c-2'
        self.error('event_id_conflict', self.broker.submit, self.producer, event)
        restarted = Broker(self.broker.directory, clock=lambda: self.now)
        self.assertEqual(first, restarted.status(self.producer, 'event-1'))

    def test_default_disabled_and_exact_grants(self):
        token = self.broker.create_producer('other')
        self.error('unauthorized', self.broker.submit, token, self.event())
        self.broker.enable('producer', 'other', True)
        self.error('event_not_authorized', self.broker.submit, token, self.event())
        self.error('invalid_event_type', self.broker.grant, 'other', 'worker', '*', True)
        self.error('isolation_review_required', self.broker.enable, 'target', 'worker', True)
        self.broker.grant('other', 'worker', 'conversation.activity')
        self.error('event_not_authorized', self.broker.submit, token, self.event())

    def test_role_and_cross_producer_separation(self):
        self.broker.submit(self.producer, self.event())
        other = self.broker.create_producer('other')
        self.broker.enable('producer', 'other', True)
        self.error('not_found', self.broker.status, other, 'event-1')
        self.error('unauthorized', self.broker.submit, self.consumer, self.event('event-2'))
        self.error('unauthorized', self.broker.lease, self.producer, self.policy_hash)
        self.error('unauthorized', self.broker.status, 'x' * 40, 'event-1')

    def test_lease_complete_and_lost_ack_are_idempotent(self):
        self.broker.submit(self.producer, self.event())
        lease = self.broker.lease(self.consumer, self.policy_hash)
        self.assertIsNone(self.broker.lease(self.consumer, self.policy_hash))
        args = (self.consumer, lease['delivery_id'], lease['lease_token'], 'completed')
        first = self.broker.finish(*args)
        self.assertEqual('completed', first['state'])
        self.assertEqual(first, self.broker.finish(*args))
        self.assertIsNone(self.broker.lease(self.consumer, self.policy_hash))
        self.error('invalid_lease', self.broker.finish, self.consumer, lease['delivery_id'], 'x' * 40, 'completed')

    def test_cross_target_lease_cannot_complete(self):
        other, other_hash = self.broker.create_target('other-worker', self.policy)
        self.broker.enable('target', 'other-worker', True, reviewed=True)
        self.broker.enable('consumer', 'consumer-other-worker', True)
        self.broker.submit(self.producer, self.event())
        lease = self.broker.lease(self.consumer, self.policy_hash)
        self.error('not_found', self.broker.finish, other, lease['delivery_id'], lease['lease_token'], 'completed')
        self.assertIsNone(self.broker.lease(other, other_hash))

    def test_revocation_during_lease_cancels_and_cannot_resurrect(self):
        self.broker.submit(self.producer, self.event())
        lease = self.broker.lease(self.consumer, self.policy_hash)
        self.broker.grant('adapter', 'worker', 'conversation.activity', False)
        self.error('event_not_authorized', self.broker.finish, self.consumer, lease['delivery_id'], lease['lease_token'], 'completed')
        self.assertEqual('cancelled', self.broker.status(self.producer, 'event-1')['state'])
        self.broker.grant('adapter', 'worker', 'conversation.activity', True)
        self.error('stale_lease', self.broker.finish, self.consumer, lease['delivery_id'], lease['lease_token'], 'completed')

    def test_expiry_and_redelivery_after_crash(self):
        event = self.event()
        self.broker.submit(self.producer, event)
        lease = self.broker.lease(self.consumer, self.policy_hash)
        self.now += 61
        self.broker.status(self.producer, 'event-1')
        self.now += 2
        new = self.broker.lease(self.consumer, self.policy_hash)
        self.assertNotEqual(new['lease_token'], lease['lease_token'])
        self.error('invalid_lease', self.broker.finish, self.consumer, lease['delivery_id'], lease['lease_token'], 'completed')
        self.now += 300
        self.assertEqual('expired', self.broker.status(self.producer, 'event-1')['state'])
        self.assertEqual('expired', self.broker.submit(self.producer, event)['state'])
        self.assertIsNone(self.broker.lease(self.consumer, self.policy_hash))

    def test_rotation_invalidates_credentials_and_lease(self):
        self.broker.submit(self.producer, self.event())
        lease = self.broker.lease(self.consumer, self.policy_hash)
        new = self.broker.rotate('consumer-worker')
        self.error('unauthorized', self.broker.lease, self.consumer, self.policy_hash)
        self.error('invalid_lease', self.broker.finish, new, lease['delivery_id'], lease['lease_token'], 'completed')
        self.assertIsNotNone(self.broker.lease(new, self.policy_hash))

    def test_policy_mismatch_disabled_target_and_renew(self):
        self.error('policy_mismatch', self.broker.lease, self.consumer, '0' * 64)
        self.broker.submit(self.producer, self.event())
        lease = self.broker.lease(self.consumer, self.policy_hash)
        self.now += 30
        renewed = self.broker.finish(self.consumer, lease['delivery_id'], lease['lease_token'])
        self.assertEqual(utc(self.now + 60), renewed['lease_expires_at'])
        self.broker.enable('target', 'worker', False)
        self.error('target_unavailable', self.broker.lease, self.consumer, self.policy_hash)
        self.error('event_not_authorized', self.broker.submit, self.producer, self.event('new'))

    def test_unknown_fields_and_opaque_ids(self):
        for key in ('prompt', 'session_id', 'workspace', 'url', 'permissions', 'priority'):
            event = self.event(); event[key] = 'value'
            self.error('invalid_fields', self.broker.submit, self.producer, event)
        for value in ('../secret', 'http://example.test', 'a\nb', 'x' * 129, '', 42):
            event = self.event(); event['refs']['cursor'] = value
            self.error('invalid_identifier', self.broker.submit, self.producer, event)
        event = self.event(); event['expires_at'] = utc(self.now + 901)
        self.error('invalid_expiry', self.broker.submit, self.producer, event)
        event = self.event(); event['occurred_at'] = utc(self.now + 100)
        self.error('invalid_expiry', self.broker.submit, self.producer, event)

    def test_rate_limit_and_target_queue_limit(self):
        for i in range(10):
            self.broker.submit(self.producer, self.event('e' + str(i)))
        self.error('rate_limited', self.broker.submit, self.producer, self.event('limited'))
        for i in range(10, 100):
            self.now += 2
            self.broker.submit(self.producer, self.event('e' + str(i)))
        self.now += 2
        self.error('queue_full', self.broker.submit, self.producer, self.event('full'))

    def test_parallel_submission_has_one_receipt(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.broker.submit(self.producer, self.event()), range(10)))
        self.assertEqual(1, len({x['receipt_id'] for x in results}))

    def test_retry_budget_exhaustion_is_terminal(self):
        event = self.event(); event['expires_at'] = utc(self.now + 900)
        self.broker.submit(self.producer, event)
        for attempt in range(5):
            lease = self.broker.lease(self.consumer, self.policy_hash)
            self.assertIsNotNone(lease)
            result = self.broker.finish(self.consumer, lease['delivery_id'], lease['lease_token'], 'retry')
            self.now += 40
        self.assertEqual('failed', result['state'])
        self.assertIsNone(self.broker.lease(self.consumer, self.policy_hash))

    def test_principal_revocation_and_no_target_fallback(self):
        self.broker.submit(self.producer, self.event())
        self.broker.enable('producer', 'adapter', False)
        self.error('unauthorized', self.broker.status, self.producer, 'event-1')
        self.assertIsNone(self.broker.lease(self.consumer, self.policy_hash))
        self.broker.enable('producer', 'adapter', True)
        self.assertEqual('cancelled', self.broker.status(self.producer, 'event-1')['state'])
        event = self.event('unknown'); event['target'] = 'missing'
        self.error('event_not_authorized', self.broker.submit, self.producer, event)
        self.broker.enable('consumer', 'consumer-worker', False)
        self.error('unauthorized', self.broker.lease, self.consumer, self.policy_hash)

    def test_cli_secret_file_and_disabled_defaults(self):
        from iam_orchestrator import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(['hooks', '--state-dir', str(self.broker.directory), 'producer-create', 'cli-producer'])
        self.assertEqual(0, result)
        response = json.loads(out.getvalue())
        token = Path(response['token_file']).read_text().strip()
        self.assertNotIn(token, out.getvalue())
        self.assertFalse(response['enabled'])
        self.assertNotIn(token.encode(), self.broker.path.read_bytes())
        self.error('unauthorized', self.broker.submit, token, self.event())

    def test_isolation_policy_does_not_allow_extra_tools(self):
        for value in (['shell'], ['iam.mail'], ['admin'], [{}]):
            policy = dict(self.policy, capabilities=value)
            self.error('invalid_capabilities', self.broker.create_target, 'bad', policy)

    def test_http_contract_and_guards(self):
        server = HookServer(self.broker, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            root = f'http://127.0.0.1:{server.server_address[1]}'
            def request(path, body=None, token=None, headers=None):
                raw = json.dumps(body).encode() if body is not None else None
                req = Request(root + path, data=raw, headers={'Authorization': 'Bearer ' + (token or self.producer), 'Content-Type': 'application/json', **(headers or {})})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            self.assertEqual(202, request('/v1/events', self.event())[0])
            self.assertEqual(200, request('/v1/events/event-1')[0])
            self.assertEqual(403, request('/v1/events/event-1', headers={'Origin': 'https://evil.test'})[0])
            self.assertEqual(421, request('/v1/events/event-1', headers={'Host': 'evil.test'})[0])
            self.assertEqual(400, request('/v1/events?token=bad', self.event())[0])
            self.assertEqual(401, request('/v1/events/event-1', token='wrong')[0])
            self.assertEqual(413, request('/v1/events', {'body': 'x' * 9000})[0])
            duplicate = Request(root + '/v1/events', data=b'{"target":"one","target":"two"}', headers={'Authorization': 'Bearer ' + self.producer, 'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(duplicate, timeout=3)
            self.assertEqual(400, caught.exception.code)
            self.assertEqual(400, request('/v1/consumer/leases', {'policy_sha256': self.policy_hash, 'wait_seconds': True}, self.consumer)[0])
            code, response = request('/v1/consumer/leases', {'policy_sha256': self.policy_hash}, self.consumer)
            self.assertEqual(200, code)
            lease = response['data']
            code, result = request('/v1/consumer/leases/' + lease['delivery_id'] + '/complete', {'lease_token': lease['lease_token'], 'outcome': 'completed'}, self.consumer)
            self.assertEqual('completed', result['data']['state'])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
