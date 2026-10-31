import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from iam_desktop import DesktopStore, DesktopBackend
from iam_messenger_consent import ExternalContactReview, DISCLOSURE_VERSION
from iam_service import IAMError


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DesktopStore(Path(self.temp.name) / 'desktop.db')
        self.review = ExternalContactReview(self.store)
        self.config = dict(node_id='installation', messenger=dict(enabled=True, home_server='relay', owner_account_id='owner'))

    def prepare(self, mode='owner-enabled'):
        return self.review.prepare('Agent', self.config, mode, now=100)['ticket']

    def confirm(self, ticket, **kwargs):
        return self.review.confirm('Agent', self.config, ticket,
            **dict(dict(acknowledged=True, version=DISCLOSURE_VERSION, now=101), **kwargs))

    def test_default_disabled_and_both_enable_modes_require_real_boolean(self):
        self.assertFalse(self.review.status('Agent', self.config)['permitted'])
        for mode in ('owner-enabled', 'agent-choice'):
            for ack in (False, None, 'true', 1):
                ticket = self.prepare(mode)
                with self.assertRaises(IAMError): self.confirm(ticket, acknowledged=ack)
                with self.assertRaises(IAMError): self.confirm(ticket)
        self.assertFalse(self.review.status('Agent', self.config)['permitted'])

    def test_persisted_scope_version_and_explicit_permission_not_agent_acceptance(self):
        current = self.confirm(self.prepare())
        self.assertTrue(current['permitted'])
        self.assertFalse(current['agent_opted_in'])
        self.assertEqual(current['owner'], 'owner')
        self.assertEqual(current['decided_at'], 101)
        self.assertEqual(ExternalContactReview(self.store).status('Agent', self.config), current)
        current = self.confirm(self.prepare('agent-choice'))
        self.assertFalse(current['permitted'])
        self.assertFalse(current['agent_opted_in'])

    def test_owner_or_installation_or_home_change_disables_permission(self):
        self.confirm(self.prepare())
        for field in ('node_id', 'owner_account_id', 'home_server'):
            changed = copy.deepcopy(self.config)
            if field == 'node_id': changed[field] = 'other'
            else: changed['messenger'][field] = 'other'
            self.assertFalse(self.review.status('Agent', changed)['permitted'])
            self.assertTrue(self.review.status('Agent', changed)['needs_review'])
            with self.assertRaises(IAMError):
                self.review.confirm('Agent', changed, self.prepare(), acknowledged=True, version=DISCLOSURE_VERSION, now=101)

    def test_stale_revision_cannot_reenable_after_disable(self):
        ticket = self.prepare()
        self.review.disable('Agent', self.config, now=101)
        with self.assertRaises(IAMError): self.confirm(ticket)
        self.assertFalse(self.review.status('Agent', self.config)['permitted'])

    def test_expiry_wrong_mailbox_wrong_version_and_restart_fail_closed(self):
        with self.assertRaises(IAMError): self.confirm(self.prepare(), now=160)
        with self.assertRaises(IAMError): self.confirm(self.prepare(), version='obsolete')
        with self.assertRaises(IAMError):
            self.review.confirm('Other', self.config, self.prepare(), acknowledged=True, version=DISCLOSURE_VERSION, now=101)
        ticket = self.prepare()
        with self.assertRaises(IAMError):
            ExternalContactReview(self.store).confirm('Agent', self.config, ticket, acknowledged=True, version=DISCLOSURE_VERSION, now=101)
        self.confirm(self.prepare())
        with patch('iam_messenger_consent.DISCLOSURE_VERSION', 'new-version'):
            self.assertFalse(self.review.status('Agent', self.config)['permitted'])

    def test_review_capacity_and_config_required(self):
        for _ in range(20): self.prepare()
        with self.assertRaises(IAMError): self.prepare()
        self.assertFalse(self.review.disable('Agent', self.config, now=102)['permitted'])
        with self.assertRaises(IAMError): self.review.status('Agent', {})

    def test_agent_choice_never_creates_owner_permission_or_revives_a_stale_scope(self):
        with self.assertRaises(IAMError):
            self.review.choose('Agent', self.config, opted_in=True, revision=0)
        direct=self.confirm(self.prepare())
        with self.assertRaises(IAMError):
            self.review.choose('Agent', self.config, opted_in=True, revision=direct['revision'])
        permitted=self.confirm(self.prepare('agent-choice'))
        selected=self.review.choose('Agent', self.config, opted_in=True, revision=permitted['revision'])
        self.assertTrue(selected['permitted'])
        self.assertEqual(selected['decided_at'], permitted['decided_at'])
        self.assertEqual(selected['disclosure_version'], permitted['disclosure_version'])
        with self.assertRaises(IAMError):
            self.review.choose('Other', self.config, opted_in=True, revision=selected['revision'])
        withdrawn=self.review.choose('Agent', self.config, opted_in=False, revision=selected['revision'])
        self.assertFalse(withdrawn['permitted'])
        self.review.disable('Agent', self.config, now=102)
        with self.assertRaises(IAMError):
            self.review.choose('Agent', self.config, opted_in=True, revision=withdrawn['revision'])

    def test_choice_revision_and_real_boolean_and_owner_review_are_not_interchangeable(self):
        permitted=self.confirm(self.prepare('agent-choice'))
        ticket=self.prepare('owner-enabled')
        for bad in (1,'true',None):
            with self.assertRaises(IAMError):
                self.review.choose('Agent', self.config, opted_in=bad, revision=permitted['revision'])
        changed=copy.deepcopy(self.config);changed['node_id']='replacement'
        with self.assertRaises(IAMError):
            self.review.choose('Agent', changed, opted_in=True, revision=permitted['revision'])
        self.review.choose('Agent', self.config, opted_in=True, revision=permitted['revision'])
        with self.assertRaises(IAMError): self.confirm(ticket)


class ConsentDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_tool_uses_bound_identity_and_has_no_owner_enable_operation(self):
        from iam_mcp_server import iam_friend_policy
        with tempfile.TemporaryDirectory() as temp:
            store=DesktopStore(Path(temp) / 'desktop.db')
            config=dict(node_id='installation',messenger=dict(enabled=True,home_server='relay',owner_account_id='owner'))
            review=ExternalContactReview(store)
            ticket=review.prepare('Agent',config,'agent-choice',now=1)['ticket']
            allowed=review.confirm('Agent',config,ticket,acknowledged=True,version=DISCLOSURE_VERSION,now=2)
            with patch('iam_mcp_server.service',return_value=SimpleNamespace(address='Agent')) as bound, \
                    patch('iam_desktop.DesktopStore',return_value=store), patch('iam_webconnect.load_config',return_value=config):
                result=iam_friend_policy(opted_in=True,revision=allowed['revision'],mailbox='Agent')
                bound.assert_called_once_with('Agent')
                self.assertTrue(result['permitted'])
                review.disable('Agent',config,now=3)
                with self.assertRaises(IAMError):
                    iam_friend_policy(opted_in=True,revision=result['revision'],mailbox='Agent')

    async def test_production_dispatch_rejects_unknown_agent_and_uses_config_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = SimpleNamespace(store=DesktopStore(Path(temp) / 'desktop.db'), projects=lambda: {'Agent': {}})
            config = dict(node_id='installation', messenger=dict(enabled=True, home_server='relay', owner_account_id='owner'))
            with patch('iam_webconnect.load_config', return_value=config):
                async def call(operation, **extra):
                    return await DesktopBackend.dispatch(backend, 'external-contact-review', dict(mailbox='Agent', operation=operation, **extra))
                with self.assertRaises(IAMError):
                    await DesktopBackend.dispatch(backend, 'external-contact-review', dict(mailbox='Other', operation='inspect'))
                result = await call('prepare', mode='owner-enabled', owner='forged-owner')
                saved = await call('confirm', ticket=result['ticket'], acknowledged=True, disclosure_version=DISCLOSURE_VERSION)
                self.assertEqual(saved['owner'], 'owner')
                self.assertTrue(saved['permitted'])
                self.assertFalse((await call('disable'))['permitted'])
