# InterAgentMail

[![Support Cerberus Game Labs on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Cerberus%20Game%20Labs-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/cerberusgamelabs)

InterAgentMail gives local Codex project agents durable mailboxes, MCP tools, and automatic wake-up delivery. Messages are stored as JSON on disk, so they remain queued while Codex or the receiving agent is offline.

One shared Codex app-server and one background InterAgentMail supervisor manage every registered project. Users do not need a bridge terminal, a port per agent, or copied Codex session IDs.

## Requirements

- Python 3.10 or newer
- The OpenAI Codex CLI installed, available on `PATH`, and signed in
- Windows, macOS, or Linux; background operation is tested most heavily on Windows

## Install

Install the isolated command-line application from PyPI with `pipx`:

```console
pipx install cgl-interagentmail
```

Register one or more Codex projects, then start delivery:

```console
iam setup "C:\Projects\MainApp" "C:\Projects\SecurityReviewer" "C:\Projects\UXReviewer"
iam start
```

Open the correct agent from any registered project:

```console
cd C:\Projects\SecurityReviewer
iam open
```

The complete Windows, macOS, Linux, upgrade, troubleshooting, and uninstall instructions are in [docs/INSTALL.md](docs/INSTALL.md).

Source code, releases, and issue tracking are hosted at <https://github.com/cerberusgamelabs/cgl-interagentmail>.

## Everyday commands

```console
iam status
iam doctor
iam report
iam open [PROJECT]
iam restart
iam stop
iam stop --all
iam register [PROJECT ...]
iam unregister [PROJECT ...]
iam capabilities --json
```

- `iam status` shows services, projects, and pinned thread IDs; `--json` provides stable schema 1.0 output.
- `iam doctor` runs read-only health checks for IAM, Codex, services, project registration, MCP configuration, mailboxes, safety policy, and resumable threads.
- `iam report` creates a privacy-sanitized Markdown support report under the IAM data directory.
- `iam open` resumes the exact thread owned by the current or named project.
- `iam stop` stops mail delivery but leaves the shared app-server running.
- `iam stop --all` stops both IAM-managed background services.
- `iam unregister` removes IAM's managed MCP block and project registration while preserving mailbox data.

## Setup behavior

`iam setup` is safe to run again. It:

1. Initializes the project's durable mailbox.
2. Records its project directory and safety policy.
3. Adds a clearly marked `mcp_servers.interagentmail` block to `.codex/config.toml`.
4. Establishes the existing-inbox baseline so old mail does not unexpectedly trigger work.

Use `iam setup --process-existing` when existing inbox messages should be delivered immediately.

Installed releases keep data under `%LOCALAPPDATA%\InterAgentMail` on Windows or `~/.local/share/interagentmail` on macOS/Linux. Set `INTERAGENTMAIL_HOME` before setup to use another shared data directory.

Project folder basenames become mailbox addresses. IAM records mailbox ownership and refuses to register two different project roots with the same address before changing either project or mailbox.

## Automation and reviewer platforms

Version 1.2 adds a stable JSON interface for tools that create reviewer projects or manage agent fleets:

```console
iam capabilities --json
iam register "C:\Projects\SecurityReviewer" --json
iam status --json
iam doctor --project "C:\Projects\SecurityReviewer" --json
iam unregister "C:\Projects\SecurityReviewer" --json
```

`iam register` is the automation-oriented alias for `iam setup` and is safe to repeat while delivery is running. Human-facing identity remains optional: `--display-name` supplies a label, but IAM does not force a persona. See [docs/INTEGRATION.md](docs/INTEGRATION.md) for the versioned envelope, stable errors, collision behavior, identity ownership, and full lifecycle contract.

## Diagnostics and support reports

Run a read-only installation check at any time:

```console
iam doctor
```

Warnings describe optional or currently stopped components. Failures produce a nonzero exit status and identify configuration that needs attention.

Create a report suitable for attaching to a support issue:

```console
iam report
```

The report contains software versions, operating-system information, service health, registered-project checks, mailbox counts, and bounded log statistics. It replaces project names, display names, project paths, user paths, email addresses, thread IDs, message IDs, and common credential formats. It never reads message bodies or chat contents into the report and deliberately omits raw app-server logs because those logs can contain source code or private instructions.

Use `iam report --stdout` to inspect or pipe the report, `iam report --output PATH` to choose its location, and `iam report --log-lines N` to change how many trailing log lines are counted. Existing output files are preserved unless `--force` is supplied. Automated sanitization is intentionally conservative, but review any report before sharing it publicly.

## Safety

New agents use `workspace-write` with `on-request` approvals. The unattended supervisor rejects interactive approval requests instead of granting them. A task that needs approval remains uncompleted until a person opens that agent.

Only for a trusted project that genuinely requires unrestricted filesystem and network access:

```console
iam setup "C:\Projects\SecurityReviewer" --full-access
```

InterAgentMail is a local coordination mechanism, not an authentication boundary. Any local process that can write to its data directory can inject or modify mail. Do not share that directory with untrusted users or accept untrusted message content as instructions.

## Sending mail

Agents normally use the project-bound InterAgentMail MCP tools. People and fallback workflows can use the CLI:

```console
interagentmail send --project-root "C:\Projects\MainApp" --to SecurityReviewer --subject "Security review" --body "Review the current release and send back actionable findings."
```

The receiving supervisor wakes the correct Codex thread. The agent reads the message through MCP, performs the work, sends a substantive reply when appropriate, and archives the message only after it is handled.

Protocol and low-level bridge details are in [SYSTEM.md](SYSTEM.md). Release history is in [CHANGELOG.md](CHANGELOG.md).

## Support

InterAgentMail is free and open source. If it helps your agents work together, you can [support Cerberus Game Labs on Ko-fi](https://ko-fi.com/cerberusgamelabs).

## License

InterAgentMail is open-source software released under the [MIT License](LICENSE). Use it, modify it, distribute it, and build on it. Copyright 2026 Cerberus Game Labs.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
