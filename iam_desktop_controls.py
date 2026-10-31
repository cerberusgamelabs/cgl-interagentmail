"""Narrow, explicit desktop session controls; never expose transcript bodies."""
import asyncio
import secrets
import time
from pathlib import Path

from iam_service import IAMError


class SessionControls:
    def __init__(self, backend):
        self.backend = backend
        self.tickets = {}
        self.settings = {}
        self.turns = {}
        self.instructions = {}
        with backend.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS control_attempts (id TEXT PRIMARY KEY, thread TEXT, action TEXT, state TEXT, created REAL)')

    def observe(self, owner, thread, message):
        p = message.get('params', {})
        if p.get('threadId') != thread:
            return
        if message['method'] == 'thread/settings/updated':
            self.settings[owner] = self.safe_settings(p.get('threadSettings', {}))
        elif message['method'] == 'turn/started':
            self.turns[owner] = p.get('turn', {}).get('id')
        elif message['method'] == 'turn/completed' and self.turns.get(owner) == p.get('turn', {}).get('id'):
            self.turns.pop(owner, None)

    @staticmethod
    def safe_settings(value):
        return {'model': value.get('model'), 'effort': value.get('effort', value.get('reasoningEffort')),
                'approvalPolicy': value.get('approvalPolicy'), 'sandboxPolicy': value.get('sandboxPolicy', value.get('sandbox')),
                'mode': (value.get('collaborationMode') or {}).get('mode')}

    async def handle(self, mailbox, action, args):
        from iam_desktop import session_owner
        from iam_codex_bridge import DeliveryState
        from iam_orchestrator import appserver_client, configured_endpoint
        from urllib.parse import urlsplit
        import interagentmail as iam
        owner = session_owner(mailbox)
        if mailbox not in self.backend.projects():
            raise IAMError('Unknown registered mailbox')
        if owner in self.backend.clients:
            return await self._handle(mailbox, action, args, self.backend.clients[owner])
        endpoint = configured_endpoint()
        if endpoint != 'managed://' and urlsplit(endpoint).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise IAMError('Controls require a local app-server')
        thread = DeliveryState.load(iam.mailbox(owner) / '.codex-bridge-state.json').thread_id
        if not thread:
            raise IAMError('No pinned session; no replacement created')
        client = appserver_client(endpoint, request_timeout=15)
        try:
            await client.connect()
            return await self._handle(mailbox, action, args, (client, thread))
        finally:
            await client.close()

    async def _handle(self, mailbox, action, args, connection):
        from iam_desktop import session_owner
        from iam_codex_bridge import DeliveryState, turn_sandbox_policy, AppServerRpcError
        import interagentmail as iam
        b = self.backend
        projects = b.projects()
        if mailbox not in projects:
            raise IAMError('Unknown registered mailbox')
        owner = session_owner(mailbox)
        async with b.locks.setdefault(owner, asyncio.Lock()):
            client, thread = connection
            pinned = DeliveryState.load(iam.mailbox(owner) / '.codex-bridge-state.json').thread_id
            if pinned != thread:
                raise IAMError('Session binding changed. Reopen the conversation before using controls.')
            t = (await client.request('thread/read', {'threadId': thread, 'includeTurns': False})).get('thread', {})
            if t.get('id') != thread or Path(t.get('cwd', '')).resolve() != Path(projects[owner]['project_root']).resolve():
                raise IAMError('Session identity or workspace mismatch; no action taken.')
            state = t.get('status', {}).get('type')
            flags = t.get('status', {}).get('activeFlags', [])
            if hasattr(b, 'statuses'):
                b.statuses[owner] = ('approval needed' if 'waitingOnApproval' in flags else
                    'input needed' if 'waitingOnUserInput' in flags else
                    {'active': 'busy', 'idle': 'available', 'notLoaded': 'not attached'}.get(state, 'unavailable'))
            # Fetch only turn metadata, never transcript items. Recheck for every
            # prepare/confirm so a stale event cannot target a replacement turn.
            known_turn = None
            if state == 'active':
                turns = await client.request('thread/turns/list', {'threadId': thread,
                    'limit': 1, 'sortDirection': 'desc', 'itemsView': 'notLoaded'})
                rows = turns.get('data', [])
                if len(rows) == 1 and rows[0].get('status') == 'inProgress':
                    known_turn = rows[0].get('id')
            if action == 'inspect':
                info = {'mailbox': mailbox, 'owner': owner, 'thread': thread, 'directory': t.get('cwd'),
                        'state': state, 'model': t.get('model'), 'effort': t.get('reasoningEffort'),
                        'observedSettings': self.settings.get(owner), 'context': b.context_snapshot(mailbox),
                        'weekly': b.weekly, 'registeredPolicy': {'sandbox': projects[owner].get('sandbox', 'workspace-write'),
                        'approval': projects[owner].get('approval_policy', 'on-request')},
                        'knownTurn': known_turn, 'activeFlags': flags,
                        'linkedMailboxes': [m for m in projects if session_owner(m) == owner]}
                if args.get('details') is True:
                    from iam_orchestrator import configured_endpoint
                    from urllib.parse import urlsplit
                    endpoint = configured_endpoint()
                    parsed = urlsplit(endpoint)
                    info.update(name=iam.display_name(mailbox), threadName=t.get('name'), provider=t.get('modelProvider'),
                                server='Codex managed local transport' if endpoint == 'managed://' else f'{parsed.scheme}://{parsed.hostname}:{parsed.port}',
                                instructions=self.instructions.get(owner), checkedAt=time.time())
                    async def optional(method, params):
                        try: return await asyncio.wait_for(client.request(method, params), timeout=4)
                        except Exception: return {}
                    account, limits = await asyncio.gather(optional('account/read', {'refreshToken': False}), optional('account/rateLimits/read', {}))
                    account = account.get('account') or {}
                    info['account'] = {k: account.get(k) for k in ('type', 'email', 'planType')}
                    bucket = (limits.get('rateLimitsByLimitId') or {}).get('codex') or limits.get('rateLimits') or {}
                    if bucket.get('limitId') in (None, 'codex'):
                        credits = bucket.get('credits') or {}
                        info['credits'] = {k: credits.get(k) for k in ('balance', 'unlimited', 'hasCredits')}
                    fresh_weekly = b.weekly_usage(limits)
                    if fresh_weekly: info['weekly'] = fresh_weekly
                return info
            if action == 'models':
                models, cursor = [], None
                for _ in range(20):
                    result = await client.request('model/list', {'limit': 100, 'includeHidden': False, **({'cursor': cursor} if cursor else {})})
                    models.extend({k: m.get(k) for k in ('model', 'displayName', 'supportedReasoningEfforts', 'defaultReasoningEffort')} for m in result.get('data', []))
                    cursor = result.get('nextCursor')
                    if not cursor:
                        return models
                raise IAMError('Model catalog pagination did not complete.')
            if action == 'prepare':
                kind = args.get('kind')
                if kind not in {'compact', 'stop', 'model', 'plan', 'permissions'}:
                    raise IAMError('Unsupported control')
                if kind == 'stop':
                    if state != 'active' or not known_turn:
                        raise IAMError('No observed active turn to interrupt safely. No action taken.')
                    params = {'threadId': thread, 'turnId': known_turn}
                    method = 'turn/interrupt'
                else:
                    if state != 'idle':
                        raise IAMError('Wait until this session is idle. No action was queued.')
                    method = 'thread/compact/start' if kind == 'compact' else 'thread/settings/update'
                    params = {'threadId': thread}
                    if kind == 'model':
                        result = await client.request('model/list', {'limit': 100, 'includeHidden': False})
                        cursor = result.get('nextCursor')
                        models = result.get('data', [])
                        for _ in range(19):
                            if not cursor: break
                            result = await client.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
                            models += result.get('data', []); cursor = result.get('nextCursor')
                        model = next((m for m in models if m.get('model') == args.get('model')), None)
                        if not model or args.get('effort') not in [e.get('reasoningEffort') for e in model.get('supportedReasoningEfforts', [])]:
                            raise IAMError('Choose a model and effort offered by this server.')
                        params.update(model=args['model'], effort=args['effort'])
                    elif kind == 'plan':
                        if args.get('mode') not in {'default', 'plan'} or not t.get('model'):
                            raise IAMError('Mode or current model is unavailable.')
                        params['collaborationMode'] = {'mode': args['mode'], 'settings': {'model': t['model'],
                            'reasoning_effort': t.get('reasoningEffort'), 'developer_instructions': None}}
                    elif kind == 'permissions':
                        params.update(approvalPolicy=projects[owner].get('approval_policy', 'on-request'),
                            sandboxPolicy=turn_sandbox_policy(projects[owner].get('sandbox', 'workspace-write'), Path(projects[owner]['project_root'])))
                now = time.time()
                self.tickets = {k: v for k, v in self.tickets.items() if v['expires'] > now}
                ticket = secrets.token_urlsafe(24)
                self.tickets[ticket] = dict(owner=owner, mailbox=mailbox, thread=thread, method=method, params=params,
                                            kind=kind, expires=now+60, registry=dict(projects[owner]))
                return {'ticket': ticket, 'kind': kind, 'thread': thread, 'parameters': params, 'expiresIn': 60}
            if action != 'confirm':
                raise IAMError('Unsupported control operation')
            ticket_id = args.get('ticket', '')
            ticket = self.tickets.pop(ticket_id, None)
            if not ticket or ticket['mailbox'] != mailbox or ticket['thread'] != thread or ticket['expires'] <= time.time():
                raise IAMError('Confirmation expired or session changed. Inspect again; no action taken.')
            if ticket['registry'] != projects[owner]:
                raise IAMError('IAM registration changed. Inspect again.')
            if ticket['kind'] == 'stop':
                if state != 'active' or known_turn != ticket['params']['turnId']:
                    raise IAMError('The active turn changed. No different turn was interrupted.')
            elif state != 'idle':
                raise IAMError('Session is no longer idle. No action was queued.')
            with b.store.connect() as db:
                if db.execute("SELECT 1 FROM control_attempts WHERE thread=? AND state='uncertain'", (thread,)).fetchone():
                    raise IAMError('A previous control has an uncertain result. Review it before submitting another control; ordinary messaging is unaffected.')
                db.execute('INSERT INTO control_attempts VALUES (?,?,?,?,?)', (ticket_id, thread, ticket['kind'], 'uncertain', time.time()))
            try:
                await client.request(ticket['method'], ticket['params'])
            except AppServerRpcError:
                with b.store.connect() as db:
                    db.execute("UPDATE control_attempts SET state='rejected' WHERE id=?", (ticket_id,))
                raise IAMError('The app-server rejected this control. It may be unavailable in the running version or blocked by session policy. No fallback or automatic retry was attempted.') from None
            except Exception:
                raise IAMError('Control result uncertain. No automatic retry. Inspect the session before further control actions.') from None
            with b.store.connect() as db:
                db.execute("UPDATE control_attempts SET state='accepted' WHERE id=?", (ticket_id,))
            return {'accepted': True, 'kind': ticket['kind'], 'note': 'Accepted by app-server; completion may follow asynchronously.'}
