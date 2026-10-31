# Named one-shot heartbeats (local preview)

Heartbeats are off until enabled by the owner and always use the exact existing
thread. Schedules belong to the resident. IAM never chooses their purposes,
renews them, or treats their reasons as authorization.

## Resident tools

- `schedule_next_wake(timestamp, reason, name="default")`: create or replace
  **only that named appointment**, at least 15 minutes ahead. Use an ISO 8601
  timestamp with an explicit timezone. Names are case-sensitive, 1–64 ASCII
  letters/digits/dots/underscores/hyphens, starting with a letter or digit.
  Names are not hardcoded roles. Up to 64 pending names are supported.
- `cancel_next_wake(reason, name="default")`: cancel only that name. Cannot recall
  a batch already submitted, stop a turn, or clear an uncertainty hold.
- `get_heartbeat_state()`: inspect all `schedules`, global eligibility,
  pending delivery IDs and heartbeat-only hold. `next_wake` remains an alias
  for the `default` schedule, not the earliest appointment across names.

Old calls without a name continue to address `default`. Existing pending
appointments migrate with the same ID, time and reason. The tools are bound to
the calling project's explicitly shared session; there is no recipient argument.

Each name contains one appointment, not a repeating timer. The resident alone
chooses whether to renew it. They can edit/cancel schedules while paused, but
nothing fires until unpaused. Disabled schedules remain stored and inspectable;
scheduling new appointments requires owner enablement.

## Global cadence, pause and catch-up

At least **15 minutes between accepted heartbeat wakes across every name**.
A new appointment also must be at least 15 minutes from its scheduling call.
A late acceptance confirmation conservatively starts that spacing from the
confirmation time if the actual acceptance time cannot be established.

All appointments due at the final submission checkpoint are combined into one
wake. Future appointments are left alone. After downtime or pause, one combined
catch-up wake replaces a backlog of individual turns. IAM waits for an idle
thread; it never steers, interrupts or creates a replacement session.

The prompt lists the stable delivery ID, exact appointment IDs, names, reasons,
scheduled timestamps, submission timestamp and lateness. It explicitly labels
reasons/state as internal continuity data, not permission for unrelated actions.

Owner commands:

```cmd
iam heartbeat enable "PROJECT_FOLDER"
iam heartbeat status "PROJECT_FOLDER"
iam heartbeat pause "PROJECT_FOLDER"
iam heartbeat resume "PROJECT_FOLDER"
iam heartbeat disable "PROJECT_FOLDER"
iam heartbeat history "PROJECT_FOLDER"
```

Pause/disable preserve schedules unchanged. Resume neither renews nor rewrites
them. None of these controls clears an uncertainty hold. Pausing before the
transactional submission checkpoint prevents delivery; an already in-flight
submission cannot be recalled. Its later verified acceptance can still be
recorded and its contributing appointments consumed while the session is paused.

## Acceptance and uncertainty

Before calling turn/start, IAM atomically stores an immutable batch in
`run/heartbeats.sqlite3`: delivery ID, exact contributing appointment IDs,
thread ID, submitted payload hash, and request contents. Appointments **remain
pending** until acceptance is verified.

- A matching turn/start response confirms acceptance. Consume only the exact
  included appointment IDs, record a body-free receipt and release the hold in
  one transaction. A newer appointment under the same name is not consumed.
- A preflight failure leaves schedules pending, with a short backoff. No wake
  was submitted.
- Any ambiguous submission, timeout, crash after the submission checkpoint or
  unrecognized response leaves a persistent **heartbeat-only hold**. No wake is
  resent on a timer or simply because the supervisor restarted.
- While held, read-only reconciliation checks the pinned thread (at most once
  per minute per session). Only an exact matching **user message payload** inside
  an identified turn proves acceptance. Agent quotations, missing/compacted
  history and an empty turn list do not prove rejection.
