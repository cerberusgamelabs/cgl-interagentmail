"""Local single-use proof of an enrolled owner's exact conversation operation.

Transport authenticates the principal independently. Issuing a challenge grants
no access. Verification consumes it durably before downstream work; uncertainty
must be held, not retried as a new agent turn. No admin operations are exposed.
"""
import base64
import hashlib
import json
import secrets

from iam_messenger_access import AccessDenied, authorize
from iam_messenger_keys import verify


class OwnerProofs:
    def __init__(self, pairings):
        self.pairings = pairings
        with pairings.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS owner_proofs (
                nonce TEXT PRIMARY KEY, grant_id TEXT NOT NULL, payload BLOB NOT NULL,
                expires INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)''')

    def scope(self, grant_id, principal, installation, conversation, operation, now):
        grant = self.pairings.get(grant_id, principal, now=now)
        authorize(enabled=True, principal=principal, grant=grant,
                  installation_id=installation, conversation_id=conversation,
                  operation=operation, now=now)
        if not grant.owner_key:
            raise AccessDenied('Local key-bound owner enrollment required')
        return grant

    @staticmethod
    def payload(nonce, grant_id, principal, installation, conversation, operation, content, expires):
        if not isinstance(content, bytes) or len(content) > 65536:
            raise AccessDenied('Invalid operation content')
        # Encode once on the local node. Android signs these exact bytes instead
        # of reserializing JSON; the dispatcher recomputes the same binding.
        return json.dumps(dict(protocol='iam-owner-proof', version=1, nonce=nonce,
            grant_id=grant_id, home_server=principal.home_server,
            account_id=principal.account_id, device_id=principal.device_id,
            installation_id=installation, conversation_id=conversation,
            operation=operation, content_sha256=hashlib.sha256(content).hexdigest(),
            expires_at=expires), sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

    def issue(self, grant_id, principal, installation, conversation, operation, content, *, now):
        grant = self.scope(grant_id, principal, installation, conversation, operation, now)
        nonce = secrets.token_urlsafe(32)
        expires = min(now + 60, grant.expires_at)
        payload = self.payload(nonce, grant_id, principal, installation, conversation, operation, content, expires)
        with self.pairings.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM owner_proofs WHERE expires <= ?', (now,))
            # Consumed records count too: bounded issuance per grant/time window.
            if (db.execute('''SELECT count(*) FROM owner_proofs WHERE grant_id=? AND
                    (instr(CAST(payload AS TEXT),'"operation":"attachment"')>0)=?''',
                    (grant_id,int(operation == 'attachment'))).fetchone()[0] >= (120 if operation == 'attachment' else 30)
                    or db.execute('SELECT count(*) FROM owner_proofs').fetchone()[0] >= 10000):
                raise AccessDenied('Device proof rate limit')
            db.execute('INSERT INTO owner_proofs (nonce, grant_id, payload, expires) VALUES (?, ?, ?, ?)',
                       (nonce, grant_id, payload, expires))
        return dict(nonce=nonce, payload=base64.b64encode(payload).decode('ascii'), expires_at=expires)

    def consume(self, nonce, signature, grant_id, principal, installation, conversation, operation, content, *, now):
        self.scope(grant_id, principal, installation, conversation, operation, now)
        with self.pairings.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM owner_proofs WHERE nonce=?', (nonce,)).fetchone()
            if row is None or row['consumed'] or row['expires'] <= now or row['grant_id'] != grant_id:
                raise AccessDenied('Device proof expired or used')
            # Recheck revocation under the same write lock used by revoke.
            grant_row = db.execute('SELECT * FROM pairing_grants WHERE id=?', (grant_id,)).fetchone()
            if grant_row is None or grant_row['revoked'] or grant_row['expires'] <= now:
                raise AccessDenied('Pairing unavailable')
            payload = self.payload(nonce, grant_id, principal, installation, conversation, operation, content, row['expires'])
            if payload != row['payload']:
                raise AccessDenied('Device proof does not match operation')
            verify(grant_row['owner_key'], signature, payload)
            db.execute('UPDATE owner_proofs SET consumed=1 WHERE nonce=?', (nonce,))
        return dict(authority='paired_owner', origin='mobile', device_id=principal.device_id)
