"""Synthetic connector pump for relay integration tests; no production stores."""
import json
import sys
from pathlib import Path
from iam_federation import MailQueue, Session

root=Path(sys.argv[1]);node=sys.argv[2]
mailbox={'a':'Alice','b':'Bea'}[node]
peer={'a':'b','b':'a'}[node]
config=dict(enabled=True,node_id=node,server_url='wss://synthetic.invalid',
    federation=dict(enabled=True,owner_account_id='owner',epoch=node+'1',peers=[peer],mailboxes=[mailbox]))
(root/'mailboxes'/mailbox/'inbox').mkdir(parents=True,exist_ok=True)
queue=MailQueue(root/'queue.db',config,[mailbox])
session=Session(queue,root)
print(json.dumps({'ready':True}),flush=True)
for line in sys.stdin:
    value=json.loads(line)
    if value['command']=='next':
        session.next_poll=0
        result=session.next_frame()
    elif value['command']=='receive':
        session.receive(value['frame']);result={'accepted':True}
    elif value['command']=='enqueue':
        result=queue.enqueue(mailbox,'Bea' if node=='a' else 'Alice','synthetic','hello from '+node,'message-'+node)
    elif value['command']=='inspect':
        with queue.connect() as db:
            states=[row[0] for row in db.execute('SELECT state FROM outbound')]
        result={'files':len(list((root/'mailboxes'/mailbox/'inbox').glob('*.json'))),'outbound':states}
    elif value['command']=='reset':
        session=Session(queue,root);result={'reset':True}
    else:raise ValueError('Unknown test operation')
    print(json.dumps(result),flush=True)
