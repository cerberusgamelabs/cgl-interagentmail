# Security policy

## Supported versions

Security fixes are provided for the latest published InterAgentMail release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability-reporting or security-advisory interface for this repository:

<https://github.com/cerberusgamelabs/cgl-interagentmail/security/advisories/new>

Include the affected version, operating system, reproduction steps, potential impact, and any suggested mitigation. Remove unrelated private mail and project data.

## Trust model

InterAgentMail is designed for trusted local users and projects. Its localhost services and local JSON mailbox files are not a multi-user authentication boundary. Anyone who can modify the IAM data directory may be able to inject or alter agent mail.
