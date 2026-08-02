# InterAgentMail integration interface

InterAgentMail 1.3.0 provides a stable local CLI interface for reviewer-pack registries, project launchers, and other automation. Integrators should discover support with `iam capabilities --json` instead of inferring behavior from the installed package version.

The integration schema is versioned separately from the IAM package. InterAgentMail 1.3.0 implements schema `1.0`.

## Compatibility discovery

```console
iam capabilities --json
```

Successful JSON commands write one JSON object to standard output:

```json
{
  "schema_version": "1.0",
  "ok": true,
  "data": {}
}
```

Failures use the same envelope and a nonzero exit code:

```json
{
  "schema_version": "1.0",
  "ok": false,
  "error": {
    "code": "IAM_ADDRESS_COLLISION",
    "message": "Mailbox address SecurityReviewer is already registered to another project.",
    "recoverable": true,
    "details": {
      "address": "SecurityReviewer"
    }
  }
}
```

Treat unknown fields as additive and ignore them. Reject an unsupported `schema_version` before relying on field semantics.

## Commands

| Command | Purpose |
| --- | --- |
| `iam capabilities --json` | Discover schema, command, and feature support. |
| `iam register PATH [PATH ...] --json` | Idempotently register projects, create mailboxes, and configure their MCP server. |
| `iam unregister PATH [PATH ...] --json` | Remove registration and IAM-managed MCP configuration while preserving mailbox data. |
| `iam status --json` | Inspect the shared services, registered projects, and thread state. |
| `iam doctor --project PATH --json` | Run read-only health checks, optionally limited to one registered project. |
| `iam user create ADDRESS --json` | Create or update a non-project human mailbox. |
| `iam user list --json` | List human mailboxes. |
| `iam web setup ADDRESS --password-stdin --json` | Configure the authenticated standalone browser companion. |
| `iam web start --json` / `iam web status --json` | Start or inspect only the browser companion. |

`iam setup` remains the human-oriented equivalent of `iam register`. All paths may be absolute or relative; IAM resolves and returns absolute project paths. When no path is supplied, register and unregister use the current directory.

## Registration

```console
iam register "C:\Projects\SecurityReviewer" --json
```

Registration returns a `data.projects` array. Each item includes:

- `status`: `registered`, `updated`, or `unchanged`;
- `address`: the mailbox address;
- `display_name`: the effective optional label;
- `project_root`, `mcp_config`, and `mailbox`;
- `sandbox` and `approval_policy`;
- `thread_state` and whether an existing pinned thread was preserved;
- `legacy_mailbox_reused`, retained for schema compatibility and always `false` because ownership migration is never implicit;
- `supervisor_reload`, currently `automatic`.

Registration is safe to repeat while the supervisor is running. The supervisor notices registry changes and reloads all project bridges. Re-registering the same path with the same options returns `unchanged`; it does not discard mail or a pinned thread.

Human, project, and legacy generic mailbox ownership is mutually exclusive; registration never migrates one identity kind into another implicitly. The default address strategy is `project-folder-basename`. Two different physical project roots cannot own the same address. IAM checks the registry and mailbox ownership before modifying the target project's MCP configuration or mailbox. Integrators should surface `IAM_ADDRESS_COLLISION` and ask the user to choose a uniquely named project folder; do not silently rename an existing mailbox.

### Identity ownership

IAM does not require or create a human persona for project agents. `--display-name` is optional and defaults to the mailbox address. A reviewer platform should own its immutable reviewer-instance identifier and persist the association with IAM's returned `address` and `project_root`. IAM owns mailbox storage and Codex thread state.

A human mailbox is created only when a person explicitly runs `iam user create` or `iam web setup`; its profile has `kind: user` and no project root. Its address and optional display name are user-controlled.

## Status and health

`iam status --json` reports shared app-server, supervisor, and optional standalone web-companion state plus every registered project. `thread_state` is `pending`, `pinned`, or `invalid`; `thread_id` is an opaque Codex identifier and may be `null`.

`iam doctor --project PATH --json` is read-only. Its command succeeds when diagnostics were collected, while its process exit code is `1` if any check failed. Read `data.summary.healthy` and the `PASS`, `WARN`, or `FAIL` status on each check. Warnings such as intentionally stopped services do not make the result unhealthy.

Use `iam report` for user-authorized support bundles. Reports are privacy-sanitized Markdown and deliberately omit message bodies, chats, raw logs, environment variables, raw configuration, and thread identifiers. Review a report before uploading it.

## Stable error codes

Schema 1.0 defines these integration errors:

| Code | Meaning |
| --- | --- |
| `IAM_ADDRESS_COLLISION` | The derived address or mailbox belongs to another physical project. |
| `IAM_PROJECT_ALREADY_REGISTERED` | The physical project is registered under a different address. |
| `IAM_PROJECT_NOT_REGISTERED` | Unregister or lookup targeted an unknown project. |
| `IAM_PROJECT_NOT_FOUND` | The requested project directory does not exist. |
| `IAM_MCP_CONFIG_CONFLICT` | An unmanaged InterAgentMail MCP section prevents safe configuration. |
| `IAM_MCP_CONFIG_INVALID` | IAM's managed MCP markers are incomplete or duplicated. |
| `IAM_CONFIG_INVALID` | IAM's registry configuration is malformed. |
| `IAM_INVALID_ARGUMENT` | A supported command received an invalid option combination. |
| `IAM_COMMAND_FAILED` | A failure has no more specific schema 1.0 mapping. |
| `IAM_USER_ADDRESS_INVALID` / `IAM_USER_NOT_FOUND` | A human mailbox address is invalid or unavailable. |
| `IAM_WEB_LAN_ACK_REQUIRED` | LAN mode was requested without explicit network-risk acknowledgment. |
| `IAM_WEB_PASSWORD_WEAK` / `IAM_WEB_PASSWORD_MISMATCH` | The application password did not meet setup requirements. |
| `IAM_WEB_CONFIG_INVALID` | The browser configuration or bound user profile is malformed. |
| `IAM_WEB_PID_CONFLICT` | IAM refused to replace or stop a live process whose instance identity could not be verified. |

A `recoverable` value of `true` means the caller can normally ask the user to correct the supplied path, name, or local configuration and retry. Always display `message` to a person when automated recovery is unavailable.

## Lifecycle guidance

A reviewer platform should:

1. Run `iam capabilities --json` and verify schema `1.0`.
2. Create the reviewer project folder and its own immutable reviewer-instance record.
3. Run `iam register PATH --json`, then persist the returned address association.
4. Run `iam start` once per operating-system user when automatic delivery is wanted.
5. Use `iam status --json` and project-scoped doctor checks for health displays.
6. Run `iam unregister PATH --json` when removing the integration. Decide separately whether the user wants preserved mailbox data deleted; IAM never deletes it during unregister.

In v1.3.0, `features.web_managed_by_iam_service` is `false`: web lifecycle commands affect only the companion. LAN configuration requires explicit risk acknowledgment, and callers must surface the trusted WPA2/WPA3 network warning.

Do not parse human-readable CLI output. Do not edit IAM's `config.json`, mailbox profiles, bridge state, or managed MCP markers directly.
