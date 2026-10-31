"""Synthetic cross-language integration helper; never uses the live IAM root."""
import json
import sqlite3
import sys
import time
from pathlib import Path
from iam_messenger_pairing import Pairings
from iam_messenger_access import Principal
from iam_messenger_connector import pairing_request, history_request, send_request, attachment_request, events_request
from iam_desktop import DesktopStore
from iam_messenger_stream import ForegroundStreams
from iam_messenger_review import PairingReview
streams=ForegroundStreams()

root=Path(sys.argv[1])
store=Pairings(root/'pairings.db')
desktop=root/'desktop.db'
DesktopStore(desktop)
with sqlite3.connect(desktop) as db:
    db.executescript('''CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,mailbox TEXT,body TEXT,created REAL,state TEXT,reply TEXT);
      CREATE TABLE IF NOT EXISTS reply_times(request_id TEXT,created REAL);
      CREATE TABLE IF NOT EXISTS notices(id TEXT,mailbox TEXT,body TEXT,created REAL,kind TEXT);
      INSERT OR IGNORE INTO requests(id,mailbox,body,created,state,reply) VALUES('synthetic','pilot','hello from fixture',100,'replied','synthetic reply');
      INSERT OR IGNORE INTO requests(id,mailbox,body,created,state,reply) VALUES('private','other','must not leave',100,'queued',NULL);''')
    db.execute('INSERT OR IGNORE INTO documents(kind,message_id,data) VALUES(?,?,?)',
               ('reply','synthetic',json.dumps([{'name':'notes.txt','data':'b3JpZ2luYWwgZmlsZQ=='}])))
config=dict(node_id='node',messenger=dict(enabled=True,send_enabled=True,home_server='home',owner_account_id='owner'))
def handle(data):
    now=int(time.time())
    if data.get('command')=='invite':
        return store.invite('home','owner','node',{'pilot'},include_history=True,now=now)
    if data.get('command')=='approve-invite':
        review=PairingReview(store)
        receipt=review.prepare_invitation(data['challenge'],{'pilot'},now=now)
        return {'grant':review.confirm_invitation(receipt['ticket'],{'pilot'},now=now)}
    if data.get('command')=='stream-updates':
        return {'updates':streams.updates(DesktopStore(desktop),store,config,now)}
    if data.get('command')=='approve':
        pending=store.inspect(data['challenge'],now=now)
        return {'grant':store.approve(data['challenge'],Principal(**pending['principal']),'node',{'pilot'},
            now=now,expires_at=now+600,history_from=0,expected_owner_key=pending['owner_key'])}
    if data.get('command')=='revoke':
        store.revoke(data['grant']);return {'revoked':True}
    if data['type'].startswith('messenger_pairing_'):
        return pairing_request(data,config,store)
    if data['type'].startswith('messenger_attachment_'):
        return attachment_request(data,config,store,desktop)
    if data['type'].startswith('messenger_send_'):
        return send_request(data,config,store,desktop,thread_lookup=lambda _: 'synthetic-pinned-thread')
    if data['type'].startswith('messenger_events_'):
        return events_request(data,config,store,desktop,streams=streams)
    return history_request(data,config,store,desktop)

if '--serve' in sys.argv:
    # Match production's resident connector; interpreter startup is not wire latency.
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        print(json.dumps(handle(json.loads(line))),flush=True)
else:
    print(json.dumps(handle(json.load(sys.stdin))))
