# Generic local hooks (local preview, not yet released)

IAM hooks is an optional, independent **event broker**, not a chat runtime. It
does not import the Codex bridge, execute commands, read IAM mail, select
sessions, fetch URLs, or start consumers. `iam start` does not start hooks.
No identities, targets or grants are enabled by installing this feature.

## Responsibility and trust boundaries

An external adapter owns detection, heartbeat scheduling, stable event IDs,
stream cursors, coalescing and retry tracking. IAM owns authenticated event
ingestion, exact target/type grants, persistence, leases, limits and revocation.
A separately restricted consumer owns content retrieval, model execution,
idempotent replies, application read acknowledgements and tool authorization.

Hook credentials do **not** authorize project operations. The only supported
target is an external pull consumer: there is no privileged-session target.
Consumer runtime isolation must be reviewed independently: scoped conversation
read/reply tools and curated public documents only; no shell, general filesystem,
IAM/admin tools, unrelated private conversations or unrestricted networking.
The policy manifest is an owner-reviewed description and configuration digest,
**not an automatically enforced sandbox or proof of isolation**. Matching its
hash prevents accidental configuration mismatch; a compromised consumer can
still claim the matching hash.

The service binds only IPv4 loopback, rejects browser Origin headers and
unexpected Host headers, and requires separate bearer credentials for producers
and consumers. No management routes, CORS, callback URLs or arbitrary execution
are exposed. HTTP is intentionally local-only. Cross-host deployment is outside
this preview. The broker directory is owner-private (POSIX 0700, Windows ACL for
current user and SYSTEM); tokens are hashed in SQLite. Provisioned raw tokens
are saved to private one-time-created files, never printed or passed as CLI
arguments. Owner/administrator and other processes with that OS user's authority
are trusted: this is **not** protection against compromised same-user processes.
Separate OS principals/container/VM, scoped tool services and secret boundaries
must protect a production consumer. Do not share the broker DB or owner credentials.

## Local administrator commands

Use `iam hooks --help`. Commands below are examples, not an instruction to
activate a real runtime before its isolation review. `--state-dir` precedes the
subcommand and must name a **dedicated** private directory; default is `hooks/`
under IAM's data root. Never select a home/project root as broker state.

```
iam hooks producer-create example-adapter
iam hooks target-create example-worker --policy-file policy.json
iam hooks grant example-adapter --target example-worker --event-type conversation.activity
iam hooks status
```

Create commands return private token file paths; the target also returns
`consumer_id` and `policy_sha256`. Producer, consumer, target, and grant all start
disabled. Supported policy format:

```json
{
  "schema_version": "1.0",
  "runtime_id": "example-runtime",
  "capabilities": ["conversation.read", "conversation.reply", "public_docs.read"],
  "isolation": "Reference to owner-reviewed runtime isolation evidence",
  "public_docs": ["owner-curated-document-set-v1"]
}
```

After separate owner review, explicit enablement would be:

```
iam hooks enable producer example-adapter
iam hooks enable consumer consumer-example-worker
iam hooks enable target example-worker --reviewed-isolation
iam hooks grant example-adapter --target example-worker --event-type conversation.activity --enable
iam hooks serve --port 8790
```

`serve` is foreground; Ctrl+C stops only this companion. `status` reports stored
configuration and event counts, not process liveness. `revoke KIND NAME` disables
that identity/target and cancels affected pending/leased events. Reissuing `grant`
without `--enable` revokes that exact grant. `rotate PRINCIPAL_ID` saves a new
token file and immediately invalidates the old credential; consumer rotation
also invalidates outstanding lease tokens and requeues eligible work. Disabled
identities stay disabled. No target policy mutation API exists; create/review a
new target for a new isolation policy rather than silently changing one.

## HTTP contract

All routes require `Authorization: Bearer TOKEN`. POST requests require a
Content-Length and `Content-Type: application/json`; duplicate JSON keys,
unknown fields, query strings and transfer-encoded requests are rejected.
Responses use `{schema_version:"1.0",ok:true,data:...}` or
`{schema_version:"1.0",ok:false,error:{code:...}}`. Treat additive response fields
as optional. Never log tokens, payloads or lease tokens.

### Producer

`POST /v1/events` (202 persisted, including an idempotent repeat):

