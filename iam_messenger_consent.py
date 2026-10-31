"""Local owner review for external contact. Not a remote control surface.

This records informed permission, not model/tool isolation. Friendship acceptance
and data authorization are separate checks required by every future friend route.
"""
import hashlib
import json
import secrets

from iam_service import IAMError

DISCLOSURE_VERSION = 'primary-context-v1'
DISCLOSURE = (
    "Outside messages enter this agent's primary context. They may influence or steer "
    "the agent, including through misleading instructions, links or attachments. "
    "Non-owner messages are NOT owner authorization. Labels and instructions reduce "
    "risk but do not guarantee protection or isolate owner tools/private context. "
    "Enable this only if you accept that risk. Stay vigilant, review unexpected "
    "behavior, and disable external contact or revoke/block a friendship when needed. "
    "Disabling cannot undo actions already taken or erase information already shared."
)
MODES = {'disabled', 'owner-enabled', 'agent-choice'}


class ExternalContactReview:
    def __init__(self, store):
        self.store = store
        self.tickets = {}
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS external_contact_consent (
                mailbox TEXT PRIMARY KEY, scope TEXT NOT NULL, mode TEXT NOT NULL,
                disclosure TEXT NOT NULL, owner TEXT NOT NULL, decided REAL NOT NULL,
                revision INTEGER NOT NULL, agent_opted_in INTEGER NOT NULL DEFAULT 0)''')

    @staticmethod
    def scope(config):
        policy = config.get('messenger', {})
        values = [policy.get('home_server'), config.get('node_id'), policy.get('owner_account_id')]
        if policy.get('enabled') is not True or any(not isinstance(v, str) or not v or len(v) > 512 for v in values):
            raise IAMError('Configure the local owner and installation before external contact')
        return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest(), values[2]

    def status(self, mailbox, config):
        scope, owner = self.scope(config)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM external_contact_consent WHERE mailbox=?', (mailbox,)).fetchone()
        current = row and row['scope'] == scope and row['disclosure'] == DISCLOSURE_VERSION
        mode = row['mode'] if current and row['mode'] in MODES else 'disabled'
        return dict(mailbox=mailbox, mode=mode, revision=row['revision'] if row else 0,
                    owner=owner, disclosure_version=DISCLOSURE_VERSION, disclosure=DISCLOSURE,
                    needs_review=bool(row and not current), decided_at=row['decided'] if current else None,
                    agent_opted_in=bool(current and row['agent_opted_in']),
                    permitted=bool(mode == 'owner-enabled' or mode == 'agent-choice' and row['agent_opted_in']))

    def prepare(self, mailbox, config, mode, *, now):
        if not isinstance(mode, str) or mode not in MODES:
            raise IAMError('Select an external contact policy')
        current = self.status(mailbox, config)
        scope, owner = self.scope(config)
        self.tickets = {k: v for k, v in self.tickets.items() if v['expires'] > now}
        if len(self.tickets) >= 20:
            raise IAMError('Too many pending reviews')
        token = secrets.token_urlsafe(32)
        self.tickets[token] = dict(mailbox=mailbox, scope=scope, owner=owner, mode=mode,
                                  revision=current['revision'], expires=now + 60,
                                  disclosure=DISCLOSURE_VERSION)
        return dict(current, proposed_mode=mode, ticket=token, confirm_before=now + 60)

    def confirm(self, mailbox, config, ticket, *, acknowledged, version, now):
        record = self.tickets.pop(ticket, None) if isinstance(ticket, str) else None
        scope, owner = self.scope(config)
        if (not record or record['mailbox'] != mailbox or record['scope'] != scope
                or record['expires'] <= now or record['disclosure'] != DISCLOSURE_VERSION
                or version != DISCLOSURE_VERSION):
            raise IAMError('Review expired or scope changed; review again')
        if record['mode'] != 'disabled' and acknowledged is not True:
            raise IAMError('Explicit acknowledgment of outside-influence risks is required')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM external_contact_consent WHERE mailbox=?', (mailbox,)).fetchone()
            revision = row['revision'] if row else 0
            if revision != record['revision']:
                raise IAMError('Policy changed during review; review again')
            db.execute('''INSERT INTO external_contact_consent
                (mailbox,scope,mode,disclosure,owner,decided,revision,agent_opted_in)
                VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(mailbox) DO UPDATE SET
                scope=excluded.scope,mode=excluded.mode,disclosure=excluded.disclosure,
                owner=excluded.owner,decided=excluded.decided,revision=excluded.revision,agent_opted_in=0''',
                (mailbox, scope, record['mode'], DISCLOSURE_VERSION, owner, now, revision + 1))
        return self.status(mailbox, config)

    def disable(self, mailbox, config, *, now):
        # Always available without a risk acknowledgment. Invalidate other reviews
        # through the same revision check; no stale ticket can re-enable afterward.
        current = self.status(mailbox, config)
        scope, owner = self.scope(config)
        token = secrets.token_urlsafe(32)
        self.tickets[token] = dict(mailbox=mailbox, scope=scope, owner=owner, mode='disabled',
                                  revision=current['revision'], expires=now + 60,
                                  disclosure=DISCLOSURE_VERSION)
        return self.confirm(mailbox, config, token, acknowledged=False,
                            version=DISCLOSURE_VERSION, now=now)

    def choose(self, mailbox, config, *, opted_in, revision):
        """Agent's own choice only; cannot create or broaden owner permission.

        The caller must bind mailbox through the normal local mailbox service.
        Keep the owner's acknowledgment/version/time intact. A concurrent owner
        disable or a changed installation invalidates the choice atomically.
        """
        if type(opted_in) is not bool or type(revision) is not int:
            raise IAMError('An explicit choice and current policy revision are required')
        scope, _ = self.scope(config)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM external_contact_consent WHERE mailbox=?', (mailbox,)).fetchone()
            if (not row or row['mode'] != 'agent-choice' or row['scope'] != scope
                    or row['disclosure'] != DISCLOSURE_VERSION or row['revision'] != revision):
                raise IAMError('Current owner permission for agent choice is required')
            db.execute('UPDATE external_contact_consent SET agent_opted_in=?,revision=revision+1 WHERE mailbox=?',
                       (int(opted_in), mailbox))
        return self.status(mailbox, config)
