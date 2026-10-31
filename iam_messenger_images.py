"""Bounded image transfers, never arbitrary local paths or external URLs."""
import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict
from uuid import UUID
from iam_messenger_access import AccessDenied

MAX_UPLOAD = 524288
UPLOAD_CHUNK = 32768
DOWNLOAD_CHUNK = 65536


def scope_key(principal, installation, conversation, grant):
    return json.dumps([asdict(principal), installation, conversation, grant], sort_keys=True)


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mobile_image_chunks (
        scope TEXT, upload TEXT, part INTEGER, total INTEGER, digest TEXT,
        data BLOB, expires INTEGER, PRIMARY KEY(scope,upload,part))''')


def validate_query(query):
    if not isinstance(query, dict):
        raise AccessDenied('Invalid image query')
    if query.get('action') in {'upload_blob','upload_status'}:
        from iam_messenger_uploads import validate
        return validate(query)
    if query.get('action') == 'read_avatar':
        if (set(query)!={'action','sha256','offset'} or not isinstance(query['sha256'],str)
                or len(query['sha256'])!=64 or any(c not in '0123456789abcdef' for c in query['sha256'])
                or type(query['offset']) is not int or not 0<=query['offset']<1000000 or query['offset']%65536):
            raise AccessDenied('Invalid avatar query')
    elif query.get('action') == 'upload':
        if (set(query) != {'action','upload','part','total','sha256','data'}
                or str(UUID(query['upload'])) != query['upload']
                or type(query['part']) is not int or type(query['total']) is not int
                or not 0 < query['total'] <= MAX_UPLOAD
                or not 0 <= query['part'] < (query['total']+UPLOAD_CHUNK-1)//UPLOAD_CHUNK
                or not isinstance(query['sha256'], str) or len(query['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in query['sha256'])
                or not isinstance(query['data'], str) or len(query['data']) > 44000):
            raise AccessDenied('Invalid upload')
        raw = base64.b64decode(query['data'], validate=True)
        expected = min(UPLOAD_CHUNK, query['total']-query['part']*UPLOAD_CHUNK)
        if len(raw) != expected or base64.b64encode(raw).decode() != query['data']:
            raise AccessDenied('Invalid chunk')
    elif query.get('action') in {'read','read_file'}:
        if (set(query) != {'action','event_id','index','offset'}
                or not isinstance(query['event_id'],str) or len(query['event_id']) > 200
                or type(query['index']) is not int or not 0 <= query['index'] < 4
                or type(query['offset']) is not int or not 0 <= query['offset'] < 4000000
                or query['offset'] % DOWNLOAD_CHUNK):
            raise AccessDenied('Invalid image read')
    else:
        raise AccessDenied('Unsupported image action')
    return query


def upload_chunk(desktop, scope, query, now):
    raw = base64.b64decode(query['data'], validate=True)
    with desktop.connect() as db:
        setup(db)
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM mobile_image_chunks WHERE expires<=?', (now,))
        rows = db.execute('SELECT * FROM mobile_image_chunks WHERE scope=? AND upload=?',
                          (scope,query['upload'])).fetchall()
        if (not rows and db.execute('SELECT count(DISTINCT upload) FROM mobile_image_chunks WHERE scope=?',
                                   (scope,)).fetchone()[0] >= 4):
            raise AccessDenied('Upload storage limit')
        if (not any(row['part'] == query['part'] for row in rows) and
                db.execute('SELECT count(*) FROM mobile_image_chunks').fetchone()[0] >= 2048):
            raise AccessDenied('Upload storage limit')
        for row in rows:
            if row['total'] != query['total'] or row['digest'] != query['sha256']:
                raise AccessDenied('Immutable upload conflict')
            if row['part'] == query['part'] and row['data'] != raw:
                raise AccessDenied('Immutable chunk conflict')
        db.execute('INSERT OR IGNORE INTO mobile_image_chunks VALUES(?,?,?,?,?,?,?)',
                   (scope,query['upload'],query['part'],query['total'],query['sha256'],raw,now+3600))
    return dict(upload=query['upload'],part=query['part'],stored=True)


def uploaded_image(db, scope, upload, digest, now):
    setup(db)
    rows = db.execute('SELECT * FROM mobile_image_chunks WHERE scope=? AND upload=? AND expires>? ORDER BY part',
                      (scope,upload,now)).fetchall()
    raw = b''.join(row['data'] for row in rows)
    if (not rows or len(raw) != rows[0]['total'] or
            [row['part'] for row in rows] != list(range(len(rows))) or
            hashlib.sha256(raw).hexdigest() != digest or rows[0]['digest'] != digest or
            not raw.startswith(b'\xff\xd8\xff')):
        raise AccessDenied('Incomplete or invalid JPEG upload')
    return {'name':'Photo.jpg','data':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}


def event_images(db, mailbox, event_id, history_from):
    try:
        ident, kind = event_id.rsplit(':',1)
    except ValueError:
        raise AccessDenied('Invalid image event') from None
    if kind == 'request':
        row = db.execute('SELECT created FROM requests WHERE id=? AND mailbox=?',(ident,mailbox)).fetchone()
        table, args = 'images WHERE request_id=?', (ident,)
    elif kind == 'reply':
        row = db.execute('''SELECT coalesce(t.created,r.created) FROM requests r LEFT JOIN reply_times t
            ON t.request_id=r.id WHERE r.id=? AND r.mailbox=? AND r.reply IS NOT NULL''',(ident,mailbox)).fetchone()
        table, args = 'published_images WHERE kind=? AND message_id=?', ('reply',ident)
    elif kind == 'notice':
        row = db.execute("SELECT created FROM notices WHERE id=? AND mailbox=? AND kind='agent'",(ident,mailbox)).fetchone()
        table, args = 'published_images WHERE kind=? AND message_id=?', ('notice',ident)
    else:
        raise AccessDenied('Invalid image event')
    if not row or row[0] < history_from:
        raise AccessDenied('Image not shared in this history')
    # Old test/installation schemas without attachment tables have no images.
    name = table.split()[0]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():
        return []
    stored = db.execute('SELECT data FROM '+table,args).fetchone()
    return json.loads(stored[0]) if stored else []


def read_chunk(desktop, mailbox, history_from, query):
    if query['action'] == 'read_file':
        with desktop.connect() as db:
            files = event_documents(db,mailbox,query['event_id'],history_from)
        if query['index'] >= len(files):
            raise AccessDenied('Document not found')
        item=files[query['index']]
        raw=base64.b64decode(item['data'],validate=True)
        if query['offset'] > len(raw) or (query['offset'] == len(raw) and raw):
            raise AccessDenied('Invalid offset')
        return dict(event_id=query['event_id'],index=query['index'],offset=query['offset'],
                    total=len(raw),name=item['name'],sha256=hashlib.sha256(raw).hexdigest(),
                    data=base64.b64encode(raw[query['offset']:query['offset']+DOWNLOAD_CHUNK]).decode())
    with desktop.connect() as db:
        images = event_images(db,mailbox,query['event_id'],history_from)
    images = desktop.validate_images(images)
    if query['index'] >= len(images):
        raise AccessDenied('Image not found')
    image = images[query['index']]
    header, data = image['data'].split(',',1)
    raw = base64.b64decode(data,validate=True)
    if query['offset'] >= len(raw):
        raise AccessDenied('Invalid offset')
    return dict(event_id=query['event_id'],index=query['index'],offset=query['offset'],
                total=len(raw),mime=header[5:].split(';')[0],sha256=hashlib.sha256(raw).hexdigest(),
                data=base64.b64encode(raw[query['offset']:query['offset']+DOWNLOAD_CHUNK]).decode())


def event_documents(db, mailbox, event_id, history_from):
    # Reuse the exact event membership/publication checks, not Desktop's broader
    # owner document lookup (which does not carry a remote history cutoff).
    event_images(db,mailbox,event_id,history_from)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():
        return []
    ident,kind=event_id.rsplit(':',1)
    row=db.execute('SELECT data FROM documents WHERE kind=? AND message_id=?',(kind,ident)).fetchone()
    from iam_desktop_files import validate_files
    return validate_files(json.loads(row[0]) if row else [])
