"""Opt-in owner pairing over the already authenticated WebConnect socket.

The local installation separately pins the home server and owner account. A
relay identity claim alone never enrolls a key: the desktop must confirm it.
"""
import time
import sqlite3
import json
from uuid import UUID
from iam_messenger_access import AccessDenied, Principal


def conversation_name(conversation):
    """Read only public name metadata; never create mailboxes or expose profiles."""
    import interagentmail as iam
    if (not isinstance(conversation, str) or conversation in ('.', '..')
            or any(c in conversation for c in '/\\\x00:')):
        return conversation
    try:
        value = json.loads((iam.MAILBOXES / conversation / 'profile.json').read_text(encoding='utf-8')).get('display_name')
        if isinstance(value, str) and value.strip() and len(value) <= 200:
            return value.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return conversation


def events_request(frame, config, store, desktop_path, *, now=None, streams=None):
    """Signed owner unread queries/read acknowledgments; never agent input."""
    from iam_messenger_proof import OwnerProofs
    from iam_messenger_keys import decode
    from iam_messenger_reads import mark, unread, subscribe, sync_state
    from iam_desktop import DesktopStore
    live_clock = now is None
    now = int(time.time()) if live_clock else now
    result = dict(protocol='iam-webconnect',version=1,type='messenger_events_result',id=frame.get('id'))
    try:
        execute = frame.get('type') == 'messenger_events_sync'
        fields = {'protocol','version','type','id','principal','installation_id','grant_id','conversation_id','content'}
        if execute: fields |= {'nonce','signature'}
        policy = config.get('messenger',{})
        if (policy.get('enabled') is not True or set(frame)!=fields or frame['protocol']!='iam-webconnect'
                or frame['version']!=1 or str(UUID(frame['id']))!=frame['id']
                or frame['type'] not in ('messenger_events_challenge','messenger_events_sync')):
            raise AccessDenied('Invalid events request')
        principal=Principal(**frame['principal'])
        if (principal.home_server!=policy.get('home_server') or principal.account_id!=policy.get('owner_account_id')
                or frame['installation_id']!=config.get('node_id')):
            raise AccessDenied('Scope')
        content=decode(frame['content'],65536)
        query=json.loads(content)
        if not isinstance(query,dict) or set(query) not in ({'read'},{'read','push'},{'read','listen'},{'read','roster'},{'read','listen','roster'}) or not isinstance(query['read'],list) or len(query['read'])>20:
            raise AccessDenied('Invalid read query')
        from iam_messenger_roster import scopes,read as read_roster
        roster=scopes(query)
        if any(not isinstance(i,str) or not i or len(i)>200 for i in query['read']):
            raise AccessDenied('Invalid read IDs')
        if 'listen' in query and (streams is None or query['read'] or str(UUID(query['listen']))!=query['listen']):
            raise AccessDenied('Invalid subscription')
        proofs=OwnerProofs(store)
        args=dict(grant_id=frame['grant_id'],principal=principal,installation=config['node_id'],
                  conversation=frame['conversation_id'],operation='events',content=content,now=now)
        if not execute:
            result.update(proofs.issue(**args))
        else:
            proofs.consume(nonce=frame['nonce'],signature=frame['signature'],**args)
            grant=proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],'events',now)
            desktop=DesktopStore(desktop_path)
            with desktop.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if query['read']: mark(db,frame['conversation_id'],query['read'],grant.history_from)
                if 'push' in query: subscribe(db,frame['grant_id'],principal,frame['conversation_id'],query['push'],time.time() if live_clock else now)
                current=sync_state(db,frame['conversation_id'],grant.history_from,principal)
                proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],'events',int(time.time()) if live_clock else now)
            result.update(current)
            result['display_name'] = conversation_name(frame['conversation_id'])
            from iam_messenger_profiles import snapshot
            result['profile'] = snapshot(desktop,frame['conversation_id'])
            if roster:
                result['roster']=read_roster(desktop,proofs,principal,config['node_id'],roster,int(time.time()) if live_clock else now)
                for item in roster:proofs.scope(item['grant'],principal,config['node_id'],item['conversation'],'events',int(time.time()) if live_clock else now)
            proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],'events',int(time.time()) if live_clock else now)
            if 'listen' in query:
                result['stream']=streams.subscribe(query['listen'],principal,config['node_id'],frame['conversation_id'],frame['grant_id'],store,now,roster=roster)
    except (AccessDenied,ValueError,TypeError,KeyError,AttributeError,sqlite3.Error,OSError):
        result['error']='events_unavailable'
    return result


