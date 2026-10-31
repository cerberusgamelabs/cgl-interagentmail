# Local Messenger crash diagnostics

Enabled on the next Desktop launch after installing this change. No automatic
restart or crash-restart loop is introduced.

- `run/desktop-crashes/events.jsonl`: UTC timestamps, launch session UUID, PID,
  lifecycle events, numeric exit codes and allowlisted crash reasons/types.
  Rotates at 1 MiB to three archives (roughly 4 MiB total).
- `run/desktop-crashes/session.json`: last launch/clean-quit marker. An unfinished
  marker is recorded on the next launch as an unclean previous session. This
  can mean crash, forced termination or power loss; it is not a diagnosis.
- `run/desktop-crashes/dumps/`: Electron Crashpad native dump storage, local only.
  Uploading is explicitly disabled. No remote report endpoint is configured.

Structured logs deliberately exclude chat, mailbox names, IPC requests, tokens,
URLs, exception messages, raw stack traces and backend stderr. They record main
exception metadata without swallowing fatal errors, renderer/child exits and
renderer hangs, backend start/error/exit, requested quit and completed quit.
Logging failures do not crash the app. A second-instance launch does not touch
the primary instance's lifecycle marker.

**Native dumps are sensitive.** They may include process memory, conversation
text or credentials. Do not automatically attach, upload or share them. Dump
retention is managed by Crashpad; the structured-log size limit does not cap
native dump storage. Review disk usage and obtain owner approval before removing
potential evidence. There is no guarantee of a dump after every crash/power loss.
This does not provide native Python backend dumps or retrospectively recover
the 2026-10-19 07:52 Electron crash.

Validation uses isolated temporary directories and mocked Electron events;
no live Messenger crash is deliberately triggered. Native dump capture needs a
separately approved isolated smoke test, not a crash of the active Messenger.

Implementation reference: [Electron crashReporter](https://www.electronjs.org/docs/latest/api/crash-reporter).
