"""Local pairing persistence. Not exposed through any gateway or MCP tool.

Only a trusted local confirmation handler may call approve/revoke. A boolean
supplied by a remote caller is NOT local confirmation. Authenticated identities
must be established by the transport before creating a request.
"""
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict

from iam_messenger_access import AccessDenied, OwnerGrant, Principal


class Pairings:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pairing_requests (
                    digest TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    installation TEXT NOT NULL, expires INTEGER NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pairing_grants (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    installation TEXT NOT NULL, conversations TEXT NOT NULL,
                    expires INTEGER NOT NULL, history_from INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pairing_invitations (
                    digest TEXT PRIMARY KEY, home TEXT NOT NULL, account TEXT NOT NULL,
                    installation TEXT NOT NULL, conversations TEXT NOT NULL,
                    history_from INTEGER NOT NULL, expires INTEGER NOT NULL,
                    cancelled INTEGER NOT NULL DEFAULT 0);
            ''')
            # Old bearer-only records remain non-owner; no implicit promotion.
            db.execute('BEGIN IMMEDIATE')
            for table in ('pairing_requests', 'pairing_grants'):
                if 'owner_key' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN owner_key TEXT')
            if 'grant_id' not in {r['name'] for r in db.execute('PRAGMA table_info(pairing_requests)')}:
                db.execute('ALTER TABLE pairing_requests ADD COLUMN grant_id TEXT')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identity(principal):
        if not isinstance(principal, Principal) or any(
            not isinstance(v, str) or not v or len(v) > 512
            for v in (principal.home_server, principal.account_id, principal.device_id)
        ) or principal.credential_kind != 'messenger_device':
            raise AccessDenied('Authenticated Messenger device required')
        return json.dumps(asdict(principal), sort_keys=True)

    @staticmethod
    def digest(challenge):
        if not isinstance(challenge, str) or not 32 <= len(challenge) <= 128:
            raise AccessDenied('Invalid pairing request')
        return hashlib.sha256(challenge.encode('utf-8')).hexdigest()

    def request(self, principal, installation, *, now, owner_key=None):
        identity = self.identity(principal)
        if owner_key is not None:
            from iam_messenger_keys import public_key
            public_key(owner_key)
        if not isinstance(installation, str) or not installation or len(installation) > 512:
            raise AccessDenied('Invalid installation')
        challenge = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM pairing_requests WHERE expires <= ?', (now,))
            if db.execute('SELECT count(*) FROM pairing_requests').fetchone()[0] >= 100:
                raise AccessDenied('Too many pending pairing requests')
            db.execute('INSERT INTO pairing_requests (digest, identity, installation, expires, owner_key) VALUES (?, ?, ?, ?, ?)',
                       (self.digest(challenge), identity, installation, now + 300, owner_key))
        return challenge

    def invite(self, home, account, installation, conversations, *, include_history, now):
        """Trusted Desktop only. The invitation is NOT a grant or login token."""
        self.identity(Principal(home, account, 'local-review', 'messenger_device'))
        if (not isinstance(installation, str) or not installation or len(installation)>512
                or type(include_history) is not bool or not isinstance(conversations, (set, frozenset))
                or not 1<=len(conversations)<=64
                or any(not isinstance(c,str) or not c or len(c)>512 for c in conversations)):
            raise AccessDenied('Select between one and 64 current conversations')
        token=secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM pairing_invitations WHERE expires<=?', (now,))
            db.execute('DELETE FROM pairing_requests WHERE expires<=?', (now,))
            if db.execute('SELECT count(*) FROM pairing_invitations').fetchone()[0]>=20:
                raise AccessDenied('Too many pairing invitations')
            db.execute('INSERT INTO pairing_invitations VALUES (?,?,?,?,?,?,?,0)',
                (self.digest(token),home,account,installation,json.dumps(sorted(conversations)),0 if include_history else now,now+300))
        return dict(challenge=token,expires_at=now+300,installation=installation,
                    home=home,account=account,conversations=sorted(conversations),include_history=include_history)

    def claim_invitation(self, token, principal, installation, owner_key, *, now):
        """Authenticated phone claims once; retries by that exact key are idempotent."""
        from iam_messenger_keys import public_key
        public_key(owner_key)
        identity=self.identity(principal)
        digest=self.digest(token)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            invite=db.execute('SELECT * FROM pairing_invitations WHERE digest=?',(digest,)).fetchone()
            if (not invite or invite['cancelled'] or invite['expires']<=now
                    or invite['home']!=principal.home_server or invite['account']!=principal.account_id
                    or invite['installation']!=installation):
                raise AccessDenied('Invitation unavailable')
            request=db.execute('SELECT * FROM pairing_requests WHERE digest=?',(digest,)).fetchone()
            if request:
                if request['identity']!=identity or request['owner_key']!=owner_key:
                    raise AccessDenied('Invitation already claimed')
            else:
                db.execute('INSERT INTO pairing_requests (digest,identity,installation,expires,owner_key) VALUES (?,?,?,?,?)',
                    (digest,identity,installation,invite['expires'],owner_key))
        return dict(challenge=token,expires_at=invite['expires'])

    def inspect_invitation(self, token, *, now):
        with self.connection() as db:
            row=db.execute('SELECT * FROM pairing_invitations WHERE digest=?',(self.digest(token),)).fetchone()
        if not row or row['cancelled'] or row['expires']<=now:
            raise AccessDenied('Invitation expired or cancelled')
        return dict(conversations=json.loads(row['conversations']),history_from=row['history_from'],
                    expires_at=row['expires'],installation=row['installation'])

    def cancel_invitation(self, token):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            digest=self.digest(token)
            row=db.execute('SELECT grant_id FROM pairing_requests WHERE digest=?',(digest,)).fetchone()
            if row and row['grant_id']:
                raise AccessDenied('Already approved; revoke the grant instead')
            db.execute('UPDATE pairing_invitations SET cancelled=1 WHERE digest=?',(digest,))
            db.execute('DELETE FROM pairing_requests WHERE digest=?',(digest,))

    def approve(self, challenge, expected_principal, installation, conversations,
                *, now, expires_at, history_from, expected_owner_key=None):
        """Local-only: expected identity must match what the owner confirmed."""
        identity = self.identity(expected_principal)
        if (type(expires_at) is not int or not now < expires_at <= now + 30 * 86400
                or type(history_from) is not int or not 0 <= history_from <= now
                or not isinstance(conversations, (set, frozenset))
                or not 1 <= len(conversations) <= 100
                or any(not isinstance(c, str) or not c or len(c) > 512 for c in conversations)):
            raise AccessDenied('Invalid grant scope or duration')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM pairing_requests WHERE digest = ?',
                             (self.digest(challenge),)).fetchone()
            if (row is None or row['consumed'] or row['expires'] <= now
                    or row['identity'] != identity or row['installation'] != installation
                    or row['owner_key'] != expected_owner_key):
                raise AccessDenied('Pairing request expired, consumed or mismatched')
            invitation=db.execute('SELECT * FROM pairing_invitations WHERE digest=?',(self.digest(challenge),)).fetchone()
            if invitation and (invitation['cancelled'] or invitation['expires']<=now
                    or set(json.loads(invitation['conversations']))!=conversations
                    or invitation['history_from']!=history_from):
                raise AccessDenied('Invitation review scope changed')
            grant_id = secrets.token_urlsafe(32)
            db.execute('INSERT INTO pairing_grants (id, identity, installation, conversations, expires, history_from, owner_key) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (grant_id, identity, installation, json.dumps(sorted(conversations)),
                        expires_at, history_from, expected_owner_key))
            db.execute('UPDATE pairing_requests SET consumed = 1, grant_id = ? WHERE digest = ?',
                       (grant_id, self.digest(challenge)))
        return grant_id

    def status(self, challenge, principal, installation, *, now):
        """Authenticated device only; challenge possession alone is insufficient.

        Retrieval expires with the original five-minute challenge. A returned
        grant ID is not owner proof: operations still require its bound key.
        """
        identity = self.identity(principal)
        with self.connection() as db:
            row = db.execute('SELECT * FROM pairing_requests WHERE digest = ?',
                             (self.digest(challenge),)).fetchone()
            if (row is None or row['expires'] <= now or row['identity'] != identity
                    or row['installation'] != installation):
                raise AccessDenied('Pairing unavailable')
            if not row['consumed']:
                return dict(state='awaiting_local_confirmation', expires_at=row['expires'])
            grant = db.execute('SELECT * FROM pairing_grants WHERE id = ?',
                               (row['grant_id'],)).fetchone()
            if (grant is None or grant['revoked'] or grant['expires'] <= now
                    or grant['identity'] != identity or grant['installation'] != installation
                    or not grant['owner_key'] or grant['owner_key'] != row['owner_key']):
                raise AccessDenied('Pairing unavailable')
            return dict(state='approved', grant_id=grant['id'], expires_at=grant['expires'],
                        conversations=json.loads(grant['conversations']))

    def inspect(self, challenge, *, now):
        """Local-only preview. Never return the stored challenge digest."""
        with self.connection() as db:
            row = db.execute('SELECT * FROM pairing_requests WHERE digest = ?',
                             (self.digest(challenge),)).fetchone()
        if row is None or row['consumed'] or row['expires'] <= now:
            raise AccessDenied('Pairing request unavailable')
        return dict(principal=json.loads(row['identity']), installation=row['installation'],
                    expires_at=row['expires'], owner_key=row['owner_key'])

    def get(self, grant_id, principal, *, now):
        identity = self.identity(principal)
        with self.connection() as db:
            row = db.execute('SELECT * FROM pairing_grants WHERE id = ?', (grant_id,)).fetchone()
        if row is None or row['identity'] != identity or row['revoked'] or row['expires'] <= now:
            raise AccessDenied('Pairing unavailable')
        return OwnerGrant(principal.home_server, principal.account_id, principal.device_id,
                          row['installation'], frozenset(json.loads(row['conversations'])),
                          row['expires'], row['history_from'], owner_key=row['owner_key'])

    def revoke(self, grant_id):
        with self.connection() as db:
            db.execute('UPDATE pairing_grants SET revoked = 1 WHERE id = ?', (grant_id,))

    def local_grants(self, conversation, *, now):
        """Trusted local UI only; never expose a grant directory remotely."""
        from iam_messenger_keys import fingerprint
        with self.connection() as db:
            rows = db.execute('SELECT * FROM pairing_grants WHERE revoked=0 AND expires>? ORDER BY expires DESC', (now,)).fetchall()
        result = []
        for row in rows:
            conversations = json.loads(row['conversations'])
            if conversation is None or conversation in conversations:
                result.append(dict(grant_id=row['id'],principal=json.loads(row['identity']),
                    conversations=conversations,installation=row['installation'],expires_at=row['expires'],
                    fingerprint=fingerprint(row['owner_key']) if row['owner_key'] else None))
            if len(result) == 100:
                break
        return result
