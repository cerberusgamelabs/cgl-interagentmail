"""Windows ACL checks must not depend on the calling shell's module paths."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from iam_hooks import HookError, private_directory


@unittest.skipUnless(os.name == "nt", "Windows ACL integration tests")
class WindowsACLTests(unittest.TestCase):
    def test_acl_query_uses_host_module_and_propagates_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            failure = subprocess.CalledProcessError(1, "powershell.exe")
            with patch("iam_hooks.subprocess.run"), patch(
                "iam_hooks.subprocess.check_output",
                side_effect=['"fixture","S-1-5-21-123"', failure],
            ) as output:
                with self.assertRaises(subprocess.CalledProcessError):
                    private_directory(Path(temporary) / "private")
            command = output.call_args.args[0][-1]
            self.assertIn("Join-Path $PSHOME", command)
            self.assertIn("Microsoft.PowerShell.Security.psd1", command)
            self.assertIn("Microsoft.PowerShell.Security\\Get-Acl", command)
            self.assertIn("$ErrorActionPreference = 'Stop'", command)

    def test_acl_check_ignores_incompatible_inherited_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "modules" / "Microsoft.PowerShell.Security"
            module.mkdir(parents=True)
            (module / "Microsoft.PowerShell.Security.psm1").write_text(
                "throw 'Incompatible inherited security module'", encoding="utf-8")
            with patch.dict(os.environ, {"PSModulePath": str(module.parent)}):
                target = root / "private"
                self.assertEqual(private_directory(target), target.absolute())

    def test_acl_check_rejects_explicit_foreign_grants(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = private_directory(Path(temporary) / "private")
            subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:(OI)(CI)R"],
                           check=True, stdout=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            with self.assertRaises(HookError) as caught:
                private_directory(target)
            self.assertEqual(caught.exception.code, "state_directory_has_other_principals")
