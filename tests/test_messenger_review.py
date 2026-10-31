from tests.test_messenger_pairing import PairingTests
from iam_messenger_review import PairingReview
from iam_messenger_access import AccessDenied


class ReviewTests(PairingTests):
    def test_revocation_is_local_scoped_expiring_and_single_use(self):
        grant=self.approve()
        review=PairingReview(self.store)
        self.assertEqual(self.store.local_grants('other',now=102),[])
        self.assertEqual(self.store.local_grants('ophelia',now=102)[0]['grant_id'],grant)
        with self.assertRaises(AccessDenied):
            review.prepare_revoke(grant,'other',now=102)
        ticket=review.prepare_revoke(grant,'ophelia',now=102)['ticket']
        with self.assertRaises(AccessDenied):
            review.confirm_revoke(ticket,'other',now=103)
        self.store.get(grant,self.person,now=103)
        ticket=review.prepare_revoke(grant,'ophelia',now=102)['ticket']
        with self.assertRaises(AccessDenied):
            review.confirm_revoke(ticket,'ophelia',now=162)
        ticket=review.prepare_revoke(grant,'ophelia',now=103)['ticket']
        review.confirm_revoke(ticket,'ophelia',now=104)
        self.assertEqual(self.store.local_grants('ophelia',now=104),[])
        with self.assertRaises(AccessDenied):
            self.store.get(grant,self.person,now=104)
        with self.assertRaises(AccessDenied):
            review.confirm_revoke(ticket,'ophelia',now=105)

    def test_review_scope_and_single_use(self):
        review = PairingReview(self.store)
        receipt = review.prepare(self.challenge, 'ophelia', include_history=True, now=101)
        self.assertEqual(receipt['identity']['account_id'], 'lily')
        grant = review.confirm(receipt['ticket'], 'ophelia', now=102)
        self.assertEqual(self.store.get(grant, self.person, now=103).history_from, 0)
        with self.assertRaises(AccessDenied):
            review.confirm(receipt['ticket'], 'ophelia', now=103)

    def test_wrong_dialog_and_expired_review(self):
        review = PairingReview(self.store)
        receipt = review.prepare(self.challenge, 'ophelia', include_history=False, now=101)
        with self.assertRaises(AccessDenied):
            review.confirm(receipt['ticket'], 'other', now=102)
        receipt = review.prepare(self.challenge, 'ophelia', include_history=False, now=101)
        with self.assertRaises(AccessDenied):
            review.confirm(receipt['ticket'], 'ophelia', now=161)
