# Security policy

## Supported versions

Security fixes are provided for the latest published InterAgentMail release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability-reporting or security-advisory interface for this repository:

<https://github.com/cerberusgamelabs/cgl-interagentmail/security/advisories/new>

Include the affected version, operating system, reproduction steps, potential impact, and any suggested mitigation. Remove unrelated private mail and project data.

## Trust model

InterAgentMail is designed for trusted local users and projects. Anyone who can modify the IAM data directory may inject or alter agent mail, replace configuration, or read stored messages. Operating-system access to that directory remains the primary boundary.

The v1.3 browser interface adds application-password authentication, salted PBKDF2 password storage, CSRF and same-origin checks, login throttling, bounded request bodies, restrictive browser headers, and in-memory expiring sessions. It does not turn file-backed IAM into a hostile multi-user service.

Local-only mode binds to loopback and is the default. LAN mode binds to all network interfaces and uses plain HTTP, so traffic is not end-to-end encrypted. Enable it only on a trusted, password-protected WPA2/WPA3 network; allow the port only on Private firewall profiles; never use open/public/guest Wi-Fi; never port-forward it; and keep the application password private. A network participant who obtains access can read messages, impersonate the human mailbox, send agent instructions, and potentially cause project modification or corruption.

The Codex app-server remains localhost-only and is never exposed by the browser companion. The web process does not grant approvals or weaken an agent's configured sandbox.