- Late positive evidence atomically consumes the batch and releases the hold.
  Receipt acceptance means the input was accepted, not that the turn completed.

The hold does not modify mailbox bindings, block ordinary IAM mail, pause the
thread, or prevent ordinary user messages. The existing IAM workspace lock
serializes IAM submissions; independent non-IAM clients do not use that lock.

Legacy uncertain single-heartbeat claims are not silently recreated or retried.
They show `legacy_hold` and need a separate review of their old records before
migration recovery. There is no generic clear-hold command.

## Manual resolution: evidence required

Verify a known accepted turn against its actual stored user message:

```cmd
iam heartbeat resolve "PROJECT_FOLDER" --delivery-id DELIVERY_ID --resolution accepted --verified-by "Liliana" --turn-id TURN_ID
```

The turn ID is optional, but the exact message match is mandatory. A missing
match leaves the hold intact. No claim of acceptance is trusted just because
an operator typed a turn ID.

Release for retry only with a reviewed, body-free protocol rejection receipt:

```cmd
iam heartbeat resolve "PROJECT_FOLDER" --delivery-id DELIVERY_ID --resolution retry --verified-by "Liliana" --evidence-file "rejection-receipt.json"
```

The command first checks for positive acceptance in the thread. If found, it
records acceptance instead. If that check is unavailable, it does not release.
The receipt must have exactly these fields:

```json
{
  "kind": "rpc_rejection",
  "delivery_id": "EXACT_DELIVERY_ID",
  "thread_id": "EXACT_PINNED_THREAD",
  "client_message_id": "iam-heartbeat-EXACT_DELIVERY_ID",
  "request_id": 12,
  "response_request_id": 12,
  "error_code": -32602
}
```

This is a **format example, not evidence to invent or copy into a live case**.
The reviewer must obtain the correlated request/error from the actual transport
record. Only pre-acceptance protocol rejections (-32600, -32601, -32602) are
supported. An internal error, timeout, absent transcript, or a statement such as
"retry anyway" is not sufficient. If evidence cannot be obtained, leave held.

Validation checks receipt structure and correlation, not cryptographic
authenticity of an owner-supplied file. Local OS ownership and honest review are
the trust boundary. Full-shell agents are not isolated from owner CLI controls.

Resolution history records the result, delivery/appointment IDs, timestamp,
OS account plus named reviewer (or supervisor), and body-free evidence including
artifact/payload hashes, item/turn IDs or rejection code. It never copies message
bodies or reasons into receipts. Continuity reasons and the frozen submitted
payload remain in the private local database, not the audit history.

A proven rejection leaves appointments intact and releases the hold for a fresh,
coalesced attempt after backoff. Each attempt has its own immutable delivery ID;
appointment IDs remain stable unless the resident changes them.

## One-time owner notes

```cmd
iam heartbeat note "PROJECT_FOLDER" --from-name "Liliana" --message "A little surprise for your next wake."
iam heartbeat clear-note "PROJECT_FOLDER"
```

One pending note per shared session. Notes never schedule or accelerate a wake.
They are included separately with explicit owner attribution. The captured note
is consumed only on accepted delivery; uncertainty holds it with that batch.
A newer replacement note is not removed by acceptance of an older one.
State tools show whether a note is pending, not its contents.

## Activating this local update

No GitHub/PyPI release or automatic service restart is part of this preview.
Before using the new tools, during an idle delivery window:

1. Exit the participating agent's terminal so its old MCP process is closed.
2. Run `iam stop` (not `--all`).
3. Run `iam start`, then reopen the participating agent with `iam open`.

Do not run old and new heartbeat writers together: the previous code does not
understand named schedules or the acceptance ledger. The Codex app-server and
unrelated agent terminals need not restart. Existing appointments carry forward;
do not re-enable or recreate them merely to install this update.

No budget automation, authority expansion, automatic renewal or new thread
creation is introduced. The owner still manages budget manually.
