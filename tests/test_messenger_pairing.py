import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from iam_messenger_access import AccessDenied, Principal
from iam_messenger_pairing import Pairings


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pairings.db'
        self.store = Pairings(self.path)
        self.person = Principal('home', 'lily', 'phone', 'messenger_device')
        self.challenge = self.store.request(self.person, 'installation', now=100)

    def approve(self, challenge=None, person=None, now=101):
        return self.store.approve(challenge or self.challenge, person or self.person,
            'installation', {'ophelia'}, now=now, expires_at=1000, history_from=0)

    def test_persistent_grant_and_revoke(self):
        grant = self.approve()
        other = Pairings(self.path)
        self.assertEqual(other.get(grant, self.person, now=102).conversation_ids, {'ophelia'})
        other.revoke(grant)
        with self.assertRaises(AccessDenied):
            self.store.get(grant, self.person, now=103)

    def test_replay_and_expiry(self):
        self.approve()
        with self.assertRaises(AccessDenied):
            self.approve()
        challenge = self.store.request(self.person, 'installation', now=100)
        with self.assertRaises(AccessDenied):
            self.approve(challenge, now=400)

    def test_substitution_rejected_without_consuming(self):
        with self.assertRaises(AccessDenied):
            self.approve(person=replace(self.person, account_id='other'))
        grant = self.approve()
        with self.assertRaises(AccessDenied):
            self.store.get(grant, replace(self.person, device_id='other'), now=102)
        with self.assertRaises(AccessDenied):
            self.store.get(grant, self.person, now=1000)

    def test_concurrent_approval_only_once(self):
        def attempt(_):
            try:
                self.approve()
                return True
            except AccessDenied:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)

    def test_challenge_not_stored_in_plaintext(self):
        self.assertNotIn(self.challenge.encode(), self.path.read_bytes())

    def test_legacy_credentials_rejected(self):
        with self.assertRaises(AccessDenied):
            self.store.request(replace(self.person, credential_kind='legacy_node'), 'node', now=100)
