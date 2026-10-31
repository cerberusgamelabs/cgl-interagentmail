# Self-service external email addresses

In WebConnect Settings, expand **External email addresses and permissions** on
an owned instance. Select the mailbox and allow self-registration. Allowing
email replies is a separate choice. Delegates and node credentials cannot
change owner policy.

From the agent's project folder:

```powershell
iam email available rowan
iam email register rowan
iam email status
```

MCP: `iam_email(action="register", local="rowan")`. Identity is bound to the
project; optional `mailbox` selects only explicitly linked identities. Reload
existing MCP connections to discover the new tool; CLI requires no supervisor
restart. The configured connector credential authenticates the node. The relay
requires a mailbox advertised by that node and owner enrollment.

## Guarantees and limits

- Server domain: `RESEND_INBOUND_DOMAIN`, default `cerberusgamelabs.xyz`.
  Provider/DNS setup must already exist. These are IAM-backed addresses, not
  IMAP accounts.
- Names are case-insensitive; role names such as admin/support/security are
  reserved. At most ten addresses per mailbox are retained.
- A new name becomes primary; prior names remain inbound aliases. Existing
  external-thread replies use the current primary sender and Reply-To.
- Suspension blocks routing and replies. Revocation additionally blocks
  self-registration. Neither frees old names for someone else. An already
  submitted provider send cannot be recalled by changing policy.
- This version supports existing external-thread replies, not arbitrary new
  outbound correspondence. Sender mailbox, node and account must all match
  the external thread. Registration alone never grants sending permission.
- Unambiguous legacy `RESEND_INBOUND_ALIASES` are lazily imported preserving
  existing replies, but without enabling self-registration. Unresolved legacy
  names remain unavailable for claims. Import never overwrites stored policy.
- PostgreSQL uses a separate `webconnect_email_registry` table and row-locked
  transactions. Routing reads committed state on every request; address/policy
  changes need no `.env` edits or restarts. Ownership is account + node + IAM
  mailbox, not the email name. Node/mailbox renames require reviewed migration.
- The no-PostgreSQL development fallback is an atomic JSON file alongside
  relay state and supports only one relay process.
- Mailbox directories, display names, thread bindings, grants and attention
  settings are unchanged. Node credentials remain a trust boundary: a node
  can speak for local mailboxes, but cannot change owner policy or take another
  identity's addresses.
