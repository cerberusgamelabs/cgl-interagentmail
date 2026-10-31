# External attention fast path (local preview, disabled by default)

This independent supervisor lane accepts **identifiers only**, verifies a
registered local cDiscord authority, and submits a clearly labeled external
attention notice to one pinned existing session. It is not ordinary IAM mail,
not a generic hook consumer, and not a new Codex daemon. Heartbeats and mailbox
delivery do not depend on its held-delivery state.

Multiple independently pinned residents are supported. Each has a separate
loopback listener port, verifier endpoint, credentials, and durable ledger.
The original registration and credentials remain in `run/fast-path/` unchanged.
Additional registrations live under `run/fast-path/residents/<sha256(mailbox)>/`.
Each directory contains `registration.json` (version 1, existing wire contract),
`credentials.json` (the two existing CDISCORD_FAST_PATH_*_SECRET keys), and its
own `deliveries.sqlite3`. Credentials must only be transferred privately.
No shared token, resident fallback, or port-based reassignment is permitted.
Duplicate resident/thread/integration identities and overlapping ports fail closed.

`iam fast-path status` lists all registrations without secrets. Use
`iam fast-path status PROJECT`, `enable PROJECT`, or `disable PROJECT` to select
one. Enable/disable without PROJECT selects the current registered project.
Registration is disabled by default and never creates attention grants.
After registration or enable, restart **only** the IAM supervisor (`iam stop`,
then `iam start`); do not use `--all` or change the runtime backend. A disable is
also checked on each incoming request and each dispatch. A stopped/failed lane
does not automatically restart, nor does it stop other valid resident lanes.
Malformed/colliding registration sets prevent fast-path startup, not ordinary mail.

No registration or endpoint exists until configured. Existing attention records
remain unaccelerated: infrastructure registration is not resident consent.

## Runtime transport: standalone server, not the managed daemon

This lane is not runtime-independent: it connects through IAM's existing
`appserver_client(url)` to read/resume the pinned thread and submit input.
The app-server references describe an actual runtime dependency, not just wire
terminology. The lane itself does not launch a Codex server or switch backends.

For this owner's Windows deployment, the approved backend is IAM's existing
hidden-console standalone `codex app-server --listen ws://127.0.0.1:4500`
with `--disable daemon_auto_start`, **not** the Codex-managed daemon or its
`app-server proxy`. The saved `windows_hidden_console_port` is 4500. IAM's
client factory rejects the managed endpoint while that configuration is set.
Do not remove that setting, start the managed daemon, or switch backends as part
of fast-path setup. Verify the configured endpoint before registration/activation.

The reusable client factory also supports a managed backend on other installs;
this lane does not independently enforce a universal daemon prohibition. If the
owner's restriction includes the standalone server as well, this implementation
does not satisfy it and must remain disabled pending a different runtime design.

## Resident boundaries

- Only temporary text attention windows, standing text subscriptions, and
  transcript-attention subscriptions are eligible. Capture alone is insufficient.
- cDiscord owns current consent, eligibility, coalescing, scope, intervals, counts,
  expiry, deletion, and transcript participant withdrawal. IAM never bypasses its
  final reserve transaction. Enabling an eligible record defaults to idle-only.
- IAM implements **start only**. Both stored modes (`idle_wake_only` and
  `idle_wake_and_steer`) wait while busy or awaiting approval. There is no steer,
  interrupt, cancellation, or approval-override fallback.
- Stored steering intent is **pending, not future consent**. A later deployment
  must not activate it. Future support requires fresh resident confirmation and
  a separately implemented capability gate. Merely changing a configuration flag
  cannot enable steering in this implementation.
- Notices contain a fixed safety label, identifiers, and provenance. No caller
  supplies prose, a callback, command, filesystem path, or routing override.
  Retrieval is through cDiscord and must revalidate current content and consent.
  It grants no reply or action authority. This is not a guarantee that the model
  cannot be influenced by external data.
- Exact thread ID, session owner, workspace and delivery requirement are checked
  against current registration. An unloaded existing thread may be resumed;
  a missing thread is never replaced or created. Registered execution settings
  remain applicable; attention does not increase permissions.

## Owner setup — not permission for a test wake

After installing updated package files, from the resident's registered folder:

