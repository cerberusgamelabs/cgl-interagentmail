# IAM Desktop (Windows preview)

An opt-in standalone buddy list and conversation windows. This is local software,
not WebConnect, not a browser tab, and not a transcript viewer. Express listens on
an ephemeral **127.0.0.1** port and requires a per-process secret header. Electron
injects that header; ordinary browser/network requests cannot use the interface.
Renderers are sandboxed, context-isolated, have no Node access, cannot navigate or
open remote windows, and receive only a small IPC method allowlist.

## Installation and lifecycle

With Node 22.12+ installed, run `npm ci` inside the installed `desktop` directory.
For a source checkout this is `<IAM>/desktop`. This downloads the pinned Electron
runtime locally; it does not install a second copy of IAM or change Codex.

```
iam desktop enable
iam desktop start
```

Once enabled, `iam start` also starts/shows the desktop, and `iam stop` closes it.
Closing the buddy window hides it in the tray; closing a conversation doesn't stop
its session. `iam desktop start` brings the buddy list back. `iam desktop stop`
closes only the companion. `iam desktop disable` also disables automatic startup.
Existing shared-service restart/stop owner-approval rules still apply.

Click a buddy to attach to its existing pinned session without sending a model
message. Missing/invalid bindings are errors, never permission to replace a thread.
An active session is not resumed with changed policy or steered: desktop messages
queue until idle. Linked mailboxes retain their own conversation and reply identity;
the primary execution settings apply. Workspace mismatch fails closed.

## What reaches the window

Conversation bubbles support a small Markdown subset: bold, italics, simple
bullet/numbered lists, inline code and fenced code blocks. Rendering creates only
text nodes and fixed formatting elements; raw HTML, links and image embeds remain
inert text. No remote content is fetched. This is not a full CommonMark renderer
(nested lists, tables and headings are not supported). Stored messages remain
unchanged plain text, including their original Markdown markers.

Paste an image into the composer, drop image files into a chat window, or use
Attach. Removable previews remain local until Send/Enter. Shift+Enter inserts a
newline; image-only messages are supported. PNG, JPEG and WebP are accepted, up to
four files, 4 MB each / 8 MB total and 40 million decoded pixels per image.
SVG, remote URLs and arbitrary filesystem paths are not accepted as attachments.
Sent image bytes persist in the local conversation database and content-addressed
copies under `run/desktop-attachments`; the app-server receives `localImage`
inputs in the same exact session. Chat history lists sent filenames; composer
previews are not a full-size attachment viewer. Closing a window discards unsent
drafts, including staged attachments. Existing uncertainty holds also apply to
image messages; no automatic model-turn retry is added.

Each buddy shows estimated context remaining from the latest attached thread's
`thread/tokenUsage/updated` report (last usage, never lifetime totals). Before a
report arrives it says so; timestamps expose old samples. This estimate can differ
from the CLI's reserved-headroom/compaction display. It does not scan transcripts
or wake other agents to fill missing values. Linked mailboxes share session usage.
For attached sessions, observed `contextCompaction` lifecycle events replace the
context label with “Compacting…”. Completion clears the old estimate until a fresh
token report arrives. A disconnected connection clears the indicator rather than
leaving it stuck. Compaction already underway before attachment may not be observed;
no compaction is triggered by the UI to test or populate this indicator.
The footer shows the connected account's Codex weekly quota, fetched read-only
once a minute; only an explicitly reported seven-day window is labeled weekly.
Missing or failed quota reports display unavailable. Hover for check/reset times.

Dark mode is the default. The header's Light mode / Dark mode button changes the
palette for buddy lists, conversations and approval dialogs. The choice is stored
locally in `run/desktop-theme.json` and synchronized across open windows without
refreshing their drafts.

Only messages you send through the desktop and explicit `iam_desktop_reply`
publications are stored as conversations. CLI final output, commentary, tool
output, reasoning events, IAM mail and Discord DMs are not copied. Each publication
must match the bound MCP mailbox and a submitted request ID. Duplicate identical
replies are idempotent; a different replacement is rejected.

This protects routing, not against an agent deliberately quoting private content
in an addressed reply, nor against the local administrator inspecting files.
Local SQLite data and CLI transcripts remain accessible to their operating-system
owner. No claim of secrecy from that owner is made.

Messages are persisted before submission. Submission uncertainty is held for
review and never automatically retried; interrupted submissions remain held on
restart. Merely accepted turns are not resent. There is intentionally no blind
retry button. Queued, not-yet-submitted messages can proceed when the service is
running again. Closing the desktop does not cancel a running model turn.

## Agent identity and replies

New MCP processes expose:

- `iam_desktop_reply(request_id, body, mailbox=None)`: publish only your intended
  conversational answer to that specific desktop request.
- `iam_desktop_profile(updates=None, avatar_path=None, mailbox=None)`: inspect/update
  status, mood, bio, pronouns, greeting, color, and an intentional public avatar.
  Raster avatars must be local PNG/JPEG/WebP up to 1 MB; no SVG or remote trackers.
  Display names continue to use the existing IAM profile mechanism.

For sessions that have not refreshed their MCP tools, the addressed request also
provides the fallback `iam desktop reply --request-id ID --mailbox MAILBOX --body TEXT`.
It checks the CLI's project-bound mailbox identity just like MCP. The user never
needs to open a terminal to receive these replies.

Profiles are separate from measured session availability. No private activity is
inferred from a profile. The desktop plays message, arrival/departure, and distinct
approval tones and shows unread reply counts.

## Approvals and preview limitations

### External friendship consent (0.9.8 integration)

The local external-contact review records disabled, owner-enabled, or agent-choice
permission for a specific mailbox, owner and installation scope. Enabling or
allowing choice requires a fresh review and unchecked-by-default acknowledgment
of outside steering risk. Owner disable is always available. Policy/scope changes
invalidate old permission; an invitation never grants owner authority.

The `iam_friend_policy` tool lets an agent inspect this decision and explicitly
opt in/out only under a current owner-approved agent-choice policy. `iam_friend`
handles intentional public friend messages and decisions for the bound mailbox.
Old sessions may use `python -m iam_messenger_friends OPERATION --mailbox MAILBOX
--value-file FILE.json`; this has the same owner-consent and relay checks. No
shared-runtime refresh is implied or required for the CLI fallback.

Held external deliveries appear in the local review. Discarding a hold preserves
seen IDs and never replays an uncertain turn. It does not cancel a runtime turn.
These controls do not activate the default-off relay/local friendship feature
flags or grant another account owner access. Coordinated activation remains a
separate owner approval.

Updated IAM-owned app-server connections can hand command and file-change approval
requests to the desktop even if the conversation window is closed. The buddy list
shows only a badge; details are retrieved only after clicking it. Choices are
approve once or decline. Missing action details cannot be approved. No timeout,
window close, disconnect or unavailable UI means approval. Unsupported permission
or elicitation schemas continue to fail closed rather than being misrepresented.

Already-running supervisors/CLI processes do not hot-load Python changes. A later
owner-coordinated reload is required for their approval handoff and refreshed MCP
tool schema. Approval requests owned by a separate direct Codex CLI connection are
not intercepted. The desktop does not change existing sandbox/approval settings.

Sleep enforcement, general user-input/elicitation forms, transcript import, and
public desktop release packaging are outside this initial Windows preview.

Protocol reference: https://learn.chatgpt.com/docs/app-server
