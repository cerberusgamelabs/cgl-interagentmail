# Local shared-session mailboxes (unreleased)

One agent can use multiple separately registered project mailboxes through one
Codex session. Each address keeps its own inbox, sent mail, archive, delivery
cursor, timers, chat subscription and team grants. Display names may match,
but names and matching thread IDs never establish this relationship.

## Link explicitly

Register both folders normally. Choose the primary folder whose existing
conversation and execution settings you want to keep. Close any independent
secondary Codex instance before linking; this command does not stop it or merge
its conversation history.

```console
iam session link "E:\Projects\OscarSecondProject" --primary "E:\Projects\OscarPrimaryProject"
iam session show "E:\Projects\OscarSecondProject"
iam open "E:\Projects\OscarSecondProject"
```

Opening either folder opens the primary's session in the **selected folder**
and retains the primary's approval/sandbox mode. IAM explicitly updates the
server's working directory and runtime workspace roots, and verifies both
before launching the terminal. To change the
shared thread, run `iam open --thread-id ...` from the primary, not the secondary.
In workspace-write mode the selected folder replaces the writable project
root; the two folders are not combined into a broader writable workspace.
Linking authorizes this shared agent to use both mailbox identities; grant it
only when both projects are intended to be operated by the same agent.

The supervisor uses one connection per primary and delivers at most one event
per shared session per polling pass. Normal mail waits for idle; urgent mail
retains the existing steering rules. Delivery state stays separate even when
one message addresses both mailboxes. Pending timers and chat events also name
the receiving mailbox. Failure to attach the primary leaves mail queued.
Incoming events never select a different workspace. Delivery uses the last
verified active workspace and pauses if the server reports a different one.

Exit the current shared-session terminal before opening another folder. IAM
holds an OS-backed terminal lock until that terminal exits, and a short-lived
workspace lock to serialize switches with supervisor delivery. Busy turns,
including approval-paused turns, cannot be switched. Lock ownership is released
by the OS on process exit; persistent lock filenames are not stale ownership.
This only coordinates IAM launches, not terminals opened directly with Codex.
Close pre-update terminals once before the first guarded workspace switch.

The installed app-server must support `thread/resume.runtimeWorkspaceRoots`
and the ordinary resume-time approval and sandbox overrides. IAM negotiates the
workspace-root capability on workspace-control connections. Unsupported operations or mismatched results
stop the launch. A partially applied/unverified switch pauses shared delivery
until a subsequent `iam open` verifies a workspace; no transcript is rewritten,
no fallback thread is created, and no model turn is sent to force persistence.

## Require an existing delivery session (local, unreleased)

For a mailbox whose automatic notifications must never choose a replacement,
configure a separate, durable supervisor delivery requirement:

```console
iam session require-existing "E:\Projects\Danny" --thread-id EXISTING_THREAD_ID --session-owner Danny
iam status --json
```

The folder defaults to the current directory. `--session-owner` is an optional
assertion of the effective primary mailbox address, not a folder. The command
requires the exact thread to be pinned already; it does not pin, resume, create,
or modify a session. Running it again explicitly updates the requirement.

This opt-in policy applies to all supervisor mail, chat and timer delivery for
the mailbox. A primary's requirement also applies to its linked secondaries;
a secondary may have an additional requirement. Missing/changed pins, changed
owners, invalid policy files, and mismatched active connections hold delivery.
An unavailable expected thread is not replaced. Guarded attachment does not
send a bootstrap turn. A guarded mailbox's first watcher initialization does
not silently baseline its existing inbox as delivered.

The supervisor checks fresh policy and binding before attachment and immediately
before a prompt is sent, including after asynchronous app-server reads. It
explicitly attaches the required ID with thread creation disabled. The guard
does not cancel a prompt already sent or undo a running turn. Direct file edits
and external clients are not an atomic distributed transaction with Codex.

`iam status --json` adds `delivery_policy` to each project: `unrestricted`,
`ready` (matching stored binding, **not** proof the thread is online), or `held`
with a reason. Text status prints held reasons. The requirement is stored in
the mailbox's `.delivery-requirement.json`, independently of bridge state.
An explicit `iam open --thread-id` does not update it automatically.

To deliberately restore ordinary delivery behavior:

```console
iam session allow-replacement "E:\Projects\Danny"
```

This removes only that mailbox's requirement file, not mail or session data;
an inherited primary requirement still applies. A different registered mailbox
without a guard retains ordinary session creation behavior. This is a supervisor
routing policy, not a sandbox or restriction on manual `codex`/`IAMBridge` use.

Restart the IAM supervisor once after installing this code; the app-server and
agent terminals do not need a restart for this feature. Subsequent policy changes
are read dynamically. Do not enable an external notifier until that supervisor
restart has occurred. No policy is installed automatically.

## Agent mailbox selection

`iam open` passes the registered sandbox and approval policy in the app-server
resume request. Local managed resumes also receive CLI permission flags;
remote resumes (including hidden-console mode) must omit those flags because
Codex rejects permission overrides when resuming a remote task.
Shared sessions use the primary mailbox's execution policy. Background mail,
chat, and timer turns explicitly carry that same registered policy; waking an
unloaded thread also supplies it on `thread/resume`. Full access with approvals
`never` is opt-in, not a default applied to every mailbox. These settings do not
override organization requirements, tool-specific policy, or agent instructions.
Existing active turns are not retroactively changed. Restart the IAM supervisor
to load delivery-code updates; terminal launch changes apply on the next
`iam open` without restarting the shared app-server.

`iam_whoami` returns `authorized_mailboxes`. IAM MCP tools accept an optional
`mailbox` address, checked on every call. For example, read and reply using
`mailbox="OscarSecondProject"` when the notification names that mailbox.
Omitting it retains the MCP server's original bound identity; selecting another
mailbox does not change subsequent calls. Inbox/outbox data is never merged.
CLI fallback: use the selected mailbox's registered folder with
`InterAgentMail ... --project-root <folder>`.

Shared-session administration is not exposed as an MCP tool. Local full-access
agents can still edit local configuration or run administrator CLI commands;
this is not an operating-system security boundary.

## Unlink safely

```console
iam session unlink "E:\Projects\OscarSecondProject"
```

This revokes cross-mailbox MCP selection and restores independent delivery.
It does not delete mail or rewrite either saved thread. The secondary's older
independent thread binding, if any, becomes effective again. Explicitly select
a different thread afterward if that older conversation is no longer desired.
Historical shared context cannot be erased by unlinking. Unlink before
unregistering a member. Chains and cycles are rejected.

## Activation for an existing installation

These are local source changes, not a new PyPI release. Restart the IAM
supervisor and reload the participating MCP instances to load the updated code
and optional tool parameter before linking. The app-server itself does not need
a restart for this feature. Subsequent link changes are reloaded by the updated
supervisor; MCP authorization is checked per request. No running service or live
mailbox binding was changed during development. Workspace switching has unit
coverage and installed-protocol inspection, but still needs a user-observed
saved-session check: open the secondary, verify `/status` and `Get-Location`,
then confirm incoming mail does not change them. The isolated turnless probe
could not complete resume because it had no persisted rollout.
