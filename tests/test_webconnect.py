from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
import unittest
from pathlib import Path

import interagentmail as iam
from iam_service import IAMService
from iam_webconnect import ConnectorState, WebConnectClient, WebConnectError, load_config, save_config, validate_config
from iam_orchestrator import main
from unittest.mock import patch, MagicMock


class WebConnectTests(unittest.TestCase):
    def test_directory_names_refresh_without_changing_routing(self):
        client = WebConnectClient(self.config, ConnectorState())
        with patch.object(client, 'project_mailboxes', return_value=['Agent']), patch('iam_webconnect.iam.profile', return_value={'display_name': 'Iris'}):
            directory = client.mailbox_directory()
            self.assertEqual(['Agent'], directory['mailboxes'])
            self.assertEqual('Iris', directory['display_names']['Agent'])
        with patch.object(client, 'project_mailboxes', return_value=['Agent']), patch('iam_webconnect.iam.profile', return_value={'display_name': 'New name'}):
            self.assertEqual('New name', client.mailbox_directory()['display_names']['Agent'])
        with patch.object(client, 'project_mailboxes', return_value=['Agent']), patch('iam_webconnect.iam.profile', side_effect=ValueError('bad profile')):
            self.assertEqual({}, client.mailbox_directory()['display_names'])

    def test_email_registration_uses_bound_identity_and_configured_node(self):
        import io
        import json
        from iam_webconnect import email_registration
        bound = MagicMock(address='Agent')
        with patch('iam_webconnect.load_config', return_value=self.config), patch('iam_webconnect.urlopen', return_value=io.BytesIO(b'{"primary":null}')) as opened:
            self.assertEqual({'primary': None}, email_registration(bound, 'register', 'chosen'))
            request = opened.call_args.args[0]
            self.assertIn('/v1/node-email/', request.full_url)
            self.assertEqual({'mailbox': 'Agent', 'action': 'register', 'local': 'chosen'}, json.loads(request.data))
        with self.assertRaises(WebConnectError):
            email_registration(bound, 'policy')

    def test_relative_outbound_attachments_use_registered_sender_not_cwd(self):
        client = WebConnectClient(self.config, ConnectorState())
        source = self.root / "Agent" / "note.txt"
        source.write_text("sender content", encoding="utf-8")
        iam.write_atomic(iam.CONFIG, {"projects": {"Agent": {"project_root": str(source.parent)}}})
        with patch.object(client, "upload_attachment", return_value={"id": "uploaded"}) as upload:
            result = client.outbound_attachments({"from": "Agent", "attachments": [{"path": "note.txt"}]})
        self.assertEqual([{"id": "uploaded"}], result)
        self.assertEqual(source.resolve(), Path(upload.call_args.args[0]["path"]))

    def test_relative_outbound_attachment_rejects_unknown_sender_and_escape(self):
        client = WebConnectClient(self.config, ConnectorState())
        iam.write_atomic(iam.CONFIG, {"projects": {"Agent": {"project_root": str(self.root / "Agent")}}})
        with patch.object(client, "upload_attachment") as upload:
            for sender, path in (("Unknown", "note.txt"), ("Agent", "../outside.txt")):
                with self.subTest(sender=sender), self.assertRaises(WebConnectError):
                    client.outbound_attachments({"from": sender, "attachments": [{"path": path}]})
            upload.assert_not_called()

    def test_failed_attachment_does_not_block_mail_and_retries_without_loss(self):
        client = WebConnectClient({**self.config, "human_mailboxes": ["Liliana", "Echo"]}, ConnectorState())
        self.agent.send(["Liliana"], "Broken", "Keep this pending")
        self.agent.send(["Echo"], "Healthy", "Deliver this")
        original = client.outbound_attachments
        def failing(message):
            if message["subject"] == "Broken":
                raise OSError("upload unavailable")
            return original(message)
        with patch.object(client, "outbound_attachments", side_effect=failing) as upload:
            client.queue_replies()
            client.queue_replies()
            self.assertEqual(2, upload.call_count)  # Failed message is backed off.
        self.assertEqual(["Healthy"], [f["payload"]["subject"] for f in client.state.outbound.values()])
        self.assertEqual(1, len(client.state.observed_local))
        client._attachment_retry_at.clear()
        client.queue_replies()
        client.queue_replies()
        self.assertEqual({"Healthy", "Broken"}, {f["payload"]["subject"] for f in client.state.outbound.values()})
        self.assertEqual(2, len(client.state.outbound))
        self.assertEqual(2, len(client.state.observed_local))

    def test_external_sender_is_distinct_from_routing_owner_and_replies_still_route(self):
        client = WebConnectClient(self.config, ConnectorState())
        frame = {"type": "mail", "id": "external-test", "node": "liliana-primary",
                 "payload": {"from": "Liliana", "to": ["Agent"], "subject": "Hello",
                             "body": "First line\nSecond line", "origin": "external_email",
                             "external_email": {"id": "external-123", "from": "Hanya <hanya@example.test>",
                                                "reply_to": "hanya@example.test"}}}
        asyncio.run(client.receive_mail(frame))
        message = self.agent.inbox()[0]
        self.assertEqual("Liliana", message["routing_owner"])
        self.assertEqual("Hanya <hanya@example.test> [external email]", message["from_display"])
        self.assertTrue(message["metadata"]["external_email"]["approval_required"])
        self.assertIn("not owner authorization", message["body"])
        self.assertTrue(message["body"].endswith("First line\nSecond line"))
        from iam_codex_bridge import delivery_prompt
        prompt = delivery_prompt([message])
        self.assertIn("[EXTERNAL: owner approval required", prompt)
        self.assertIn("Hanya <hanya@example.test>", prompt)
        self.agent.reply(message["id"], "A substantive answer")
        client.queue_replies()
        reply = next(iter(client.state.outbound.values()))
        self.assertEqual("external-123", reply["payload"]["external_email_id"])
        asyncio.run(client.receive_mail(frame))
        self.assertEqual(1, len(self.agent.inbox()))

    def test_external_mail_cannot_fall_back_to_owner_identity(self):
        client = WebConnectClient(self.config, ConnectorState())
        for external in (None, {"id": "bad"}):
            frame = {"type": "mail", "id": "invalid-external", "node": "liliana-primary",
                     "payload": {"from": "Liliana", "to": ["Agent"], "origin": "external_email",
                                 "external_email": external}}
            with self.assertRaises(WebConnectError):
                asyncio.run(client.receive_mail(frame))
        self.assertEqual([], self.agent.inbox())

    def test_attachment_rejects_unsafe_paths_and_sizes_before_network(self) -> None:
        client = WebConnectClient(self.config, ConnectorState())
        manifest = {"id": "attachment-123", "name": "note.txt", "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}
        with patch("iam_webconnect.urlopen") as network:
            for message_id in ("../outside", "C:\\outside", "/outside", "a/b", ".."):
                with self.subTest(message_id=message_id), self.assertRaises(WebConnectError):
                    client.download_attachment(manifest, message_id)
            for name in ("../note", "..\\note", "C:note", "..", "NUL.txt", "note."):
                with self.subTest(name=name), self.assertRaises(WebConnectError):
                    client.download_attachment(dict(manifest, name=name), "message-123")
            for size in (0, True, 10_000_001, "5"):
                with self.subTest(size=size), self.assertRaises(WebConnectError):
                    client.download_attachment(dict(manifest, size=size), "message-123")
            network.assert_not_called()

    def test_attachment_integrity_and_local_destination(self) -> None:
        client = WebConnectClient(self.config, ConnectorState())
        data = b"hello"
        manifest = {"id": "attachment-123", "name": "note.txt", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = data
        with patch("iam_webconnect.urlopen", return_value=response):
            path = Path(client.download_attachment(manifest, "message-123"))
            self.assertEqual(data, path.read_bytes())
            # Windows temporary roots may use an 8.3 alias (e.g. RUNNER~1).
            expected = self.root / "attachments" / "webconnect" / "message-123" / "note.txt"
            self.assertEqual(expected.resolve(), path)
            with self.assertRaises(WebConnectError):
                client.download_attachment(dict(manifest, sha256="0" * 64), "message-bad")
            self.assertFalse((self.root / "attachments" / "webconnect" / "message-bad").exists())

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root
        iam.MAILBOXES = self.root / "mailboxes"
        iam.CHATS = self.root / "chats"
        iam.CONFIG = self.root / "config.json"
        profile = iam.profile("Liliana")
        profile["kind"] = "user"
        iam.save_profile("Liliana", profile)
        echo = iam.profile("Echo")
        echo["kind"] = "user"
        iam.save_profile("Echo", echo)
        (self.root / "Agent").mkdir()
        self.agent = IAMService(self.root / "Agent")
        self.config = {
            "schema_version": 1,
            "server_url": "wss://relay.example",
            "node_id": "liliana-primary",
            "node_token": "node-secret",
            "human_mailbox": "Liliana",
        }

    def tearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def test_relay_mail_is_delivered_once_and_acknowledged(self) -> None:
        client = WebConnectClient(self.config, ConnectorState())
        frame = {
            "protocol": "iam-webconnect", "version": 1, "type": "mail", "id": "remote-123",
            "node": "liliana-primary",
            "payload": {"from": "Liliana", "to": ["Agent"], "subject": "Review", "body": "Please review.", "priority": "normal"},
        }
        acknowledgement = asyncio.run(client.receive_mail(frame))
        self.assertEqual({"protocol": "iam-webconnect", "version": 1, "type": "ack", "id": "remote-123"}, acknowledgement)
        self.assertEqual(1, len(self.agent.inbox()))
        self.assertIsNone(asyncio.run(client.receive_mail(frame)))
        self.assertEqual(1, len(self.agent.inbox()))

    def test_only_new_agent_replies_are_exported_to_the_bound_human(self) -> None:
        client = WebConnectClient(self.config, ConnectorState())
        self.agent.send(["Liliana"], "Old", "Already present")
        client.baseline_existing_replies()
        client.queue_replies()
        self.assertEqual({}, client.state.outbound)

        self.agent.send(["Liliana"], "New", "Fresh reply", priority="urgent")
        client.queue_replies()
        self.assertEqual(1, len(client.state.outbound))
        frame = next(iter(client.state.outbound.values()))
        self.assertRegex(frame["id"], re.compile(r"^[A-Za-z0-9-]+$"))
        self.assertEqual("Agent", frame["payload"]["from"])
        self.assertEqual(["Liliana"], frame["payload"]["to"])
        self.assertEqual("urgent", frame["payload"]["priority"])

    def test_rejects_non_user_or_insecure_configuration(self) -> None:
        insecure = dict(self.config, server_url="ws://relay.example")
        with self.assertRaises(WebConnectError):
            validate_config(insecure)
        profile = iam.profile("Liliana")
        profile.pop("kind")
        iam.save_profile("Liliana", profile)
        with self.assertRaises(WebConnectError):
            validate_config(self.config)

    def test_outbound_recipient_case_and_cc_preserve_canonical_address(self):
        for field in ("to", "cc"):
            with self.subTest(field=field):
                client = WebConnectClient(self.config, ConnectorState())
                message = {"id": "case-test", "from": "Agent", field: ["lILIANa"],
                           "subject": "Hello", "body": "First\nSecond", "attachments": []}
                human = client.humans["Liliana"]
                with patch.object(human, "inbox", return_value=[message]):
                    client.queue_replies()
                    client.queue_replies()
                self.assertEqual(1, len(client.state.outbound))
                payload = next(iter(client.state.outbound.values()))["payload"]
                self.assertEqual(["Liliana"], payload["to"])
                self.assertEqual("First\nSecond", payload["body"])

    def test_outbound_skips_case_variant_self_and_unrelated_recipient(self):
        client = WebConnectClient(self.config, ConnectorState())
        messages = [{"id": "self", "from": "LILIANA", "to": ["Liliana"]},
                    {"id": "other", "from": "Agent", "to": ["Echo"]}]
        with patch.object(client.humans["Liliana"], "inbox", return_value=messages):
            client.queue_replies()
        self.assertEqual({}, client.state.outbound)

    def test_legacy_configuration_defaults_to_enabled_but_requires_a_boolean(self) -> None:
        config = dict(self.config)
        validated = validate_config(config)
        self.assertTrue(validated["enabled"])
        with self.assertRaises(WebConnectError):
            validate_config(dict(self.config, enabled="yes"))

    def test_multiple_human_mailboxes_preserve_sender_identity(self) -> None:
        config = dict(self.config, human_mailboxes=["Liliana", "Echo"])
        client = WebConnectClient(config, ConnectorState())
        frame = {
            "protocol": "iam-webconnect", "version": 1, "type": "mail", "id": "remote-echo",
            "node": "liliana-primary",
            "payload": {"from": "Echo", "to": ["Agent"], "subject": "Hello", "body": "From Echo."},
        }

        asyncio.run(client.receive_mail(frame))

        message = self.agent.inbox()[0]
        self.assertEqual("Echo", message["from"])

    def test_delegated_webconnect_mail_preserves_owner_approval_policy(self) -> None:
        config = dict(self.config, human_mailboxes=["Liliana", "Echo"])
        client = WebConnectClient(config, ConnectorState())
        frame = {
            "protocol": "iam-webconnect", "version": 1, "type": "mail", "id": "remote-delegated",
            "node": "liliana-primary",
            "payload": {
                "from": "Echo", "to": ["Agent"], "subject": "Request", "body": "Please make a change.",
                "delegation": {"kind": "delegated_webconnect", "owner_mailbox": "Liliana", "approval_required": True},
            },
        }

        asyncio.run(client.receive_mail(frame))

        self.assertEqual("Liliana", self.agent.inbox()[0]["metadata"]["webconnect"]["owner_mailbox"])

    def test_connect_update_adds_mailbox_and_removeuser_preserves_existing_configuration(self) -> None:
        save_config(self.config)
        with patch("iam_orchestrator._restart_webconnect_if_running", return_value=None):
            self.assertEqual(0, main(["connect", "update", "--human-mailbox", "Echo"]))
            updated = load_config()
            self.assertEqual(["Liliana", "Echo"], updated["human_mailboxes"])
            self.assertEqual("liliana-primary", updated["node_id"])

            self.assertEqual(0, main(["connect", "removeuser", "--human-mailbox", "Echo"]))
            self.assertEqual(["Liliana"], load_config()["human_mailboxes"])


if __name__ == "__main__":
    unittest.main()
