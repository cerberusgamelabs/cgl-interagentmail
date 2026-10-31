# Windows console-popup compatibility mode

This opt-in local workaround avoids rebuilding Codex. It is not an upstream
Codex fix and is not enabled automatically.

Codex's Windows detached daemon can spawn visible Git, shell, and helper
consoles. IAM can instead own a shared app-server launched with
`CREATE_NEW_CONSOLE` plus `STARTF_USESHOWWINDOW / SW_HIDE`. Ordinary console
children can inherit that hidden console. This is different from
`CREATE_NO_WINDOW`, which does not give the server an attached console.

## Switch during a maintenance window

Finish current turns and `/exit` **all** agent terminals. Do not load the same
saved thread in both backends simultaneously. Then, in Command Prompt:

```cmd
iam stop --all
codex app-server daemon stop
iam app-server-mode hidden-console
iam start
```

Reopen each project using `iam open`. The saved endpoint applies automatically
to IAM open/start/restart/status/doctor/report and CLI request delivery. Saved
mail, thread IDs, project permissions, and transcripts are not rewritten by
the mode switch. Explicit `--url` arguments can override the saved WebSocket
endpoint, but `managed://` is rejected while hidden-console mode is selected.

The app-server stays alive independently of the terminals. `iam stop` stops
delivery only; `iam stop --all` also stops the IAM-owned app-server. IAM does
not stop Codex's native managed daemon automatically.

The default endpoint is `ws://127.0.0.1:4500`. To choose another port while IAM
services are stopped, use `iam app-server-mode hidden-console --port 4521`.
Only loopback URLs are generated. A reachable unverified listener is never
silently adopted. Startup and opening terminals refuse to proceed while the
native managed daemon's recorded PID is alive, even if the compatibility
server is already reachable. This is a passive PID-file check: it never opens
a native daemon proxy connection. Malformed daemon state fails closed. A
recycled PID can conservatively block startup and requires inspection; IAM
does not automatically kill the recorded process.

IAM disables `daemon_auto_start` for each compatibility app-server launch and
remote terminal invocation. This does not stop a daemon already running or
change the user's global Codex configuration.

Compatibility startup is serialized with an OS-held lock. A failed protocol
probe does not authorize launching over a live recorded process or occupied
port. New ownership records are published only after the launched process is
alive and its endpoint responds; failed launches preserve previous records.
Stale records are not automatically adopted merely because a server responds:
inspect the listener and its launcher ancestry before repairing ownership.

## Limits and security

- Use `iam open`, not plain `codex`: plain Codex can auto-start its native
  detached daemon again, reintroducing both popups and competing backends.
- The compatibility server is a local WebSocket app-server, **not** the native
  daemon/control-socket service. Other software relying on daemon discovery or
  remote control may not follow this switch.
- The loopback app-server is not an authenticated multi-user boundary. Other
  processes/users on the computer may access it. Never expose or port-forward
  it to a LAN or the internet. Use this workaround only on a trusted machine.
- This does not alter sandbox or approval policies, hide interactive agent
  windows, disable tools, or close visible helper windows indiscriminately.
- A subprocess that explicitly creates its own visible console can still open
  a window. Existing windows from the old backend are not hidden retroactively.
- No supervisor or backend is restarted merely by installing this code.

## Permission handoff compatibility

IAM now opts into the app-server permission-profile API. For thread creation
and resume, each registered sandbox mode maps to its corresponding built-in
profile (`:read-only`, `:workspace`, or `:danger-full-access`). It checks that
the profile is allowed for the project and verifies the returned approval,
sandbox, and active-profile fields. A denial, timeout, missing policy response,
or mismatch stops the launch rather than silently using workspace permissions.
Only an explicit method-not-found response to profile discovery permits the
legacy sandbox format. No rejected mutation is retried with another policy.
Full-access unattended turns select the named profile when their connection
has already negotiated profile support. Otherwise their existing sandbox form
is retained: no discovery RPC is inserted inside deadline-bound submission.
Custom turn sandbox policies retain their exact existing form.

This does not modify running sessions or server defaults. Reopen an idle agent
with `iam open` to apply and verify its registered policy. Reload only the IAM
supervisor when ready to pick up the updated unattended-delivery code. Do not
clear bindings or restart the shared runtime to apply this change. Verification
at resume cannot prevent another client from subsequently changing permissions;
confirm `/status` after the TUI opens. Live TUI handoff verification is still
required; local tests cover protocol selection, rejection and response checks.

## Revert

Exit agent terminals, then:

```cmd
iam stop --all
iam app-server-mode managed
iam start
```

Reopen projects with `iam open`. Normal managed mode remains the default when
no compatibility setting is stored.

## Verification so far

On Windows with Codex 0.157.0, an isolated test created an ephemeral thread in
a temporary Git repository and ran a two-second Python command through
`command/exec`, without a model turn or using real project sessions. Sampling
visible console windows every 30 ms observed three Git windows and one Python
window with a detached server, versus zero new visible windows with a hidden
attached console. Commands completed successfully in both cases.

This is controlled reproduction evidence, not yet verification of every live
MCP, hook, PTY, and model-turn path. Confirm those after the coordinated switch.

The actual IAM launcher was also exercised with an isolated Codex home and
temporary loopback port: startup, repeated startup detection, WebSocket
handshake, ephemeral Git-backed thread creation, command execution, and
IAM-owned process-tree shutdown all succeeded. No new visible console windows
were sampled during that session/command test, and the user's live daemon was
not modified or stopped. The nine compatibility-mode unit tests and 85 existing
orchestrator/bridge/shared-session tests passed.
