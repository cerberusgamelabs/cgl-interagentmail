# Installing InterAgentMail

This guide is suitable for linking directly from the Cerberus Game Labs website. InterAgentMail runs locally and connects local Codex project agents; it does not require a hosted InterAgentMail account.

InterAgentMail is open-source software distributed under the MIT License.

## What gets installed

- `iam`: setup, agent opening, and background-service management
- `interagentmail`: human/fallback mailbox commands
- `iam-mcp`: the project-bound MCP server configured by `iam setup`
- `iam-codex-bridge`: the low-level manual bridge, normally not needed

One IAM installation can manage any number of projects for one operating-system user.

### Maintainer check for editable installations

An editable install can expose changed source while retaining an older module
discovery map. After adding modules to `pyproject.toml`, refresh the installation
using the interpreter that runs IAM, then verify discovery outside the checkout:

```cmd
.venv\Scripts\python.exe -m pip install --no-deps --editable .
.venv\Scripts\python.exe scripts\check_installed_modules.py
```

The check uses an isolated Python subprocess in a temporary directory and must
report an empty `missing` list. Running imports from the repository root is not
a substitute: that can hide missing installed modules. `--no-deps` is appropriate
only when runtime dependencies are already provisioned; it does not validate them.
Already-running Python processes may retain the previous editable finder in
memory. Coordinate any required component reload separately; never use a shared
IAM/Codex restart as an implicit installation step. Passing discovery is not an
end-to-end delivery or service-health check.

## Windows installation

### 1. Install and sign in to Codex CLI

If `codex --version` already works, skip to step 2.

From PowerShell, use OpenAI's official installer:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://chatgpt.com/codex/install.ps1 | iex"
```

Alternatively, if Node.js and npm are already installed:

```cmd
npm install --global @openai/codex
```

Then start Codex once and follow the sign-in flow:

```cmd
codex
```

Exit Codex after sign-in and verify:

```cmd
codex --version
```

Official Codex installation options are maintained at <https://github.com/openai/codex#installing-and-running-codex-cli>.

### 2. Install Python and pipx

Install Python 3.10 or newer from <https://www.python.org/downloads/windows/>. Enable **Add Python to PATH** in the Python installer.

In Command Prompt or PowerShell:

```cmd
py -m pip install --user pipx
py -m pipx ensurepath
```

Close every terminal window and open a new one so the updated `PATH` is loaded.

### 3. Install InterAgentMail

Install the current release from PyPI:

```cmd
pipx install cgl-interagentmail
iam --help
```

The signed-off GitHub release wheel is also available as a direct-install fallback:

```cmd
pipx install https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.4.0/cgl_interagentmail-1.4.0-py3-none-any.whl
iam --help
```

To install a wheel downloaded directly from Cerberus Game Labs instead, use its actual downloaded path:

```cmd
pipx install "%USERPROFILE%\Downloads\cgl_interagentmail-1.4.0-py3-none-any.whl"
iam --help
```

Do not unzip the wheel.

## macOS installation

Install Codex and pipx with Homebrew, then sign in to Codex:

```bash
brew install --cask codex
brew install pipx
pipx ensurepath
codex
```

Open a new terminal, then install IAM:

```bash
pipx install cgl-interagentmail
iam --help
```

## Linux installation

Install the Codex CLI using OpenAI's installer:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
```

Install Python 3.10+ and pipx with the operating system's package manager. On Ubuntu 23.04 or newer:

```bash
sudo apt update
sudo apt install pipx
pipx ensurepath
```

Open a new terminal, then install IAM:

```bash
pipx install cgl-interagentmail
iam --help
```

For older Ubuntu releases and other distributions, follow <https://pipx.pypa.io/stable/installation/>.

## First-time project setup

Run setup once for every project that should have its own Codex agent and mailbox. Paths may be supplied together:

```cmd
iam setup "C:\Projects\MainApp" "C:\Projects\SecurityReviewer" "C:\Projects\UXReviewer"
```

On macOS/Linux:

```bash
iam setup ~/Projects/MainApp ~/Projects/SecurityReviewer ~/Projects/UXReviewer
```

Project folder names become mailbox addresses by default. Set a human-facing sender name while configuring one project:

```cmd
iam setup "C:\Projects\MainApp" --display-name "Adrian"
```

Setup modifies only a clearly marked InterAgentMail block in each project's `.codex/config.toml`. Running setup again updates that managed block without replacing the rest of the file. Display names are optional; without one, IAM uses the mailbox address and does not create a human persona.

The project folder basename is its mailbox address. IAM refuses a second physical project with the same basename before changing the mailbox or project configuration. Use unique project folder names for separate reviewer instances.

