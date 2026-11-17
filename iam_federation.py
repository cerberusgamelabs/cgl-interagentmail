"""Opt-in same-account mail transport primitives; no owner/runtime authority.

The relay is an authenticated courier, not an owner-proof issuer. Remote
Messenger owner submissions must use a separate locally approved signed grant.
"""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class FederationError(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', value) and value not in {'__proto__', 'constructor', 'prototype'}


def address(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9 ._-]{1,200}', value) and value == value.strip() and value not in {'.', '..'}


def policy(config, registered):
    value = config.get('federation', {})
    if value.get('enabled') is not True:
        raise FederationError('Federation is disabled')
    if (config.get('enabled') is not True or not identifier(config.get('node_id'))
            or not identifier(value.get('owner_account_id')) or not identifier(value.get('epoch'))
            or not isinstance(value.get('peers'), list) or not 1 <= len(value['peers']) <= 32
            or any(not identifier(p) or p == config['node_id'] for p in value['peers'])
            or len(set(value['peers'])) != len(value['peers'])
            or not isinstance(value.get('mailboxes'), list) or len(value['mailboxes']) > 500
            or any(not address(m) or m not in registered for m in value['mailboxes'])
            or len(set(value['mailboxes'])) != len(value['mailboxes'])):
        raise FederationError('Invalid federation scope')
    return value


class MailQueue:
    """SQLite journal scoped to one endpoint, node and local opt-in generation.

    Inbound publication failures are held, not blindly replayed. A durable relay
    receipt is not an agent turn acceptance or a reply.
    """
    def __init__(self, path, config, registered):
        self.path = Path(path)
        self.config = config
        self.registered = set(registered)
        self.policy = policy(config, self.registered)
        self.scope = digest([config['server_url'], config['node_id'], self.policy])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS directory(scope TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbound(scope TEXT, id TEXT, fingerprint TEXT NOT NULL,
                  data TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(scope,id));
                CREATE TABLE IF NOT EXISTS inbound(scope TEXT, id TEXT, fingerprint TEXT NOT NULL,
                  state TEXT NOT NULL, PRIMARY KEY(scope,id));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def set_roster(self, owner, rows):
        if (owner != self.policy['owner_account_id'] or not isinstance(rows, list)
                or len(encoded(rows).encode()) > 120000):
            raise FederationError('Remote directory rejected')
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'node', 'mailbox'}
                    or row['node'] not in self.policy['peers'] or not address(row['mailbox'])):
                raise FederationError('Remote directory rejected')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO directory VALUES(?,?)', (self.scope, encoded(rows)))

    def roster(self):
        with self.connect() as db:
            row = db.execute('SELECT data FROM directory WHERE scope=?', (self.scope,)).fetchone()
        return json.loads(row[0]) if row else []

    def enqueue(self, sender, recipient, subject, body, message_id=None, thread=None):
        matches = [r for r in self.roster() if r['mailbox'] == recipient]
        if sender not in self.policy['mailboxes'] or recipient in self.registered or len(matches) != 1:
            raise FederationError('Unknown or ambiguous remote mailbox')
        message_id = message_id or str(uuid4())
        value = dict(id=message_id, target=matches[0]['node'], to=recipient, **{'from': sender}, subject=subject, body=body)
        if thread is not None:
            if not identifier(thread):
                raise FederationError('Invalid remote thread')
            value['thread'] = thread
        if (not identifier(message_id) or not isinstance(subject, str) or len(subject.encode()) > 300
                or not isinstance(body, str) or len(body.encode()) > 100000
                or len(encoded(value).encode()) > 120000):
            raise FederationError('Remote message exceeds protocol bounds')
        fingerprint = digest(value)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint,state FROM outbound WHERE scope=? AND id=?', (self.scope, message_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise FederationError('Message identity conflict')
                return dict(id=message_id, state=old[1])
            if db.execute('SELECT count(*) FROM outbound').fetchone()[0] >= 10000:
                raise FederationError('Federation queue capacity reached')
            db.execute('INSERT INTO outbound VALUES(?,?,?,?,?)', (self.scope, message_id, fingerprint, encoded(value), 'queued'))
        return dict(id=message_id, state='queued')

    def outgoing(self):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM outbound WHERE scope=? AND state='queued' ORDER BY rowid LIMIT 1", (self.scope,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def receipt(self, message_id, state):
        if state not in {'queued', 'delivered', 'held'}:
            raise FederationError('Invalid relay receipt')
        with self.connect() as db:
            db.execute('UPDATE outbound SET state=? WHERE scope=? AND id=?',
                       ('relay_queued' if state == 'queued' else state, self.scope, message_id))

    def awaiting_receipts(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT id FROM outbound WHERE scope=? AND state='relay_queued' ORDER BY rowid", (self.scope,))]

    def receive(self, message, publish):
        if not isinstance(message, dict) or not isinstance(message.get('scope'), dict):
            raise FederationError('Invalid inbound federation envelope')
        scope = message.get('scope', {})
        if (message.get('target') != self.config['node_id'] or message.get('source') not in self.policy['peers']
                or message.get('to') not in self.policy['mailboxes'] or not address(message.get('from'))
                or message.get('from') in self.registered
                or not identifier(message.get('id')) or scope.get('owner') != self.policy['owner_account_id']
                or scope.get('targetEpoch') != self.policy['epoch']
                or not isinstance(message.get('body'), str) or not isinstance(message.get('subject'), str)
                or ('thread' in message and not identifier(message['thread']))
                or len(encoded(message).encode()) > 145000):
            raise FederationError('Inbound federation scope rejected')
        key = digest([message['source'], message['id']])
        fingerprint = digest(message)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint,state FROM inbound WHERE scope=? AND id=?', (self.scope, key)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise FederationError('Inbound identity conflict')
                return old[1]
            db.execute('INSERT INTO inbound VALUES(?,?,?,?)', (self.scope, key, fingerprint, 'held'))
        # Claim is durable before side effects. A crash is an explicit held delivery.
        publish(key, message)
        with self.connect() as db:
            db.execute("UPDATE inbound SET state='delivered' WHERE scope=? AND id=?", (self.scope, key))
        return 'delivered'


def publish_mail(root, key, message):
    """Publish only to an already registered local inbox; never create a remote agent."""
    import interagentmail as iam
    target = Path(root) / 'mailboxes' / message['to'] / 'inbox'
    if not target.is_dir():
        raise FederationError('Local recipient inbox is unavailable')
    payload = dict(id=key, thread=message.get('thread') or 'federation-' + key, reply_to=None, originator=message['from'],
        **{'from': message['from']}, from_display=message['from'], to=[message['to']], to_display=[message['to']],
        cc=[], cc_display=[], subject=message['subject'], body=message['body'], priority='normal',
        attachments=[], references=[], signature=message['from'], created_at=datetime.now(timezone.utc).isoformat(), read_at=None,
        metadata={'federation': {'source_node': message['source'], 'owner_account': message['scope']['owner'],
                                 'remote_id': message['id'], 'owner_authority': False}})
    iam.write_atomic(target / (key + '.json'), payload)


def route_mail(sender, to, cc, subject, body, attach, thread, reply_to, originator, refs, priority, metadata, delivery_key):
    """Normal mail entrypoint: opt-in text-only remote delivery, never silent fallback.

    Mixed local/remote batches and attachments are rejected before any publication.
    They require transactional fanout/attachment work, not copying local paths.
    """
    import interagentmail as iam
    config_file = iam.ROOT / 'webconnect.json'
    if not config_file.exists():
        return None
    config = iam.load(config_file)
    if config.get('federation', {}).get('enabled') is not True:
        return None
    registered = iam.load(iam.CONFIG).get('projects', {})
    queue = MailQueue(iam.ROOT/'run'/'federation.sqlite3', config, registered)
    roster = queue.roster()
    remote = {r['mailbox'] for r in roster}
    recipients = list(dict.fromkeys([*to, *cc]))
    for recipient in recipients:
        if recipient in remote and (recipient in registered or iam.mailbox(recipient).exists()):
            raise FederationError('Local and remote mailbox names collide; explicit resolution required')
    if not any(recipient in remote for recipient in recipients):
        if any(recipient not in registered and not iam.mailbox(recipient).exists() for recipient in recipients):
            raise FederationError('Unknown recipient while federation is enabled; refresh the directory')
        return None
    if (len(to) != 1 or cc or attach or refs or metadata or priority not in (None, 'normal')
            or originator not in (None, sender)):
        raise FederationError('Remote text mail currently requires one recipient, no attachments or forwarded metadata')
    if delivery_key is not None:
        iam.validate_delivery_key(delivery_key)
    message_id = digest([queue.scope, sender, delivery_key]) if delivery_key is not None else uuid4().hex
    thread = thread or 'thr-' + message_id
    receipt = queue.enqueue(sender, to[0], subject, body, message_id, thread)
    # The outbox journal is authoritative; this is only the sender's local view.
    iam.ensure_box(sender)
    payload = dict(id=message_id, thread=thread, reply_to=reply_to, originator=sender,
        **{'from':sender}, from_display=iam.display_name(sender), to=to, to_display=to,
        cc=[], cc_display=[], subject=subject, body=body, priority='normal', attachments=[],
        references=[], signature=iam.display_name(sender), created_at=datetime.now(timezone.utc).isoformat(),
        read_at=None, metadata={'federation':{'target_node':next(r['node'] for r in roster if r['mailbox']==to[0]),
                                           'delivery_state':receipt['state'], 'owner_authority':False}})
    path = iam.message_path(iam.mailbox(sender)/'sent',message_id)
    if not path.exists():
        iam.write_atomic(path,payload)
    return iam.receipt_result(delivery_key,'pending',message_id) if delivery_key is not None else payload


def delivery_receipt(sender, key):
    """Resolve a current-scope idempotency receipt without exposing message text."""
    import interagentmail as iam
    config_path = iam.ROOT / 'webconnect.json'
    journal = iam.ROOT / 'run' / 'federation.sqlite3'
    if not config_path.exists() or not journal.exists():
        return None
    config = iam.load(config_path)
    if config.get('federation', {}).get('enabled') is not True:
        return None
    queue = MailQueue(journal, config, iam.load(iam.CONFIG).get('projects', {}))
    message_id = digest([queue.scope, sender, key])
    with queue.connect() as db:
        row = db.execute('SELECT state FROM outbound WHERE scope=? AND id=?', (queue.scope, message_id)).fetchone()
    if row is None:
        return None
    status = 'committed' if row[0] == 'delivered' else 'pending'
    return {**iam.receipt_result(key, status, message_id), 'transport': 'federation', 'delivery_state': row[0]}


class Session:
    """Bounded request/reply pump sharing the connector's single authenticated socket."""
    def __init__(self, queue, root):
        self.queue, self.root = queue, Path(root)
        self.pending = None
        self.operation = 'offer'
        self.ack = None
        self.next_poll = 0
        self.blocked = False
        self.receipt_cursor = 0

    def next_frame(self, now=None):
        now = time.monotonic() if now is None else now
        if self.blocked:
            return None
        if self.pending:
            if now - self.pending[2] > 20:
                raise FederationError('Federation response timeout')
            return None
        if now < self.next_poll:
            return None
        operation = self.operation
        if operation == 'offer':
            value = {k:self.queue.policy[k] for k in ('peers', 'mailboxes', 'epoch')}
        elif operation == 'send':
            outgoing = self.queue.outgoing()
            if not outgoing:
                awaiting = self.queue.awaiting_receipts()
                if awaiting:
                    operation='receipt'
                    value={'id':awaiting[self.receipt_cursor % len(awaiting)]}
                    self.receipt_cursor += 1
                else:
                    operation = 'pull'
                    value = {}
            else:
                value = outgoing[0]
        elif operation == 'ack':
            value = self.ack
        else:
            value = {}
        frame = dict(protocol='iam-webconnect', version=1, type='federation', id=str(uuid4()), operation=operation, value=value)
        self.pending = (frame, operation, now)
        return frame

    def receive(self, frame, now=None):
        now = time.monotonic() if now is None else now
        if (not self.pending or frame.get('protocol') != 'iam-webconnect' or frame.get('version') != 1
                or frame.get('type') != 'federation_result' or frame.get('id') != self.pending[0]['id']):
            raise FederationError('Uncorrelated federation result')
        request, operation, _ = self.pending
        self.pending = None
        if frame.get('error') or frame.get('owner_account_id') != self.queue.policy['owner_account_id']:
            self.blocked = True
            raise FederationError('Federation rejected; explicit review required')
        result = frame.get('result', {})
        if not isinstance(result, dict):
            raise FederationError('Invalid federation result')
        if operation == 'offer':
            if result.get('advertised') is not True:
                raise FederationError('Federation offer not accepted')
            self.operation = 'roster'
        elif operation == 'roster':
            self.queue.set_roster(frame['owner_account_id'], result.get('roster'))
            self.operation = 'send'
        elif operation in ('send','receipt'):
            if result.get('id') != request['value']['id']:
                raise FederationError('Mismatched relay receipt')
            self.queue.receipt(result['id'], result.get('state'))
            self.operation = 'pull'
        elif operation == 'pull':
            messages = result.get('messages')
            if not isinstance(messages, list) or len(messages) > 1:
                raise FederationError('Invalid federation batch')
            if messages:
                message = messages[0]
                state = self.queue.receive(message, lambda key,m:publish_mail(self.root,key,m))
                if state != 'delivered':
                    self.blocked = True
                    raise FederationError('Inbound publication held for review')
                self.ack = dict(source=message['source'], id=message['id'])
                self.operation = 'ack'
            else:
                self.operation = 'roster'
                self.next_poll = now + 2
        elif operation == 'ack':
            if result.get('id') != self.ack['id'] or result.get('state') != 'delivered':
                raise FederationError('Invalid federation acknowledgment')
            self.ack = None
            self.operation = 'roster'
            self.next_poll = now + 2