def attachment_request(frame, config, store, desktop_path, *, now=None):
    from iam_messenger_proof import OwnerProofs
    from iam_messenger_keys import decode
    from iam_messenger_images import validate_query, scope_key, upload_chunk, read_chunk
    from iam_desktop import DesktopStore
    from iam_service import IAMError
    live_clock = now is None
    now = int(time.time()) if live_clock else now
    result = dict(protocol='iam-webconnect',version=1,type='messenger_attachment_result',id=frame.get('id'))
    try:
        execute = frame.get('type') == 'messenger_attachment_transfer'
        fields = {'protocol','version','type','id','principal','installation_id','grant_id','conversation_id','content'}
        if execute: fields |= {'nonce','signature'}
        policy = config.get('messenger',{})
        if (policy.get('enabled') is not True or set(frame) != fields or frame['protocol'] != 'iam-webconnect'
                or frame['version'] != 1 or str(UUID(frame['id'])) != frame['id']
                or frame['type'] not in ('messenger_attachment_challenge','messenger_attachment_transfer')):
            raise AccessDenied('Invalid attachment request')
        principal = Principal(**frame['principal'])
        if (principal.home_server != policy.get('home_server') or principal.account_id != policy.get('owner_account_id')
                or frame['installation_id'] != config.get('node_id')):
            raise AccessDenied('Scope')
        content = decode(frame['content'],65536)
        query = validate_query(json.loads(content))
        if query['action'] in {'upload','upload_blob','upload_status'} and policy.get('send_enabled') is not True:
            raise AccessDenied('Sending disabled')
        proofs = OwnerProofs(store)
        args = dict(grant_id=frame['grant_id'],principal=principal,installation=config['node_id'],
                    conversation=frame['conversation_id'],operation='attachment',content=content,now=now)
        if not execute:
            result.update(proofs.issue(**args))
        else:
            proofs.consume(nonce=frame['nonce'],signature=frame['signature'],**args)
            grant = proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],'attachment',now)
            desktop = DesktopStore(desktop_path)
            if query['action'] == 'upload':
                value = upload_chunk(desktop,scope_key(principal,config['node_id'],frame['conversation_id'],frame['grant_id']),query,now)
            elif query['action'] in {'upload_blob','upload_status'}:
                from iam_messenger_uploads import transfer
                value=transfer(desktop,scope_key(principal,config['node_id'],frame['conversation_id'],frame['grant_id']),query,now)
            elif query['action']=='read_avatar':
                from iam_messenger_profiles import read_avatar
                value=read_avatar(desktop,frame['conversation_id'],query)
            else:
                value = read_chunk(desktop,frame['conversation_id'],grant.history_from,query)
            proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],
                         'attachment',int(time.time()) if live_clock else now)
            result['attachment'] = value
    except (AccessDenied,ValueError,TypeError,KeyError,AttributeError,sqlite3.Error,OSError,IAMError):
        result['error'] = 'attachment_unavailable'
    return result


def send_request(frame, config, store, desktop_path, *, now=None, thread_lookup=None):
    from iam_messenger_proof import OwnerProofs
    from iam_messenger_keys import decode
    from iam_messenger_send import parse_content, enqueue
    from iam_desktop import DesktopStore
    now = int(time.time()) if now is None else now
    result = dict(protocol='iam-webconnect', version=1, type='messenger_send_result', id=frame.get('id'))
    try:
        policy = config.get('messenger', {})
        execute = frame.get('type') == 'messenger_send_submit'
        fields = {'protocol','version','type','id','principal','installation_id','grant_id','conversation_id','content'}
        if execute:
            fields |= {'nonce','signature'}
        if (policy.get('enabled') is not True or policy.get('send_enabled') is not True
                or set(frame) != fields or frame['protocol'] != 'iam-webconnect' or frame['version'] != 1
                or str(UUID(frame['id'])) != frame['id']
                or frame['type'] not in ('messenger_send_challenge','messenger_send_submit')):
            raise AccessDenied('Disabled or invalid')
        principal = Principal(**frame['principal'])
        if (principal.home_server != policy.get('home_server') or principal.account_id != policy.get('owner_account_id')
                or frame['installation_id'] != config.get('node_id')):
            raise AccessDenied('Scope')
        content = decode(frame['content'],4096)
        parse_content(content)
        if not execute:
            result.update(OwnerProofs(store).issue(frame['grant_id'],principal,config['node_id'],
                frame['conversation_id'],'send',content,now=now))
        else:
            if thread_lookup is None:
                import interagentmail as iam
                from iam_service import session_owner
                from iam_codex_bridge import DeliveryState
                from iam_orchestrator import registered_projects
                if frame['conversation_id'] not in registered_projects():
                    raise AccessDenied('Unknown conversation')
                thread = DeliveryState.load(iam.mailbox(session_owner(frame['conversation_id'])) / '.codex-bridge-state.json').thread_id
            else:
                thread = thread_lookup(frame['conversation_id'])
            result.update(enqueue(DesktopStore(desktop_path),store,principal,config['node_id'],frame['conversation_id'],
                frame['grant_id'],content,frame['nonce'],frame['signature'],thread,now=now))
            if result['state'] == 'mobile_queued':
                result['state'] = 'queued'
    except (AccessDenied,ValueError,TypeError,KeyError,AttributeError,sqlite3.Error,OSError):
        result['error'] = 'send_unavailable'
    return result


