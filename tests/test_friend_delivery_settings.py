import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from iam_messenger_friend_delivery import FriendQueue, FriendWorker
from iam_messenger_friends import friend_operation
from iam_service import IAMError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue=FriendQueue(Path(self.temp.name)/'queue.db')

    def event(self,identifier,conversation='friend',kind='human'):
        return dict(id=identifier,kind='message',conversation=conversation,peer={'kind':kind})

    def finish(self,events,now,scope=''):
        identifier=self.queue.claim('Agent','thread',events,now,scope)
        self.queue.accepted(identifier,identifier)
        self.queue.complete('thread',identifier)

    def choose(self,conversation='friend',limit=50,cooldown=0,paused=False,revision=0,scope=''):
        return self.queue.settings('Agent',conversation,'human',scope=scope,now=1000,
            updates=dict(daily_limit=limit,cooldown_seconds=cooldown,paused=paused,revision=revision))

    def test_defaults_and_scoped_choices(self):
        self.assertEqual(self.queue.settings('Agent','friend','human',now=1000)['daily_limit'],20)
        self.assertEqual(self.queue.settings('Agent','agent','agent',now=1000)['daily_limit'],4)
        self.choose(scope='installation-one')
        self.assertEqual(self.queue.settings('Agent','friend','human',scope='installation-one',now=1000)['daily_limit'],50)
        for mailbox,scope,conversation in [('Other','installation-one','friend'),('Agent','installation-two','friend'),('Agent','installation-one','other')]:
            self.assertEqual(self.queue.settings(mailbox,conversation,'human',scope=scope,now=1000)['revision'],0)

    def test_validation_and_revision_fail_closed(self):
        for field,value in [('daily_limit',True),('daily_limit',0),('daily_limit',1001),('daily_limit',1.5),
                            ('cooldown_seconds',-1),('cooldown_seconds',86401),('paused','false'),('revision',False)]:
            updates=dict(daily_limit=50,cooldown_seconds=0,paused=False,revision=0)
            updates[field]=value
            with self.assertRaises(IAMError):self.queue.settings('Agent','friend','human',updates=updates)
        self.choose()
        with self.assertRaises(IAMError):self.choose()
        self.assertEqual(self.queue.settings('Agent','friend','human')['revision'],1)

    def test_more_than_twenty_without_global_ceiling(self):
        self.choose(limit=25)
        for i in range(25):self.finish([self.event(str(i))],1000+i)
        status=self.queue.settings('Agent','friend','human',now=1100)
        self.assertEqual(status['used_last_24h'],25)
        self.assertEqual(status['waiting_reason'],'daily_limit')
        with self.assertRaises(IAMError):self.queue.claim('Agent','thread',[self.event('blocked')],1100)
        self.finish([self.event('unrelated','other')],1100)
        self.assertNotIn('blocked',self.queue.seen('Agent'))
        self.assertTrue(self.queue.settings('Agent','friend','human',now=1000+86400)['eligible'])

    def test_batch_counts_once_per_friend_and_cooldown_is_per_friend(self):
        self.finish([self.event('one'),self.event('two'),self.event('three','other')],1000)
        self.assertEqual(self.queue.settings('Agent','friend','human',now=1001)['used_last_24h'],1)
        self.assertEqual(self.queue.settings('Agent','other','human',now=1001)['used_last_24h'],1)
        self.assertEqual(self.queue.settings('Agent','friend','human',now=1001)['waiting_reason'],'cooldown')
        self.finish([self.event('four','third')],1001)

    def test_pause_does_not_mark_seen_and_resume_does_not_reset_usage(self):
        self.choose(paused=True)
        with self.assertRaises(IAMError):self.queue.claim('Agent','thread',[self.event('pending')],1000)
        self.assertNotIn('pending',self.queue.seen('Agent'))
        self.choose(paused=False,revision=1)
        self.finish([self.event('pending')],1000)
        self.choose(paused=True,revision=2)
        self.choose(paused=False,revision=3)
        self.assertEqual(self.queue.settings('Agent','friend','human',now=1001)['used_last_24h'],1)
        with self.assertRaises(IAMError):self.queue.claim('Agent','thread',[self.event('pending')],1001)

    def test_accepted_and_held_turns_still_block_even_with_zero_cooldown(self):
        self.choose()
        identifier=self.queue.claim('Agent','thread',[self.event('one')],1000)
        self.queue.accepted(identifier,'turn')
        self.assertFalse(self.queue.allowed('Agent',10000))
        with self.assertRaises(IAMError):self.queue.claim('Agent','thread',[self.event('two')],10000)
        self.queue.recover()
        self.choose(limit=1000,revision=1)
        self.assertFalse(self.queue.allowed('Agent',10000))

    def test_legacy_unattributed_usage_is_preserved_not_replayed(self):
        with self.queue.connect() as db:
            db.execute('INSERT INTO deliveries(id,mailbox,thread,state,ids,created) VALUES(?,?,?,?,?,?)',
                       ('old','Agent','thread','completed','["old-event"]',1000))
            db.execute('INSERT INTO seen VALUES(?,?,?)',('Agent','old-event',1000))
        self.choose(limit=1)
        status=self.queue.settings('Agent','friend','human',now=1400)
        self.assertEqual(status['legacy_unattributed_turns'],1)
        self.assertEqual(status['waiting_reason'],'daily_limit')
        self.choose(limit=50,revision=1)
        self.assertIn('old-event',self.queue.seen('Agent'))
        self.assertEqual(self.queue.settings('Agent','friend','human',now=87400)['used_last_24h'],0)

    def test_choice_after_fetch_is_rechecked_at_claim(self):
        self.assertTrue(self.queue.settings('Agent','friend','human',now=1000)['eligible'])
        self.choose(paused=True)
        with self.assertRaises(IAMError):self.queue.claim('Agent','thread',[self.event('incoming')],1000)
        self.assertEqual(self.queue.seen('Agent'),set())


