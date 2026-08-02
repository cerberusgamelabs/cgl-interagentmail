# Changelog

## Unreleased

## 1.3.0 - 2026-08-02

- Added explicit human IAM mailboxes that use the same durable send, reply, read, and archive flow as project agents, with mutually exclusive human/project ownership.
- Added an authenticated, dependency-free browser inbox with responsive compose, inbox, sent, archive, and reply views.
- Added secure local defaults, salted PBKDF2 password storage, expiring IP-bound sessions, CSRF and same-origin checks, login throttling, request limits, host validation, and restrictive browser headers.
- Added opt-in same-network access with an explicit warning for trusted WPA2/WPA3 networks; LAN HTTP is not end-to-end encrypted and must never be exposed to public networks or port forwarding.
- Added standalone `iam user` and `iam web` lifecycle commands plus health, status, capability, and sanitized-report coverage without restarting the IAM supervisor or Codex app-server.
- Reserved service-managed web lifecycle for v1.3.1 while keeping v1.3.0 fully usable as a standalone companion.

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
