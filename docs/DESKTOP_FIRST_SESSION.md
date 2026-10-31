# First conversation from Messenger

Opening an unbound mailbox is read-only. Its first Send checks for a binding;
if none exists, a native confirmation shows the name, workspace and registered
sandbox/approval policy. Cancel is the default and keeps the draft. Confirmation
expires after 60 seconds and is bound to the mailbox and reviewed settings.

Creation is refused if there is prior Messenger history, delivered mail, a
previous creation journal, a linked primary mailbox, or an existing app-server
thread (including archived and non-interactive sources). These checks do not
prove absence of externally deleted history; the confirmation explicitly asks
the owner to cancel if the agent has had another conversation.

After rechecking, Messenger records an attempt in the primary mailbox's
`.desktop-first-session.json` BEFORE calling `thread/start`. It records the
returned ID before pinning `.codex-bridge-state.json`. It verifies workspace
and returned policies, then keeps the client alive for the normal Send path.
Creating the session does not itself send an artificial bootstrap prompt.

## Uncertain outcomes

A timeout, shutdown, invalid server response or disk-write failure may leave a
creation journal without a completed binding. Further creation is blocked,
including after restart. Do not delete the journal to retry. Inspect its receipt
and the workspace's app-server threads; use the existing explicit
`iam open --thread-id <verified-id>` workflow to reconcile the intended session.
No automatic guess-by-newest or blind retry is implemented. A binding created
elsewhere during the operation is not deliberately overwritten. Avoid running
CLI binding/creation concurrently with the confirmation.

This is desktop-only onboarding. Existing mobile pairings and authority are
unchanged. Activation needs an explicitly coordinated Messenger-only restart.

Protocol reference: https://learn.chatgpt.com/docs/app-server
Tests: `tests.test_desktop_onboarding` and `desktop/test/onboarding.test.cjs`.
