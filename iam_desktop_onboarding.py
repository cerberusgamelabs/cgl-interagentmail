"""Explicit local first-session creation; never replace or guess a binding."""
import asyncio
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import interagentmail as iam
from iam_service import IAMError, session_owner
from iam_codex_bridge import DeliveryState, thread_sandbox_mode
from iam_orchestrator import appserver_client, configured_endpoint, session_guard


SOURCES = ['cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
           'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown']


class FirstSession:
    def __init__(self, backend):
        self.backend = backend
        self.reviews = {}

    def context(self, mailbox):
        config = iam.load(iam.CONFIG)
        projects = config.get('projects', {})
        if mailbox not in projects:
            raise IAMError('Unknown registered mailbox')
        owner = session_owner(mailbox, config)
        entry = projects[owner]
        root = Path(entry['project_root']).resolve()
        if not root.is_dir():
            raise IAMError('Registered workspace is unavailable')
        policy = entry.get('approval_policy', 'on-request')
        sandbox = entry.get('sandbox', 'workspace-write')
        if policy not in {'never', 'on-request', 'untrusted'} or sandbox not in {'read-only', 'workspace-write', 'danger-full-access'}:
            raise IAMError('Invalid registered session policy')
        state = DeliveryState.load(iam.mailbox(owner) / '.codex-bridge-state.json')
        return owner, root, sandbox, policy, state

    def status(self, mailbox):
        *_, state = self.context(mailbox)
        return {'state': 'bound' if state.thread_id else 'unbound'}

    def journal(self, owner):
        return iam.mailbox(owner) / '.desktop-first-session.json'

    def local_check(self, mailbox, context):
        owner, root, sandbox, policy, state = context
        if state.thread_id:
            raise IAMError('A session was already bound. Retry Send to use that session.')
        if mailbox != owner:
            raise IAMError('Start the primary mailbox session first; linked mailboxes cannot create one.')
        if state.delivered:
            raise IAMError('Prior mail delivery exists. Bind the intended existing session; no replacement created.')
        with self.backend.store.connect() as db:
            previous = db.execute('SELECT 1 FROM requests WHERE mailbox=? LIMIT 1', (owner,)).fetchone()
        if previous:
            raise IAMError('Messenger history exists. Review the missing binding before starting another session.')
        if self.journal(owner).exists():
            raise IAMError('An earlier first-session attempt needs reconciliation. No second session created.')

    def client(self):
        endpoint = configured_endpoint()
        if endpoint != 'managed://' and urlsplit(endpoint).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise IAMError('First sessions require a local app-server')
        return appserver_client(endpoint, request_timeout=30)

    async def check_threads(self, client, root):
        # Include non-interactive and archived sessions, not just the default CLI list.
        for archived in (False, True):
            result = await client.request('thread/list', {'cwd': str(root), 'limit': 1,
                'sourceKinds': SOURCES, 'archived': archived})
            if not isinstance(result.get('data'), list) or result.get('nextCursor') or result['data']:
                raise IAMError('Existing or uncertain session history. Bind the intended thread; no replacement created.')

    async def review(self, mailbox):
        context = self.context(mailbox)
        owner, root, sandbox, policy, state = context
        if state.thread_id:
            return {'state': 'bound'}
        self.local_check(mailbox, context)
        client = self.client()
        try:
            await client.connect()
            await self.check_threads(client, root)
        finally:
            await client.close()
        token = secrets.token_urlsafe(24)
        self.reviews = {k: v for k, v in self.reviews.items() if v['expires'] > time.monotonic()}
        self.reviews[token] = {'mailbox': mailbox, 'settings': (owner, str(root), sandbox, policy),
                               'expires': time.monotonic() + 60}
        return {'state': 'confirm', 'token': token, 'name': iam.display_name(mailbox),
                'workspace': str(root), 'sandbox': sandbox, 'approval': policy}

    async def confirm(self, mailbox, token):
        review = self.reviews.pop(token, None)
        if not review or review['mailbox'] != mailbox or review['expires'] <= time.monotonic():
            raise IAMError('First-session confirmation expired. Press Send to review again.')
        context = self.context(mailbox)
        owner, root, sandbox, policy, state = context
        if review['settings'] != (owner, str(root), sandbox, policy):
            raise IAMError('Registration changed. Review again before starting.')
        async with self.backend.locks.setdefault(owner, asyncio.Lock()):
            with session_guard(owner, 'desktop-first-session') as acquired:
                if not acquired:
                    raise IAMError('First-session creation already in progress')
                self.local_check(mailbox, self.context(mailbox))
                client = self.client()
                adopted = False
                try:
                    await client.connect()
                    await self.check_threads(client, root)
                    fresh = self.context(mailbox)
                    if (fresh[0], str(fresh[1]), fresh[2], fresh[3]) != review['settings']:
                        raise IAMError('Registration changed during review')
                    self.local_check(mailbox, fresh)
                    # Persist uncertainty BEFORE crossing the RPC boundary. Never auto-repeat start.
                    record = {'state': 'creating', 'workspace': str(root), 'sandbox': sandbox,
                              'approval': policy, 'created': time.time()}
                    iam.write_atomic(self.journal(owner), record)
                    try:
                        result = await client.request('thread/start', {'cwd': str(root),
                            'sandbox': thread_sandbox_mode(sandbox), 'approvalPolicy': policy})
                    except Exception as exc:
                        from iam_desktop import record_failure
                        record_failure('first-session-start', exc)
                        raise IAMError('Session creation was not confirmed. Draft kept; review the creation attempt before retrying. No second session will be created.') from None
                    thread = result.get('thread') or {}
                    thread_id = thread.get('id')
                    if not isinstance(thread_id, str) or not thread_id:
                        raise IAMError('Creation outcome uncertain. Review before retrying; no second thread will be created.')
                    record.update(state='created', thread_id=thread_id)
                    iam.write_atomic(self.journal(owner), record)
                    expected = {'read-only':'readOnly', 'workspace-write':'workspaceWrite', 'danger-full-access':'dangerFullAccess'}[sandbox]
                    if (not thread.get('cwd') or Path(thread['cwd']).resolve() != root or
                            result.get('approvalPolicy') != policy or (result.get('sandbox') or {}).get('type') != expected):
                        raise IAMError('Created session settings were not confirmed. No message sent; binding needs review.')
                    fresh = self.context(mailbox)
                    if fresh[-1].thread_id not in (None, thread_id) or (fresh[0], str(fresh[1]), fresh[2], fresh[3]) != review['settings']:
                        raise IAMError('Binding or registration changed during creation. No binding overwritten.')
                    fresh[-1].thread_id = thread_id
                    fresh[-1].save()
                    record['state'] = 'bound'
                    iam.write_atomic(self.journal(owner), record)
                    self.backend.adopt_client(owner, thread_id, client, result)
                    adopted = True
                    return {'state': 'bound'}
                finally:
                    if not adopted:
                        await client.close()
