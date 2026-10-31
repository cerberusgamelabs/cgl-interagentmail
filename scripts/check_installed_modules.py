"""Check installed module discovery without the checkout masking missing modules.

Run with the same Python interpreter used by IAM. No services are started and
no application stores are opened. Exit nonzero for an incomplete installation.
"""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def declared_modules(root):
    config = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^py-modules\s*=\s*(\[[\s\S]*?\])", config, re.MULTILINE)
    if not match:
        raise ValueError("Missing setuptools py-modules declaration")
    modules = ast.literal_eval(match.group(1))
    if not isinstance(modules, list) or not all(isinstance(n, str) for n in modules):
        raise ValueError("Invalid module declaration")
    return modules


def main():
    root = Path(__file__).resolve().parents[1]
    modules = declared_modules(root)
    probe = (
        "import importlib.util,json,sys; "
        "names=json.loads(sys.argv[1]); "
        "missing=[n for n in names if importlib.util.find_spec(n) is None]; "
        "print(json.dumps({'checked':len(names),'missing':missing})); "
        "sys.exit(bool(missing))"
    )
    with tempfile.TemporaryDirectory(prefix="iam-installed-check-") as directory:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", probe, json.dumps(modules)],
            cwd=directory, capture_output=True, text=True, timeout=30,
        )
    print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