Automation and reviewer platforms should use `iam register PATH --json` and discover compatibility with `iam capabilities --json`. The stable schema, lifecycle, collision errors, and identity ownership rules are documented in [INTEGRATION.md](INTEGRATION.md).

## Start automatic delivery

Run this once after signing in or rebooting:

```cmd
iam start
```

`iam start` asks the Codex CLI to start its managed local app-server daemon, then launches one hidden IAM supervisor. The supervisor watches every registered mailbox but opens an isolated stdio proxy only when a mailbox has undelivered mail. It retains that connection while a turn is active or awaiting approval, then releases it after the turn is idle. It does not open one terminal per agent. Running it again is harmless.

Check everything with:

```cmd
iam status
```

## Open and use an agent

Open a terminal in a registered project and run:

```cmd
iam open
```

Or name the folder from anywhere:

```cmd
iam open "C:\Projects\SecurityReviewer"
```

Open other agents in other terminal tabs. They share the CLI-managed daemon but retain separate project folders, mailboxes, and Codex threads. Background delivery uses isolated temporary proxies, so one unavailable project does not prevent the others from receiving mail. IAM applies the registered sandbox and approval policy before resuming a terminal; a failed policy application leaves the pin unchanged and opens no terminal.

If the receiving Codex UI is closed, the background supervisor can still wake its saved thread and perform work. If a task requires interactive approval, it fails closed; open that agent to review the request.

## Human mailbox and browser interface

The browser is optional. It uses an ordinary IAM mailbox for your identity, so agents receive normal mail and their replies appear in your browser inbox. First configure the safe default, which listens only on this computer:

```cmd
iam web setup MyMailbox --display-name "My Name"
iam web start
```

Enter and confirm a password of at least 12 characters when prompted. IAM stores a salted PBKDF2 password digest, not the password. Open <http://127.0.0.1:8787>, then select any existing agent mailbox, write a subject and request, and send it. If `iam start` is running, registered agents wake automatically; otherwise the request remains queued.

The web companion is independent:

```cmd
iam web status
iam web stop
iam web password
iam web start
```

Changing the password or network configuration requires only `iam web stop`; neither `iam` delivery nor the Codex app-server is restarted. In v1.4.0 this is deliberately a standalone companion. Service-managed web lifecycle remains planned for a future release.

To let another device on the same network connect, stop the web companion if needed and explicitly opt in:

```cmd
iam web setup MyMailbox --display-name "My Name" --lan --acknowledge-network-risk
iam web start
```

On the other device, browse to `http://PC_PRIVATE_IP:8787`. A Windows firewall prompt may appear on first use; allow Private networks only, never Public networks. You may need to find the host PC's private IPv4 address with `ipconfig` on Windows or `ip addr`/`ifconfig` on macOS or Linux.

> LAN mode uses HTTP and is not end-to-end encrypted. Use it only on a trusted, password-protected WPA2/WPA3 network. Do not enable it on open Wi-Fi, public, guest, dormitory, hotel, cafe, or other untrusted networks. Keep the application password private and never expose or port-forward port 8787. Anyone who captures the password or gains access can read mail, send instructions to agents, and potentially modify or corrupt projects.

`iam user create ADDRESS --display-name "Name"` can create a human mailbox without enabling the browser. `iam user list` shows human mailboxes.

## Existing mail and offline behavior

Mail remains queued on disk while IAM is stopped. On first setup, existing messages become a baseline and only later messages wake the agent. To deliberately process the pre-existing inbox:

```cmd
iam setup "C:\Projects\MainApp" --process-existing
```

## Safety options

The default is `workspace-write` with approvals requested when needed. This is the appropriate starting point.

For a trusted reviewer that must inspect files outside its project or use unrestricted tools:

```cmd
iam setup "C:\Projects\SecurityReviewer" --full-access
```

Full access removes the Codex filesystem sandbox for mail-triggered turns. Do not enable it for untrusted repositories or untrusted senders. IAM listens on localhost and is intended for one trusted local user; its mailbox files are not cryptographically authenticated.

## Update

### Shared-service operational safety

Agents must not autonomously run `iam stop`, `iam stop --all`, `iam restart`,
or equivalent process termination for routine repairs. Stop suspends shared
delivery; restart uses the all-stop path and can interrupt every session on
the IAM-owned app-server. It is not a supervisor-only reload.

Normal maintenance requires the human owner's explicit clearance for the exact
operation and coordinated time. At Cerberus Game Labs that owner is Liliana.
General repair approval or another agent's reply is insufficient. Re-confirm
if the maintenance window or active-work conditions have changed; a delayed
reply must not trigger a badly timed restart. Inspect command scope first.

