"""Signed owner text submissions. No live activation or generic RPC surface.

The dispatcher must revalidate the grant and pinned thread immediately before
turn/start. Proofs expire after at most sixty seconds; busy queues fail closed.
"""
import json
import time
import hashlib
import base64
import sqlite3
from pathlib import Path
from dataclasses import asdict
from uuid import UUID

from iam_messenger_access import AccessDenied, Principal
from iam_messenger_proof import OwnerProofs
from iam_messenger_uploads import validate_manifest, manifest_digest


def stored_manifest(db, request_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_attachment_manifests'").fetchone():return None
    row=db.execute('SELECT digest FROM mobile_attachment_manifests WHERE id=?',(request_id,)).fetchone()
    return row[0] if row else None


def lookup_request(path, principal, installation, conversation, grant_id, history_from, query):
    """Signed history lookup. Absence is explicit; errors never imply absence."""
    if (not isinstance(query, dict) or set(query) not in ({'request_id', 'body_sha256'}, {'request_id','body_sha256','image_sha256'}, {'request_id','body_sha256','attachments_sha256'})
            or str(UUID(query['request_id'])) != query['request_id']
            or not isinstance(query['body_sha256'], str) or len(query['body_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in query['body_sha256'])):
        raise AccessDenied('Invalid lookup')
    for key in ('image_sha256','attachments_sha256'):
        if key in query and (not isinstance(query[key],str) or len(query[key])!=64 or any(c not in '0123456789abcdef' for c in query[key])):
            raise AccessDenied('Invalid attachment lookup')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        row = db.execute('''SELECT r.mailbox,r.created,r.body,r.state,m.provenance
            FROM requests r LEFT JOIN mobile_requests m ON m.id=r.id WHERE r.id=?''',
            (query['request_id'],)).fetchone()
        image_row = db.execute('SELECT data FROM images WHERE request_id=?',(query['request_id'],)).fetchone()
        attachments_digest=stored_manifest(db,query['request_id'])
    finally:
        db.close()
    if row is None:
        return dict(request_id=query['request_id'], found=False)
    images = json.loads(image_row[0]) if image_row else []
    image_digest = hashlib.sha256(base64.b64decode(images[0]['data'].split(',',1)[1],validate=True)).hexdigest() if images else None
    if attachments_digest != query.get('attachments_sha256') or (not attachments_digest and image_digest != query.get('image_sha256')):
        raise AccessDenied('Image identity conflict')
    provenance = json.loads(row['provenance']) if row['provenance'] else {}
    if (row['mailbox'] != conversation or row['created'] < history_from
            or provenance.get('principal') != asdict(principal)
            or provenance.get('installation') != installation
            or provenance.get('conversation') != conversation
            or provenance.get('grant_id') != grant_id
            or hashlib.sha256(row['body'].encode('utf-8')).hexdigest() != query['body_sha256']):
        raise AccessDenied('Request identity conflict')
    state = 'queued' if row['state'] == 'mobile_queued' else row['state']
    if state not in {'queued','submitting','accepted','held','replied','blocked','failed'}:
        raise AccessDenied('Unknown delivery state')
    return dict(request_id=query['request_id'], found=True, state=state)


def parse_content(content):
    value = json.loads(content.decode('utf-8'))
    if (not isinstance(value, dict) or set(value) not in ({'request_id','body'}, {'request_id','body','image_upload','image_sha256'}, {'request_id','body','attachments'})
            or str(UUID(value['request_id'])) != value['request_id']
            or not isinstance(value['body'], str) or not value['body'].strip()
            or len(content) > 3000):
        raise AccessDenied('Invalid text submission')
    if 'image_upload' in value:
        if (str(UUID(value['image_upload'])) != value['image_upload'] or not isinstance(value['image_sha256'],str)
                or len(value['image_sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['image_sha256'])):
            raise AccessDenied('Invalid image manifest')
    if 'attachments' in value:validate_manifest(value['attachments'])
    return value


def enqueue(desktop, pairings, principal, installation, conversation, grant_id,
            content, nonce, signature, pinned_thread, *, now=None):
    now = int(time.time()) if now is None else now
    value = parse_content(content)
    if not isinstance(pinned_thread, str) or not pinned_thread:
        raise AccessDenied('Existing thread required')
    proofs = OwnerProofs(pairings)
    proofs.consume(nonce, signature, grant_id, principal, installation,
                   conversation, 'send', content, now=now)
    with pairings.connection() as db:
        expires = db.execute('SELECT expires FROM owner_proofs WHERE nonce=?', (nonce,)).fetchone()[0]
    provenance = dict(principal=asdict(principal), installation=installation,
                      conversation=conversation, grant_id=grant_id, thread=pinned_thread)
    encoded = json.dumps(provenance, sort_keys=True)
    with desktop.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM requests WHERE id=?', (value['request_id'],)).fetchone()
        prior = db.execute('SELECT * FROM mobile_requests WHERE id=?', (value['request_id'],)).fetchone()
        if old:
            image_row = db.execute('SELECT data FROM images WHERE request_id=?',(value['request_id'],)).fetchone()
            old_images = json.loads(image_row[0]) if image_row else []
            digest = hashlib.sha256(base64.b64decode(old_images[0]['data'].split(',',1)[1],validate=True)).hexdigest() if old_images else None
            attachment_digest=manifest_digest(value['attachments']) if 'attachments' in value else None
            if (not prior or prior['provenance'] != encoded or old['body'] != value['body']
                    or old['mailbox'] != conversation or stored_manifest(db,value['request_id'])!=attachment_digest
                    or (attachment_digest is None and digest != value.get('image_sha256'))):
                raise AccessDenied('Request identity conflict')
            return dict(request_id=value['request_id'], state=old['state'])
        if db.execute('SELECT count(*) FROM requests WHERE mailbox=? AND state IN (?,?,?)',
                      (conversation, 'queued', 'submitting', 'mobile_queued')).fetchone()[0] >= 10:
            raise AccessDenied('Conversation queue full')
        db.execute('INSERT INTO mobile_requests VALUES(?,?,?)', (value['request_id'], encoded, expires))
        # A distinct state prevents an OLD Desktop process from dispatching this
        # row as unlabelled local input before the reviewed Desktop reload.
        db.execute('INSERT INTO requests(id,mailbox,body,created,state,thread_id) VALUES(?,?,?,?,?,?)',
                   (value['request_id'], conversation, value['body'], now, 'mobile_queued', pinned_thread))
        if 'image_upload' in value:
            from iam_messenger_images import uploaded_image, scope_key
            image = uploaded_image(db,scope_key(principal,installation,conversation,grant_id),value['image_upload'],value['image_sha256'],now)
            db.execute('INSERT INTO images VALUES(?,?)',(value['request_id'],json.dumps([image])))
            db.execute('DELETE FROM mobile_image_chunks WHERE scope=? AND upload=?',
                       (scope_key(principal,installation,conversation,grant_id),value['image_upload']))
        if 'attachments' in value:
            from iam_messenger_uploads import collect,forget
            from iam_messenger_images import scope_key
            from iam_service import IAMError
            scope=scope_key(principal,installation,conversation,grant_id)
            try:images,files=collect(db,desktop,scope,value['attachments'],now)
            except IAMError:raise AccessDenied('Invalid attachment contents') from None
            if images:db.execute('INSERT INTO images VALUES(?,?)',(value['request_id'],json.dumps(images)))
            if files:desktop.save_documents(db,'request',value['request_id'],files)
            db.execute('CREATE TABLE IF NOT EXISTS mobile_attachment_manifests (id TEXT PRIMARY KEY,digest TEXT NOT NULL)')
            db.execute('INSERT INTO mobile_attachment_manifests VALUES(?,?)',(value['request_id'],manifest_digest(value['attachments'])))
            forget(db,scope,value['attachments'])
    return dict(request_id=value['request_id'], state='queued')


def authorize_dispatch(desktop, pairings, request_id, config, thread, *, now=None):
    now = int(time.time()) if now is None else now
    with desktop.connect() as db:
        row = db.execute('SELECT * FROM mobile_requests WHERE id=?', (request_id,)).fetchone()
    if row is None:
        return None
    value = json.loads(row['provenance'])
    policy = config.get('messenger', {})
    principal = Principal(**value['principal'])
    if (policy.get('enabled') is not True or policy.get('send_enabled') is not True
            or row['expires'] <= now or value['thread'] != thread
            or value['installation'] != config.get('node_id')
            or principal.home_server != policy.get('home_server')
            or principal.account_id != policy.get('owner_account_id')):
        raise AccessDenied('Mobile delivery unavailable')
    OwnerProofs(pairings).scope(value['grant_id'], principal, value['installation'],
                               value['conversation'], 'send', now)
    return value
