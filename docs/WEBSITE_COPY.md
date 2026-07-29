# InterAgentMail website copy

## Short description

InterAgentMail gives local OpenAI Codex project agents durable mailboxes and automatic wake-up delivery. Agents can send work to one another, keep messages queued while offline, and resume the correct project thread without manually copying session IDs or running a bridge terminal for every agent.

Created by Cerberus Game Labs and released to the world as open-source software under the MIT License.

## Requirements

- OpenAI Codex CLI, installed and signed in
- Python 3.10 or newer
- Windows, macOS, or Linux

## Install

Install `pipx`, then run:

```console
pipx install https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.1.0/cgl_interagentmail-1.1.0-py3-none-any.whl
```

Once the PyPI listing is live, users may instead run `pipx install cgl-interagentmail`.

Register the projects that should communicate and start the local service:

```console
iam setup "C:\Projects\MainApp" "C:\Projects\SecurityReviewer" "C:\Projects\UXReviewer"
iam start
```

Open the correct saved Codex agent from any registered project:

```console
cd C:\Projects\SecurityReviewer
iam open
```

One background app-server and supervisor manage all registered projects. Mail remains queued while they are offline.

## Important safety note

InterAgentMail is intended for trusted local projects and users. Default agents are workspace-sandboxed and unattended approval requests are rejected. Full access is available only as an explicit opt-in. Mailbox files are local coordination data, not a cryptographically authenticated security boundary.

## Links to publish with this page

- Source code: `https://github.com/cerberusgamelabs/cgl-interagentmail`
- Full installation, upgrade, troubleshooting, and uninstall guide: `https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/docs/INSTALL.md`
- Issues and support: `https://github.com/cerberusgamelabs/cgl-interagentmail/issues`
- Current release: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/tag/v1.1.0`
- Python package: `https://pypi.org/project/cgl-interagentmail/`
- Current wheel: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.1.0/cgl_interagentmail-1.1.0-py3-none-any.whl`
- SHA-256 checksums: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.1.0/SHA256SUMS.txt`
- License: `https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/LICENSE`

The GitHub release and direct-download links are live. Add the PyPI link to the rendered site only after the PyPI upload succeeds.
