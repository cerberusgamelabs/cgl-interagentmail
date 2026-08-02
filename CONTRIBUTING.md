# Contributing to InterAgentMail

InterAgentMail is a Cerberus Game Labs gift to the agent-development community. Bug reports, documentation improvements, compatibility fixes, and focused feature contributions are welcome.

## Before opening an issue

- Search the existing issues.
- Run `iam doctor`, then generate and review an `iam report` for the issue.
- Never attach raw app-server logs without inspecting them; they can contain project source or private instructions.
- Use the private process in `SECURITY.md` for suspected vulnerabilities.

## Development setup

```console
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

Changes to `iam_web.py` must include tests for authentication boundaries, CSRF/origin enforcement, mailbox authorization, and request validation. Do not use real mailbox data or restart a developer's running IAM services during tests.

On Windows, activate the virtual environment or invoke `.venv\Scripts\python.exe` directly. On macOS/Linux, use `.venv/bin/python`.

## Pull requests

- Keep changes focused and explain the user-visible behavior.
- Add or update tests for behavior changes.
- Preserve safe defaults: unattended work must never grant its own approval.
- Do not commit mailbox data, messages, Codex session IDs, logs, credentials, or machine-specific paths.
- Update `README.md`, `docs/INSTALL.md`, and `CHANGELOG.md` when user-facing behavior changes.
- Preserve the schema 1.0 envelope and stable error codes in `docs/INTEGRATION.md`; add compatibility tests for integration changes.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
