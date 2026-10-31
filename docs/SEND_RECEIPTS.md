# Opt-in send receipts

`interagentmail.py send --delivery-key <key>` preserves the ordinary send
arguments but returns JSON with exactly the keys
`{version, delivery_key, status, message_id}` instead of a bare mail ID.
`version` is the integer literal `1`; `delivery_key` echoes the requested key.
`status` is one of the four values below. `message_id` is a nonempty string for
known records and null for unknown. Producers must reject unexpected versions,
key mismatches, malformed fields, and unknown fields rather than guessing.
`interagentmail.py send-receipt --project-root <root> --delivery-key <key>`
queries the sender's receipt without publishing mail. IAMService.send also
accepts delivery_key. Existing unkeyed sends are unchanged.

Keys are sender-mailbox scoped, 1 to 200 ASCII letters/digits/underscore/dot/colon/
hyphen. Producers must persist a versioned key before invoking the child, using
resident, delivery kind and immutable delivery UUID; do not generate a new key
on timeout. Recipients and full send intent are fingerprinted. Changing a
recipient or payload under an existing key is a conflict, not a new delivery.

- `committed`: all recipient copies and sender sent copy were published, or
  existing copies were verified after an interrupted journal commit.
- `pending`: identity was journaled, but complete publication is not confirmed.
- `conflict`: same key with different intent, or an inconsistent existing copy.
- `unknown`: no receipt for that sender/key. This is **not** authoritative absence
  of historical mail or permission to replay a legacy uncertain send.

Command success is not delivery success: producers must parse status. Receipts
do not indicate Codex acceptance, completion, read state or consumption, and do
not grant permission to wake, steer or replay. No old uncertain sends migrate
automatically. No automatic expiration or deletion of receipt records is present.

The SQLite journal is in the sender mailbox, alongside equally sensitive mail
data. It stores the original envelope and intent digest. A pending identity is
committed before mailbox writes. Competing keyed calls serialize on SQLite;
only the creating invocation publishes missing files. Matching retries verify
inbox/archive/sent copies, allowing read_at changes, but never recreate missing
copies. Partial sends remain pending for deliberate operational reconciliation.
Previously committed receipts do not recreate subsequently deleted mail.

This handles process interruptions and lost CLI output; it is not a claim of
cross-filesystem transactional publication or power-loss-proof mailbox writes.
An unknown/partial/conflicting result must remain held. Original unkeyed sends
and older running MCP processes do not acquire these semantics retroactively.
