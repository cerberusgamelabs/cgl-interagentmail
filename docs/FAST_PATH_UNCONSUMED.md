# Accepted but unconsumed attention

Local implementation; activation requires a coordinated owner-approved window.

IAM polls runtime-accepted records without starting or steering a turn. Legacy
verifier responses remain valid but do not authorize consumption expiry. The
extended response must carry all four nullable fields: runtimeAcceptedAt,
consumptionGraceUntil, acceptedTurnId, terminalTurnState. The grace is exactly
15 minutes from the verifier's persisted original acceptance timestamp.

After grace IAM requires the original runtime_acceptance receipt, exact thread
and workspace identity, and the exact accepted turn's terminal status. It maps
completed/failed unchanged; explicit interrupted maps to cancelled with
failureCode runtime_turn_interrupted. Unknown/missing evidence stays held.
This follows the terminal statuses described in the official app-server docs:
https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server

IAM persists an identifier-only runtime_accepted_unconsumed report before sending
it to cDiscord. Report retries never resubmit runtime notices. cDiscord owns the
atomic retrieval/consumption fence. Retrieval/conflicting evidence ends automatic
reconciliation in consumption_review; consumed ends in resident_consumed.
The original acceptance receipt is not replaced. Source content is never read or
marked read by this reconciliation.

## Activation

Coordinate the cDiscord deployment with Sylvie. The currently running IAM
supervisor retains its imported code; loading this change requires replacing
only that supervisor process in an explicitly owner-approved maintenance window,
then starting its replacement against configured_endpoint(). Verify the supervisor
PID/command immediately before stopping it; do not stop its process tree or the
app-server. The app-server and Messenger do not require a restart for this change.
Ordinary mail/heartbeat delivery pauses briefly during supervisor replacement.
Once activated, reconciliation is automatic for eligible records: activation is
therefore approval for live reports, not merely a code refresh.

No lifecycle action or live reconciliation was performed during implementation.
