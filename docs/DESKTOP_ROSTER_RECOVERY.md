# Buddy-list recovery

The October 20 diagnostics recorded a snapshot `PermissionError` while reading
the registration config. They do not establish which process denied access or
prove that this was the sole cause of the persistent blank window.

The desktop now reads one configuration snapshot per roster request, retries
OS read failures at most three times (50 ms and 100 ms delays), and rejects
invalid/missing configuration instead of presenting an empty successful list.
There is no cached authorization fallback. Sending and controls retain their
fresh checks.

Roster rendering builds an off-screen fragment and replaces the visible list
only on success. Failed refreshes retain the previous roster and show a
persistent unavailable/stale status. An empty registry and an empty search
result have distinct messages. Normal polling recovers on the next success.

CLI `IAMCommandError` exceptions inherit `SystemExit`; desktop request handling
now contains that specific type rather than letting it escape the service.
Usage and delivery handling also contain it. Delivery polling retries reads
without replaying uncertain submissions; only queued items are considered.

Tests: `tests.test_desktop_recovery`, `tests.test_desktop`,
`tests.test_desktop_responding`, and `desktop/test/roster-recovery.test.cjs`.
Activation requires a coordinated Messenger-only restart. Do not restart the
shared IAM supervisor or Codex app-server for this change.
