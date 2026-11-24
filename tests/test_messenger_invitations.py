import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from tests.test_messenger_pairing import PairingTests
from iam_messenger_review import PairingReview
from iam_messenger_access import AccessDenied
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64
from unittest.mock import patch
from iam_messenger_proof import OwnerProofs


class InvitationTests(PairingTests):
    def test_desktop_grant_is_explicit_dynamic_and_revocable(self):
        invitation=self.store.invite('home','lily','installation',set(),include_history=False,now=100,desktop_scope=True)
        token=invitation['challenge']
        self.store.claim_invitation(token,self.person,'installation',self.key,now=101)
        with self.assertRaises(AccessDenied):
            self.store.approve(token,self.person,'installation',set(),now=102,expires_at=1000,history_from=100,expected_owner_key=self.key)
        review=PairingReview(self.store)
        receipt=review.prepare_invitation(token,set(),now=102)
        self.assertTrue(receipt['desktop_scope'])
        self.assertIsNone(receipt['expires_at'])
        grant=review.confirm_invitation(receipt['ticket'],{'future'},now=103)
        with patch.object(self.store,'desktop_conversations',return_value=['future']):
            proofs=OwnerProofs(self.store)
            self.assertTrue(self.store.status(token,self.person,'installation',now=104)['desktop_scope'])
            self.assertEqual(self.store.status(token,self.person,'installation',now=104)['conversations'],['future'])
            self.assertIsNone(self.store.status(token,self.person,'installation',now=104)['expires_at'])
            proofs.scope(grant,self.person,'installation','future','history',100+50*365*86400)
            with self.assertRaises(AccessDenied):self.store.status(token,self.person,'installation',now=500)
            proofs.scope(grant,self.person,'installation','@desktop','events',500)
            self.assertEqual(proofs.scope(grant,self.person,'installation','future','send',104).history_from,100)
            proofs.scope(grant,self.person,'installation','@desktop','events',104)
            for conversation,operation,installation in [('missing','send','installation'),('@desktop','send','installation'),('future','send','other')]:
                with self.assertRaises(AccessDenied):proofs.scope(grant,self.person,installation,conversation,operation,104)
            with patch.object(self.store,'desktop_conversations',return_value=[]):
                with self.assertRaises(AccessDenied):proofs.scope(grant,self.person,'installation','future','send',104)
            self.store.revoke(grant)
            with self.assertRaises(AccessDenied):proofs.scope(grant,self.person,'installation','@desktop','events',104)

    def setUp(self):
        super().setUp()
        self.key=base64.b64encode(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        self.invite=self.store.invite('home','lily','installation',{'ophelia','sophie'},include_history=False,now=100)
        self.token=self.invite['challenge']

    def claim(self, person=None, now=101):
        return self.store.claim_invitation(self.token,person or self.person,'installation',self.key,now=now)

    def test_claim_is_not_approval_and_review_grants_exact_snapshot(self):
        self.claim();self.claim()
        self.assertEqual(self.store.status(self.token,self.person,'installation',now=102)['state'],'awaiting_local_confirmation')
        review=PairingReview(self.store)
        receipt=review.prepare_invitation(self.token,{'ophelia','sophie','future'},now=103)
        grant=review.confirm_invitation(receipt['ticket'],{'ophelia','sophie','future'},now=104)
        actual=self.store.get(grant,self.person,now=105)
        self.assertEqual(actual.conversation_ids,{'ophelia','sophie'})
        self.assertEqual(actual.history_from,100)
        self.assertEqual(self.store.status(self.token,self.person,'installation',now=105)['conversations'],['ophelia','sophie'])
        with self.assertRaises(AccessDenied): self.store.cancel_invitation(self.token)
        self.store.revoke(grant)
        with self.assertRaises(AccessDenied): self.store.status(self.token,self.person,'installation',now=106)

    def test_wrong_identity_expiry_and_cancel(self):
        for person in (replace(self.person,account_id='other'),replace(self.person,home_server='other')):
            with self.assertRaises(AccessDenied): self.claim(person)
        with self.assertRaises(AccessDenied): self.claim(now=400)
        self.claim()
        review=PairingReview(self.store)
        receipt=review.prepare_invitation(self.token,{'ophelia','sophie'},now=102)
        self.store.cancel_invitation(self.token)
        with self.assertRaises(AccessDenied): review.confirm_invitation(receipt['ticket'],{'ophelia','sophie'},now=103)
        with self.assertRaises(AccessDenied): self.claim()

    def test_competing_phone_claim_only_one_and_no_plaintext_secret(self):
        def attempt(device):
            try:self.claim(replace(self.person,device_id=device));return True
            except AccessDenied:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt,['one','two'])),1)
        self.assertNotIn(self.token.encode(),self.path.read_bytes())

    def test_removed_conversation_blocks_review(self):
        self.claim()
        with self.assertRaises(AccessDenied):PairingReview(self.store).prepare_invitation(self.token,{'ophelia'},now=102)

    def test_legacy_dialog_cannot_expand_or_change_invitation_scope(self):
        self.claim()
        with self.assertRaises(AccessDenied):
            self.store.approve(self.token,self.person,'installation',{'other'},now=102,
                expires_at=1000,history_from=0,expected_owner_key=self.key)
