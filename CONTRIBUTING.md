# Contributing to InterAgentMail

InterAgentMail is a Cerberus Game Labs gift to the agent-development community. Bug reports, documentation improvements, compatibility fixes, and focused feature contributions are welcome.

## Before opening an issue

- Search the existing issues.
- Run `iam --version`, `codex --version`, and `iam status`.
- Remove private project paths, message bodies, tokens, and account information from logs.
- Use the private process in `SECURITY.md` for suspected vulnerabilities.

## Development setup

```console
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

On Windows, activate the virtual environment or invoke `.venv\Scripts\python.exe` directly. On macOS/Linux, use `.venv/bin/python`.

## Pull requests

- Keep changes focused and explain the user-visible behavior.
- Add or update tests for behavior changes.
- Preserve safe defaults: unattended work must never grant its own approval.
- Do not commit mailbox data, messages, Codex session IDs, logs, credentials, or machine-specific paths.
- Update `README.md`, `docs/INSTALL.md`, and `CHANGELOG.md` when user-facing behavior changes.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