```json
{
  "schema_version": "1.0",
  "event_id": "evt-001",
  "target": "example-worker",
  "type": "conversation.activity",
  "occurred_at": "2026-09-16T22:00:00Z",
  "expires_at": "2026-09-16T22:10:00Z",
  "refs": {"stream_id": "stream-001", "cursor": "cursor-001"}
}
```

IDs/ref values: ASCII letters, digits, underscore or hyphen, 1–128 characters.
Type: lowercase letter followed by lowercase letters/digits/dot/underscore/hyphen,
max 64. Grant exact strings; there are no wildcard grants. `conversation.activity`
and `heartbeat.due` are examples, not hardcoded application behaviors. `refs`
and either of its keys are optional. No text, prompts, names, URLs, permissions,
commands or session/workspace parameters. Timestamps must be UTC `Z` format.

Dedup key is `(authenticated producer,event_id)`, with a canonical JSON digest:
same ID/payload returns current status, altered payload returns 409. Retrying
does not renew expiry. `GET /v1/events/EVENT_ID` returns only the caller's own
receipt/status, never another producer's record. Repeats still require an active
grant. Missing/revoked credentials return 401; denied grants 403; missing receipts
404; conflict/stale leases/policy mismatch 409; queue/rate limits 429 with
Retry-After. Expired/invalid new event timestamps return 400.

### Consumer

`POST /v1/consumer/leases` with
`{"policy_sha256":"DIGEST_FROM_TARGET_CREATE","wait_seconds":0}`. Wait is an
integer 0–20; credentials select the target, never a caller-provided target ID.
200 `data:null` means no eligible lease. Otherwise data contains `delivery_id`,
`producer_id`, `event`, `policy_sha256`, `lease_token`, `lease_expires_at`.

`POST /v1/consumer/leases/DELIVERY_ID/renew`:
`{"lease_token":"TOKEN"}`.

`POST /v1/consumer/leases/DELIVERY_ID/complete`:
`{"lease_token":"TOKEN","outcome":"completed"}`.
Outcomes are `completed`, `failed`, `retry`. Repeating successful terminal
completion with the same valid credential/lease/outcome is idempotent; retry or
expired lease acknowledgements must be reconciled with status. No transcript or
arbitrary error body is accepted. Completion means the consumer claims handling;
it is not proof of actual model comprehension or user-visible delivery.

## Queue guarantees and limits

SQLite transactions serialize submission, leasing and administrator changes.
Active producer, grant and reviewed target are checked at submission, dispatch
and acknowledgement; consumer authentication is repeated during long-polling.
Unavailable/unconfigured targets fail closed. Offline consumers leave events
queued until expiry. There is no fallback to another target or Codex session.

* Body: 8 KiB; maximum TTL 15 minutes; future clock skew at most 30 seconds.
* Active queue: 1,000 per producer / 100 per target (queued plus leased).
* Admission: token bucket 30 new events/minute, burst 10, per producer.
* One active lease per target; 60-second leases, renewable only until expiry.
* At most five dispatch attempts; explicit retry uses bounded jittered backoff.
* Up to 16 concurrent HTTP workers; saturated sockets close rather than spawn
  unbounded threads. Long polls are additionally capped at eight to leave room
  for submissions and acknowledgements. Clients must bound their connections
  and retry with jitter.
* Terminal dedup records remain seven days beyond event expiry. Do not retry
  old events past that window. Expired payloads cannot be resubmitted as new work
  without a new event ID and valid timestamps.

States: queued, leased, completed, failed, expired, cancelled. Revoked events
record outcome `revoked`. Queued work is not evidence that a consumer is running.
No silent queue eviction occurs. Cleanup happens on service operations.

Delivery is bounded **at least once**, not exactly once. Consumers must persist
event deduplication and use idempotent conversation APIs to cover a crash after
a reply but before ACK. SSE receipt, broker acceptance, lease acquisition,
application read acknowledgement and completion are distinct steps. Revocation
cannot undo already executed actions; consumers must recheck authorization at
the side-effect boundary and stop on expiry/revocation. A renewal confirms current
broker permission only at that instant, not a permanent execution grant.

## Verification

`python -m unittest tests.test_hooks -v` uses temporary state/identities and a
temporary loopback listener, never real mail, AIRC, Codex or production grants.
Consumer isolation and authorization tests belong to the separate runtime;
passing broker tests does not validate that runtime. Do not enable real targets
until those tests and owner review are complete.
