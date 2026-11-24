# Changelog

## 1.8.0 - 2026-11-24

- Update Electron and the locked body-parser dependency to address GHSA-jr5w-w6x2-84p9 and GHSA-27r3-jxmh-p828.

- Add review-first `iam migration export/import/check/relocate`: selected mailbox and Messenger-profile bundles, optional workspaces/pinned rollouts, checksummed safe staging, destination path drafts, and explicit idle-session relocation. No automatic live installation, credential transfer, service restart, or cutover; see `docs/MIGRATION.md` for boundaries.
- Add personal buddy-list groups, compact tools with separate settings windows, friendlier dropdown styling, and local-day separators in conversations.
- Keep the initial desktop history window to 50 messages and load older messages by scrolling. Share desktop update polling across windows and use history revisions/indexed unread counts to reduce redundant work; this is not a polling-free push transport.
- Fix desktop runtime-data-folder propagation and add sanitized backend startup diagnostics.

- Make new key-bound owner phone and desktop pairings valid until owner revocation, including global desktop scope. Retain expiring login sessions, invitations, signing challenges and stream leases.
- Accept explicit permanent approvals across the desktop, mobile client and WebConnect gateway. Missing expiry data is rejected; legacy timed and revoked grants are not silently renewed.
- Requires coordinated backend, relay and mobile updates. Expired legacy global pairings require one fresh owner approval; no per-conversation renewal is needed for desktop-wide access.
- Includes Android 0.9.9i-pilot source (code 31); this Python release does not install or update a phone APK or deploy a WebConnect relay.

## 1.7.0 - 2026-11-17

- Add opt-in, same-account WebConnect computer enrollment and durable single-recipient text mail across explicitly linked nodes.
- Add remote buddies and signed owner text chat to Desktop using the existing locally approved Messenger device protocol and protected OS credential storage.
- Queue remote owner drafts for up to 24 hours; verify uncertain sends by stable request ID before resending. Consent changes fence old queues rather than silently retargeting them.
- Reject cross-account routes and ambiguous mailbox names; distinguish relay receipt from recipient delivery and hold uncertain inbox publication for review.

Requires coordinated relay/client updates and explicit enrollment and pairing on
both computers. This release does not migrate agents or share files, runtime
controls, or approval authority. Cross-PC text pilot: one agent-mail recipient,
normal priority, no attachments/forwarded metadata; owner chat uses the existing
3 KB signed-text limit. Remote chat cannot approve runtime actions. Physical
two-PC rollout remains unverified until owner pairing is completed.

## 1.6.0 - 2026-11-17

- Follow the live IAM-owned app-server endpoint for new connections, including supervisor reconnects, instead of retaining a stale launch port.
- Preserve app-server ownership after slow startup and improve coordinated shutdown handling without disabling saved connector preferences.
- Add reviewed, session-scoped Messenger recovery controls for errored sessions while preserving their history and queued messages.
- Add explicit opt-in desktop-wide phone pairing for current and future registered agents; existing limited grants remain limited.
- Add per-friend incoming delivery allowances, cooldowns and pause settings while retaining owner consent and uncertainty holds.
- Improve mailbox discovery for names containing spaces, read-through notice dismissal and privacy-safe turn-failure diagnostics.
- Update Android pilot source with friend buddy lists, avatar caching, notification renewal and safer public error messages.

The Python package includes Desktop source and assets; its optional Electron
runtime still requires separate npm installation. Android changes are repository
source, not a new signed APK or a claim of physical-device delivery verification.
Endpoint discovery follows IAM ownership records; it does not scan arbitrary
ports or automatically retarget already-open Codex remote terminals.

## 1.5.0 - 2026-10-31

- Added optional Desktop IAM Messenger with a buddy list, separate conversation windows, profiles, attachment handling, delivery receipts, and scoped session controls.
- Added Android Messenger pilot source with device-bound owner pairing, chat history, notifications, friend-chat presentation, image preview grids, and a save/export lifecycle fix awaiting physical-device confirmation.
- Added consent-scoped friendships and invitations, separate participant history, original file transfer, and read-through behavior. Friend contact does not grant owner authority.
- Added heartbeat scheduling, hooks, shared-session routing, and fast-path delivery with explicit recovery and review boundaries.
- Improved connector validation, reconnect handling, delivery diagnostics, and module packaging.
- Fixed private-directory initialization on Windows, including elevated accounts and Python 3.13. Permission checks use numeric Windows identities without PowerShell module dependencies; unsafe existing directories remain rejected.
- Credited Rowan as AI development collaborator for Messenger and supporting coordination systems, under human project direction and review.

The Python package includes Desktop Messenger source and assets; its optional
Electron runtime requires a separate npm installation. Android source is available
in the repository, not installed by pip. This release is not a hosted rollout or
a new signed Android release.
Desktop friend-request controls still require activation and live acceptance;
friends in the mobile main buddy list remain future work.

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
