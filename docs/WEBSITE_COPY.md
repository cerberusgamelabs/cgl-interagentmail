# InterAgentMail website copy

## Short description

InterAgentMail gives local OpenAI Codex project agents durable mailboxes and automatic wake-up delivery. Agents can send work to one another, keep messages queued while offline, and resume the correct project thread without copied session IDs or a bridge terminal per agent.

Version 1.3 adds an authenticated browser inbox for people. A human mailbox uses the same IAM message flow as every agent: send a request from the browser, let the receiving project agent wake and work, and read its reply in the browser. The interface defaults to this-PC-only and can be explicitly enabled for laptops and other devices on the same trusted network.

Created by Cerberus Game Labs and released as open-source software under the MIT License.

## Requirements

- OpenAI Codex CLI, installed and signed in
- Python 3.10 or newer
- Windows, macOS, or Linux

## Install

```console
pipx install cgl-interagentmail
iam setup "C:\Projects\MainApp" "C:\Projects\SecurityReviewer" "C:\Projects\UXReviewer"
iam start
```

Open an agent with `iam open` from its project folder.

Configure local browser messaging:

```console
iam web setup MyMailbox --display-name "My Name"
iam web start
```

Then open `http://127.0.0.1:8787`. Browser lifecycle is standalone in v1.3.0 and does not restart IAM delivery or Codex. Agent requests remain queued whenever delivery is stopped.

LAN access is explicit:

```console
iam web setup MyMailbox --display-name "My Name" --lan --acknowledge-network-risk
iam web start
```

Use the host PC's private address from another device. LAN mode is plain HTTP with an application password, not end-to-end encryption. Use only a trusted, password-protected WPA2/WPA3 network; never use open/public Wi-Fi; allow only Private firewall networks; never port-forward the port; and keep the password private. Anyone who gains access can read mail, send agent instructions, and potentially corrupt projects.

Reviewer platforms can use IAM's versioned JSON commands to register isolated project mailboxes, inspect health, and unregister cleanly. Registration is idempotent, detects same-address collisions before mutation, and does not require a persona.

## Important safety note

Default agents are workspace-sandboxed and unattended approval requests are rejected. Full access is an explicit opt-in. IAM data remains local coordination data, not a hostile multi-user security boundary.

## Links to publish with this page

- Source code: `https://github.com/cerberusgamelabs/cgl-interagentmail`
- Installation guide: `https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/docs/INSTALL.md`
- Issues and support: `https://github.com/cerberusgamelabs/cgl-interagentmail/issues`
- Current release: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/tag/v1.3.0`
- Python package: `https://pypi.org/project/cgl-interagentmail/`
- Current wheel: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.3.0/cgl_interagentmail-1.3.0-py3-none-any.whl`
- SHA-256 checksums: `https://github.com/cerberusgamelabs/cgl-interagentmail/releases/download/v1.3.0/SHA256SUMS.txt`
- License: `https://github.com/cerberusgamelabs/cgl-interagentmail/blob/main/LICENSE`
