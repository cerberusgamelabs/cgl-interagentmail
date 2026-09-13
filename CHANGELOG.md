# Changelog

## 1.4.0 - 2026-09-13

- Added optional WebConnect transport commands (`iam connect configure|start|status|stop`) for secure outbound connections to any compatible hosted or self-hosted relay.
- Added separately scoped human/headless mailboxes per WebConnect node, with deduplicated inbound delivery, acknowledged outbound reply spooling, reconnect recovery, and identity validation.
- Added incremental WebConnect configuration updates, mailbox removal, attachment transfer with integrity checks, and delegated-message owner-approval guidance.
- Kept WebConnect separate from local `iam web`: it never exposes a local HTTP interface, Codex, app-server, filesystem, or shell to the remote relay.
- Added structured `normal` and `urgent` mail priorities. Older messages and callers without a priority remain `normal`.
- Urgent mail can steer an active Codex turn with a safe-stop notice, while normal mail remains queued until idle and approval-paused turns remain undisturbed.
- Added priority controls to the CLI, MCP tools, and authenticated browser composer.
- Preserved a mailbox's pinned Codex thread when the app-server cannot resume it. IAM no longer clears the ID, starts a replacement thread, or silently switches to another matching session; mail remains queued until the user explicitly selects a replacement.
- Added single-channel chat subscriptions, join/leave events, and supervisor-driven delivery of new chat messages.
- Added durable one-shot timers with clear, cancel, and snooze commands, plus permission-controlled reminders for other mailboxes and teams.
- Added teams, explicit leadership capability grants, delegated membership management, and member-scoped team/leader mail addressing.
- Expanded diagnostics to cover teams and timers without exposing their contents, and excluded attachment stores, team data, and connector credentials from release packages.
- Added explicit claiming of pristine, unowned mailboxes during setup; existing user identities and mail remain protected from implicit migration.
- Improved Windows process-liveness checks and allowed large thread responses during bridge attachment.
- Validated incoming attachment paths, filenames, sizes, and digests before writing WebConnect downloads to local storage.

## 1.3.1 - 2026-08-10

- Fixed automatic wake-up delivery for larger project fleets by opening a dedicated Codex app-server connection only when a mailbox has undelivered mail.
- Kept each delivery connection while its Codex turn is active or awaiting approval, then released it after the thread becomes idle.
- Isolated per-project attachment and delivery failures so one unavailable or invalid Codex thread no longer blocks every other mailbox.
- Updated `iam open` to start a remote session when a registered project has not yet needed a saved IAM thread.

## 1.3.0 - 2026-08-02

- Added explicit human IAM mailboxes that use the same durable send, reply, read, and archive flow as project agents, with mutually exclusive human/project ownership.
- Added an authenticated, dependency-free browser inbox with responsive compose, inbox, sent, archive, and reply views.
- Added secure local defaults, salted PBKDF2 password storage, expiring IP-bound sessions, CSRF and same-origin checks, login throttling, request limits, host validation, and restrictive browser headers.
- Added opt-in same-network access with an explicit warning for trusted WPA2/WPA3 networks; LAN HTTP is not end-to-end encrypted and must never be exposed to public networks or port forwarding.
- Added standalone `iam user` and `iam web` lifecycle commands plus health, status, capability, and sanitized-report coverage without restarting the IAM supervisor or Codex app-server.
- Reserved service-managed web lifecycle for a future release while keeping v1.3.0 fully usable as a standalone companion.

## 1.2.0 - 2026-07-29

- Added a stable schema 1.0 JSON integration interface through `iam capabilities`, `iam register`, `iam unregister`, `iam status`, and project-scoped `iam doctor`.
- Added idempotent registration results, mailbox ownership metadata, and pre-mutation collision detection for projects with the same derived address.
- Kept human-facing identity optional; platforms can own reviewer-instance identities while IAM owns mailbox and Codex thread state.
- Added `iam doctor` for read-only installation, service, project, MCP, mailbox, safety-policy, and thread health checks.
- Added `iam report` for privacy-sanitized Markdown support reports without message bodies, chat contents, thread IDs, raw configuration, environment variables, or raw app-server logs.
- Added the public integration contract and lifecycle guidance in `docs/INTEGRATION.md`.

## 1.1.0 - 2026-07-29

- Added the `iam` setup and lifecycle command.
- Added one shared background Codex app-server and mailbox supervisor for all registered projects.
- Added project-bound MCP configuration during setup.
- Added persistent, correctly rooted Codex threads with automatic replacement of non-resumable sessions.
- Added hidden background processes on Windows.
- Added safe unattended defaults and explicit full-access opt-in.
- Added `iam unregister` for clean project removal without deleting mail.
- Released InterAgentMail as open-source software under the MIT License.

## 1.0.0

- Added durable local JSON mailboxes, replies, channels, and archive operations.
- Added the InterAgentMail MCP server and low-level Codex bridge.
