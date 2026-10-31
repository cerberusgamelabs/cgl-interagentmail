"""Ephemeral, signed foreground leases. Hints contain no message bodies.

The connector checks durable local state; phones subscribe rather than polling
history. Each hint is authorized again against the local grant before delivery.
Disconnect drops all leases; reconnect requires a fresh signed subscription.
"""
import hashlib
import json
from iam_messenger_access import AccessDenied
from iam_messenger_proof import OwnerProofs


class ForegroundStreams:
    def __init__(self):
        self.leases={}

    def subscribe(self, stream_id, principal, installation, conversation, grant_id, pairings, now,roster=None):
        grant=OwnerProofs(pairings).scope(grant_id,principal,installation,conversation,'events',now)
        self.leases={key:value for key,value in self.leases.items() if value['expires']>now}
        if stream_id in self.leases and self.leases[stream_id]['principal']!=principal:
            raise AccessDenied('Stream identity conflict')
        # A new foreground connection replaces the same phone's previous view.
        self.leases={key:value for key,value in self.leases.items() if value['principal']!=principal}
        if len(self.leases)>=64:raise AccessDenied('Stream limit')
        expires=min(now+300,grant.expires_at)
        if roster:
            for scope in roster:
                other=OwnerProofs(pairings).scope(scope['grant'],principal,installation,scope['conversation'],'events',now)
                expires=min(expires,other.expires_at)
        self.leases[stream_id]=dict(principal=principal,installation=installation,conversation=conversation,roster=roster,
            grant_id=grant_id,expires=expires,fingerprint=None,revision=0)
        return dict(id=stream_id,expires_at=expires)

    def updates(self, desktop, pairings, config, now):
        result=[]
        for stream_id,lease in list(self.leases.items()):
            principal=lease['principal'];policy=config.get('messenger',{})
            frame=dict(protocol='iam-webconnect',version=1,type='messenger_stream_state',stream_id=stream_id,
                account_id=principal.account_id,device_id=principal.device_id)
            try:
                if (lease['expires']<=now or policy.get('enabled') is not True
                        or principal.account_id!=policy.get('owner_account_id') or principal.home_server!=policy.get('home_server')):
                    raise AccessDenied('Stream expired')
                proofs=OwnerProofs(pairings)
                grant=proofs.scope(lease['grant_id'],principal,lease['installation'],lease['conversation'],'events',now)
                with desktop.connect() as db:
                    # Read metadata, not transcript text. Changes to reply/state and
                    # exact read markers invalidate the foreground snapshot.
                    rows=db.execute('''SELECT id,state,reply IS NOT NULL,error='Turn finished; no reply published.' FROM requests
                        WHERE mailbox=? AND created>=? ORDER BY id''',(lease['conversation'],grant.history_from)).fetchall()
                    from iam_messenger_reads import unread, incoming
                    ids=[row['event_id'] for row in incoming(db,lease['conversation'],grant.history_from)]
                    unread_ids=unread(db,lease['conversation'],grant.history_from)
                    from iam_messenger_profiles import snapshot
                    from iam_messenger_connector import conversation_name
                    public_profile=snapshot(desktop,lease['conversation'])
                    roster=None
                    if lease.get('roster'):
                        from iam_messenger_roster import read
                        roster=read(desktop,proofs,principal,lease['installation'],lease['roster'],now)
                    fingerprint=hashlib.sha256(json.dumps([list(map(tuple,rows)),ids,unread_ids,
                        conversation_name(lease['conversation']),public_profile,roster],sort_keys=True).encode()).hexdigest()
                proofs.scope(lease['grant_id'],principal,lease['installation'],lease['conversation'],'events',now)
                if fingerprint!=lease['fingerprint']:
                    lease['fingerprint']=fingerprint;lease['revision']+=1
                    result.append(dict(frame,revision=lease['revision'],state='changed'))
            except AccessDenied:
                result.append(dict(frame,revision=lease['revision']+1,state='ended'))
                self.leases.pop(stream_id,None)
        return result
