from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import interagentmail as iam
from iam_mcp_server import mcp
from mcp import Client
from iam_orchestrator import setup_project, main as orchestrator_main


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_mailbox_selection_is_authorized_and_defaults_stay_bound(self) -> None:
        setup_project(self.project)
        secondary = self.root / "Secondary"
        secondary.mkdir()
        setup_project(secondary)
        outsider = self.root / "Other"
        outsider.mkdir()
        setup_project(outsider)
        orchestrator_main(["session", "link", str(secondary), "--primary", str(self.project)])
        async with Client(mcp) as client:
            identity = await client.call_tool("iam_whoami", {})
            self.assertEqual(["Alpha", "Secondary"], identity.structured_content["authorized_mailboxes"])
            sent = await client.call_tool("iam_send", {"mailbox": "Secondary", "to": ["Other"], "subject": "Test", "body": "Secondary identity"})
            self.assertEqual("Secondary", sent.structured_content["from"])
            default = await client.call_tool("iam_whoami", {})
            self.assertEqual("Alpha", default.structured_content["address"])
            denied = await client.call_tool("iam_inbox", {"mailbox": "Other"})
            self.assertTrue(denied.is_error)
            denied = await client.call_tool("iam_friend_policy", {"mailbox": "Other", "opted_in": True, "revision": 0})
            self.assertTrue(denied.is_error)

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "Alpha"
        self.project.mkdir()
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        self.old_project_root = os.environ.get("IAM_PROJECT_ROOT")
        iam.MAILBOXES = self.root / "mailboxes"
        iam.ROOT = self.root
        iam.CHATS = self.root / "chats"
        iam.CONFIG = self.root / "config.json"
        os.environ["IAM_PROJECT_ROOT"] = str(self.project)

    async def asyncTearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        if self.old_project_root is None:
            os.environ.pop("IAM_PROJECT_ROOT", None)
        else:
            os.environ["IAM_PROJECT_ROOT"] = self.old_project_root
        self.temp.cleanup()

    async def test_in_memory_handshake_tools_and_identity(self) -> None:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertEqual(
                {
                    "iam_whoami",
                    "iam_email",
                    "iam_desktop_reply",
                    "iam_desktop_notify",
                    "iam_desktop_profile",
                    "iam_friend_policy",
                    "iam_friend",
                    "schedule_next_wake",
                    "cancel_next_wake",
                    "get_heartbeat_state",
                    "iam_list_mailboxes",
                    "iam_inbox",
                    "iam_read",
                    "iam_send",
                    "iam_reply",
                    "iam_archive",
                    "iam_timer_set",
                    "iam_timer_set_team",
                    "iam_timer_set_for",
                    "iam_timer_list",
                    "iam_timer_clear",
                    "iam_timer_cancel",
                    "iam_timer_snooze",
                    "iam_team_add_member",
                    "iam_team_remove_member",
                    "iam_list_channels",
                    "iam_chat_tail",
                    "iam_chat_join",
                    "iam_chat_leave",
                    "iam_chat_subscriptions",
                    "iam_chat_post",
                    "iam_chat_seen",
                },
                names,
            )
            result = await client.call_tool("iam_whoami", {})
            self.assertEqual("Alpha", result.structured_content["address"])
            self.assertEqual(str(self.project.resolve()), result.structured_content["project_root"])

    async def test_named_heartbeat_tools_are_bound_and_backward_compatible(self):
        from iam_heartbeat import Heartbeats
        setup_project(self.project)
        Heartbeats().configure("Alpha", "enable", "existing")
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools
            schedule = next(t for t in tools if t.name == "schedule_next_wake")
            self.assertEqual("default", schedule.input_schema["properties"]["name"]["default"])
            for name in (None, "drawing"):
                args = {"timestamp": "2099-01-01T00:00:00Z", "reason": "Explore"}
                if name:
                    args["name"] = name
                result = await client.call_tool("schedule_next_wake", args)
                self.assertFalse(result.is_error)
            state = await client.call_tool("get_heartbeat_state", {})
            self.assertEqual({"default", "drawing"}, set(state.structured_content["schedules"]))
            await client.call_tool("cancel_next_wake", {"reason": "not now"})
            self.assertEqual({"drawing"}, set(Heartbeats().state("Alpha")["schedules"]))
            self.assertNotIn("heartbeat_resolve", {t.name for t in tools})

    async def test_email_tool_uses_bound_identity_and_rejects_foreign_mailbox(self):
        from unittest.mock import patch
        setup_project(self.project)
        with patch('iam_webconnect.email_registration', return_value={'primary': None}) as call:
            async with Client(mcp) as client:
                result = await client.call_tool('iam_email', {'action': 'status'})
                self.assertFalse(result.is_error)
                self.assertEqual('Alpha', call.call_args.args[0].address)
                call.reset_mock()
                result = await client.call_tool('iam_email', {'mailbox': 'Other'})
                self.assertTrue(result.is_error)
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
