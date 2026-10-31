"""Read-through owner state shared by desktop and paired owner devices.

History fetches never mark read. IDs are checked against conversation and the
device's history cutoff; a batch cannot acknowledge other conversations.
"""
from iam_messenger_access import AccessDenied
import hashlib
import json
import time


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS read_events (event_id TEXT PRIMARY KEY)')
    db.execute("INSERT OR IGNORE INTO read_events SELECT id || ':reply' FROM seen")
    db.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
        id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, principal TEXT NOT NULL,
        mailbox TEXT NOT NULL, token TEXT NOT NULL, fingerprint TEXT NOT NULL DEFAULT '',
        revision INTEGER NOT NULL DEFAULT 0, notify_from REAL NOT NULL DEFAULT 0)''')
    if 'notify_from' not in {row[1] for row in db.execute('PRAGMA table_info(push_subscriptions)')}:
        db.execute('ALTER TABLE push_subscriptions ADD COLUMN notify_from REAL NOT NULL DEFAULT 0')
        # Legacy subscriptions did not record enrollment time. Begin at migration,
        # preserving historical unread state rather than guessing what was read.
        db.execute('UPDATE push_subscriptions SET notify_from=?',(time.time(),))


def subscribe(db, grant_id, principal, mailbox, push, now=None):
    from dataclasses import asdict
    from uuid import UUID
    if (not isinstance(push,dict) or set(push)!={'id','token'} or
            not isinstance(push['id'],str) or str(UUID(push['id']))!=push['id'] or
            not isinstance(push['token'],str) or not 20<=len(push['token'])<=4096 or
            any(ord(c)<33 or ord(c)>126 for c in push['token'])):
        raise AccessDenied('Invalid push subscription')
    identity=json.dumps(asdict(principal),sort_keys=True)
    old=db.execute('SELECT * FROM push_subscriptions WHERE id=?',(push['id'],)).fetchone()
    if old and (old['principal']!=identity or old['mailbox']!=mailbox):
        raise AccessDenied('Subscription conflict')
    if db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0]>=640 and not old:
        raise AccessDenied('Subscription limit')
    db.execute('''INSERT INTO push_subscriptions(id,grant_id,principal,mailbox,token,notify_from)
        VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET grant_id=excluded.grant_id,token=excluded.token''',
        (push['id'],grant_id,identity,mailbox,push['token'],time.time() if now is None else now))


def push_states(desktop, pairings, config, now):
    from iam_messenger_access import Principal
    from iam_messenger_proof import OwnerProofs
    policy=config.get('messenger',{})
    if policy.get('enabled') is not True: return []
    result=[]
    with desktop.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT * FROM push_subscriptions').fetchall():
            principal=Principal(**json.loads(row['principal']))
            if principal.account_id!=policy.get('owner_account_id') or principal.home_server!=policy.get('home_server'): continue
            try:
                grant=OwnerProofs(pairings).scope(row['grant_id'],principal,config['node_id'],row['mailbox'],'events',now)
                ids=unread(db,row['mailbox'],max(grant.history_from,row['notify_from']))
                expires=grant.expires_at
            except AccessDenied:
                ids=[]; expires=now+60
            fingerprint=hashlib.sha256(json.dumps(ids).encode()).hexdigest()
            revision=row['revision']+(fingerprint!=row['fingerprint'])
            db.execute('UPDATE push_subscriptions SET fingerprint=?,revision=? WHERE id=?',(fingerprint,revision,row['id']))
            result.append(dict(protocol='iam-webconnect',version=1,type='messenger_push_state',
                subscription_id=row['id'],revision=revision,unread=len(ids),expires_at=expires,
                token=row['token'],account_id=principal.account_id,device_id=principal.device_id))
    return result


def sync_state(db, mailbox, history_from, principal):
    from dataclasses import asdict
    ids=unread(db,mailbox,history_from)
    rows=db.execute('SELECT * FROM push_subscriptions WHERE principal=? AND mailbox=?',
                    (json.dumps(asdict(principal),sort_keys=True),mailbox)).fetchall()
    versions=[]
    for row in rows:
        notification_ids=unread(db,mailbox,max(history_from,row['notify_from']))
        fingerprint=hashlib.sha256(json.dumps(notification_ids).encode()).hexdigest()
        revision=row['revision']+(fingerprint!=row['fingerprint'])
        db.execute('UPDATE push_subscriptions SET fingerprint=?,revision=? WHERE id=?',(fingerprint,revision,row['id']))
        versions.append(dict(id=row['id'],revision=revision,unread=len(notification_ids)))
    deliveries=[dict(request_id=row['id'],delivery='replied' if row['reply'] else 'no_reply' if row['no_reply'] else 'received' if row['state']=='accepted' else 'sent')
        for row in db.execute('''SELECT id,state,reply IS NOT NULL reply,
            error='Turn finished; no reply published.' no_reply FROM requests
            WHERE mailbox=? AND created>=? ORDER BY created DESC,id DESC LIMIT 100''',(mailbox,history_from))]
    return dict(unread=len(ids),subscriptions=versions,deliveries=deliveries)


def incoming(db, mailbox, history_from=0):
    return db.execute('''SELECT * FROM (
        SELECT r.id || ':reply' event_id, coalesce(t.created,r.created) created
        FROM requests r LEFT JOIN reply_times t ON t.request_id=r.id
        WHERE r.mailbox=? AND r.reply IS NOT NULL
        UNION ALL SELECT id || ':notice', created FROM notices WHERE mailbox=? AND kind='agent'
        ) WHERE created >= ? ORDER BY created,event_id''', (mailbox,mailbox,history_from)).fetchall()


def unread(db, mailbox, history_from=0):
    read = {r[0] for r in db.execute('SELECT event_id FROM read_events')}
    return [r[0] for r in incoming(db,mailbox,history_from) if r[0] not in read]


def mark(db, mailbox, ids, history_from=0):
    if (not isinstance(ids,list) or not 1 <= len(ids) <= 20 or
            any(not isinstance(i,str) or not i or len(i)>200 for i in ids)):
        raise AccessDenied('Invalid read events')
    rows = incoming(db,mailbox,history_from)
    allowed = {r[0] for r in rows}
    if not set(ids) <= allowed:
        raise AccessDenied('Read events outside granted conversation')
    # A visible incoming event acknowledges the permitted history before it.
    # Validate every anchor first, then use the same chronological ordering as
    # history. Never extend the cutoff, cross mailboxes, or consume future rows.
    last = max(index for index,row in enumerate(rows) if row[0] in ids)
    ids = [row[0] for row in rows[:last+1]]
    db.executemany('INSERT OR IGNORE INTO read_events VALUES(?)', [(i,) for i in ids])
    # Keep old clients' reply counters compatible. Notices have their own IDs.
    db.executemany('INSERT OR IGNORE INTO seen VALUES(?)', [(i[:-6],) for i in ids if i.endswith(':reply')])
    return {'unread':len(unread(db,mailbox,history_from))}
