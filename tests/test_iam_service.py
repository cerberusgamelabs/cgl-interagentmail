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
        self.old_paths = (iam.MAILBOXES, iam.CHATS, iam.TEAMS, iam.CONFIG)
        iam.MAILBOXES = self.root / "mailboxes"
        iam.CHATS = self.root / "chats"
        iam.TEAMS = self.root / "teams"
        iam.CONFIG = self.root / "config.json"
        self.alpha_root = self.root / "Alpha"
        self.beta_root = self.root / "Beta"
        self.alpha_root.mkdir()
        self.beta_root.mkdir()
        self.alpha = IAMService(self.alpha_root)
        self.beta = IAMService(self.beta_root)

    def tearDown(self) -> None:
        iam.MAILBOXES, iam.CHATS, iam.TEAMS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def test_message_lifecycle_is_structured(self) -> None:
        sent = self.alpha.send(["Beta"], "Review", "Please review this.")
        self.assertEqual([sent["id"]], [message["id"] for message in self.beta.inbox()])
        self.assertEqual("normal", sent["priority"])

        read = self.beta.read(sent["id"][:12])
        self.assertIsNotNone(read["read_at"])

        reply = self.beta.reply(sent["id"], "Done.")
        self.assertEqual(sent["thread"], reply["thread"])
        self.assertEqual("Alpha", reply["to"][0])

        archived = self.beta.archive(sent["id"])
        self.assertEqual(sent["id"], archived["id"])
        self.assertEqual([], self.beta.inbox())

    def test_urgent_priority_is_preserved_and_invalid_priority_is_rejected(self) -> None:
        sent = self.alpha.send(["Beta"], "Urgent review", "Please stop safely.", priority="urgent")
        self.assertEqual("urgent", sent["priority"])
        self.assertEqual("urgent", self.beta.inbox()[0]["priority"])
        with self.assertRaises(IAMError):
            self.alpha.send(["Beta"], "Bad", "No", priority="immediate")

    def test_identity_is_bound_and_path_traversal_is_rejected(self) -> None:
        self.assertEqual("Alpha", self.alpha.whoami()["address"])
        with self.assertRaises(IAMError):
            self.alpha.send(["../outside"], "No", "No")
        with self.assertRaises(IAMError):
            self.alpha.read("*")

    def test_private_chat_access_is_enforced(self) -> None:
        self.alpha.chat_join("alpha-beta", "private", "Beta")
        entry = self.alpha.chat_post("Hello")
        self.assertEqual("Alpha", entry["address"])
        self.assertEqual("Hello", self.beta.chat_tail("alpha-beta")["messages"][0]["message"])

        gamma_root = self.root / "Gamma"
        gamma_root.mkdir()
        gamma = IAMService(gamma_root)
        with self.assertRaises(IAMError):
            gamma.chat_tail("alpha-beta")

    def test_chat_join_replaces_previous_subscription_and_records_system_events(self) -> None:
        self.alpha.chat_join("first")
        self.alpha.chat_post("Opening the first channel.")
        result = self.alpha.chat_join("second")

        self.assertEqual("first", result["previous_channel"])
        self.assertEqual("second", self.alpha.chat_subscription()["channel"])
        self.assertEqual("system", self.alpha.chat_tail("first")["messages"][0]["type"])
        self.assertIn("left the chat", self.alpha.chat_tail("first")["messages"][0]["message"])
        self.assertEqual("system", self.alpha.chat_tail("second")["messages"][0]["type"])
        self.assertIn("joined the chat", self.alpha.chat_tail("second")["messages"][0]["message"])

    def test_chat_updates_skip_own_entries_and_preserve_multiline_content(self) -> None:
        self.alpha.chat_join("reviewers")
        self.alpha.chat_post("Ready.")
        self.beta.chat_join("reviewers")
        self.beta.chat_post("Line one\nLine two")

        state, updates = self.alpha.chat_updates()
        self.assertEqual("reviewers", state["channel"])
        self.assertEqual(
            ["Beta has joined the chat.", "Line one\nLine two"],
            [item["message"] for item in updates],
        )
        self.alpha.chat_mark_delivered(state, updates)
        self.assertEqual([], self.alpha.chat_updates()[1])

    def test_human_mailbox_can_join_and_post_to_public_chat(self) -> None:
        human = IAMService.for_mailbox("Human")
        iam.save_profile("Human", {"address": "Human", "display_name": "Liliana", "kind": "user"})
        human.chat_join("breakroom")
        entry = human.chat_post("Hello from the human mailbox.")

        self.assertEqual("Human", entry["address"])
        self.assertEqual("Liliana", entry["agent"])

    def test_chat_post_requires_active_subscription(self) -> None:
        with self.assertRaisesRegex(IAMError, "No active chat channel"):
            self.alpha.chat_post("This must not create or post to a channel.")

    def test_timer_lifecycle_is_temporary(self) -> None:
        timer = self.alpha.timer_set("Check the audit.", "2030-01-01T00:00:00+00:00")
        self.assertEqual([timer["id"]], [row["id"] for row in self.alpha.timer_list()])
        self.alpha.timer_snooze(timer["id"], "2030-01-02T00:00:00+00:00")
        self.assertEqual("2030-01-02T00:00:00+00:00", self.alpha.timer_list()[0]["due_at"])
        self.alpha.timer_clear(timer["id"])
        self.assertEqual([], self.alpha.timer_list())

    def test_granted_leader_can_fan_out_but_member_cannot(self) -> None:
        team = IAMService.team_create("reviewers", ["Alpha", "Beta"])
        IAMService.team_set_leader("reviewers", "Alpha")
        with self.assertRaises(IAMError):
            self.alpha.timer_set_team("reviewers", "Review.", "2030-01-01T00:00:00+00:00")
        IAMService.team_grant("reviewers", "Alpha", "timer.schedule_team")
        timers = self.alpha.timer_set_team("reviewers", "Review.", "2030-01-01T00:00:00+00:00")
        self.assertEqual({"Alpha", "Beta"}, {timer["target"] for timer in timers})
        self.assertEqual(1, len(self.beta.timer_list()))
        self.assertEqual(team["id"], self.beta.timer_list()[0]["authorization"]["team_id"])

    def test_direct_timer_requires_explicit_team_scoped_grant(self) -> None:
        IAMService.team_create("reviewers", ["Alpha", "Beta"])
        IAMService.team_set_leader("reviewers", "Alpha")
        with self.assertRaises(IAMError):
            self.alpha.timer_set_for("Beta", "Check this.", "2030-01-01T00:00:00+00:00")
        IAMService.team_grant("reviewers", "Alpha", "timer.schedule")
        timer = self.alpha.timer_set_for("Beta", "Check this.", "2030-01-01T00:00:00+00:00")
        self.assertEqual("Beta", timer["target"])
        self.assertEqual("direct", timer["authorization"]["kind"])

    def test_team_and_leader_mail_aliases_require_membership(self) -> None:
        IAMService.team_create("reviewers", ["Alpha", "Beta"])
        IAMService.team_set_leader("reviewers", "Beta")
        sent = self.alpha.send(["team:reviewers"], "Review", "Please review.")
        self.assertEqual(["Beta"], sent["to"])
        self.assertEqual(sent["id"], self.beta.inbox()[0]["id"])
        leader = self.alpha.send(["leader:reviewers"], "Escalation", "Need direction.")
        self.assertEqual(["Beta"], leader["to"])
        gamma_root = self.root / "Gamma"
        gamma_root.mkdir()
        gamma = IAMService(gamma_root)
        with self.assertRaises(IAMError):
            gamma.send(["team:reviewers"], "No", "No.")

    def test_delegated_leader_can_manage_members_but_not_leaders_or_grants(self) -> None:
        IAMService.team_create("art", ["Alpha", "Beta"])
        IAMService.team_set_leader("art", "Alpha")
        with self.assertRaises(IAMError):
            self.alpha.team_add_member_as_leader("art", "Gamma")
        IAMService.team_grant("art", "Alpha", "team.manage_members")
        gamma_root = self.root / "Gamma"
        gamma_root.mkdir()
        IAMService(gamma_root)
        self.alpha.team_add_member_as_leader("art", "Gamma")
        self.assertIn("Gamma", IAMService.team_show("art")["members"])
        self.alpha.team_remove_member_as_leader("art", "Gamma")
        self.assertNotIn("Gamma", IAMService.team_show("art")["members"])
        with self.assertRaises(IAMError):
            self.alpha.team_remove_member_as_leader("art", "Alpha")


if __name__ == "__main__":
    unittest.main()
