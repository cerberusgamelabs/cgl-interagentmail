from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import interagentmail as iam
from iam_codex_bridge import DeliveryState
from iam_orchestrator import MCP_BEGIN, registered_projects, serve_once, setup_project, unregister_project


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
        self.assertEqual(first, second)
        self.assertEqual(str(self.project), registered_projects()["ExampleProject"]["project_root"])
        self.assertEqual("Example Agent", iam.display_name("ExampleProject"))

    def test_setup_rejects_conflicting_unmanaged_mcp_section(self) -> None:
        config_path = self.project / ".codex" / "config.toml"
        config_path.write_text("[mcp_servers.interagentmail]\ncommand = 'custom'\n", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "already defines"):
            setup_project(self.project)

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
        self.assertEqual(str(self.project), start["cwd"])
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
