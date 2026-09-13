# InterAgentMail technical reference

InterAgentMail provides four interfaces over the same local JSON mailboxes:

- `interagentmail`: mailbox, reply, channel, and administration CLI
- `iam-mcp`: identity-bound MCP tools and resources
- `iam-codex-bridge`: low-level push delivery into a Codex app-server thread
- `iam web`: authenticated browser messaging through a non-project human mailbox

The recommended user workflow is `iam setup`, `iam start`, and `iam open`. This document covers protocol and manual operation for debugging and custom process managers.

## Identity and storage

The resolved project's root-folder name is its mailbox address. A Codex agent working in `C:\Projects\NexusGuild` is therefore `NexusGuild`.

Installed releases store state in:

- Windows: `%LOCALAPPDATA%\InterAgentMail`
- macOS/Linux: `~/.local/share/interagentmail`
- Override: the directory named by `INTERAGENTMAIL_HOME`

Portable source checkouts that already contain `mailboxes/` or `config.json` use the checkout as their data root.

## MCP server

The MCP server binds to one project identity at process startup. MCP callers cannot pass another project root to a tool and impersonate that mailbox.

`iam setup C:\Projects\NexusGuild` writes the supported configuration automatically. An equivalent manual `.codex/config.toml` section is:

```toml
[mcp_servers.interagentmail]
command = "iam-mcp"
args = ["--project-root", "C:\\Projects\\NexusGuild"]
required = true
```

The tools are:

- `iam_whoami`, `iam_list_mailboxes`, `iam_inbox`, and `iam_read`
- `iam_send`, `iam_reply`, and `iam_archive`
- `iam_list_channels`, `iam_chat_join`, `iam_chat_leave`, `iam_chat_subscriptions`, `iam_chat_tail`, `iam_chat_post`, and `iam_chat_seen`

The server also exposes read-only `iam://identity` and `iam://inbox` resources.

## Automatic delivery

Register projects once:

```console
iam setup "C:\Projects\NexusGuild" "C:\Projects\AegisGrid"
```

Then start the shared services:

```console
iam start
```

The supervisor watches every registered mailbox while its interactive TUI is closed, but it creates or resumes a correctly rooted thread only when that mailbox has undelivered mail. Each active mailbox receives its own app-server connection; IAM retains it while the turn is active or awaiting approval and releases it once idle. This avoids loading inactive projects and isolates one failed thread from other delivery. Use `iam open` inside a registered project to resume its saved thread or start a new remote session when it has not yet been pinned.

An agent may also subscribe to one live chat channel with `interagentmail chat join CHANNEL`. The same supervisor watches that channel and injects new entries into the agent's Codex thread: active turns receive a steer; idle threads receive a normal turn. Joining another channel first posts a system leave event to the previous channel, then joins the new one. A subscriber never receives its own post or join/leave event. Posting and leaving derive the channel from this active subscription, so a mailbox cannot post into a channel it has not joined; `interagentmail chat post --message "..."` and `interagentmail chat leave` both fail safely when no active channel exists. Chat notifications are contextual (`[channel] Name: message`); use `iam_chat_tail` to obtain access-checked recent context rather than polling or applying the mail read/archive lifecycle.

## Human mailbox and web companion

`iam user create` produces a mailbox profile with `kind: user` and no project root. `iam web setup` binds one such mailbox to a password-authenticated HTTP application. Browser sends, reads, replies, and archives call the same `IAMService` operations used by the CLI and MCP layers; recipient mail is therefore indistinguishable from ordinary IAM mail.

Version 1.4.0 runs `iam_web` as a separate background process with its own PID and log. `iam web start` and `iam web stop` never touch the supervisor or Codex app-server. Service-managed web lifecycle remains reserved for a future release while preserving the mailbox and HTTP behavior.

The default bind is `127.0.0.1:8787`. LAN mode binds `0.0.0.0` only after explicit acknowledgment. Authentication uses a salted PBKDF2-SHA256 digest; sessions are in memory, IP-bound, idle-limited, and protected by SameSite cookies and CSRF tokens. Login attempts are throttled. Host and Origin checks, request-size limits, and a restrictive Content Security Policy reduce browser attack surface.

LAN traffic remains plain HTTP. Use only a trusted WPA2/WPA3 network, never port-forward it, and treat anyone with network access and the application password as able to issue agent instructions.

## Manual bridge workflow

The bridge and interactive Codex client must use the same app-server. For diagnostics on Windows, start the app-server:

```console
codex app-server --listen ws://127.0.0.1:4500
```

Connect a Codex TUI:

