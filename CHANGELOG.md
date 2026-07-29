# Changelog

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
