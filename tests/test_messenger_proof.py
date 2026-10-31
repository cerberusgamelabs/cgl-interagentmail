import base64
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from iam_messenger_access import AccessDenied, Principal
from iam_messenger_pairing import Pairings
from iam_messenger_proof import OwnerProofs
from iam_messenger_review import PairingReview


class ProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'pairings.db'
        self.store = Pairings(self.path)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.encoded = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode('ascii')
        self.person = Principal('home', 'lily', 'phone', 'messenger_device')
        challenge = self.store.request(self.person, 'node', now=100, owner_key=self.encoded)
        review = PairingReview(self.store)
        with self.assertRaises(AccessDenied):
            review.prepare(challenge, 'conversation', include_history=True, now=101)
        preview = review.prepare(challenge, 'conversation', include_history=True, owner_access=True, now=101)
        self.assertEqual(len(preview['key_fingerprint']), 64)
        grant_id = review.confirm(preview['ticket'], 'conversation', now=102)
        self.args = dict(grant_id=grant_id, principal=self.person, installation='node',
                         conversation='conversation', operation='send', content=b'{"body":"hello"}')
        self.proofs = OwnerProofs(self.store)

    def signed(self, **changes):
        proof = self.proofs.issue(**(self.args | changes), now=103)
        signature = base64.b64encode(self.key.sign(base64.b64decode(proof['payload']),
                                    ec.ECDSA(hashes.SHA256()))).decode('ascii')
        return dict(nonce=proof['nonce'], signature=signature)

    def test_valid_and_replay_after_reopen(self):
        signed = self.signed()
        result = self.proofs.consume(**self.args, **signed, now=104)
        self.assertEqual(result['authority'], 'paired_owner')
        with self.assertRaises(AccessDenied):
            OwnerProofs(Pairings(self.path)).consume(**self.args, **signed, now=105)

    def test_changed_operation_content_identity_and_scope(self):
        signed = self.signed()
        for change in ({'content': b'{"body":"delete files"}'}, {'operation': 'history'},
                       {'conversation': 'other'}, {'installation': 'other'},
                       {'principal': replace(self.person, device_id='other')},
                       {'principal': replace(self.person, account_id='other')},
                       {'principal': replace(self.person, home_server='other')}):
            with self.subTest(change=change), self.assertRaises(AccessDenied):
                self.proofs.consume(**(self.args | change), **signed, now=104)
        self.proofs.consume(**self.args, **signed, now=104)

    def test_wrong_signature_and_expiry(self):
        signed = self.signed()
        wrong = ec.generate_private_key(ec.SECP256R1())
        signed_wrong = signed | {'signature': base64.b64encode(wrong.sign(b'other', ec.ECDSA(hashes.SHA256()))).decode()}
        with self.assertRaises(AccessDenied):
            self.proofs.consume(**self.args, **signed_wrong, now=104)
        with self.assertRaises(AccessDenied):
            self.proofs.consume(**self.args, **signed, now=163)

    def test_revoke_after_challenge(self):
        signed = self.signed()
        self.store.revoke(self.args['grant_id'])
        with self.assertRaises(AccessDenied):
            self.proofs.consume(**self.args, **signed, now=104)

    def test_concurrent_use_only_once(self):
        signed = self.signed()
        def use(_):
            try:
                self.proofs.consume(**self.args, **signed, now=104)
                return True
            except AccessDenied:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(use, range(2))), 1)

    def test_old_grants_never_promoted(self):
        challenge = self.store.request(self.person, 'node', now=100)
        grant_id = self.store.approve(challenge, self.person, 'node', {'conversation'},
                                      now=101, expires_at=1000, history_from=0)
        with self.assertRaises(AccessDenied):
            self.proofs.issue(**(self.args | {'grant_id': grant_id}), now=103)

    def test_invalid_key_rejected(self):
        for key in ('nonsense', base64.b64encode(b'not a key').decode()):
            with self.assertRaises(AccessDenied):
                self.store.request(self.person, 'node', now=100, owner_key=key)

    def test_unimplemented_admin_proof_denied(self):
        with self.assertRaises(AccessDenied):
            self.signed(operation='approval')

    def test_key_substitution_during_local_confirmation(self):
        challenge = self.store.request(self.person, 'node', now=100, owner_key=self.encoded)
        with self.assertRaises(AccessDenied):
            self.store.approve(challenge, self.person, 'node', {'conversation'},
                now=101, expires_at=1000, history_from=0, expected_owner_key='different')

    def test_bounded_challenges(self):
        for _ in range(30):
            self.signed()
        with self.assertRaises(AccessDenied):
            self.signed()
