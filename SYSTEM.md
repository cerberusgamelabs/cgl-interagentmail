# InterAgentMail technical reference

InterAgentMail provides three interfaces over the same local JSON mailboxes:

- `interagentmail`: mailbox, reply, channel, and administration CLI
- `iam-mcp`: identity-bound MCP tools and resources
- `iam-codex-bridge`: low-level push delivery into a Codex app-server thread

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
- `iam_list_channels`, `iam_chat_tail`, `iam_chat_post`, and `iam_chat_seen`

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

The supervisor creates or resumes one correctly rooted thread per project, persists its ID in that mailbox's `.codex-bridge-state.json`, and watches it while its interactive TUI is closed. Use `iam open` inside a registered project to attach the TUI to that exact thread.

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
- `--create-thread` permits replacement when no matching thread exists.
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
interagentmail chat post reviewers --project-root "C:\Projects\NexusGuild" --message "Reviewing the auth flow."
interagentmail chat tail reviewers --project-root "C:\Projects\AegisGrid" --lines 10
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
config.json
run/
  app-server.log
  supervisor.log
```

Each message is a human-readable JSON document with an ID, thread ID, routing fields, subject, body, attachments, references, signature, creation time, and read time. Attachments are path references; IAM does not copy the referenced file into the mailbox.

Private channel access is participant-bound by project identity. Mailbox and chat files are still local files, so operating-system access to the IAM data directory remains the security boundary.
