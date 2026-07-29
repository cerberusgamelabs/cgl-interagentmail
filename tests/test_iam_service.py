from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import interagentmail as iam
from iam_service import IAMError, IAMService


class IAMServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.MAILBOXES = self.root / "mailboxes"
        iam.CHATS = self.root / "chats"
        iam.CONFIG = self.root / "config.json"
        self.alpha_root = self.root / "Alpha"
        self.beta_root = self.root / "Beta"
        self.alpha_root.mkdir()
        self.beta_root.mkdir()
        self.alpha = IAMService(self.alpha_root)
        self.beta = IAMService(self.beta_root)

    def tearDown(self) -> None:
        iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def test_message_lifecycle_is_structured(self) -> None:
        sent = self.alpha.send(["Beta"], "Review", "Please review this.")
        self.assertEqual([sent["id"]], [message["id"] for message in self.beta.inbox()])

        read = self.beta.read(sent["id"][:12])
        self.assertIsNotNone(read["read_at"])

        reply = self.beta.reply(sent["id"], "Done.")
        self.assertEqual(sent["thread"], reply["thread"])
        self.assertEqual("Alpha", reply["to"][0])

        archived = self.beta.archive(sent["id"])
        self.assertEqual(sent["id"], archived["id"])
        self.assertEqual([], self.beta.inbox())

    def test_identity_is_bound_and_path_traversal_is_rejected(self) -> None:
        self.assertEqual("Alpha", self.alpha.whoami()["address"])
        with self.assertRaises(IAMError):
            self.alpha.send(["../outside"], "No", "No")
        with self.assertRaises(IAMError):
            self.alpha.read("*")

    def test_private_chat_access_is_enforced(self) -> None:
        entry = self.alpha.chat_post("alpha-beta", "Hello", "private", "Beta")
        self.assertEqual("Alpha", entry["address"])
        self.assertEqual("Hello", self.beta.chat_tail("alpha-beta")["messages"][0]["message"])

        gamma_root = self.root / "Gamma"
        gamma_root.mkdir()
        gamma = IAMService(gamma_root)
        with self.assertRaises(IAMError):
            gamma.chat_tail("alpha-beta")


if __name__ == "__main__":
    unittest.main()
