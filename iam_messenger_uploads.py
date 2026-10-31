"""Original attachment staging, bound to exact signed owner scope and manifest."""
import base64
import hashlib
import json
import re
from uuid import UUID
from iam_messenger_access import AccessDenied

CHUNK = 35000
MAX_FILE = 4000000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def fingerprint(meta):
    return hashlib.sha256(encoded({k:meta[k] for k in ('kind','name','size','sha256')}).encode()).hexdigest()


def validate(query):
    status = query.get('action') == 'upload_status'
    fields = {'action','upload','kind','name','size','sha256'} | (set() if status else {'part','data'})
    if (set(query) != fields or query.get('action') not in ('upload_blob','upload_status')
            or str(UUID(query['upload'])) != query['upload']
            or query['kind'] not in ('image','document')
            or not isinstance(query['name'],str) or not 1 <= len(query['name']) <= 120
            or re.search(r'[\x00-\x1f\x7f<>:"/\\|?*]',query['name']) or query['name'].endswith(('.', ' '))
            or type(query['size']) is not int or not 0 <= query['size'] <= MAX_FILE
            or not isinstance(query['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',query['sha256'])):
        raise AccessDenied('Invalid attachment upload')
    suffix=query['name'].rsplit('.',1)[-1].lower()
    allowed={'image':{'png','jpg','jpeg','webp'},'document':{'txt','md','csv','json','log','pdf','docx','xlsx','pptx'}}
    if '.' not in query['name'] or suffix not in allowed[query['kind']]:
        raise AccessDenied('Unsupported attachment type')
    if not status:
        if (type(query['part']) is not int or not 0 <= query['part'] < max(1,(query['size']+CHUNK-1)//CHUNK)
                or not isinstance(query['data'],str) or len(query['data'])>46668):
            raise AccessDenied('Invalid attachment chunk')
        raw=base64.b64decode(query['data'],validate=True)
        if len(raw)!=min(CHUNK,query['size']-query['part']*CHUNK) or base64.b64encode(raw).decode()!=query['data']:
            raise AccessDenied('Invalid attachment chunk')
    return query


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mobile_uploads (
        scope TEXT,upload TEXT,metadata TEXT,size INTEGER,expires INTEGER,PRIMARY KEY(scope,upload))''')
    db.execute('''CREATE TABLE IF NOT EXISTS mobile_upload_parts (
        scope TEXT,upload TEXT,part INTEGER,data BLOB,PRIMARY KEY(scope,upload,part))''')


def transfer(desktop,scope,query,now):
    meta={k:query[k] for k in ('kind','name','size','sha256')}
    metadata=encoded(meta)
    with desktop.connect() as db:
        setup(db);db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM mobile_upload_parts WHERE EXISTS (SELECT 1 FROM mobile_uploads u WHERE u.scope=mobile_upload_parts.scope AND u.upload=mobile_upload_parts.upload AND u.expires<=?)',(now,))
        db.execute('DELETE FROM mobile_uploads WHERE expires<=?',(now,))
        old=db.execute('SELECT metadata FROM mobile_uploads WHERE scope=? AND upload=?',(scope,query['upload'])).fetchone()
        if old and old[0]!=metadata:raise AccessDenied('Immutable attachment conflict')
        if query['action']=='upload_blob':
            if not old:
                count,size=db.execute('SELECT count(*),coalesce(sum(size),0) FROM mobile_uploads WHERE scope=?',(scope,)).fetchone()
                global_size=db.execute('SELECT coalesce(sum(size),0) FROM mobile_uploads').fetchone()[0]
                if count>=8 or size+query['size']>16000000 or global_size+query['size']>64000000:
                    raise AccessDenied('Attachment storage limit')
                db.execute('INSERT INTO mobile_uploads VALUES(?,?,?,?,?)',(scope,query['upload'],metadata,query['size'],now+3600))
            raw=base64.b64decode(query['data'],validate=True)
            previous=db.execute('SELECT data FROM mobile_upload_parts WHERE scope=? AND upload=? AND part=?',(scope,query['upload'],query['part'])).fetchone()
            if previous and previous[0]!=raw:raise AccessDenied('Immutable chunk conflict')
            db.execute('INSERT OR IGNORE INTO mobile_upload_parts VALUES(?,?,?,?)',(scope,query['upload'],query['part'],raw))
        parts=db.execute('SELECT part FROM mobile_upload_parts WHERE scope=? AND upload=? ORDER BY part',(scope,query['upload'])).fetchall()
        next_part=0
        for row in parts:
            if row[0]!=next_part:break
            next_part+=1
        return dict(upload=query['upload'],digest=fingerprint(meta),next_part=next_part,
                    **(dict(part=query['part'],stored=True) if query['action']=='upload_blob' else {}))


def validate_manifest(items):
    if not isinstance(items,list) or not 1<=len(items)<=8:raise AccessDenied('Invalid attachment manifest')
    seen=set()
    for item in items:
        if (not isinstance(item,dict) or set(item)!={'upload','digest'} or str(UUID(item['upload']))!=item['upload']
                or not isinstance(item['digest'],str) or not re.fullmatch('[0-9a-f]{64}',item['digest']) or item['upload'] in seen):
            raise AccessDenied('Invalid attachment manifest')
        seen.add(item['upload'])
    return items


def manifest_digest(items):
    return hashlib.sha256(encoded([dict(upload=i['upload'],digest=i['digest']) for i in items]).encode()).hexdigest()


def collect(db,desktop,scope,items,now):
    from iam_desktop_files import validate_files
    setup(db);images=[];files=[]
    for item in validate_manifest(items):
        row=db.execute('SELECT metadata FROM mobile_uploads WHERE scope=? AND upload=? AND expires>?',(scope,item['upload'],now)).fetchone()
        if not row:raise AccessDenied('Upload expired or missing; resume upload')
        meta=json.loads(row[0])
        parts=db.execute('SELECT part,data FROM mobile_upload_parts WHERE scope=? AND upload=? ORDER BY part',(scope,item['upload'])).fetchall()
        raw=b''.join(p['data'] for p in parts)
        if ([p['part'] for p in parts]!=list(range(max(1,(meta['size']+CHUNK-1)//CHUNK))) or
                len(raw)!=meta['size'] or hashlib.sha256(raw).hexdigest()!=meta['sha256'] or fingerprint(meta)!=item['digest']):
            raise AccessDenied('Incomplete or changed attachment')
        data=base64.b64encode(raw).decode()
        if meta['kind']=='document':files.append(dict(name=meta['name'],data=data))
        else:
            suffix=meta['name'].rsplit('.',1)[-1].lower();mime='image/'+('jpeg' if suffix in ('jpg','jpeg') else suffix)
            images.append(dict(name=meta['name'],data='data:'+mime+';base64,'+data))
    return desktop.validate_images(images),validate_files(files)


def forget(db,scope,items):
    for item in items:
        db.execute('DELETE FROM mobile_upload_parts WHERE scope=? AND upload=?',(scope,item['upload']))
        db.execute('DELETE FROM mobile_uploads WHERE scope=? AND upload=?',(scope,item['upload']))
