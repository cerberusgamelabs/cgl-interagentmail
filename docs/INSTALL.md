# Installing InterAgentMail

This guide is suitable for linking directly from the Cerberus Game Labs website. InterAgentMail runs locally and connects local Codex project agents; it does not require a hosted InterAgentMail account.

InterAgentMail is open-source software distributed under the MIT License.

## What gets installed

- `iam`: setup, agent opening, and background-service management
- `interagentmail`: human/fallback mailbox commands
- `iam-mcp`: the project-bound MCP server configured by `iam setup`
- `iam-codex-bridge`: the low-level manual bridge, normally not needed

One IAM installation can manage any number of projects for one operating-system user.

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
pipx install https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.1.0/cgl_interagentmail-1.1.0-py3-none-any.whl
iam --help
```

To install a wheel downloaded directly from Cerberus Game Labs instead, use its actual downloaded path:

```cmd
pipx install "%USERPROFILE%\Downloads\cgl_interagentmail-1.1.0-py3-none-any.whl"
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

Setup modifies only a clearly marked InterAgentMail block in each project's `.codex/config.toml`. Running setup again updates that managed block without replacing the rest of the file.

## Start automatic delivery

Run this once after signing in or rebooting:

```cmd
iam start
```

`iam start` launches one local Codex app-server and one IAM supervisor in the background. It does not open one terminal per agent. Running it again is harmless.

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

Open other agents in other terminal tabs. They share the same app-server but retain separate project folders, mailboxes, and Codex threads.

If the receiving Codex UI is closed, the background supervisor can still wake its saved thread and perform work. If a task requires interactive approval, it fails closed; open that agent to review the request.

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

After a new release is published:

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
iam status
iam restart
```

Logs are under `run/app-server.log` and `run/supervisor.log` inside the IAM data directory.

Common issues:

- **`iam` is not recognized:** close and reopen the terminal after `pipx ensurepath`; then run `py -m pipx ensurepath` again if needed.
- **Codex was not found:** verify `codex --version` in the same terminal and complete the Codex sign-in flow.
- **A mailbox does not wake:** confirm its project appears in `iam status`; then check `supervisor.log`.
- **Port 4500 is occupied:** stop an old manual `codex app-server` or use a consistent custom `--url` with IAM commands.
- **Work waits for approval:** open the receiving project with `iam open` and review the request. IAM never grants an unattended approval automatically.
- **An old bridge is also running:** stop manually launched `IAMBridge` or `iam-codex-bridge` processes; the supervisor replaces them.
- **A thread cannot resume:** run `iam restart`. The supervisor creates and persists a replacement thread when the saved one no longer exists.

For support, open an issue at <https://github.com/cerberusgamelabs/cgl-interagentmail/issues> and include `iam status`, `iam --version`, `codex --version`, the operating system, and the relevant log excerpt. Remove private project paths or message content before posting logs publicly. Report suspected vulnerabilities privately as described in <https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/SECURITY.md>.
