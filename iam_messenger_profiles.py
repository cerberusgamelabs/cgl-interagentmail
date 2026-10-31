"""Small public profile projection and hash-bound avatar reads; no file paths."""
import base64
import hashlib
import re
import json
import time
from iam_messenger_access import AccessDenied


def avatar(profile):
    value=profile.get('avatar','')
    if not isinstance(value,str) or len(value)>1400000:return None
    match=re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)',value)
    if not match:return None
    try:raw=base64.b64decode(match[2],validate=True)
    except ValueError:return None
    if not 0<len(raw)<=1000000:return None
    return dict(mime=match[1],sha256=hashlib.sha256(raw).hexdigest(),total=len(raw)),raw


def snapshot(desktop,mailbox):
    profile=desktop.profile(mailbox)
    result={key:profile[key] for key,limit in [('status',180),('presence',20)]
            if isinstance(profile.get(key),str) and len(profile[key])<=limit}
    if result.get('presence') not in ('available','busy','away','do_not_disturb',''):result.pop('presence',None)
    picture=avatar(profile)
    if picture:result['avatar']=picture[0]
    with desktop.connect() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mobile_public_runtime'").fetchone():
            row=db.execute('SELECT data,seen FROM mobile_public_runtime WHERE mailbox=?',(mailbox,)).fetchone()
            if row:
                runtime=json.loads(row[0]);runtime['stale']=time.time()-row[1]>30
                if runtime['stale']:runtime['availability']='unavailable'
                result['runtime']=runtime
    return result


def publish_runtime(desktop,buddies,now=None):
    """Cache only already observed metadata. Never attach or open a session."""
    now=time.time() if now is None else now
    with desktop.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS mobile_public_runtime(mailbox TEXT PRIMARY KEY,data TEXT,seen REAL)')
        for buddy in buddies:
            context=buddy.get('context') or {}
            safe_context={}
            if context.get('state')=='compacting':safe_context['state']='compacting'
            elif type(context.get('remainingPercent')) is int and 0<=context['remainingPercent']<=100:
                safe_context=dict(remainingPercent=context['remainingPercent'],stale=bool(context.get('stale')))
            runtime=dict(availability=buddy.get('availability','unavailable') if buddy.get('sessionConnected') else 'not attached',context=safe_context)
            db.execute('INSERT OR REPLACE INTO mobile_public_runtime VALUES(?,?,?)',(buddy['mailbox'],json.dumps(runtime),now))


def read_avatar(desktop,mailbox,query):
    picture=avatar(desktop.profile(mailbox))
    if not picture or picture[0]['sha256']!=query['sha256']:raise AccessDenied('Avatar changed')
    meta,raw=picture;offset=query['offset']
    if offset>=len(raw):raise AccessDenied('Invalid avatar offset')
    return dict(meta,offset=offset,data=base64.b64encode(raw[offset:offset+65536]).decode())