```text
iam fast-path register --resident RESIDENT_ID --verifier-url http://127.0.0.1:VERIFIER_PORT --port IAM_PORT
iam fast-path status
```

`register` verifies the current pinned session, creates disabled configuration,
and generates two independent random 32-byte bearer credentials. It does not
start listeners, wake a resident, enable grants, or change heartbeat schedules.
The default IAM port is 43200; choose a free port different from the verifier.
Only successful listener startup proves a port was available. Never stop an
unrelated listener to take its port.

Private files live under `run/fast-path/` (already excluded from packaging/git):

- `registration.json`: generated integration ID/generation, resident, mailbox,
  owner, exact thread, workspace, listener port, verifier URL, inbound token hash.
- `credentials.json`: raw `CDISCORD_FAST_PATH_DOORBELL_SECRET` and
  `CDISCORD_FAST_PATH_VERIFIER_SECRET` for the protected local handoff.
- `deliveries.sqlite3`: candidate and submission ledger, fixed notices, body-free
  acceptance evidence and state audit; no Discord bodies or transcript contents.

The dedicated directory requires owner/SYSTEM-only Windows ACLs or POSIX 0700.
ACL failures stop setup. Never copy credentials into mail, prompts, command lines,
support reports, public docs or logs. Transfer them directly to the matching
owner-private cDiscord configuration. Both services run under a trusted OS owner;
this is not isolation from malicious processes using that same account.

Only after endpoint/binding/credential coordination and separate resident consent:

```text
iam fast-path enable
```

An explicit supervisor restart loads the listener; leave the existing standalone
runtime server untouched. Do not launch or restart the Codex-managed daemon.
This command still enables no cDiscord attention record.
`iam fast-path disable` invalidates new candidates/submissions on the next fresh
check; an input already submitted cannot be recalled. No automatic configuration
replacement/rotation is offered: disable and review pending state first. A new
binding requires a new registration generation and deliberate private migration,
never editing an old attempt into a different session.

For an editable checkout whose generated importer predates the module, update
the editable installation before restarting. Do not overwrite locked IAM launchers
while services are using them. The local development importer has been updated
without reinstalling launchers or restarting services.

## Wire v1

`POST /v1/fast-path/doorbell` on literal `127.0.0.1` requires the inbound bearer,
exact Host, JSON Content-Type, bounded Content-Length (16 KiB), no Origin,
no duplicate headers/JSON keys, and no chunked request body. Requests/headers
have deadlines and concurrent handlers are bounded. Identifiers are ASCII
`[A-Za-z0-9_.:-]{1,200}`; unknown fields fail closed.

Candidate fields: `version:1`, `integrationId`, `registrationGeneration`,
`residentId`, `threadBinding`, `deliveryKind:text|transcript`, `deliveryId`,
`sourceId`, `accelerationMode`, positive integer `policyGeneration`, and fixed
`label:"EXTERNAL ATTENTION EVENT"`. Thread binding is an equality assertion,
never a routing choice. On durable insertion/exact duplicate:

```json
{"version":1,"state":"recorded","duplicate":false,"integrationId":"id","registrationGeneration":"generation","deliveryId":"delivery"}
```

HTTP 202 means custody only. Dedup key is integration/generation/kind/delivery.
Altered canonical payload under the same key is HTTP 409. Identical retries never
reset held, consumed or terminal state. The initial ledger caps at 10,000 records
and fails closed when full; it never silently prunes dedup evidence. Status
reports state counts without exposing secrets or notices.

Verifier routes are fixed `/v1/fast-path/reserve`, `/status`, `/outcome`, with the
separate outbound credential. Literal loopback only, no redirects/proxies,
bounded replies. Schemas align with cDiscord `docs/FAST_PATH_PROTOCOL.md`.
Reserve requests bind operation `start`, the exact attempt and policy generation.
Responses must echo all bindings and committed mode plus deadline/state.

## Failure states and recovery

Queueing conveys no authority. After idle verification, IAM persists `reserving`
and calls reserve. cDiscord's transaction is the final consent cutoff. Authorization
is for this attempt only, at most five seconds, configurable shorter on cDiscord.
IAM enforces both the original absolute deadline and a conservative monotonic
budget starting before the reserve request. Readiness/policy are rechecked before
submission; no operation conversion occurs.

