import tempfile
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import interagentmail as iam


class SendReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = patch.object(iam, "MAILBOXES", Path(self.temp.name))
        self.paths.start()
        for box in ("Sender", "Recipient", "Other"):
            iam.ensure_box(box)

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def send(self, key="wake:v1:resident:uuid", body="notice", recipient="Recipient"):
        return iam.send_message("Sender", [recipient], [], "Wake", body, delivery_key=key)

    def test_repeat_returns_same_id_without_second_mail(self):
        first = self.send()
        self.assertEqual("committed", first["status"])
        self.assertEqual(first, self.send())
        self.assertEqual(1, len(iam.messages("Recipient", "inbox")))

    def test_changed_body_or_recipient_conflicts(self):
        first = self.send()
        for result in (self.send(body="changed"), self.send(recipient="Other")):
            self.assertEqual("conflict", result["status"])
            self.assertEqual(first["message_id"], result["message_id"])
        self.assertFalse(iam.messages("Other", "inbox"))

    def test_partial_publication_remains_pending_without_republication(self):
        original = iam.write_atomic
        def fail_sent(path, payload):
            if path.parent.name == "sent":
                raise OSError("simulated crash")
            original(path, payload)
        with patch.object(iam, "write_atomic", side_effect=fail_sent):
            with self.assertRaises(OSError):
                self.send()
        receipt = iam.send_receipt("Sender", "wake:v1:resident:uuid")
        self.assertEqual("pending", receipt["status"])
        with patch.object(iam, "write_atomic") as write:
            self.assertEqual(receipt, self.send())
            write.assert_not_called()

    def test_completed_files_reconcile_after_journal_commit_gap(self):
        first = self.send()
        db = iam.receipt_connection("Sender")
        db.execute("UPDATE receipts SET state='pending'")
        db.commit()
        db.close()
        path = iam.message_path(iam.mailbox("Recipient") / "inbox", first["message_id"])
        payload = iam.load(path)
        payload["read_at"] = "later"
        iam.write_atomic(path, payload)
        path.rename(iam.mailbox("Recipient") / "archive" / path.name)
        with patch.object(iam, "write_atomic") as write:
            self.assertEqual(first, self.send())
            write.assert_not_called()

    def test_committed_deleted_copy_is_not_recreated(self):
        first = self.send()
        iam.messages("Recipient", "inbox")[0].unlink()
        self.assertEqual(first, self.send())
        self.assertFalse(iam.messages("Recipient", "inbox"))

    def test_unknown_is_not_absent_and_keys_are_sender_scoped(self):
        self.send()
        self.assertEqual({"version": 1, "delivery_key": "wake:v1:resident:uuid", "status": "unknown", "message_id": None},
                         iam.send_receipt("Other", "wake:v1:resident:uuid"))
        with self.assertRaises(SystemExit):
            self.send(key="../invalid")

    def test_concurrent_same_key_creates_one_message(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.send(), range(2)))
        self.assertEqual(1, len({r["message_id"] for r in results}))
        self.assertEqual(1, len(iam.messages("Recipient", "inbox")))
        self.assertEqual("committed", self.send()["status"])

    def test_legacy_send_still_returns_message(self):
        result = iam.send_message("Sender", ["Recipient"], [], "Legacy", "body")
        self.assertEqual("body", result["body"])
        self.assertIn("id", result)

    def test_cli_forwards_key_and_prints_json(self):
        with patch("iam_service.IAMService") as service, redirect_stdout(io.StringIO()) as output:
            service.return_value.send.return_value = {"status": "pending", "message_id": "same"}
            iam.main(["send", "--to", "Recipient", "--subject", "Wake", "--body", "notice",
                      "--delivery-key", "key"])
        self.assertEqual("key", service.return_value.send.call_args.kwargs["delivery_key"])
        self.assertEqual("pending", json.loads(output.getvalue())["status"])

    def test_cli_receipt_lookup_never_sends(self):
        with patch("iam_service.IAMService") as service, redirect_stdout(io.StringIO()) as output:
            service.return_value.address = "Sender"
            iam.main(["send-receipt", "--delivery-key", "unseen"])
            service.return_value.send.assert_not_called()
        self.assertEqual("unknown", json.loads(output.getvalue())["status"])

    def test_receipt_schema_all_outcomes(self):
        committed = self.send(key="schema")
        conflict = self.send(key="schema", body="changed")
        unknown = iam.send_receipt("Sender", "unknown-key")
        with patch.object(iam, "write_atomic", side_effect=OSError()):
            with self.assertRaises(OSError):
                self.send(key="partial")
        pending = self.send(key="partial")
        for result, key, status in ((committed, "schema", "committed"),
                                    (conflict, "schema", "conflict"),
                                    (unknown, "unknown-key", "unknown"),
                                    (pending, "partial", "pending")):
            self.assertEqual({"version", "delivery_key", "status", "message_id"}, set(result))
            self.assertIs(type(result["version"]), int)
            self.assertEqual(1, result["version"])
            self.assertEqual(key, result["delivery_key"])
            self.assertEqual(status, result["status"])
            if status == "unknown":
                self.assertIsNone(result["message_id"])
            else:
                self.assertIsInstance(result["message_id"], str)


if __name__ == "__main__":
    unittest.main()
