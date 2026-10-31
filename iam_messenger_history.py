"""Read-only, projected Desktop history. No paths, errors or approval details.

Does not mark messages seen. Large text is explicitly flagged as truncated;
attachments and full-text retrieval require separate operations.
"""
import json
import base64
import hashlib
import math
import sqlite3
from pathlib import Path
from iam_messenger_access import AccessDenied


def text_chunk(path, mailbox, history_from, query):
    """Read one bounded UTF-8 chunk of an explicitly published history event."""
    if (not isinstance(query, dict) or set(query) != {'event_id', 'offset'}
            or not isinstance(query['event_id'], str) or not 1 <= len(query['event_id']) <= 200
            or type(query['offset']) is not int or query['offset'] < 0):
        raise AccessDenied('Invalid text query')
    event_id, offset = query['event_id'], query['offset']
    identity, separator, kind = event_id.rpartition(':')
    if not separator or not identity or kind not in {'request', 'reply', 'notice'}:
        raise AccessDenied('Invalid text event')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if kind == 'request':
            row = db.execute('SELECT body FROM requests WHERE id=? AND mailbox=? AND created>=?',
                             (identity, mailbox, history_from)).fetchone()
        elif kind == 'reply':
            row = db.execute('''SELECT r.reply FROM requests r LEFT JOIN reply_times t ON t.request_id=r.id
                WHERE r.id=? AND r.mailbox=? AND r.reply IS NOT NULL
                AND coalesce(t.created,r.created)>=?''', (identity, mailbox, history_from)).fetchone()
        else:
            row = db.execute("SELECT body FROM notices WHERE id=? AND mailbox=? AND kind='agent' AND created>=?",
                             (identity, mailbox, history_from)).fetchone()
        if row is None:
            raise AccessDenied('Text unavailable')
        data = (row[0] or '').encode('utf-8')
        if len(data) > 1_000_000 or offset > len(data):
            raise AccessDenied('Text size or offset unavailable')
        return dict(event_id=event_id, offset=offset, total=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    data=base64.b64encode(data[offset:offset + 65536]).decode('ascii'))
    finally:
        db.close()


def history_page(path, mailbox, history_from, content):
    try:
        args = json.loads(content)
        if not isinstance(args, dict) or set(args) not in ({'after', 'limit'},{'before','limit'}):
            raise ValueError()
        reverse='before' in args
        after, limit = args.get('before') if reverse else args['after'], args['limit']
        latest=reverse and after is None
        if latest: after=[0,'']
        if (not isinstance(after, list) or len(after) != 2
                or type(after[0]) not in (int, float) or not math.isfinite(after[0]) or after[0] < 0
                or not isinstance(after[1], str) or len(after[1]) > 200
                or type(limit) is not int or not 1 <= limit <= 50):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AccessDenied('Invalid history query') from None
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        comparator='<' if reverse else '>'
        condition='1=1' if latest else f'(created {comparator} ? OR (created = ? AND event_id {comparator} ?))'
        order='created DESC,event_id DESC' if reverse else 'created,event_id'
        parameters=(mailbox,mailbox,mailbox,history_from)+(() if latest else (after[0],after[0],after[1]))+(limit+1,)
        rows = db.execute(f'''SELECT * FROM (
            SELECT id || ':request' AS event_id, id AS request_id, created,
                   'owner' AS sender, body, state,
                   CASE WHEN reply IS NOT NULL THEN 'replied'
                        WHEN error='Turn finished; no reply published.' THEN 'no_reply'
                        WHEN state='accepted' THEN 'received' ELSE 'sent' END AS delivery
                   FROM requests WHERE mailbox=?
            UNION ALL
            SELECT r.id || ':reply', r.id, coalesce(t.created,r.created),
                   'resident', r.reply, 'replied', NULL FROM requests r
            LEFT JOIN reply_times t ON t.request_id=r.id
            WHERE r.mailbox=? AND r.reply IS NOT NULL
            UNION ALL
            SELECT id || ':notice', NULL, created, 'resident', body, 'replied', NULL
            FROM notices WHERE mailbox=? AND kind='agent'
            ) WHERE created >= ? AND {condition}
            ORDER BY {order} LIMIT ?''', parameters).fetchall()
        events, size = [], 0
        for row in rows[:limit]:
            event = dict(row)
            from iam_messenger_images import event_images
            images = event_images(db,mailbox,event['event_id'],history_from)
            event['image_count'] = min(len(images),4)
            from iam_messenger_images import event_documents
            event['documents'] = [dict(name=item['name'],size=len(base64.b64decode(item['data'])))
                                  for item in event_documents(db,mailbox,event['event_id'],history_from)]
            text = event['body'] or ''
            event['body'] = text[:16000]
            event['truncated'] = len(text) > 16000
            if event['state'] == 'mobile_queued':
                event['state'] = 'queued'
            if event['state'] not in {'queued','submitting','accepted','held','replied','failed'}:
                event['state'] = 'held'
            length = len(json.dumps(event,ensure_ascii=False).encode('utf-8'))
            if events and size + length > 100000:
                break
            events.append(event)
            size += length
        cursor = [events[-1]['created'],events[-1]['event_id']] if events else after
        if reverse: events.reverse()  # Display remains chronological; only retrieval reverses.
        return dict(events=events, after=cursor, more=len(rows)>len(events))
    finally:
        db.close()