class LocalOperationTests(unittest.TestCase):
    def test_active_own_roster_only_and_settings_never_sent_to_relay(self):
        identifier='959afcd2-7297-4f7e-9d84-6927773230d3'
        config=dict(enabled=True,node_id='node',node_token='test-only',server_url='wss://relay.example',
                    messenger={'friendships_enabled':True})
        for state in ['active','revoked','absent']:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp:
                queue=FriendQueue(Path(temp)/'queue.db')
                roster={'relationships':[] if state=='absent' else [{'id':identifier,'state':state,'peer':{'kind':'human'}}]}
                response=io.BytesIO(json.dumps(roster).encode())
                with patch('iam_webconnect.load_config',return_value=config), patch('iam_desktop.DesktopStore'), \
                     patch('iam_messenger_consent.ExternalContactReview') as review, \
                     patch('iam_messenger_friends.build_opener') as opener, \
                     patch('iam_messenger_friend_delivery.FriendQueue',return_value=queue):
                    review.return_value.status.return_value={'permitted':True,'revision':1}
                    review.return_value.scope.return_value=('scope','owner')
                    opener.return_value.open.return_value=response
                    value=dict(id=identifier,daily_limit=100,cooldown_seconds=0,paused=False,revision=0)
                    if state=='active':
                        result=friend_operation(SimpleNamespace(address='Agent'),'delivery_settings',value)
                        self.assertEqual(result['daily_limit'],100)
                    else:
                        with self.assertRaises(IAMError):friend_operation(SimpleNamespace(address='Agent'),'delivery_settings',value)
                    sent=json.loads(opener.return_value.open.call_args.args[0].data)
                    self.assertEqual(sent['operation'],'roster')
                    self.assertEqual(sent['value'],{})
                    self.assertEqual(queue.settings('Other',identifier,'human',scope='scope')['revision'],0)

    def test_owner_disabled_never_reads_roster_or_writes_settings(self):
        with patch('iam_webconnect.load_config',return_value={'enabled':True,'messenger':{'friendships_enabled':True}}), \
             patch('iam_desktop.DesktopStore'),patch('iam_messenger_consent.ExternalContactReview') as review, \
             patch('iam_messenger_friends.build_opener') as opener,patch('iam_messenger_friend_delivery.FriendQueue') as queue:
            review.return_value.status.return_value={'permitted':False}
            with self.assertRaises(IAMError):friend_operation(SimpleNamespace(address='Agent'),'delivery_settings',{'id':'any'})
            opener.assert_not_called();queue.assert_not_called()


class DeferredSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_excluded_empty_inbox_keeps_signal_until_allowance_returns(self):
        with tempfile.TemporaryDirectory() as temp:
            queue=FriendQueue(Path(temp)/'queue.db');queue.signal('Agent')
            queue.settings('Agent','friend','human',updates=dict(daily_limit=20,cooldown_seconds=300,paused=True,revision=0))
            backend=SimpleNamespace(store=SimpleNamespace(path=Path(temp)/'desktop.db'),locks={},projects=lambda:{'Agent':{}})
            worker=FriendWorker(backend,queue);worker.permitted=lambda _:True
            async def call(mailbox,operation,value):
                if operation=='roster':return {'relationships':[dict(id='friend',state='active',peer={'kind':'human'})]}
                self.assertEqual(value['exclude'],['friend'])
                return {'events':[]}
            worker.call=call
            from contextlib import contextmanager
            @contextmanager
            def guard(*args):yield True
            with patch('iam_messenger_friend_delivery.session_owner',return_value='Agent'), \
                 patch('iam_messenger_friend_delivery.attention_allowed',return_value=True),patch('iam_orchestrator.session_guard',guard):
                await worker.deliver(queue.pending(0)[0])
            self.assertEqual(len(queue.pending(10**12)),1)
            self.assertEqual(queue.seen('Agent'),set())
