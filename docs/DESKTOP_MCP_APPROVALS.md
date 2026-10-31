# Messenger MCP tool approvals

Messenger defaults to approving one call. For form-mode MCP tool approvals with
`_meta.codex_approval_kind = mcp_tool_call`, it offers only the persistence values
advertised in `_meta.persist`: session and/or always. The owner must select one.
The response carries the selection as `_meta.persist`, separate from form content.

The backend checks the original pending request again before recording a decision.
Generic forms, unoffered persistence, and persistence on declines are rejected.
No approval policy is automatically changed by installing this UI.

## Response confirmation and recovery

The durable lifecycle is pending -> decided -> sent -> resolved. `sent` records
an attempted transport write, not a confirmed answer or successful execution.
Only the matching connection's `serverRequest/resolved` confirms that the request
was answered or cleared. No automatic resend occurs after an ambiguous write.
Historical `answered` rows are not retroactively treated as confirmed resolution.

Important Notices shows responses awaiting confirmation and approvals whose live
connection or lease was lost. These are non-actionable recovery notices, without
commands, prompt contents, or an approval button. Dismissal does not grant access
or resolve the server request. Review the session before recovery; cancelling a
stuck turn does not approve it. Old request IDs are never replayed on a new connection.

A healthy connection can renew its lease, restoring the original actionable
pending prompt. A new Desktop backend alone neither revives a dead transport nor
resubmits its decision. Lease expiry does not rewrite the owner's request state.
The notice disappears when the original connection confirms resolution or a
pending request regains a healthy lease. A new loss episode is shown again.

Protocol reference: https://learn.chatgpt.com/docs/app-server#command-execution-approvals.

Remembered permission applies to later tool calls, not only the displayed message
body. Codex controls the configuration scope and whether a permanent save succeeds;
Messenger does not claim that an always choice is isolated to one resident.
Use session scope when permanent approval is not intended.

Verification: installed Codex `app-server generate-json-schema` includes `_meta`
on `McpServerElicitationRequestResponse`; upstream `codex-rs/core/src/mcp_tool_call.rs`
parses `_meta.persist` values session/always. Live persistent approval still needs
an owner-selected test after Messenger reload. Do not automatically replay a tool
call to test permissions.
