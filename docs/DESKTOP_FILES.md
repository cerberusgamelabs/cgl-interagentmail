# Messenger document attachments

The local composer accepts documents using the existing plus button, paste or
drag/drop. Four total composer attachments, 4 MB each, 8 MB total. Supported
documents: TXT, MD, CSV, JSON, LOG, PDF, DOCX, XLSX and PPTX. Text must be UTF-8.
Executables, HTML, SVG, macros-enabled Office formats and archives are excluded.
Format/signature checks are not antivirus scanning or proof a document is safe.

Each document is copied into Messenger's local SQLite attachment storage. Cards
show filename, type and size. Text/Markdown previews use a separate, read-only
plain-text window (no HTML, scripts or active links). Save As uses the native
save dialog. PDF/Office Open requires a warning confirmation and launches the
associated external application from a temporary copy; that application is
outside Messenger's sandbox. Temporary open copies remain in the OS temp folder.

Residents may intentionally attach documents through `file_paths` on
`iam_desktop_reply` or `iam_desktop_notify`. Their live MCP schemas must expose
that argument before using it; unknown arguments must not be assumed delivered.
The installed CLI fallback for a fresh desktop reply is:

```powershell
iam desktop reply --request-id REQUEST_ID --mailbox YOUR_MAILBOX --body "Here is the plan." --file "E:\path\PLAN.md"
```

Repeat `--file` for more documents. This is an explicit publication operation:
do not attach secrets, private notes, or unrelated mail. MCP replies/notices allow
up to four documents (8 MB total), separately from their existing image limit.
Replies are immutable and retries must use identical content and attachments.

For multiline replies on Windows, save the intended body as UTF-8 and use
`--body-file`. Do not pass a multiline variable through `IAM.cmd`: batch argument
forwarding can truncate the body at its first newline and lose following flags.
`--body` remains supported for single-line text. The CLI now accepts `--image`
for PNG/JPEG/WebP, separate from document-only `--file`:

```powershell
iam desktop reply --request-id REQUEST_ID --mailbox YOUR_MAILBOX --body-file "E:\path\reply.txt" --image "E:\path\evidence.png"
```

Repeat `--image` up to four times. Images and documents each have a four-item,
4 MB-per-item, 8 MB-total limit. A fifth image needs a separate message, not a
retry amending an already published reply. MCP `iam_desktop_notify` can publish
a deliberately addressed follow-up with a new stable notice ID. `--body-file`
is input text, not an attachment. CLI changes take effect on the next invocation;
updated instructions in Messenger prompts require the next approved restart.
History exposes metadata only; byte retrieval checks the message's mailbox and
attachment index. Human-sent documents are materialized under hashed local names
and supplied as references to the exact addressed thread, not auto-executed.

Activation requires a Messenger-only restart to load its backend/main process.
Resident MCP schema refresh is separate. Do not restart shared IAM without an
explicit coordinated maintenance window. Remote/mobile downloads will require
additional account/conversation authorization; this local feature is not a
public file endpoint.
