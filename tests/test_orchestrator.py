from __future__ import annotations

import asyncio
import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import interagentmail as iam
from iam_codex_bridge import AppServerError, DeliveryState
from iam_service import IAMError, IAMService
from iam_orchestrator import (
    CHECK_FAIL,
    IAMCommandError,
    INTEGRATION_SCHEMA_VERSION,
    MANAGED_ENDPOINT,
    MCP_BEGIN,
    apply_thread_policy,
    appserver_client,
    cmd_open,
    cmd_start,
    cmd_report,
    collect_diagnostics,
    command_version,
    configure_web,
    create_user_mailbox,
    ensure_appserver,
    load_web_config,
    main,
    mcp_python_executable,
    pid_alive,
    registered_projects,
    repair_codex_daemon_state_acl,
    render_report,
    sanitize_report,
    serve_once,
    setup_project,
    stop_process,
    unregister_project,
)


class OrchestratorSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"
        self.project = self.root / "ExampleProject"
        (self.project / ".codex").mkdir(parents=True)
        (self.project / ".codex" / "config.toml").write_text(
            'model = "gpt-5.6-sol"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def test_setup_is_idempotent_and_preserves_project_config(self) -> None:
        first = setup_project(self.project, display_name="Example Agent")
        second = setup_project(self.project, display_name="Example Agent")

        config_text = (self.project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5.6-sol"', config_text)
        self.assertEqual(1, config_text.count(MCP_BEGIN))
        self.assertIn("[mcp_servers.interagentmail]", config_text)
        self.assertIn("--project-root", config_text)
        self.assertEqual("registered", first["status"])
        self.assertEqual("unchanged", second["status"])
        self.assertEqual({key: value for key, value in first.items() if key != "status"}, {key: value for key, value in second.items() if key != "status"})
        self.assertEqual(str(self.project.resolve()), iam.profile("ExampleProject")["project_root"])
        self.assertEqual("project", iam.profile("ExampleProject")["kind"])
        self.assertEqual(str(self.project.resolve()), registered_projects()["ExampleProject"]["project_root"])
        self.assertEqual("Example Agent", iam.display_name("ExampleProject"))

    @unittest.skipUnless(os.name == "nt", "Windows-only MCP launcher")
    def test_managed_mcp_uses_windowless_python(self) -> None:
        executable = Path(mcp_python_executable())
        self.assertEqual("pythonw.exe", executable.name.lower())
        self.assertTrue(executable.is_file())

        setup_project(self.project)
        config_text = (self.project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("pythonw.exe", config_text.lower())

    @unittest.skipUnless(os.name == "nt", "Windows-only process termination")
    def test_failed_taskkill_is_reported_instead_of_claiming_success(self) -> None:
        with (
            patch("iam_orchestrator.subprocess.run") as run,
            patch("iam_orchestrator.pid_alive", return_value=True),
        ):
            run.return_value.returncode = 5
            run.return_value.stdout = ""
            run.return_value.stderr = "Access is denied."
            with self.assertRaisesRegex(IAMCommandError, "Access is denied"):
                stop_process(1234, tree=True)

    def test_managed_endpoint_uses_cli_proxy(self) -> None:
        with (
            patch("iam_orchestrator.codex_executable", return_value="codex"),
            patch("iam_orchestrator.AppServerClient") as client_type,
        ):
            appserver_client(MANAGED_ENDPOINT, request_timeout=7)

        client_type.assert_called_once_with(
            command=["codex", "app-server", "proxy"],
            websocket_stdio=True,
            request_timeout=7,
            experimental_api=True,
        )

    @unittest.skipUnless(os.name == "nt", "Windows-only daemon ACL repair")
    def test_daemon_acl_repair_uses_private_current_user_dacl(self) -> None:
        home = self.root / "profile"
        state_dir = home / ".codex" / "app-server-daemon"
        state_dir.mkdir(parents=True)
        results = [
            subprocess.CompletedProcess(["whoami"], 0, "desktop\\agent\n", ""),
            subprocess.CompletedProcess(["icacls"], 0, "ok", ""),
            subprocess.CompletedProcess(["icacls"], 0, "ok", ""),
            subprocess.CompletedProcess(["icacls"], 0, "ok", ""),
        ]
        with (
            patch("iam_orchestrator.Path.home", return_value=home),
            patch("iam_orchestrator.subprocess.run", side_effect=results) as run,
        ):
            self.assertEqual(state_dir, repair_codex_daemon_state_acl())

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(["whoami"], commands[0])
        self.assertEqual(["icacls", str(state_dir), "/reset"], commands[1])
        self.assertIn("desktop\\agent:(OI)(CI)(F)", commands[2])
        self.assertEqual(["icacls", str(state_dir), "/inheritance:r"], commands[3])

    def test_managed_daemon_privacy_error_repairs_acl_and_retries(self) -> None:
        failed = subprocess.CompletedProcess(
            ["codex"],
            1,
            "",
            "socket directory is not private to the current user",
        )
        started = subprocess.CompletedProcess(["codex"], 0, "", "")
        with (
            patch("iam_orchestrator.appserver_available", new=AsyncMock(side_effect=[False, True])),
            patch("iam_orchestrator.codex_executable", return_value="codex"),
            patch("iam_orchestrator.subprocess.run", side_effect=[failed, started]) as run,
            patch("iam_orchestrator.repair_codex_daemon_state_acl", return_value=Path("daemon")) as repair,
        ):
            result = ensure_appserver(MANAGED_ENDPOINT, wait_seconds=1)

        self.assertEqual("managed daemon started", result)
        self.assertEqual(2, run.call_count)
        repair.assert_called_once_with()

    def test_open_managed_resume_preserves_unattended_policy_without_remote_override(self) -> None:
        setup_project(self.project, sandbox="danger-full-access", approval_policy="never")
        args = argparse.Namespace(project=str(self.project), url=MANAGED_ENDPOINT, thread_id="chosen-thread")
        with (
            patch("iam_orchestrator.ensure_appserver"),
            patch("iam_orchestrator.ensure_daemon"),
            patch("iam_orchestrator.thread_available", new_callable=AsyncMock, return_value=True),
            patch("iam_orchestrator.apply_thread_policy", new_callable=AsyncMock) as apply_policy,
            patch("iam_orchestrator.codex_executable", return_value="codex"),
            patch("iam_orchestrator.subprocess.run") as run,
            redirect_stdout(io.StringIO()),
        ):
            run.return_value.returncode = 0
            with self.assertRaises(SystemExit):
                cmd_open(args)

        command = run.call_args.args[0]
        self.assertNotIn("--remote", command)
        self.assertEqual("resume", command[1])
        self.assertEqual("danger-full-access", command[command.index("-s") + 1])
        self.assertEqual("never", command[command.index("-a") + 1])
        self.assertIn("chosen-thread", command)
        apply_policy.assert_awaited_once()
        state = DeliveryState.load(iam.mailbox("ExampleProject") / ".codex-bridge-state.json")
        self.assertEqual("chosen-thread", state.thread_id)

    def test_remote_resume_applies_server_policy_without_cli_permission_overrides(self) -> None:
        setup_project(self.project, sandbox="danger-full-access", approval_policy="never")
        args = argparse.Namespace(project=str(self.project), url="ws://127.0.0.1:4500", thread_id="chosen-thread")
        with (
            patch("iam_orchestrator.ensure_appserver"),
            patch("iam_orchestrator.ensure_daemon"),
            patch("iam_orchestrator.thread_available", new_callable=AsyncMock, return_value=True),
            patch("iam_orchestrator.apply_thread_policy", new_callable=AsyncMock) as policy,
            patch("iam_orchestrator.codex_executable", return_value="codex"),
            patch("iam_orchestrator.subprocess.run") as run,
            redirect_stdout(io.StringIO()),
        ):
            run.return_value.returncode = 0
            with self.assertRaises(SystemExit):
                cmd_open(args)
        command = run.call_args.args[0]
        self.assertEqual(args.url, command[command.index("--remote") + 1])
        self.assertEqual("daemon_auto_start", command[command.index("--disable") + 1])
        for flag in ("-s", "-a", "--sandbox", "--ask-for-approval", "--dangerously-bypass-approvals-and-sandbox"):
            self.assertNotIn(flag, command)
        policy.assert_awaited_once()
        self.assertEqual("never", policy.await_args.args[3]["approval_policy"])
        self.assertEqual("danger-full-access", policy.await_args.args[3]["sandbox"])

    def test_remote_policy_uses_settings_update_then_verifies_effective_policy(self) -> None:
        client = AsyncMock()
        client.request.side_effect = [
            {"thread": {"id": "chosen-thread", "status": {"type": "idle"}}},
            {},
            {"thread": {"id": "chosen-thread", "status": {"type": "idle"}},
             "approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}},
        ]
        entry = {"sandbox": "danger-full-access", "approval_policy": "never"}
        with patch("iam_orchestrator.appserver_client", return_value=client):
            result = asyncio.run(apply_thread_policy(
                "ws://127.0.0.1:4500", "chosen-thread", self.project, entry))
        calls = client.request.call_args_list
        self.assertEqual(["thread/resume", "thread/settings/update", "thread/resume"],
                         [call.args[0] for call in calls])
        self.assertNotIn("sandbox", calls[0].args[1])
        self.assertNotIn("approvalPolicy", calls[0].args[1])
        self.assertEqual("dangerFullAccess", calls[1].args[1]["sandboxPolicy"]["type"])
        self.assertEqual("never", calls[1].args[1]["approvalPolicy"])
        self.assertEqual("dangerFullAccess", result["sandbox"]["type"])

    def test_open_does_not_repin_or_launch_when_policy_application_fails(self) -> None:
        setup_project(self.project, sandbox="danger-full-access", approval_policy="never")
        state = DeliveryState.load(iam.mailbox("ExampleProject") / ".codex-bridge-state.json")
        state.thread_id = "old-thread"
        state.save()
        args = argparse.Namespace(project=str(self.project), url=MANAGED_ENDPOINT, thread_id="chosen-thread")
        with (
            patch("iam_orchestrator.ensure_appserver"),
            patch("iam_orchestrator.ensure_daemon"),
            patch("iam_orchestrator.thread_available", new_callable=AsyncMock, return_value=True),
            patch(
                "iam_orchestrator.apply_thread_policy",
                new_callable=AsyncMock,
                side_effect=IAMError("policy rejected"),
            ),
            patch("iam_orchestrator.subprocess.run") as run,
        ):
            with self.assertRaisesRegex(IAMError, "policy rejected"):
                cmd_open(args)

        run.assert_not_called()
        self.assertEqual(
            "old-thread",
            DeliveryState.load(iam.mailbox("ExampleProject") / ".codex-bridge-state.json").thread_id,
        )

    def test_setup_rejects_user_mailbox_and_preserves_web_identity(self) -> None:
        create_user_mailbox("ExampleProject", "Human User")
        configure_web("ExampleProject", "correct horse battery staple")

        with self.assertRaises(SystemExit) as raised:
            setup_project(self.project)

        self.assertEqual("IAM_ADDRESS_COLLISION", raised.exception.error_code)
        profile = iam.profile("ExampleProject")
        self.assertEqual("user", profile["kind"])
        self.assertNotIn("project_root", profile)
        self.assertEqual("ExampleProject", load_web_config()["mailbox"])
        self.assertNotIn("ExampleProject", registered_projects())
        config_text = (self.project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual('model = "gpt-5.6-sol"\n', config_text)
        self.assertNotIn(MCP_BEGIN, config_text)

    def test_setup_rejects_legacy_generic_mailbox_without_migration(self) -> None:
        iam.ensure_box("ExampleProject")

        with self.assertRaises(SystemExit) as raised:
            setup_project(self.project)

        self.assertEqual("IAM_ADDRESS_COLLISION", raised.exception.error_code)
        config_text = (self.project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual('model = "gpt-5.6-sol"\n', config_text)
        self.assertNotIn(MCP_BEGIN, config_text)

    def test_setup_explicitly_claims_only_an_untouched_generic_mailbox(self) -> None:
        iam.ensure_box("ExampleProject")

        result = setup_project(self.project, claim_empty_mailbox=True)

        self.assertTrue(result["legacy_mailbox_reused"])
        self.assertEqual("project", iam.profile("ExampleProject")["kind"])
        self.assertEqual(str(self.project.resolve()), iam.profile("ExampleProject")["project_root"])

    def test_setup_refuses_to_claim_a_generic_mailbox_with_mail(self) -> None:
        iam.ensure_box("ExampleProject")
        iam.write_atomic(
            iam.mailbox("ExampleProject") / "inbox" / "message.json",
            {"id": "message", "body": "preserve me"},
        )

        with self.assertRaises(SystemExit) as raised:
            setup_project(self.project, claim_empty_mailbox=True)

        self.assertEqual("IAM_ADDRESS_COLLISION", raised.exception.error_code)

    def test_setup_rejects_conflicting_unmanaged_mcp_section(self) -> None:
        config_path = self.project / ".codex" / "config.toml"
        config_path.write_text("[mcp_servers.interagentmail]\ncommand = 'custom'\n", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "already defines"):
            setup_project(self.project)

    def test_setup_rejects_incomplete_managed_mcp_markers(self) -> None:
        config_path = self.project / ".codex" / "config.toml"
        config_path.write_text(MCP_BEGIN + "\n", encoding="utf-8")

        with self.assertRaises(SystemExit) as raised:
            setup_project(self.project)

        self.assertEqual("IAM_MCP_CONFIG_INVALID", raised.exception.error_code)
        self.assertEqual(MCP_BEGIN + "\n", config_path.read_text(encoding="utf-8"))

    def test_unregister_preserves_other_config_and_mailbox(self) -> None:
        setup_project(self.project)
        mailbox = iam.mailbox("ExampleProject")

        result = unregister_project(self.project)

        config_text = (self.project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5.6-sol"', config_text)
        self.assertNotIn(MCP_BEGIN, config_text)
        self.assertNotIn("[mcp_servers.interagentmail]", config_text)
        self.assertNotIn("ExampleProject", registered_projects())
        self.assertTrue(mailbox.is_dir())
        self.assertEqual(str(mailbox), result["mailbox"])

    def test_unregister_rejects_broken_managed_markers(self) -> None:
        setup_project(self.project)
        config_path = self.project / ".codex" / "config.toml"
        config_path.write_text(config_path.read_text(encoding="utf-8").replace("# END INTERAGENTMAIL", ""), encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "Cannot safely edit"):
            unregister_project(self.project)


    def test_setup_does_not_require_a_human_facing_identity(self) -> None:
        result = setup_project(self.project)

        self.assertEqual("ExampleProject", result["display_name"])
        self.assertEqual("ExampleProject", iam.display_name("ExampleProject"))

    def test_setup_rejects_same_address_for_a_different_project(self) -> None:
        first = self.root / "one" / "SharedName"
        second = self.root / "two" / "SharedName"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        setup_project(first)

        with self.assertRaisesRegex(SystemExit, "already registered to another project"):
            setup_project(second)

        self.assertEqual(str(first.resolve()), iam.profile("SharedName")["project_root"])
        self.assertFalse((second / ".codex" / "config.toml").exists())

    def test_root_command_preserves_registered_projects(self) -> None:
        setup_project(self.project)
        new_root = self.root / "projects"
        new_root.mkdir()

        iam.main(["root", str(new_root)])

        config = iam.load(iam.CONFIG)
        self.assertEqual(str(new_root.resolve()), config["project_root"])
        self.assertIn("ExampleProject", config["projects"])


class WindowsProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-only process flags")
    def test_diagnostic_version_probe_is_headless(self):
        with patch("iam_orchestrator.subprocess.run", return_value=
                   subprocess.CompletedProcess(["codex", "--version"], 0, "codex-cli test\n", "")) as run:
            self.assertEqual(("codex-cli test", None), command_version("codex"))
        self.assertTrue(run.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(subprocess.SW_HIDE, run.call_args.kwargs["startupinfo"].wShowWindow)

    class _Kernel32:
        def __init__(self, exit_code: int) -> None:
            self.exit_code = exit_code
            self.closed = False

        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
            return 123

        def GetExitCodeProcess(self, _handle: int, result: Any) -> int:
            result._obj.value = self.exit_code
            return 1

        def CloseHandle(self, _handle: int) -> int:
            self.closed = True
            return 1

    def test_windows_exited_process_is_not_alive(self) -> None:
        kernel32 = self._Kernel32(exit_code=0)
        with patch("iam_orchestrator.os.name", "nt"), patch("iam_orchestrator.ctypes.windll", type("Windll", (), {"kernel32": kernel32})(), create=True):
            self.assertFalse(pid_alive(33040))
        self.assertTrue(kernel32.closed)

    def test_windows_still_active_process_is_alive(self) -> None:
        kernel32 = self._Kernel32(exit_code=259)
        with patch("iam_orchestrator.os.name", "nt"), patch("iam_orchestrator.ctypes.windll", type("Windll", (), {"kernel32": kernel32})(), create=True):
            self.assertTrue(pid_alive(33040))
        self.assertTrue(kernel32.closed)


class IntegrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "iam-home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"
        self.project = self.root / "AutomationProject"
        self.project.mkdir()

    def tearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def run_json(self, argv: list[str]) -> tuple[int, dict[str, Any]]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv)
        return code, json.loads(output.getvalue())

    def test_capabilities_uses_versioned_success_envelope(self) -> None:
        code, payload = self.run_json(["capabilities", "--json"])

        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertEqual(INTEGRATION_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual("project-folder-basename", payload["data"]["address_strategy"])
        self.assertFalse(payload["data"]["features"]["human_identity_required"])

    def test_register_status_and_unregister_json_lifecycle(self) -> None:
        code, registered = self.run_json(["register", str(self.project), "--json"])
        self.assertEqual(0, code)
        project = registered["data"]["projects"][0]
        self.assertEqual("registered", project["status"])
        self.assertEqual("AutomationProject", project["address"])

        with (
            patch("iam_orchestrator.appserver_available", new=AsyncMock(return_value=False)),
            patch("iam_orchestrator.read_pid", return_value=None),
        ):
            code, status = self.run_json(["status", "--json"])
        self.assertEqual(0, code)
        self.assertEqual("pending", status["data"]["projects"][0]["thread_state"])

        code, removed = self.run_json(["unregister", str(self.project), "--json"])
        self.assertEqual(0, code)
        self.assertTrue(removed["data"]["projects"][0]["mailbox_preserved"])
        self.assertTrue(iam.mailbox("AutomationProject").is_dir())

    def test_register_collision_returns_stable_error_envelope(self) -> None:
        first = self.root / "one" / "SharedName"
        second = self.root / "two" / "SharedName"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        setup_project(first)

        code, payload = self.run_json(["register", str(second), "--json"])

        self.assertEqual(1, code)
        self.assertFalse(payload["ok"])
        self.assertEqual("IAM_ADDRESS_COLLISION", payload["error"]["code"])
        self.assertTrue(payload["error"]["recoverable"])

    def test_register_invalid_mailbox_profile_returns_stable_error(self) -> None:
        iam.ensure_box("AutomationProject")
        profile_path = iam.mailbox("AutomationProject") / "profile.json"
        profile_path.write_text("not-json\n", encoding="utf-8")

        code, payload = self.run_json(["register", str(self.project), "--json"])

        self.assertEqual(1, code)
        self.assertEqual("IAM_CONFIG_INVALID", payload["error"]["code"])
        self.assertEqual("not-json\n", profile_path.read_text(encoding="utf-8"))
        self.assertFalse((self.project / ".codex" / "config.toml").exists())

    def test_web_setup_creates_authenticated_user_mailbox_without_plaintext_password(self) -> None:
        with patch("iam_orchestrator.sys.stdin", io.StringIO("correct horse battery staple\n")):
            code, payload = self.run_json([
                "web", "setup", "Human", "--display-name", "Adrian", "--password-stdin", "--json"
            ])

        self.assertEqual(0, code)
        self.assertEqual("user", payload["data"]["user_mailbox"]["kind"])
        self.assertEqual("Human", payload["data"]["web"]["mailbox"])
        web_config = (iam.ROOT / "web.json").read_text(encoding="utf-8")
        self.assertNotIn("correct horse battery staple", web_config)
        self.assertIn("pbkdf2-sha256", web_config)

    def test_web_lan_setup_requires_explicit_network_risk_acknowledgement(self) -> None:
        code, payload = self.run_json([
            "web", "setup", "Human", "--lan", "--password-stdin", "--json"
        ])

        self.assertEqual(1, code)
        self.assertEqual("IAM_WEB_LAN_ACK_REQUIRED", payload["error"]["code"])
        self.assertFalse(iam.mailbox("Human").exists())

    def test_web_status_reports_invalid_configuration_without_hiding_it(self) -> None:
        iam.ROOT.mkdir(parents=True)
        (iam.ROOT / "web.json").write_text("not-json\n", encoding="utf-8")

        with (
            patch("iam_orchestrator.read_pid", return_value=None),
            patch("iam_orchestrator.pid_alive", return_value=False),
        ):
            code, payload = self.run_json(["web", "status", "--json"])

        self.assertEqual(0, code)
        self.assertTrue(payload["data"]["web"]["configured"])
        self.assertFalse(payload["data"]["web"]["running"])
        self.assertEqual("IAM_WEB_CONFIG_INVALID", payload["data"]["web"]["error"]["code"])

    def test_web_status_flags_live_instance_identity_mismatch(self) -> None:
        with patch("iam_orchestrator.sys.stdin", io.StringIO("correct horse battery staple\n")):
            setup_code, _payload = self.run_json([
                "web", "setup", "Human", "--password-stdin", "--json"
            ])
        self.assertEqual(0, setup_code)
        run_dir = iam.ROOT / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "web.instance").write_text("expected-instance\n", encoding="utf-8")
        with (
            patch("iam_orchestrator.read_pid", return_value=4321),
            patch("iam_orchestrator.pid_alive", return_value=True),
            patch("iam_orchestrator.web_available", return_value=False),
        ):
            code, payload = self.run_json(["web", "status", "--json"])

        self.assertEqual(0, code)
        self.assertFalse(payload["data"]["web"]["running"])
        self.assertEqual(
            "IAM_WEB_RUNTIME_IDENTITY_MISMATCH",
            payload["data"]["web"]["error"]["code"],
        )

    def test_web_start_spawns_only_the_companion_process(self) -> None:
        with patch("iam_orchestrator.sys.stdin", io.StringIO("correct horse battery staple\n")):
            setup_code, _payload = self.run_json([
                "web", "setup", "Human", "--password-stdin", "--json"
            ])
        self.assertEqual(0, setup_code)
        with (
            patch("iam_orchestrator.read_pid", return_value=None),
            patch("iam_orchestrator.pid_alive", return_value=False),
            patch("iam_orchestrator.spawn_background", return_value=4321) as spawn,
            patch("iam_orchestrator.web_available", return_value=True),
            patch("iam_orchestrator.ensure_appserver") as appserver,
            patch("iam_orchestrator.ensure_daemon") as supervisor,
        ):
            code, payload = self.run_json(["web", "start", "--json"])

        self.assertEqual(0, code)
        self.assertEqual("started", payload["data"]["status"])
        command = spawn.call_args.args[0]
        self.assertIn("iam_web", command)
        self.assertNotIn("app-server", command)
        self.assertIn("--instance-token", command)
        instance = command[command.index("--instance-token") + 1]
        self.assertEqual(instance, (iam.ROOT / "run" / "web.instance").read_text(encoding="utf-8").strip())
        appserver.assert_not_called()
        supervisor.assert_not_called()

    def test_doctor_json_can_target_one_project(self) -> None:
        setup_project(self.project)
        other = self.root / "OtherProject"
        other.mkdir()
        setup_project(other)
        with (
            patch("iam_orchestrator.shutil.which", return_value="codex"),
            patch("iam_orchestrator.command_version", return_value=("codex-cli 0.145.0", None)),
            patch("iam_orchestrator.appserver_available", new=AsyncMock(return_value=False)),
            patch("iam_orchestrator.pid_alive", return_value=False),
        ):
            code, payload = self.run_json(["doctor", "--project", str(self.project), "--json"])

        self.assertEqual(0, code)
        diagnostics = payload["data"]["diagnostics"]
        self.assertEqual(str(self.project.resolve()), diagnostics["target_project"])
        self.assertEqual(["AutomationProject"], [item["address"] for item in diagnostics["projects"]])


class DiagnosticCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "iam-home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"
        self.project = self.root / "PrivateProject"
        self.project.mkdir()
        setup_project(self.project, display_name="Private Agent")

    def tearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def diagnostics(self) -> dict[str, Any]:
        with (
            patch("iam_orchestrator.shutil.which", return_value="codex"),
            patch("iam_orchestrator.command_version", return_value=("codex-cli 0.145.0", None)),
            patch("iam_orchestrator.appserver_available", new=AsyncMock(return_value=False)),
            patch("iam_orchestrator.pid_alive", return_value=False),
        ):
            return collect_diagnostics()

    def test_doctor_checks_registered_project_without_starting_services(self) -> None:
        diagnostics = self.diagnostics()

        failures = [check for check in diagnostics["checks"] if check["status"] == CHECK_FAIL]
        self.assertEqual([], failures)
        self.assertEqual(1, len(diagnostics["projects"]))
        self.assertTrue(diagnostics["projects"][0]["mcp"])
        self.assertFalse(diagnostics["services"]["appserver_reachable"])
        self.assertFalse((iam.ROOT / "run").exists())

    def test_doctor_reports_broken_managed_mcp_configuration(self) -> None:
        (self.project / ".codex" / "config.toml").write_text("# missing IAM block\n", encoding="utf-8")

        diagnostics = self.diagnostics()

        failures = [check for check in diagnostics["checks"] if check["status"] == CHECK_FAIL]
        self.assertTrue(any(check["name"] == "MCP PrivateProject" for check in failures))

    def test_doctor_reports_out_of_order_mcp_markers_without_crashing(self) -> None:
        (self.project / ".codex" / "config.toml").write_text(
            "# END INTERAGENTMAIL\n# BEGIN INTERAGENTMAIL (managed by `iam setup`)\n",
            encoding="utf-8",
        )

        diagnostics = self.diagnostics()

        failures = [check for check in diagnostics["checks"] if check["status"] == CHECK_FAIL]
        self.assertTrue(any("out of order" in check["detail"] for check in failures))

    def test_doctor_reports_malformed_project_entry_without_crashing(self) -> None:
        iam.write_atomic(iam.CONFIG, {"projects": {"BrokenProject": "not-an-object"}})

        diagnostics = self.diagnostics()

        failures = [check for check in diagnostics["checks"] if check["status"] == CHECK_FAIL]
        self.assertTrue(any(check["name"] == "Project BrokenProject" for check in failures))
        self.assertEqual("unavailable", diagnostics["projects"][0]["project_root"])

    def test_report_redacts_identifiers_paths_email_and_tokens(self) -> None:
        diagnostics = self.diagnostics()
        diagnostics["checks"].append({
            "status": "WARN",
            "name": "Synthetic",
            "detail": (
                f"{self.project} PrivateProject Private Agent private@example.com "
                "019faf07-ce7e-7e11-8271-3ab14d95b555 token=supersecret"
            ),
        })

        report = render_report(diagnostics)

        self.assertNotIn(str(self.project), report)
        self.assertNotIn("PrivateProject", report)
        self.assertNotIn("Private Agent", report)
        self.assertNotIn("private@example.com", report)
        self.assertNotIn("019faf07-ce7e-7e11-8271-3ab14d95b555", report)
        self.assertNotIn("supersecret", report)
        self.assertIn("<PROJECT_1>", report)
        self.assertIn("<EMAIL>", report)
        self.assertIn("<ID>", report)

    def test_report_summarizes_logs_and_never_copies_raw_content(self) -> None:
        run_dir = iam.ROOT / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "app-server.log").write_text(
            "ERROR private source text password=hunter2\nWARNING do-not-share-this-instruction\n",
            encoding="utf-8",
        )
        message_path = iam.mailbox("PrivateProject") / "inbox" / "1780000000-1234abcd.json"
        message_path.write_text('{"body": "unpublishable mailbox content"}\n', encoding="utf-8")

        report = render_report(self.diagnostics())

        self.assertIn("errors 1; warnings 1", report)
        self.assertNotIn("private source text", report)
        self.assertNotIn("do-not-share-this-instruction", report)
        self.assertNotIn("hunter2", report)
        self.assertNotIn("unpublishable mailbox content", report)
        self.assertIn("inbox 1", report)

    def test_report_command_writes_requested_file(self) -> None:
        destination = self.root / "support" / "report.md"
        args = argparse.Namespace(url="ws://127.0.0.1:4500", output=str(destination), stdout=False, force=False, log_lines=50)
        with (
            patch("iam_orchestrator.shutil.which", return_value="codex"),
            patch("iam_orchestrator.command_version", return_value=("codex-cli 0.145.0", None)),
            patch("iam_orchestrator.appserver_available", new=AsyncMock(return_value=False)),
            patch("iam_orchestrator.pid_alive", return_value=False),
        ):
            result = cmd_report(args)

        self.assertEqual(0, result)
        self.assertTrue(destination.is_file())
        self.assertIn("# InterAgentMail diagnostic report", destination.read_text(encoding="utf-8"))

    def test_report_command_refuses_to_overwrite_without_force(self) -> None:
        destination = self.root / "existing-report.md"
        destination.write_text("keep me\n", encoding="utf-8")
        args = argparse.Namespace(url="ws://127.0.0.1:4500", output=str(destination), stdout=False, force=False, log_lines=50)
        with (
            patch("iam_orchestrator.shutil.which", return_value="codex"),
            patch("iam_orchestrator.command_version", return_value=("codex-cli 0.145.0", None)),
            patch("iam_orchestrator.appserver_available", new=AsyncMock(return_value=False)),
            patch("iam_orchestrator.pid_alive", return_value=False),
        ):
            with self.assertRaisesRegex(SystemExit, "Refusing to overwrite"):
                cmd_report(args)

        self.assertEqual("keep me\n", destination.read_text(encoding="utf-8"))

    def test_sanitizer_handles_generic_user_paths(self) -> None:
        text = r"C:\Users\alice\project /home/bob/project /Users/carol/project"
        sanitized = sanitize_report(text, [])
        self.assertNotIn("alice", sanitized)
        self.assertNotIn("bob", sanitized)
        self.assertNotIn("carol", sanitized)


class FakeSupervisorClient:
    instances: list["FakeSupervisorClient"] = []
    failing_cwds: set[str] = set()

    def __init__(self, url: str, **_kwargs: Any) -> None:
        self.url = url
        self.closed = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.created = False
        self.approval_policy = "on-request"
        self.sandbox_policy: dict[str, Any] = {"type": "workspaceWrite"}
        self.instances.append(self)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self.closed.set()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        cwd = str(params.get("cwd", ""))
        if cwd in self.failing_cwds:
            raise AppServerError("simulated mailbox attachment failure")
        if method == "thread/list":
            if self.created:
                return {"data": [{"id": "supervised-thread", "status": {"type": "idle"}}]}
            return {"data": []}
        if method == "thread/start":
            self.created = True
            return {"thread": {"id": "supervised-thread", "status": {"type": "idle"}}}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}
        if method == "thread/settings/update":
            self.approval_policy = params["approvalPolicy"]
            self.sandbox_policy = params["sandboxPolicy"]
            return {}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}},
                    "approvalPolicy": self.approval_policy, "sandbox": self.sandbox_policy}
        if method == "turn/start":
            return {"turn": {"id": "delivery-turn", "status": "inProgress"}}
        raise AssertionError(f"Unexpected method {method}")


class SupervisorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_mail_does_not_block_other_mailboxes(self):
        other = self.root / "OtherProject"
        other.mkdir()
        setup_project(other)
        self.baseline(self.project)
        self.baseline(other)
        broken = iam.mailbox("SupervisedProject") / "inbox" / "broken.json"
        broken.write_text("{unfinished", encoding="utf-8")
        message = IAMService(self.project).send(["OtherProject"], "Valid", "Deliver normally")
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient), self.assertLogs("interagentmail", level="WARNING"):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        saved = DeliveryState.load(iam.mailbox("OtherProject") / ".codex-bridge-state.json")
        self.assertTrue(saved.is_delivered(message["id"]))
        self.assertEqual("{unfinished", broken.read_text(encoding="utf-8"))
        bad_state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertFalse(bad_state.is_delivered("broken"))

    async def test_guard_rechecks_binding_after_async_thread_read(self):
        path = iam.mailbox("SupervisedProject") / ".codex-bridge-state.json"
        state = DeliveryState.load(path)
        state.thread_id = "expected"
        state.save()
        iam.write_atomic(iam.mailbox("SupervisedProject") / ".delivery-requirement.json",
                         {"session_owner": "SupervisedProject", "thread_id": "expected"})
        message = IAMService(self.project).send(["SupervisedProject"], "Wake", "Fixed notice")
        class Changing(FakeSupervisorClient):
            async def request(self, method, params):
                result = await super().request(method, params)
                if method == "thread/read" and sum(m == method for m, _ in self.calls) == 2:
                    saved = DeliveryState.load(path)
                    saved.thread_id = None
                    saved.save()
                return result
        with patch("iam_orchestrator.AppServerClient", Changing):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        self.assertFalse(any(method in {"thread/start", "turn/start", "turn/steer"}
                             for client in FakeSupervisorClient.instances for method, _ in client.calls))
        self.assertFalse(DeliveryState.load(path).is_delivered(message["id"]))

    async def test_guarded_missing_or_changed_pin_never_activates_or_consumes(self):
        path = iam.mailbox("SupervisedProject") / ".codex-bridge-state.json"
        iam.write_atomic(iam.mailbox("SupervisedProject") / ".delivery-requirement.json",
                         {"session_owner": "SupervisedProject", "thread_id": "expected"})
        message = IAMService(self.project).send(["SupervisedProject"], "Wake", "Fixed notice")
        for pin in (None, "different"):
            state = DeliveryState.load(path)
            state.thread_id = pin
            state.save()
            with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
                await serve_once("ws://127.0.0.1:4500", run_once=True)
            self.assertEqual([], FakeSupervisorClient.instances)
            saved = DeliveryState.load(path)
            self.assertEqual(pin, saved.thread_id)
            self.assertFalse(saved.is_delivered(message["id"]))

    async def test_guarded_self_mail_uses_only_expected_thread_without_bootstrap(self):
        path = iam.mailbox("SupervisedProject") / ".codex-bridge-state.json"
        state = DeliveryState.load(path)
        state.thread_id = "expected"
        state.save()
        iam.write_atomic(iam.mailbox("SupervisedProject") / ".delivery-requirement.json",
                         {"session_owner": "SupervisedProject", "thread_id": "expected"})
        message = IAMService(self.project).send(["SupervisedProject"], "Wake", "Fixed notice")
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        calls = FakeSupervisorClient.instances[0].calls
        self.assertFalse(any(method == "thread/start" for method, _ in calls))
        turns = [params for method, params in calls if method == "turn/start"]
        self.assertEqual(1, len(turns))
        self.assertEqual("expected", turns[0]["threadId"])
        self.assertIn(message["id"], turns[0]["input"][0]["text"])
        self.assertTrue(DeliveryState.load(path).is_delivered(message["id"]))

    async def test_guarded_unavailable_session_never_falls_back(self):
        path = iam.mailbox("SupervisedProject") / ".codex-bridge-state.json"
        state = DeliveryState.load(path)
        state.thread_id = "expected"
        state.save()
        iam.write_atomic(iam.mailbox("SupervisedProject") / ".delivery-requirement.json",
                         {"session_owner": "SupervisedProject", "thread_id": "expected"})
        message = IAMService(self.project).send(["SupervisedProject"], "Wake", "Fixed notice")
        class Unavailable(FakeSupervisorClient):
            async def request(self, method, params):
                self.calls.append((method, params))
                raise AppServerError("Unavailable")
        with patch("iam_orchestrator.AppServerClient", Unavailable):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        self.assertFalse(any(method in {"thread/start", "turn/start", "turn/steer"}
                             for client in FakeSupervisorClient.instances for method, _ in client.calls))
        self.assertFalse(DeliveryState.load(path).is_delivered(message["id"]))

    async def test_explicit_repin_reloads_before_more_delivery(self) -> None:
        self.baseline(self.project)
        sender = IAMService(self.root / "Sender")
        sender.send(["SupervisedProject"], "First", "Start delivery")
        state_path = iam.mailbox("SupervisedProject") / ".codex-bridge-state.json"

        async def repin(_seconds):
            state = DeliveryState.load(state_path)
            state.thread_id = "replacement-thread"
            state.save()
            sender.send(["SupervisedProject"], "Second", "Must not reach old thread")

        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient), patch("iam_orchestrator.asyncio.sleep", repin):
            await serve_once("ws://127.0.0.1:4500")
        turns = [params for method, params in FakeSupervisorClient.instances[0].calls if method == "turn/start"]
        self.assertEqual(1, len(turns))
        self.assertEqual("replacement-thread", DeliveryState.load(state_path).thread_id)

    async def test_shared_mailboxes_use_one_connection_and_preserve_secondary_binding(self) -> None:
        secondary = self.root / "Secondary"
        secondary.mkdir()
        setup_project(secondary, sandbox="danger-full-access", approval_policy="never")
        old_state = DeliveryState.load(iam.mailbox("Secondary") / ".codex-bridge-state.json")
        old_state.thread_id = "old-secondary-thread"
        old_state.save()
        main(["session", "link", str(secondary), "--primary", str(self.project)])
        self.baseline(self.project)
        self.baseline(secondary)
        sender = IAMService(self.root / "Sender")
        first = sender.send(["SupervisedProject", "Secondary"], "Shared", "Two separate copies")
        passes = 0

        async def stop_after_two_passes(_seconds):
            nonlocal passes
            passes += 1
            if passes == 2:
                raise asyncio.CancelledError()

        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient), patch("iam_orchestrator.asyncio.sleep", stop_after_two_passes):
            with self.assertRaises(asyncio.CancelledError):
                await serve_once("ws://127.0.0.1:4500")
        self.assertEqual(1, len(FakeSupervisorClient.instances))
        calls = FakeSupervisorClient.instances[0].calls
        turns = [params for method, params in calls if method == "turn/start"]
        self.assertEqual(2, len(turns))
        self.assertEqual({"supervised-thread"}, {turn["threadId"] for turn in turns})
        self.assertNotEqual(turns[0]["clientUserMessageId"], turns[1]["clientUserMessageId"])
        secondary_turn = next(turn for turn in turns if "mailbox='Secondary'" in turn["input"][0]["text"])
        self.assertEqual("on-request", secondary_turn["approvalPolicy"])
        self.assertEqual([str(self.project.resolve())], secondary_turn["sandboxPolicy"]["writableRoots"])
        saved = DeliveryState.load(old_state.path)
        self.assertEqual("old-secondary-thread", saved.thread_id)
        self.assertTrue(saved.is_delivered(first["id"]))

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"
        self.project = self.root / "SupervisedProject"
        self.project.mkdir()
        setup_project(self.project)
        FakeSupervisorClient.instances.clear()
        FakeSupervisorClient.failing_cwds.clear()

    async def asyncTearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def baseline(self, project: Path) -> None:
        service = IAMService(project)
        state = DeliveryState.load(iam.mailbox(service.address) / ".codex-bridge-state.json")
        state.initialize(service.inbox(), process_existing=False)

    async def test_supervisor_is_lazy_until_mail_arrives(self) -> None:
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500", run_once=True)

        self.assertEqual([], FakeSupervisorClient.instances)
        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertIsNone(state.thread_id)

    async def test_supervisor_delivers_heartbeat_without_mail_or_thread_creation(self) -> None:
        from iam_heartbeat import Heartbeats
        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        state.thread_id = "pinned-heartbeat"
        state.save()
        store = Heartbeats()
        store.configure("SupervisedProject", "enable", state.thread_id)
        with patch("iam_heartbeat.time.time", return_value=0):
            store.schedule("SupervisedProject", "1970-01-01T00:15:00Z", "Review work")
        client = AsyncMock()
        async def request(method, params):
            if method == "thread/read":
                return {"thread": {"id": state.thread_id, "cwd": str(self.project.resolve()), "status": {"type": "idle"}}}
            if method == "turn/start":
                return {"turn": {"id": "heartbeat-turn"}}
            self.fail("Unexpected method: " + method)
        client.request.side_effect = request
        with patch("iam_orchestrator.appserver_client", return_value=client):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        self.assertEqual("accepted", store.state("SupervisedProject")["outcome"])
        self.assertEqual(1, sum(c.args[0] == "turn/start" for c in client.request.call_args_list))

    async def test_start_only_starts_shared_services_without_loading_mailbox_threads(self) -> None:
        args = argparse.Namespace(url="ws://127.0.0.1:4500")
        output = io.StringIO()
        with (
            patch("iam_orchestrator.AppServerClient", FakeSupervisorClient),
            patch("iam_orchestrator.ensure_appserver", return_value="started") as appserver,
            patch("iam_orchestrator.ensure_daemon", return_value="started") as supervisor,
            redirect_stdout(output),
        ):
            cmd_start(args)

        appserver.assert_called_once_with(args.url)
        supervisor.assert_called_once_with(args.url)
        self.assertEqual([], FakeSupervisorClient.instances)
        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertIsNone(state.thread_id)
        self.assertNotIn("Codex mailboxes", output.getvalue())

    async def test_heartbeat_uncertainty_does_not_block_ordinary_mail(self):
        from iam_heartbeat import Heartbeats
        self.baseline(self.project)
        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        state.thread_id = "supervised-thread"
        state.save()
        store = Heartbeats()
        store.configure("SupervisedProject", "enable", state.thread_id)
        with patch("iam_heartbeat.time.time", return_value=0):
            store.schedule("SupervisedProject", "1970-01-01T00:15:00Z", "Private purpose")
        batch = store.claim("SupervisedProject")
        store.hold("SupervisedProject", batch["id"])
        sender_root = self.root / "Sender"
        sender_root.mkdir()
        mail = IAMService(sender_root).send(["SupervisedProject"], "Ordinary", "Deliver despite heartbeat hold")
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        saved = DeliveryState.load(state.path)
        self.assertTrue(saved.is_delivered(mail["id"]))
        self.assertTrue(store.state("SupervisedProject")["heartbeat_held"])
        self.assertEqual(batch["id"], store.state("SupervisedProject")["delivery"]["id"])

    async def test_external_attention_wait_does_not_block_ordinary_mail(self):
        self.baseline(self.project)
        sender_root = self.root / "Sender"
        sender_root.mkdir()
        mail = IAMService(sender_root).send(["SupervisedProject"], "Ordinary", "Independent of external attention")
        async def held_lane(*_args):
            await asyncio.Event().wait()
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient), patch("iam_fast_path.supervised_lane", held_lane):
            await serve_once("ws://127.0.0.1:4500", run_once=True)
        saved = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertTrue(saved.is_delivered(mail["id"]))

    async def test_supervisor_creates_and_pins_thread_only_for_new_mail(self) -> None:
        self.baseline(self.project)
        sender_root = self.root / "Sender"
        sender_root.mkdir()
        sender = IAMService(sender_root)
        message = sender.send(["SupervisedProject"], "Review", "Please review this project.")

        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500", run_once=True)

        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertEqual("supervised-thread", state.thread_id)
        self.assertTrue(state.is_delivered(message["id"]))
        self.assertEqual(1, len(FakeSupervisorClient.instances))
        start = next(
            params
            for method, params in FakeSupervisorClient.instances[0].calls
            if method == "thread/start"
        )
        self.assertEqual(str(self.project.resolve()), start["cwd"])
        self.assertEqual("workspace-write", start["sandbox"])
        self.assertEqual("on-request", start["approvalPolicy"])
        delivery = next(
            params
            for method, params in FakeSupervisorClient.instances[0].calls
            if method == "turn/start"
        )
        self.assertIn(message["id"], delivery["input"][0]["text"])

    async def test_failed_mailbox_does_not_block_other_mailboxes(self) -> None:
        broken_root = self.root / "BrokenProject"
        sender_root = self.root / "Sender"
        broken_root.mkdir()
        sender_root.mkdir()
        setup_project(broken_root)
        self.baseline(self.project)
        self.baseline(broken_root)
        sender = IAMService(sender_root)
        delivered = sender.send(["SupervisedProject"], "Review", "Deliver this.")
        sender.send(["BrokenProject"], "Review", "This attachment will fail.")
        FakeSupervisorClient.failing_cwds.add(str(broken_root.resolve()))

        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500", run_once=True)

        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertTrue(state.is_delivered(delivered["id"]))
        self.assertGreaterEqual(len(FakeSupervisorClient.instances), 2)


if __name__ == "__main__":
    unittest.main()
