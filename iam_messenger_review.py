"""Local-only pairing review tickets. Never register this in remote dispatch."""
import secrets
from iam_messenger_access import AccessDenied, Principal


class PairingReview:
    def __init__(self, store):
        self.store = store
        self.tickets = {}
        self.revocations = {}

    def prepare_revoke(self, grant_id, conversation, *, now):
        records = self.store.local_grants(conversation,now=now)
        record = next((r for r in records if r['grant_id'] == grant_id),None)
        if record is None:
            raise AccessDenied('Grant unavailable for this conversation')
        self.revocations = {k:v for k,v in self.revocations.items() if v['deadline'] > now}
        if len(self.revocations) >= 20:
            raise AccessDenied('Too many pending reviews')
        ticket = secrets.token_urlsafe(32)
        self.revocations[ticket] = dict(grant_id=grant_id,conversation=conversation,deadline=now+60)
        return dict(record,ticket=ticket,confirm_before=now+60)

    def confirm_revoke(self, ticket, conversation, *, now):
        record = self.revocations.pop(ticket,None)
        if not record or record['deadline'] <= now or record['conversation'] != conversation:
            raise AccessDenied('Review expired or does not match this conversation')
        self.store.revoke(record['grant_id'])

    def prepare(self, challenge, conversation_id, *, include_history, now, owner_access=False):
        if type(include_history) is not bool:
            raise AccessDenied('Explicit history decision required')
        pending = self.store.inspect(challenge, now=now)
        if type(owner_access) is not bool or bool(pending['owner_key']) != owner_access:
            raise AccessDenied('Explicit key-bound owner authority confirmation required')
        key_fingerprint = None
        if owner_access:
            from iam_messenger_keys import fingerprint
            key_fingerprint = fingerprint(pending['owner_key'])
        self.tickets = {k: v for k, v in self.tickets.items() if v['deadline'] > now}
        if len(self.tickets) >= 20:
            raise AccessDenied('Too many pending reviews')
        ticket = secrets.token_urlsafe(32)
        record = dict(challenge=challenge, principal=Principal(**pending['principal']),
                      installation=pending['installation'], conversation_id=conversation_id,
                      history_from=0 if include_history else now, owner_key=pending['owner_key'],
                      expires_at=now + 7 * 86400, deadline=min(now + 60, pending['expires_at']))
        self.tickets[ticket] = record
        return dict(ticket=ticket, identity=pending['principal'],
                    installation=record['installation'], conversation=conversation_id,
                    include_history=include_history, expires_at=record['expires_at'],
                    confirm_before=record['deadline'], owner_access=owner_access,
                    key_fingerprint=key_fingerprint)

    def confirm(self, ticket, conversation_id, *, now):
        record = self.tickets.pop(ticket, None)
        if not record or record['deadline'] <= now or record['conversation_id'] != conversation_id:
            raise AccessDenied('Review expired or does not match this conversation')
        return self.store.approve(record['challenge'], record['principal'], record['installation'],
            {record['conversation_id']}, now=now, expires_at=record['expires_at'],
            history_from=record['history_from'], expected_owner_key=record['owner_key'])

    def prepare_invitation(self, token, current_conversations, *, now):
        invitation=self.store.inspect_invitation(token,now=now)
        if not set(invitation['conversations']).issubset(current_conversations):
            raise AccessDenied('Conversation list changed; create a new invitation')
        receipt=self.prepare(token, 'buddies', include_history=invitation['history_from']==0,
                             now=now,owner_access=True)
        record=self.tickets[receipt['ticket']]
        record['conversations']=set(invitation['conversations'])
        record['history_from']=invitation['history_from']
        return dict(receipt,conversations=invitation['conversations'])

    def confirm_invitation(self, ticket, current_conversations, *, now):
        record=self.tickets.pop(ticket,None)
        if (not record or record['deadline']<=now or not record.get('conversations')
                or not record['conversations'].issubset(current_conversations)):
            raise AccessDenied('Review expired or conversations changed')
        self.store.inspect_invitation(record['challenge'],now=now)
        return self.store.approve(record['challenge'],record['principal'],record['installation'],
            record['conversations'],now=now,expires_at=record['expires_at'],
            history_from=record['history_from'],expected_owner_key=record['owner_key'])