IAM durably writes `submitting` **before** entering `turn/start`. A response with
a turn ID records acceptance. Correlated JSON-RPC invalid-request/method/params
rejections are terminal, never retried automatically. Other errors conservatively
hold rather than guessing whether a turn was created.

- `reserve_held`: no runtime submission was initiated, but the reservation is
  uncertain or could not be used. Recovery queries the exact attempt. It does not
  reconstruct a dispatch budget or submit from a recovered reservation. A proven
  expired commit reports `expired_before_submit`; source content stays unread.
  An exact `attempt_not_found` response while IAM is durably in this pre-submission
  state terminalizes the local candidate as `reservation_absent`; IAM neither
  reclaims it nor reports an outcome for a nonexistent attempt. A missing record
  is never used as evidence after IAM has entered runtime submission state.
  In this preview the candidate ID then stays terminal. Deliberate later
  reconsideration requires a fresh immutable source delivery under current
  consent; an identical doorbell does not rearm the expired candidate.
  For the same content, current consent alone is not sufficient: minting that
  fresh delivery requires a fresh resident action (explicit renewal/reactivation
  or an explicit retrieval/reconsideration request). An unchanged activation
  must not automatically reissue expired content. Naturally new Discord content
  or a new voice utterance may produce its own delivery under a valid subscription.
- `submitting` after a crash or `ambiguous_held`: possible runtime submission.
  No duplicate/replacement attempt, regardless of deadline expiry. Positive
  reconciliation searches the exact thread for the unique, exact user-input
  notice. Missing/compacted history cannot prove rejection. The lane remains
  held if acceptance cannot be proven; there is no blind retry button.
- Outcome reporting retries the identical body-free evidence only, never the
  wake. Late positive acceptance is reported without re-submitting input.

No test wake is authorized by installation or registration. Tests use temporary
databases, fake Codex clients, and loopback fixtures. A resident must authorize
a narrowly scoped real test separately.

## Runtime limitations

### Expired malformed reservations and consumed outcomes

New submissions still require a strictly positive reservation duration no
greater than five seconds. Recovery of durable `reserving`/`reserve_held` rows
may retire an oversized interval only after its valid deadline has passed,
with the full schema, identity and mode matched and no runtime receipt present.
IAM preserves the attempt and reports `expired_before_submit` with
`invalid_deadline_before_submission`; it never reclaims or submits that row.
Malformed timestamps, reversed intervals, future deadlines and binding
mismatches remain held. The timing exception applies only to retirement and
that retirement's report reconciliation, never fresh delivery authorization.

An exact, validated verifier `resident_consumed` status supersedes a pending
report for locally proven `runtime_accepted` or `expired_before_submit`.
Clear only that report, preserving the local outcome and any runtime receipt.
Consumption is not proof that IAM's own uncertain submission was accepted:
`submitting`/`ambiguous_held` retain their positive-runtime-evidence requirement.
No retrieval, read marking, rearming or permission changes occur here.

These source changes require an explicitly owner-coordinated reload to become
active. They do not authorize any lifecycle operation or direct ledger edits.

The lane shares IAM's workspace lock with mail/heartbeat delivery but cannot lock
an independent Codex UI/client. The final read and `turn/start` are not a
cross-process atomic idle compare-and-submit. Do not interpret this as a hard
scheduler guarantee against simultaneous external user input. Any stronger
guarantee requires a runtime capability. Steering stays unavailable.
`idle_wake_only` is therefore **best-effort**, not atomic. IAM must not cancel,
steer, supersede or reinterpret simultaneous user input. A proven runtime
busy/rejection is terminal for that attempt; uncertain acceptance stays held.
The current client recognizes correlated protocol rejection codes only; unknown
application errors are held conservatively, never inferred safe to retry from
their text. Observing busy before submission merely queues without submitting.

Five seconds limits submission initiation, not acceptance or model response time.
SQLite durability, socket availability, session loading and model execution affect
latency. A held lane or listener failure never suppresses normal mailbox/heartbeat
state; a brief same-session workspace lock still serializes actual submissions.

The input/resume/history protocol follows the
[official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).
No undocumented exactly-once or steering-boundary guarantee is assumed.
