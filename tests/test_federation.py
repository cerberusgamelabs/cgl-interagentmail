import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from iam_federation import MailQueue, Session, FederationError


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = dict(enabled=True, node_id='b', server_url='wss://example.invalid',
            federation=dict(enabled=True, owner_account_id='owner', epoch='b1', peers=['a'], mailboxes=['Bea']))
        self.queue = MailQueue(self.root/'queue.db', self.config, ['Bea'])
        self.queue.set_roster('owner', [dict(node='a', mailbox='Alice')])
        self.message = dict(id='one', source='a', target='b', to='Bea', **{'from':'Alice'},
            body='hello', subject='test', scope=dict(owner='owner', targetEpoch='b1'))

    def test_disabled_and_unregistered_scope_rejected(self):
        for change in [dict(enabled=False), dict(mailboxes=['Unknown']), dict(peers=['b'])]:
            config=copy.deepcopy(self.config);config['federation'].update(change)
            with self.assertRaises(FederationError):MailQueue(self.root/'other.db', config, ['Bea'])

    def test_outbox_is_durable_and_conflicting_retry_rejected(self):
        self.queue.enqueue('Bea', 'Alice', 'test', 'hello', 'one')
        reopened=MailQueue(self.root/'queue.db', self.config, ['Bea'])
        self.assertEqual(len(reopened.outgoing()),1)
        self.assertEqual(reopened.enqueue('Bea','Alice','test','hello','one')['state'],'queued')
        with self.assertRaises(FederationError):reopened.enqueue('Bea','Alice','test','changed','one')
        reopened.receipt('one','queued')
        self.assertEqual(reopened.outgoing(),[])

    def test_duplicates_do_not_publish_twice(self):
        seen=[]
        self.assertEqual(self.queue.receive(self.message,lambda *args:seen.append(args)), 'delivered')
        self.assertEqual(self.queue.receive(self.message,lambda *args:seen.append(args)), 'delivered')
        self.assertEqual(len(seen),1)
        with self.assertRaises(FederationError):self.queue.receive(self.message|dict(body='other'),lambda *_:None)

    def test_uncertain_publication_is_held_not_replayed(self):
        def fail(*_):raise OSError('synthetic failure')
        with self.assertRaises(OSError):self.queue.receive(self.message,fail)
        self.assertEqual(self.queue.receive(self.message,lambda *_:self.fail('must not replay')), 'held')

    def test_foreign_owner_destination_peer_and_epoch_rejected(self):
        for change in [dict(source='c'),dict(target='c'),dict(to='../Bea'),dict(scope={'owner':'foreign','targetEpoch':'b1'}),dict(scope={'owner':'owner','targetEpoch':'old'})]:
            with self.assertRaises(FederationError):self.queue.receive(self.message|change,lambda *_:self.fail('published'))

    def test_directory_cannot_override_local_or_ambiguous_mailboxes(self):
        self.queue.set_roster('owner',[dict(node='a',mailbox='Bea')])
        with self.assertRaises(FederationError):self.queue.enqueue('Bea','Bea','','')
        with self.assertRaises(FederationError):self.queue.set_roster('foreign',[])

    def test_scope_change_does_not_reuse_old_outbox_or_directory(self):
        self.queue.enqueue('Bea','Alice','','hello')
        config=copy.deepcopy(self.config);config['federation']['epoch']='b2'
        other=MailQueue(self.root/'queue.db',config,['Bea'])
        self.assertEqual(other.outgoing(),[])
        self.assertEqual(other.roster(),[])

    def test_session_requires_correlated_same_owner_response(self):
        session=Session(self.queue,self.root);request=session.next_frame(now=1)
        result=dict(protocol='iam-webconnect',version=1,type='federation_result',id=request['id'],owner_account_id='foreign',result={'advertised':True})
        with self.assertRaises(FederationError):session.receive(result,now=2)
        self.assertTrue(session.blocked)

    def test_session_publishes_and_acknowledges_only_local_mail(self):
        (self.root/'mailboxes'/'Bea'/'inbox').mkdir(parents=True)
        session=Session(self.queue,self.root)
        def respond(result):
            request=session.next_frame(now=1)
            session.receive(dict(protocol='iam-webconnect',version=1,type='federation_result',
                id=request['id'],owner_account_id='owner',result=result),now=1)
            return request
        self.assertEqual(respond({'advertised':True})['operation'],'offer')
        respond({'roster':[dict(node='a',mailbox='Alice')]})
        self.assertEqual(respond({'messages':[self.message]})['operation'],'pull')
        self.assertEqual(respond({'id':'one','state':'delivered'})['operation'],'ack')
        self.assertEqual(len(list((self.root/'mailboxes'/'Bea'/'inbox').glob('*.json'))),1)

    def test_normal_mail_routes_remote_without_creating_local_recipient(self):
        import interagentmail as iam
        with patch.multiple(iam,ROOT=self.root,MAILBOXES=self.root/'mailboxes',CONFIG=self.root/'config.json'):
            iam.write_atomic(self.root/'webconnect.json',self.config)
            iam.write_atomic(iam.CONFIG,{'projects':{'Bea':{}}})
            queue=MailQueue(self.root/'run'/'federation.sqlite3',self.config,['Bea'])
            queue.set_roster('owner',[dict(node='a',mailbox='Alice')])
            iam.ensure_box('Bea')
            result=iam.send_message('Bea',['Alice'],[],'test','hello')
            self.assertEqual(result['metadata']['federation']['delivery_state'],'queued')
            self.assertFalse(iam.mailbox('Alice').exists())
            tracked=iam.send_message('Bea',['Alice'],[],'tracked','hello',delivery_key='receipt-one')
            self.assertEqual(iam.send_receipt('Bea','receipt-one')['status'],'pending')
            queue.receipt(tracked['message_id'],'delivered')
            self.assertEqual(iam.send_receipt('Bea','receipt-one')['status'],'committed')
            self.assertEqual(iam.send_receipt('Bea','unknown')['status'],'unknown')
            self.assertEqual(queue.outgoing()[0]['body'],'hello')
            self.assertEqual(queue.outgoing()[0]['thread'],result['thread'])
            with self.assertRaises(SystemExit):iam.send_message('Bea',['Alice','Bea'],[],'test','mixed')
            with self.assertRaises(SystemExit):iam.send_message('Bea',['Missing'],[],'test','unknown')

    def test_local_scope_incoming_cannot_impersonate_local_agent(self):
        message=self.message|{'from':'Bea'}
        with self.assertRaises(FederationError):self.queue.receive(message,lambda *_:self.fail('published'))


if __name__ == '__main__':unittest.main()
