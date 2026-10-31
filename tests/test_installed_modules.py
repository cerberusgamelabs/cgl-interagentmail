"""Installed-environment regression: never resolve modules from the checkout cwd."""
from pathlib import Path
import subprocess
import sys
import unittest

from scripts.check_installed_modules import declared_modules


class InstalledModulesTests(unittest.TestCase):
    def test_all_runtime_modules_are_declared(self):
        root = Path(__file__).resolve().parents[1]
        expected = {p.stem for p in root.glob("iam_*.py")} | {"interagentmail"}
        self.assertEqual(expected, set(declared_modules(root)))

    def test_installed_discovery(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts/check_installed_modules.py")],
            capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
