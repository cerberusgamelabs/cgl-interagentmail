from __future__ import annotations

import asyncio
import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import interagentmail as iam
from iam_codex_bridge import DeliveryState
from iam_orchestrator import (
    CHECK_FAIL,
    INTEGRATION_SCHEMA_VERSION,
    MCP_BEGIN,
    cmd_report,
    collect_diagnostics,
    main,
    registered_projects,
    render_report,
    sanitize_report,
    serve_once,
    setup_project,
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
        self.assertEqual(str(self.project.resolve()), registered_projects()["ExampleProject"]["project_root"])
        self.assertEqual("Example Agent", iam.display_name("ExampleProject"))

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

    def __init__(self, url: str, **_kwargs: Any) -> None:
        self.url = url
        self.closed = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.created = False
        self.instances.append(self)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self.closed.set()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        if method == "thread/list":
            return {"data": []}
        if method == "thread/start":
            self.created = True
            return {"thread": {"id": "supervised-thread", "status": {"type": "idle"}}}
        if method == "turn/start":
            self.closed.set()
            return {"turn": {"id": "bootstrap-turn", "status": "inProgress"}}
        raise AssertionError(f"Unexpected method {method}")


class SupervisorIntegrationTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    async def test_supervisor_creates_and_pins_safe_project_thread(self) -> None:
        with patch("iam_orchestrator.AppServerClient", FakeSupervisorClient):
            await serve_once("ws://127.0.0.1:4500")

        state = DeliveryState.load(iam.mailbox("SupervisedProject") / ".codex-bridge-state.json")
        self.assertEqual("supervised-thread", state.thread_id)
        start = next(
            params
            for method, params in FakeSupervisorClient.instances[0].calls
            if method == "thread/start"
        )
        self.assertEqual(str(self.project.resolve()), start["cwd"])
        self.assertEqual("workspace-write", start["sandbox"])
        self.assertEqual("on-request", start["approvalPolicy"])
        bootstrap = next(
            params
            for method, params in FakeSupervisorClient.instances[0].calls
            if method == "turn/start"
        )
        self.assertEqual("supervised-thread", bootstrap["threadId"])
        self.assertIn("InterAgentMail ready", bootstrap["input"][0]["text"])


if __name__ == "__main__":
    unittest.main()
