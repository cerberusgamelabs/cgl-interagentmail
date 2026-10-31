# Restored chat attachment

Restored and manually opened chat windows now check for a saved binding and
attach automatically. No binding means no automatic thread creation: first
Send retains its separate confirmation flow. Restoration never sends input.

The existing backend attachment path reads the exact pinned thread first.
Active sessions are left alone without resuming or steering them. Idle sessions
resume without policy overrides and must match registered permissions.
Attachment is serialized per session owner, including linked mailbox windows.

A persistent status bar offers **Retry connection** on failure or a busy
session. Retries are explicit, not an endless background resume loop, and do
not touch the composer or its draft. Success hides the bar. Normal roster refresh
also clears it when the backend reports a live session connection, including
connections established through sending. Busy availability alone is not proof
of connection. Late failed reconnect responses cannot resurrect the banner
after a refresh has confirmed the connection. This follow-up requires the next
approved Messenger restart; no restart was performed for it.

Tests: `desktop/test/reconnect.test.cjs`, `tests.test_desktop_reconnect`.
Owner requested no restart during agent setup; activation is deferred until
explicit restart permission is given.
