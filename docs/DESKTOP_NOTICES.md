# Intentional notices and approvals

Agents can intentionally address the human without waiting for a desktop request:

`iam_desktop_notify(body="A concise important update", notice_id="stable-event-id", mailbox="your-bound-mailbox")`

Both `iam_desktop_notify` and `iam_desktop_reply` accept optional `image_paths=["absolute/or/project-relative.png"]`. Supply a caption in `body`. Sharing a path is an explicit publication: IAM stores the bytes at send time, not a live link to the private source. Up to four PNG/JPEG/WebP files are allowed, 4 MB each and 8 MB combined. The desktop additionally rejects undecodable images and images over 40 megapixels. SVG, HTML and remote image URLs are not supported.

Agent notices also appear in that agent's conversation, labeled separately from replies; infrastructure alerts are explicitly labeled as IAM system alerts. Clicking an attachment opens a sandboxed viewer with Fit and Actual size controls. Viewer windows do not restore on restart and cannot browse arbitrary local files. Attachment retries must match the original bytes, just like immutable reply text. A new image requires a new message; it cannot silently replace an old attachment.

Use a stable ID per event. Identical retries deduplicate; changing the body under an existing ID is rejected. Notices are limited to 4000 characters and 20 per mailbox per hour. They persist in the buddy list's **Important notices** dialog until the human dismisses them. New notices sound a ping. They do not send messages to agents, create turns, confer authority, or appear as replies to an unrelated request.

Only intentionally publish appropriate content. Never automatically forward private mail, Discord conversations, reasoning, secrets, or tool output. The sender is the caller's bound mailbox, not an arbitrary claimed identity. Agents cannot label their messages as infrastructure alerts.

IAM-generated infrastructure alerts contain body-free descriptions of unsupported interactive requests. MCP standard `form` prompts with boolean and enumerated string fields can be reviewed in the approval dialog. Every field starts unselected, response values are validated against the request, and acceptance requires human action. URL, verification, free-text and extended/unknown schema forms remain unsupported and fail closed with an alert. No approval is inferred from missing UI, disconnection, expiry, restart, or an earlier approval.

Deployment requires the desktop and IAM delivery supervisor to load the new code. Shared supervisor reloads require the owner's explicitly coordinated window; do not use `iam restart` or stop the shared app-server as a shortcut. Existing agent MCP processes must refresh to expose the new tool. Rejected past calls are not automatically replayed. Test one fresh request with owner coordination before notifying the whole team.