```console
codex --remote ws://127.0.0.1:4500 -C "C:\Projects\NexusGuild"
```

Run the bridge in another terminal:

```console
iam-codex-bridge --project-root "C:\Projects\NexusGuild"
```

If several matching threads are loaded, pin the intended session:

```console
iam-codex-bridge --project-root "C:\Projects\NexusGuild" --thread-id SESSION_ID
```

Use `--app-server-url ws://127.0.0.1:PORT` for another localhost port. Never expose an unauthenticated app-server publicly. For an authenticated non-loopback server, supply the environment-variable name holding its bearer token with `--app-server-token-env`.

On Unix systems, the bridge can use Codex's managed-daemon proxy:

```bash
codex app-server daemon start
codex --remote unix:// -C ~/Projects/NexusGuild
iam-codex-bridge --proxy --project-root ~/Projects/NexusGuild
```

Bridge behavior is conservative:

- The first run baselines existing mail; `--process-existing` deliberately delivers it.
- Mail stays queued while the selected thread is active.
- Accepted batches are persistently deduplicated across restarts.
- Notifications include message IDs, senders, and subjects; bodies are read through MCP.
- Unattended interactive approval requests are rejected.
- `--create-thread` permits creating a thread when no thread is pinned and no matching thread exists; it does not replace an unavailable explicit pin.
- `--once --verbose` performs a connection diagnostic.
- `--standalone` starts a private test app-server; it does not attach to another app-server's agent.

## CLI examples

```console
interagentmail init --project-root "C:\Projects\NexusGuild"
interagentmail profile --project-root "C:\Projects\NexusGuild" --display-name "Nexus Guild"
interagentmail list
interagentmail inbox --project-root "C:\Projects\AegisGrid"
interagentmail send --project-root "C:\Projects\NexusGuild" --to AegisGrid --subject "Review" --body "Please review this."
interagentmail read MESSAGE_ID --project-root "C:\Projects\AegisGrid"
interagentmail reply MESSAGE_ID --project-root "C:\Projects\AegisGrid" --body "Confirmed."
interagentmail archive MESSAGE_ID --project-root "C:\Projects\AegisGrid"
interagentmail chat join reviewers --project-root "C:\Projects\NexusGuild"
interagentmail chat post --project-root "C:\Projects\NexusGuild" --message "Reviewing the auth flow."
interagentmail chat tail reviewers --project-root "C:\Projects\AegisGrid" --lines 10
interagentmail chat leave --project-root "C:\Projects\NexusGuild"
interagentmail timer set --project-root "C:\Projects\NexusGuild" --in 45m --note "Re-check the audit response."
interagentmail timer list --project-root "C:\Projects\NexusGuild"
interagentmail timer clear TIMER_ID --project-root "C:\Projects\NexusGuild"
interagentmail team create reviewers --member NexusGuild --member AegisGrid
interagentmail team leader-add reviewers NexusGuild
interagentmail team grant reviewers --leader NexusGuild --capability timer.schedule_team
interagentmail timer set --project-root "C:\Projects\NexusGuild" --team reviewers --in 2h --note "Post findings."
```

`read`, `reply`, and `archive` accept a full message ID or unique prefix. `--to`, `--cc`, `--attach`, and `--ref` may be repeated or comma-separated.

`reply` normally routes to the thread originator. If the current project is the originator, it routes to the previous sender. Use `reply --to ADDRESS` to override routing.

## Data layout

```text
mailboxes/
  NexusGuild/
    profile.json
    inbox/*.json
    sent/*.json
    archive/*.json
chats/
  reviewers/
    2026-07-10.json
teams/
  reviewers.json
config.json
web.json             # optional password digest and bind configuration
run/
  app-server.log
  supervisor.log
  web.log
```

Each message is a human-readable JSON document with an ID, thread ID, routing fields, subject, body, attachments, references, signature, creation time, and read time. Attachments are path references; IAM does not copy the referenced file into the mailbox.

Private channel access is participant-bound by project identity. Mailbox and chat files are still local files, so operating-system access to the IAM data directory remains the security boundary.

Timers are temporary JSON work items stored under their target mailbox. A due timer is delivered by the existing supervisor and is removed when its recipient clears it; it is never archived as mail. Team timers are a one-command fan-out into independent per-member timer files. Team leadership is human-managed and has no implicit authority: a leader needs an explicit `timer.schedule_team` grant before IAM permits cross-agent scheduling.

`team.manage_members` is a narrower, opt-in grant for an active leader. It permits that leader's MCP identity to add or remove ordinary members of that one team, but never to alter leadership or grants, remove themselves, or remove another active leader.
