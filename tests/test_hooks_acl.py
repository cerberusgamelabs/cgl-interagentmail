"""Windows ACL checks must not depend on the calling shell's module paths."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from iam_hooks import HookError, private_directory, _windows_directory_principals


@unittest.skipUnless(os.name == "nt", "Windows ACL integration tests")
class WindowsACLTests(unittest.TestCase):
    def test_acl_query_failure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("iam_hooks._windows_directory_principals", side_effect=OSError("ACL read failed")):
                with self.assertRaises(OSError):
                    private_directory(Path(temporary) / "private")

    def test_new_directory_removes_creation_defaults_and_returns_numeric_sids(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "private"
            mkdir = Path.mkdir

            def creation_defaults(path, *args, **kwargs):
                mkdir(path, *args, **kwargs)
                if path == target:
                    subprocess.run(["icacls", str(path), "/grant",
                                    "*S-1-5-32-544:(OI)(CI)F", "*S-1-3-4:(OI)(CI)F"],
                                   check=True, stdout=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)

            with patch.object(Path, "mkdir", creation_defaults):
                private_directory(target)
            owner, principals = _windows_directory_principals(target)
            self.assertTrue(owner.startswith("S-1-"))
            self.assertEqual(set(principals), {owner, "S-1-5-18"})
            self.assertNotIn("S-1-5-32-544", principals)
            self.assertNotIn("S-1-3-4", principals)

    def test_existing_directory_is_not_reset_or_reowned(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = private_directory(Path(temporary) / "private")
            content = target / "keep.txt"
            content.write_text("existing data", encoding="utf-8")
            run = subprocess.run
            with patch("iam_hooks.subprocess.run", wraps=run) as calls:
                private_directory(target)
            for call in calls.call_args_list:
                self.assertNotIn("/reset", call.args[0])
                self.assertNotIn("/setowner", call.args[0])
            self.assertEqual(content.read_text(encoding="utf-8"), "existing data")

    def test_existing_administrators_and_owner_rights_grants_remain_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index, sid in enumerate(("S-1-5-32-544", "S-1-3-4")):
                target = private_directory(Path(temporary) / str(index))
                subprocess.run(["icacls", str(target), "/grant", f"*{sid}:(OI)(CI)R"],
                               check=True, stdout=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                with self.assertRaises(HookError):
                    private_directory(target)
                self.assertIn(sid, _windows_directory_principals(target)[1])

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
