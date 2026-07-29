# Publishing InterAgentMail

This is the Cerberus Game Labs maintainer checklist for a public release.

## Before the first public release

InterAgentMail is released under the MIT License, copyright 2026 Cerberus Game Labs. The root `LICENSE` file and PEP 639 package metadata must be present in every release artifact.

1. Confirm the canonical GitHub links in `pyproject.toml` still resolve.
2. Confirm that the `cgl-interagentmail` PyPI name is still available. The first successful upload reserves it.
3. Configure PyPI Trusted Publishing. No long-lived PyPI token is required.

Do not put PyPI tokens in the repository, command history, or website files.

### First-time PyPI trusted publisher

Because the PyPI project does not exist yet, sign in to PyPI and create a pending GitHub publisher with these exact values:

```text
PyPI project name: cgl-interagentmail
Owner: cerberusgamelabs
Repository: cgl-interagentmail
Workflow filename: publish.yml
Environment: pypi
```

Also create a GitHub environment named `pypi` at `Settings > Environments > New environment`. Requiring a Cerberus Game Labs maintainer to approve deployments is recommended.

After the pending publisher exists, open the repository's **Actions > Publish to PyPI** workflow, choose **Run workflow**, keep tag `v1.1.0`, and approve the `pypi` environment deployment if prompted. The workflow builds and tests in a job without publishing credentials, then passes only the distributions to the OIDC-enabled publishing job.

The pending publisher does not reserve the package name. Run the workflow promptly after configuring it.

## Build locally

From PowerShell in the repository:

```powershell
.\scripts\build-release.ps1
```

The script runs the tests, creates a wheel and source distribution, checks both with Twine, inspects their contents for excluded private/runtime paths, and writes SHA-256 checksums.

Expected files for version 1.1.0:

```text
dist/cgl_interagentmail-1.1.0-py3-none-any.whl
dist/cgl_interagentmail-1.1.0.tar.gz
dist/SHA256SUMS.txt
```

Review the script output and manually inspect the archive listing before upload. Both artifacts must include the MIT license. Neither artifact should contain `mailboxes`, `chats`, `run`, `config.json`, `.venv`, `DEV`, or the chat-reader application. The source archive intentionally includes the automated tests; the wheel does not.

## Test the package in a clean environment

Install the built wheel into a disposable pipx environment:

```powershell
pipx install --force .\dist\cgl_interagentmail-1.1.0-py3-none-any.whl
iam --help
interagentmail --help
iam-mcp --help
```

Use temporary project directories for a setup/open smoke test. Do not point a release smoke test at production mailboxes.

## Upload to TestPyPI

```powershell
python -m twine upload --repository testpypi dist\cgl_interagentmail-1.1.0*
```

Install from TestPyPI while allowing dependencies from the main index:

```powershell
pipx install --pip-args="--extra-index-url https://pypi.org/simple" --index-url https://test.pypi.org/simple/ cgl-interagentmail
```

Verify all four commands and perform a temporary-project smoke test.

## Publish to PyPI

PyPI releases are immutable. Confirm the version, metadata, README rendering, license, and checksums before uploading:

```powershell
python -m twine upload dist\cgl_interagentmail-1.1.0-py3-none-any.whl dist\cgl_interagentmail-1.1.0.tar.gz
```

Then install from PyPI into a clean pipx environment and repeat the smoke test:

```powershell
pipx install --force cgl-interagentmail
```

## Publish on cerberusgamelabs.xyz

Upload or link:

- the wheel for direct-download users;
- the source distribution;
- `SHA256SUMS.txt`;
- the rendered contents of `docs/INSTALL.md`;
- the PyPI project page.

`docs/WEBSITE_COPY.md` contains concise landing-page copy. Verify every public download URL from a logged-out browser and check the downloaded file's SHA-256 hash before announcing the release.

## Future releases

1. Update the version in `pyproject.toml`.
2. Add a dated entry to `CHANGELOG.md`.
3. Run the complete build script.
4. Test the wheel in a clean environment.
5. Publish the immutable artifacts.
6. Update website links and checksums.
7. Verify `pipx upgrade cgl-interagentmail` followed by `iam restart`.
