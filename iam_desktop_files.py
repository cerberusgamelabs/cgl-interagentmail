"""Explicit, bounded document sharing. Never executes or renders document HTML."""
import base64
import hashlib
import json
import re
from pathlib import Path
from iam_service import IAMError

EXTENSIONS = {'.txt', '.md', '.csv', '.json', '.log', '.pdf', '.docx', '.xlsx', '.pptx'}
TEXT = {'.txt', '.md', '.csv', '.json', '.log'}


def validate_files(items):
    if not isinstance(items, list) or len(items) > 4:
        raise IAMError('Attach at most four documents')
    result, total = [], 0
    for item in items:
        if not isinstance(item, dict):
            raise IAMError('Invalid document')
        name, data = item.get('name'), item.get('data')
        if (not isinstance(name, str) or not 1 <= len(name) <= 120 or
                re.search(r'[\x00-\x1f<>:"/\\|?*]', name) or name.endswith((' ', '.')) or
                Path(name).suffix.lower() not in EXTENSIONS):
            raise IAMError('Use a plain document filename: TXT, MD, CSV, JSON, LOG, PDF, DOCX, XLSX or PPTX')
        if not isinstance(data, str) or len(data) > 5400000:
            raise IAMError('Invalid document data')
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            raise IAMError('Invalid document encoding') from None
        total += len(raw)
        if len(raw) > 4000000 or total > 8000000:
            raise IAMError('Documents: 4 MB each, 8 MB total')
        ext = Path(name).suffix.lower()
        if ext in TEXT:
            try: raw.decode('utf-8-sig')
            except UnicodeDecodeError: raise IAMError('Text documents must use UTF-8') from None
            if b'\x00' in raw: raise IAMError('Binary data is not a text document')
        elif ext == '.pdf' and not raw.startswith(b'%PDF-'):
            raise IAMError('Invalid PDF signature')
        elif ext in {'.docx', '.xlsx', '.pptx'} and not raw.startswith(b'PK\x03\x04'):
            raise IAMError('Invalid Office document signature')
        result.append({'name': name, 'data': data})
    return result


def local_publish_files(paths, project_root):
    if paths is None: return []
    if not isinstance(paths, list) or len(paths) > 4: raise IAMError('Attach at most four documents')
    items = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute(): path = Path(project_root) / path
        with path.open('rb') as stream: raw = stream.read(4000001)
        if len(raw) > 4000000: raise IAMError('Document exceeds 4 MB')
        items.append({'name': path.name, 'data': base64.b64encode(raw).decode('ascii')})
    return validate_files(items)


class DocumentStore:
    def document_refs(self, db, kind, message_id, mailbox):
        row = db.execute('SELECT data FROM documents WHERE kind=? AND message_id=?', (kind, message_id)).fetchone()
        return [{'mailbox': mailbox, 'kind': kind, 'message_id': message_id, 'index': index,
                 'name': item['name'], 'size': len(base64.b64decode(item['data']))}
                for index, item in enumerate(json.loads(row[0]) if row else [])]

    def save_documents(self, db, kind, message_id, items, immutable=False):
        row = db.execute('SELECT data FROM documents WHERE kind=? AND message_id=?', (kind, message_id)).fetchone()
        if (row or immutable) and (json.loads(row[0]) if row else []) != items:
            raise IAMError('Published documents cannot be replaced')
        if items: db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?)', (kind, message_id, json.dumps(items)))

    def document(self, mailbox, kind, message_id, index):
        if kind not in {'request', 'reply', 'notice'} or type(index) is not int or not 0 <= index < 4:
            raise IAMError('Invalid document reference')
        with self.connect() as db:
            table = 'notices' if kind == 'notice' else 'requests'
            if not db.execute(f'SELECT 1 FROM {table} WHERE id=? AND mailbox=?', (message_id, mailbox)).fetchone():
                raise IAMError('Document does not belong to this mailbox')
            row = db.execute('SELECT data FROM documents WHERE kind=? AND message_id=?', (kind, message_id)).fetchone()
            items = validate_files(json.loads(row[0]) if row else [])
            if index >= len(items): raise IAMError('Document unavailable')
            return items[index]

    def document_inputs(self, request_id, mailbox):
        with self.connect() as db: refs = self.document_refs(db, 'request', request_id, mailbox)
        entries = []
        for ref in refs:
            item = self.document(mailbox, 'request', request_id, ref['index'])
            raw = base64.b64decode(item['data'])
            folder = self.path.parent / 'desktop-documents'
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (hashlib.sha256(raw).hexdigest() + Path(item['name']).suffix.lower())
            if target.exists():
                if target.read_bytes() != raw: raise IAMError('Document integrity mismatch')
            else:
                with target.open('xb') as stream: stream.write(raw)
            entries.append({'name': item['name'], 'path': str(target.resolve())})
        return entries
