# Moving IAM projects between PCs

`iam migration` is a **review-first migration toolkit**, not an unattended
cutover wizard. Introduced in IAM 1.8.0. Both PCs need version 1.8.0 or later
to use these commands.

It exports selected mailboxes and their Messenger profiles (including embedded
avatars), optionally copies workspaces and exact pinned Codex rollouts, and
imports into a new staging folder. It generates destination registration,
mailbox-profile and IAM MCP drafts from one explicit path map. It also provides
an explicit idle-session workspace reconciliation command.

**Import does not install or activate agents.** The `staged-not-activated`
result is intentional. Automated transactional installation/cutover and
automatic external MCP configuration are not implemented in this version.
No original files are removed and no processes are started/stopped. Do not
run a source agent or bot concurrently with its migrated copy.

## 1. Export on the source PC

Stop the selected agents and their tool services yourself in an approved
maintenance window. `--source-stopped` is your assertion, not a remote check.
The command checks copied files for changes, but is not a filesystem snapshot.
Select every desired mailbox explicitly, including support agents:

```cmd
iam migration export --mailbox CGLWebDev --mailbox CGLSupportAgent-001 --mailbox CGLSupportAgent-002 --mailbox CGLSupportAgent-003 --mailbox CGLSupportAgent-004 --mailbox CGLSupportAgent-005 --include-workspaces --include-sessions --output C:\Transfers\maeve-team.zip --source-stopped
```

Choose an output outside the source directories. Existing output is never
overwritten. `--codex-home` can select the configured source Codex home when
it differs from the default. Only the exact pinned rollout is selected; an
absent/ambiguous session or shared-session alias blocks export.

Use `--resource cdiscord=C:\path\to\cDiscord` for optional tool source files.
Resources are staged, not installed or run. Nested selected workspaces can
appear twice in the archive; review copies rather than blindly overlaying.

### Private data and exclusions

The ZIP contains private mail and may contain full model/tool transcripts.
Transfer it privately. It is **not encrypted or a general secret scrubber**.
Workspace content can contain secrets in arbitrary files; review your files
before opting into workspace/history export.

Known credential files (`.env*`, credential-named files, private-key formats,
Codex auth), databases, `.git`, dependency/build folders, symlinks/junctions,
and project `.codex/config.toml` are excluded from workspace/resource copies.
MCP names are recorded for review but their environment values are not copied.
Do not put credentials in ordinary source/document files.

Global IAM/Codex databases, saved process IDs, global config, authentication,
Messenger trust grants, heartbeat schedules, and team authority grants are not
migrated. cDiscord's database and external env file require a separate,
explicit, consistent and private transfer. Mailbox timers are not replayed.
Messenger profile data is included, but Messenger's own message-history DB,
attachments and buddy-group preferences are not included in this first version.

## 2. Stage on the destination PC

Supply one absolute destination per selected mailbox. The import verifies all
archive names, inventory, sizes, hashes and mailbox/thread identities before
extracting. It refuses an existing staging folder. The default uncompressed
size limit is 20 GiB (`--max-gib` can explicitly change it).

Example for a bundle containing only Maeve:

```cmd
iam migration import C:\Transfers\maeve.zip --staging C:\Transfers\maeve-stage --map CGLWebDev=C:\Workspace\CGLStaff\CGLWebDev
iam migration check C:\Transfers\maeve-stage
```

For a team bundle add `--map` for every support agent as well, e.g.
`--map CGLSupportAgent-001=C:\Workspace\CGLStaff\CGLWebDev\support-agents\CGLSupportAgent-001`.
Quote the entire `MAILBOX=path` argument if it contains spaces.

Staging layout:

* `payload/mailboxes/`: mailbox data and unchanged bindings.
* `payload/projects/`: optional source workspace files.
* `payload/sessions/`: exact original rollout bytes, with original relative paths.
* `payload/messenger/`: per-mailbox Messenger profile JSON including picture.
* `payload/resources/`: optional tool sources.
* `destination-drafts/MAILBOX/profile.json`: corrected mailbox profile.
* `destination-drafts/MAILBOX/iam-mcp.toml`: generated for this PC's IAM Python.
* `registration-draft.json`: selected entries only; **merge, never replace global config**.
* `validation-report.json`: outstanding checks and manual cutover steps.

Keep the stage and back up existing destination data. Do not overwrite its
global `desktop.sqlite3`, Codex state indexes, config or authentication. Profile
JSON is a per-row import source, not a replacement database. No data merge or
credential installation is done by `migration import`.

The dependency report only locates executables; it does not assert Node ABI
compatibility or run imported code. For cDiscord, review the local/global MCP
config, Node path, `dist/mcp/main.js`, working directory, private env path,
database path and any IAM sender/script paths. Install locked dependencies and
build on the destination; a native `better-sqlite3` compile may require MSVC
Desktop development with C++ and a Windows SDK. No packages are installed
automatically and no arbitrary MCP commands are executed by migration checks.

## 3. Reconcile a copied session's runtime workspace

This command is for an **already installed** project with the correct path in
both the destination registration and mailbox profile. The original rollout
must already be discoverable by the destination app server. It does not index
missing sessions or create replacement threads.

Ensure the source agent is stopped, destination supervisor/connector are
stopped, and its Messenger/terminal is closed. Coordinate this with the owner:
the migration command never stops shared services. Keep the destination app
server available. Provide the exact pinned thread and the old workspace you
expect it to report:

```cmd
iam migration relocate --mailbox CGLWebDev --thread-id 01a1987c-781f-7662-9e0b-906711c125b2 --from E:\Workstuff\CGLStaff\CGLWebDev --to C:\Workspace\CGLStaff\CGLWebDev --source-stopped --destination-quiescent
```

This uses the local configured app server, checks the pinned ID/old workspace
and idle state, requests a workspace change using the existing thread, applies
the registered policy, and reads metadata back. It sends **no model input**.
Required MCP initialization can still block the resume. Do not bypass required
tools or edit rollout text to defeat the checks.

A failure/timeout after resume can mean the workspace changed but verification
did not finish: inspect the actual current state before retrying. It is not an
automatic rollback. Keep both copies until tool initialization, message delivery
and ticket access have been verified. Runtime workspace success is not an
end-to-end migration success report.