def history_request(frame, config, store, desktop_path, *, now=None):
    """Only signed history reads; no caller-selected RPC or local file path."""
    from iam_messenger_proof import OwnerProofs
    from iam_messenger_history import history_page
    from iam_messenger_keys import decode
    live_clock = now is None
    now = int(time.time()) if live_clock else now
    result = dict(protocol='iam-webconnect', version=1,
                  type='messenger_history_result', id=frame.get('id'))
    try:
        policy = config.get('messenger', {})
        execute = frame.get('type') == 'messenger_history_read'
        fields = {'protocol','version','type','id','principal','installation_id',
                  'grant_id','conversation_id','content'}
        if execute:
            fields |= {'nonce','signature'}
        if (not isinstance(policy,dict) or policy.get('enabled') is not True
                or set(frame) != fields or frame['protocol'] != 'iam-webconnect'
                or frame['version'] != 1 or str(UUID(frame['id'])) != frame['id']
                or frame['type'] not in ('messenger_history_challenge','messenger_history_read')):
            raise AccessDenied('Invalid request')
        principal = Principal(**frame['principal'])
        if (not policy.get('home_server') or not policy.get('owner_account_id')
                or principal.home_server != policy['home_server']
                or principal.account_id != policy['owner_account_id']
                or frame['installation_id'] != config.get('node_id')):
            raise AccessDenied('Invalid scope')
        content = decode(frame['content'],4096)
        proofs = OwnerProofs(store)
        args = dict(grant_id=frame['grant_id'],principal=principal,installation=config['node_id'],
                    conversation=frame['conversation_id'],operation='history',content=content,now=now)
        if not execute:
            result.update(proofs.issue(**args))
        else:
            proofs.consume(nonce=frame['nonce'],signature=frame['signature'],**args)
            grant = proofs.scope(frame['grant_id'],principal,config['node_id'],
                                 frame['conversation_id'],'history',now)
            query = json.loads(content)
            if isinstance(query,dict) and set(query) == {'lookup'}:
                from iam_messenger_send import lookup_request
                page = dict(lookup=lookup_request(desktop_path,principal,config['node_id'],
                    frame['conversation_id'],frame['grant_id'],grant.history_from,query['lookup']))
            elif isinstance(query,dict) and set(query) == {'text'}:
                from iam_messenger_history import text_chunk
                page = dict(text=text_chunk(desktop_path,frame['conversation_id'],grant.history_from,query['text']))
            else:
                page = history_page(desktop_path,frame['conversation_id'],grant.history_from,content)
            proofs.scope(frame['grant_id'],principal,config['node_id'],frame['conversation_id'],
                         'history',int(time.time()) if live_clock else now)
            result.update(page)
    except (AccessDenied,ValueError,TypeError,KeyError,AttributeError,sqlite3.Error,OSError):
        result['error'] = 'history_unavailable'
    return result


def pairing_request(frame, config, store, *, now=None):
    now = int(time.time()) if now is None else now
    result = dict(protocol='iam-webconnect', version=1,
                  type='messenger_pairing_result', id=frame.get('id'))
    try:
        policy = config.get('messenger', {})
        if not isinstance(policy, dict) or policy.get('enabled') is not True:
            raise AccessDenied('disabled')
        status = frame.get('type') == 'messenger_pairing_status'
        claim = frame.get('type') == 'messenger_pairing_claim'
        if (set(frame) != {'protocol','version','type','id','principal','installation_id',
                          'challenge' if status else 'owner_key'} | ({'challenge'} if claim else set())
                or frame['protocol'] != 'iam-webconnect' or frame['version'] != 1
                or frame['type'] not in ('messenger_pairing_request', 'messenger_pairing_status', 'messenger_pairing_claim')
                or str(UUID(frame['id'])) != frame['id']):
            raise AccessDenied('invalid')
        principal = Principal(**frame['principal'])
        if (not policy.get('home_server') or not policy.get('owner_account_id')
                or principal.home_server != policy['home_server']
                or principal.account_id != policy['owner_account_id']
                or frame['installation_id'] != config.get('node_id')):
            raise AccessDenied('scope')
        if status:
            result.update(store.status(frame['challenge'], principal, config['node_id'], now=now))
            if result.get('state') == 'approved' and result.get('conversations'):
                result['display_names'] = {c: conversation_name(c) for c in result['conversations']}
        elif claim:
            result.update(store.claim_invitation(frame['challenge'],principal,config['node_id'],frame['owner_key'],now=now))
        else:
            if not frame['owner_key']:
                raise AccessDenied('key required')
            challenge = store.request(principal, config['node_id'], now=now, owner_key=frame['owner_key'])
            result.update(challenge=challenge, expires_at=now+300)
    except (AccessDenied, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, OSError):
        result['error'] = 'pairing_unavailable'
    return result
