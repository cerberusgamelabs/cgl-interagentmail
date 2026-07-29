from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import interagentmail as iam
from iam_mcp_server import mcp
from mcp import Client


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "Alpha"
        self.project.mkdir()
        self.old_paths = (iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        self.old_project_root = os.environ.get("IAM_PROJECT_ROOT")
        iam.MAILBOXES = self.root / "mailboxes"
        iam.CHATS = self.root / "chats"
        iam.CONFIG = self.root / "config.json"
        os.environ["IAM_PROJECT_ROOT"] = str(self.project)

    async def asyncTearDown(self) -> None:
        iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
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
                    "iam_list_mailboxes",
                    "iam_inbox",
                    "iam_read",
                    "iam_send",
                    "iam_reply",
                    "iam_archive",
                    "iam_list_channels",
                    "iam_chat_tail",
                    "iam_chat_post",
                    "iam_chat_seen",
                },
                names,
            )
            result = await client.call_tool("iam_whoami", {})
            self.assertEqual("Alpha", result.structured_content["address"])
            self.assertEqual(str(self.project.resolve()), result.structured_content["project_root"])


if __name__ == "__main__":
    unittest.main()