Only an immediate, evidenced emergency where waiting would cause greater harm
and obtaining clearance is unsafe permits autonomous containment. Stuck mail,
timeouts, failed tests and pending reloads do not qualify. Use the narrowest
necessary action, preserve evidence, and report the reason, affected services
and recovery state immediately. When uncertain, stop and ask.

After a new release is published, in an owner-approved maintenance window:

```cmd
pipx upgrade cgl-interagentmail
iam restart
```

Project registrations and mailbox data remain in the IAM data directory across upgrades.

## Remove a project

From the project folder:

```cmd
iam unregister
```

Or remove several registrations explicitly:

```cmd
iam unregister "C:\Projects\MainApp" "C:\Projects\SecurityReviewer"
```

This removes IAM's managed MCP configuration and registration but deliberately preserves the mailbox and messages.

## Uninstall InterAgentMail

First unregister the projects you still have, then stop IAM and remove the isolated application:

```cmd
iam unregister "C:\Projects\MainApp" "C:\Projects\SecurityReviewer" "C:\Projects\UXReviewer"
iam stop --all
pipx uninstall cgl-interagentmail
```

Mailbox data is intentionally not deleted. It remains at `%LOCALAPPDATA%\InterAgentMail` on Windows or `~/.local/share/interagentmail` on macOS/Linux unless `INTERAGENTMAIL_HOME` was set. A user may archive or delete that directory separately after verifying it contains no mail they need.

## Troubleshooting

Start with:

```cmd
iam doctor
iam doctor --project "C:\Projects\SecurityReviewer" --json
```

`iam doctor` performs read-only checks and distinguishes failures from warnings such as intentionally stopped background services. Use the project-scoped JSON form for automation. If you need support, generate a shareable Markdown report:

```cmd
iam report
```

By default the report is written under the IAM data directory's `reports` folder. It includes versions, service health, registration and MCP checks, mailbox counts, and log statistics. It excludes mailbox and chat contents, thread IDs, raw configuration, environment variables, and raw app-server log lines. Project names and paths, user paths, email addresses, IDs, and common credential formats are replaced. Review the generated file before sharing it publicly.

Use `iam report --output PATH` to choose a destination or `iam report --stdout` to print it. IAM refuses to replace an existing output file unless `--force` is provided. Do not automatically follow diagnosis with `iam restart`: it uses the all-stop path and can interrupt all sessions on the shared IAM-owned app-server. Follow the shared-service operational safety policy above.

IAM logs are under `run/supervisor.log` and, when configured, `run/web.log` inside the IAM data directory. The supervisor log rotates at 10 MiB with three backups. `run/app-server.log` may remain from a pre-managed-daemon installation but is no longer written by the default setup.

Common issues:

- **`iam` is not recognized:** close and reopen the terminal after `pipx ensurepath`; then run `py -m pipx ensurepath` again if needed.
- **Codex was not found:** verify `codex --version` in the same terminal and complete the Codex sign-in flow.
- **A mailbox does not wake:** confirm its project appears in `iam status`; then check `supervisor.log`. Each new message activates only that mailbox's temporary delivery connection, so other projects remain unaffected by a failed attachment.
- **Port 4500 is occupied:** the default managed transport uses no TCP port. If you explicitly use a legacy `--url ws://...`, stop the old listener or consistently choose another loopback URL.
- **Windows helper windows flash during turns:** the opt-in [hidden-console compatibility mode](WINDOWS_CONSOLE_WORKAROUND.md) runs an IAM-owned shared server without rebuilding Codex. Read its maintenance-window switch steps, limitations, and loopback security warning before enabling it.
- **Work waits for approval:** open the receiving project with `iam open` and review the request. IAM never grants an unattended approval automatically.
- **An old bridge is also running:** stop manually launched `IAMBridge` or `iam-codex-bridge` processes; the supervisor replaces them.
- **A thread cannot resume:** IAM preserves the existing thread ID and queues work. Check `iam doctor` and the supervisor log; retry after transient app-server failures. To explicitly select another saved session for the same project, use `iam open --thread-id SESSION_ID`. Restarting does not silently replace a pinned thread.
- **The browser does not open:** run `iam web status`, then inspect `run/web.log`; `iam web start` does not start agent delivery.
- **A laptop cannot connect:** confirm LAN mode is configured, use the host PC's private IP, and allow the port on Private networks only. Never weaken a Public-network firewall to make it work.

For support, open an issue at <https://github.com/cerberusgamelabs/cgl-interagentmail/issues> and attach the output from `iam report` after reviewing it. Report suspected vulnerabilities privately as described in <https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/SECURITY.md>.
