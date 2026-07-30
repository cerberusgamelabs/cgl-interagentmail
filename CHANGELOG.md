# Changelog

## Unreleased

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
