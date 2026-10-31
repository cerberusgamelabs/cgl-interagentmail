"""Local shared-session administration and mailbox authorization regressions."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import interagentmail as iam
from iam_service import IAMService, IAMError, mailbox_service, session_owner
from iam_orchestrator import main, setup_project, unregister_project, cmd_open, session_guard, switch_session_workspace, verify_session_workspace
from iam_codex_bridge import DeliveryState
from argparse import Namespace


class SharedSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, value in {"ROOT": self.root / "home", "CONFIG": self.root / "home/config.json",
                            "MAILBOXES": self.root / "home/mailboxes", "CHATS": self.root / "home/chats"}.items():
            p = patch.object(iam, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.projects = {}
        for name in ("Primary", "Secondary", "Other"):
            root = self.root / name
            root.mkdir()
            setup_project(root, display_name="Oscar")
            self.projects[name] = root

    def link(self):
        with redirect_stdout(io.StringIO()):
            main(["session", "link", str(self.projects["Secondary"]), "--primary", str(self.projects["Primary"])])

    def test_existing_delivery_policy_is_separate_and_fail_closed(self):
        from iam_orchestrator import check_delivery_requirement
        state = DeliveryState.load(iam.mailbox("Primary") / ".codex-bridge-state.json")
        state.thread_id = "expected"
        state.save()
        self.link()
        with redirect_stdout(io.StringIO()):
            main(["session", "require-existing", str(self.projects["Secondary"]),
                  "--thread-id", "expected", "--session-owner", "Primary"])
        self.assertEqual("expected", check_delivery_requirement("Secondary", "expected")["thread_id"])
        with self.assertRaisesRegex(IAMError, "active_thread_mismatch"):
            check_delivery_requirement("Secondary", "stale")
        for value, reason in [(None, "thread_missing"), ("different", "thread_mismatch")]:
            state.thread_id = value
            state.save()
            with self.assertRaisesRegex(IAMError, reason):
                check_delivery_requirement("Secondary")
        with redirect_stdout(io.StringIO()):
            main(["session", "unlink", str(self.projects["Secondary"])])
        with self.assertRaisesRegex(IAMError, "owner_mismatch"):
            check_delivery_requirement("Secondary")
        with redirect_stdout(io.StringIO()):
            main(["session", "allow-replacement", str(self.projects["Secondary"])])
        self.assertIsNone(check_delivery_requirement("Secondary"))

    def test_existing_delivery_policy_rejects_missing_pin_and_invalid_policy(self):
        from iam_orchestrator import check_delivery_requirement
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            main(["session", "require-existing", str(self.projects["Primary"]), "--thread-id", "missing"])
        iam.write_atomic(iam.mailbox("Primary") / ".delivery-requirement.json", {})
        with self.assertRaisesRegex(IAMError, "invalid_delivery_requirement"):
            check_delivery_requirement("Primary")

    def test_primary_requirement_is_inherited_and_status_reports_hold(self):
        from iam_orchestrator import check_delivery_requirement, collect_status
        self.link()
        state = DeliveryState.load(iam.mailbox("Primary") / ".codex-bridge-state.json")
        state.thread_id = "expected"
        state.save()
        with redirect_stdout(io.StringIO()):
            main(["session", "require-existing", str(self.projects["Primary"]), "--thread-id", "expected"])
        self.assertEqual("expected", check_delivery_requirement("Secondary")["thread_id"])
        state.thread_id = None
        state.save()
        with patch("iam_orchestrator.appserver_available", AsyncMock(return_value=False)), patch("iam_orchestrator.read_pid", return_value=None):
            status = collect_status()
        for row in status["projects"]:
            if row["address"] in {"Primary", "Secondary"}:
                self.assertEqual("held", row["delivery_policy"]["state"])
                self.assertEqual("delivery_thread_missing", row["delivery_policy"]["reason"])

    def test_same_name_grants_nothing_and_link_keeps_mail_separate(self):
        with self.assertRaises(IAMError):
            mailbox_service(self.projects["Primary"], "Secondary")
        self.link()
        primary = mailbox_service(self.projects["Primary"])
        secondary = mailbox_service(self.projects["Primary"], "Secondary")
        sender = IAMService(self.projects["Other"])
        msg = sender.send(["Secondary"], "Request", "Please reply")
        self.assertEqual([], primary.inbox())
        self.assertEqual(msg["id"], secondary.read(msg["id"])["id"])
        reply = secondary.reply(msg["id"], "Done")
        self.assertEqual("Secondary", reply["from"])
        self.assertEqual([], primary.list_messages("sent"))
        secondary.archive(msg["id"])
        self.assertEqual([], secondary.inbox())
        self.assertEqual(1, len(secondary.list_messages("archive")))
        with self.assertRaises(IAMError):
            mailbox_service(self.projects["Other"], "Secondary")

    def test_unlink_revokes_access_without_changing_mail_or_old_binding(self):
        state = DeliveryState.load(iam.mailbox("Secondary") / ".codex-bridge-state.json")
        state.thread_id = "old-thread"
        state.save()
        self.link()
        with self.assertRaises(IAMError):
            unregister_project(self.projects["Primary"])
        with redirect_stdout(io.StringIO()):
            main(["session", "unlink", str(self.projects["Secondary"])])
        with self.assertRaises(IAMError):
            mailbox_service(self.projects["Primary"], "Secondary")
        self.assertEqual("old-thread", DeliveryState.load(state.path).thread_id)
        self.assertTrue(iam.mailbox("Secondary").is_dir())

    def test_chains_cycles_and_foreign_root_are_rejected(self):
        self.link()
        with self.assertRaises(SystemExit):
            main(["session", "link", str(self.projects["Other"]), "--primary", str(self.projects["Secondary"])])
        with self.assertRaises(SystemExit):
            main(["session", "link", str(self.projects["Primary"]), "--primary", str(self.projects["Other"])])
        with self.assertRaises(IAMError):
            mailbox_service(self.root / "wrong/Primary", "Secondary")
        config = iam.load(iam.CONFIG)
        config["session_mailboxes"]["Primary"] = "Secondary"
        with self.assertRaises(IAMError):
            session_owner("Secondary", config)

    def test_open_secondary_uses_selected_root_and_primary_binding(self):
        self.link()
        state = DeliveryState.load(iam.mailbox("Primary") / ".codex-bridge-state.json")
        state.thread_id = "shared-thread"
        state.save()
        args = Namespace(project=str(self.projects["Secondary"]), url="ws://localhost:4500", thread_id=None)
        client = MagicMock()
        client.connect = AsyncMock()
        client.close = AsyncMock()
        client.request = AsyncMock(return_value={"thread": {"status": {"type": "idle"}}})
        with patch("iam_orchestrator.ensure_appserver"), patch("iam_orchestrator.ensure_daemon"), \
             patch("iam_orchestrator.AppServerClient", return_value=client), \
             patch("iam_orchestrator.switch_session_workspace", new_callable=AsyncMock) as switch, \
             patch("iam_orchestrator.codex_executable", return_value="codex"), \
             patch("iam_orchestrator.wait_for_thread", return_value="shared-thread"), \
             patch("iam_orchestrator.subprocess.run") as run, redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            with self.assertRaises(SystemExit):
                cmd_open(args)
            command = run.call_args.args[0]
            self.assertEqual(str(self.projects["Secondary"]), command[command.index("-C") + 1])
            self.assertEqual(str(self.projects["Secondary"]), run.call_args.kwargs["cwd"])
            self.assertEqual(self.projects["Secondary"], switch.call_args.args[2])
            self.assertIn("shared-thread", command)
            self.assertEqual("daemon_auto_start", command[command.index("--disable") + 1])
            primary = iam.load(iam.CONFIG)["projects"]["Primary"]
            self.assertNotIn("-s", command)
            self.assertNotIn("-a", command)
            self.assertEqual(primary, switch.call_args.args[3])
            args.thread_id = "different-thread"
            with self.assertRaises(IAMError):
                cmd_open(args)
            self.assertEqual(1, run.call_count)

    def test_terminal_lock_is_exclusive_and_released(self):
        with session_guard("Primary", "terminal") as acquired:
            self.assertTrue(acquired)
            with session_guard("Primary", "terminal") as duplicate:
                self.assertFalse(duplicate)
        with session_guard("Primary", "terminal") as acquired:
            self.assertTrue(acquired)

    def test_workspace_verification_rejects_cwd_or_roots_mismatch(self):
        root = self.projects["Secondary"]
        result = {"thread": {"id": "shared"}, "cwd": str(root), "runtimeWorkspaceRoots": [str(root)]}
        verify_session_workspace(result, "shared", root)
        for change in ({"cwd": str(self.projects["Primary"])}, {"runtimeWorkspaceRoots": []}, {"thread": {"id": "other"}}):
            with self.assertRaises(IAMError):
                verify_session_workspace({**result, **change}, "shared", root)

    def test_switch_refuses_active_turn_before_any_mutation(self):
        import asyncio
        client = MagicMock()
        client.request = AsyncMock(return_value={"thread": {"status": {"type": "active", "activeFlags": ["waitingOnApproval"]}}})
        with self.assertRaises(IAMError):
            asyncio.run(switch_session_workspace(client, "shared", self.projects["Secondary"], {}))
        self.assertEqual(1, client.request.call_count)

    def test_switch_updates_both_runtime_fields_and_preserves_policy(self):
        import asyncio
        root = self.projects["Secondary"]
        client = MagicMock()
        client.request = AsyncMock(side_effect=[
            {"thread": {"status": {"type": "idle"}}},
            {"thread": {"id": "shared", "status": {"type": "idle"}}, "cwd": str(root), "runtimeWorkspaceRoots": [str(root)]},
            {},
            {"thread": {"id": "shared", "status": {"type": "idle"}},
             "approvalPolicy": "on-request", "sandbox": {"type": "readOnly"}},
        ])
        asyncio.run(switch_session_workspace(client, "shared", root, {"sandbox": "read-only", "approval_policy": "on-request"}))
        calls = client.request.call_args_list
        self.assertEqual("thread/resume", calls[1].args[0])
        self.assertNotIn("sandbox", calls[1].args[1])
        self.assertNotIn("approvalPolicy", calls[1].args[1])
        self.assertEqual([str(root)], calls[1].args[1]["runtimeWorkspaceRoots"])
        self.assertEqual("thread/settings/update", calls[2].args[0])
        self.assertEqual("readOnly", calls[2].args[1]["sandboxPolicy"]["type"])
        self.assertEqual("on-request", calls[2].args[1]["approvalPolicy"])


if __name__ == "__main__":
    unittest.main()
