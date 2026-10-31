import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import interagentmail as iam
from iam_heartbeat import Heartbeats, deliver_due, reconcile, release_with_receipt, acceptance_evidence
from iam_service import IAMError


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = iam.ROOT, iam.CONFIG, iam.MAILBOXES
        iam.ROOT = Path(self.temp.name)
        iam.CONFIG = iam.ROOT / "config.json"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        self.projects = {"Alpha": {"project_root": str(iam.ROOT), "sandbox": "read-only", "approval_policy": "on-request"}}
        iam.write_atomic(iam.CONFIG, {"projects": self.projects})
        iam.write_atomic(iam.mailbox("Alpha") / ".codex-bridge-state.json", {"thread_id": "exact"})
        self.now = 1000
        self.clock = patch("iam_heartbeat.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.store = Heartbeats()

    def tearDown(self):
        self.clock.stop()
        iam.ROOT, iam.CONFIG, iam.MAILBOXES = self.saved
        self.temp.cleanup()

    def schedule(self, name="default", due=1900, reason=None):
        return self.store.schedule("Alpha", datetime.fromtimestamp(due, timezone.utc).isoformat(),
                                   reason or "Reason for " + name, name)

    def appointment(self):
        self.store.configure("Alpha", "enable", "exact")
        self.schedule()
        self.now = 2000

    def client(self, status="idle", fail=None, history=None):
        client = AsyncMock()
        async def request(method, params):
            if method == fail:
                raise TimeoutError("private diagnostic must not be logged")
            if method in {"thread/read", "thread/resume"}:
                return {"thread": {"id": "exact", "cwd": str(iam.ROOT),
                    "status": {"type": "idle" if method == "thread/resume" else status},
                    "turns": history or []}}
            if method == "turn/start":
                return {"turn": {"id": "turn-1"}}
            self.fail("Unexpected method: " + method)
        client.request.side_effect = request
        return client

    def deliver(self, client=None):
        client = client or self.client()
        return asyncio.run(deliver_due("Alpha", "url", self.projects, lambda _: client))

    def history_for(self, delivery, kind="userMessage", text=None):
        return [{"id": "verified-turn", "items": [{"id": "item-1", "type": kind,
            "content": [{"type": "text", "text": delivery["prompt"] if text is None else text}]}]}]

    def resolve(self, delivery):
        return self.store.resolve("Alpha", delivery["id"], accepted=True,
            evidence={"kind": "turn_start_response", "turn_id": "turn-1"}, actor="test", turn_id="turn-1")

    def test_disabled_and_minimum_timezone_validation(self):
        self.assertFalse(self.store.state("Alpha")["enabled"])
        with self.assertRaises(IAMError):
            self.schedule()
        self.store.configure("Alpha", "enable", "exact")
        for value in ("2099-01-01T00:00:00", "bad", "1970-01-01T00:30:00Z"):
            with self.assertRaises(IAMError):
                self.store.schedule("Alpha", value, "reason")
        self.schedule()
        self.assertEqual(900, self.store.state("Alpha")["minimum_interval_seconds"])

    def test_named_schedules_and_default_compatibility(self):
        self.appointment()
        self.schedule("watercolors", 4000)
        self.schedule("friends", 4000)
        original = self.store.state("Alpha")["schedules"]["friends"]
        self.schedule("watercolors", 5000, "new resident choice")
        state = self.store.state("Alpha")
        self.assertEqual(state["next_wake"], state["schedules"]["default"])
        self.assertEqual(original, state["schedules"]["friends"])
        self.store.cancel("Alpha", "not now", "watercolors")
        self.assertEqual({"default", "friends"}, set(self.store.state("Alpha")["schedules"]))
        self.store.cancel("Alpha", "default only")
        self.assertEqual({"friends"}, set(self.store.state("Alpha")["schedules"]))

    def test_invalid_names_and_capacity(self):
        self.store.configure("Alpha", "enable", "exact")
        for name in ("", "../bad", "bad name", "x" * 65):
            with self.assertRaises(IAMError):
                self.schedule(name)
        for i in range(64):
            self.schedule("purpose-" + str(i))
        with self.assertRaises(IAMError):
            self.schedule("overflow")
        self.schedule("purpose-0", 3000)

    def test_pause_and_disable_preserve_exact_appointments(self):
        self.appointment()
        before = self.store.state("Alpha")["schedules"]
        for action in ("pause", "resume", "disable", "enable"):
            self.store.configure("Alpha", action, "exact")
            self.assertEqual(before, self.store.state("Alpha")["schedules"])
            if action in ("pause", "disable"):
                self.assertIsNone(self.store.due("Alpha"))
        self.assertIsNotNone(self.store.due("Alpha"))

    def test_resident_can_edit_while_paused_without_waking(self):
        self.appointment()
        self.store.configure("Alpha", "pause")
        self.schedule("art", 4000)
        self.store.cancel("Alpha", "not now")
        self.now = 5000
        self.assertIsNone(self.store.due("Alpha"))
        self.store.configure("Alpha", "resume")
        self.assertEqual({"art"}, set(self.store.due("Alpha")["schedules"]))

    def test_simultaneous_and_overdue_coalesce_after_pause(self):
        self.appointment()
        self.schedule("social", 4000)
        self.schedule("future", 9000)
        self.store.configure("Alpha", "pause")
        self.now = 6000
        self.store.configure("Alpha", "resume")
        before = self.store.state("Alpha")["schedules"]
        client = self.client()
        self.assertTrue(self.deliver(client))
        calls = [c for c in client.request.call_args_list if c.args[0] == "turn/start"]
        self.assertEqual(1, len(calls))
        prompt = calls[0].args[1]["input"][0]["text"]
        for name in ("default", "social"):
            self.assertIn(before[name]["id"], prompt)
            self.assertIn(before[name]["reason"], prompt)
        self.assertNotIn(before["future"]["id"], prompt)
        self.assertIn("overdue by:", prompt)
        self.assertIn("NOT authorization", prompt)
        self.assertEqual({"future"}, set(self.store.state("Alpha")["schedules"]))

    def test_global_actual_wake_interval(self):
        self.appointment()
        self.schedule("second", 2900)
        self.assertTrue(self.deliver())
        self.now = 2900 - 1
        self.assertFalse(self.deliver())
        self.now = 2900
        self.assertTrue(self.deliver())
        self.assertEqual({}, self.store.state("Alpha")["schedules"])

    def test_future_becomes_due_inside_cooldown_then_coalesces(self):
        self.store.configure("Alpha", "enable", "exact")
        self.schedule("first", 1900)
        self.schedule("second", 1950)
        self.schedule("third", 2000)
        self.now = 1900
        self.assertTrue(self.deliver())
        self.now = 2000
        self.assertFalse(self.deliver())
        self.now = 2800
        self.assertTrue(self.deliver())
        receipt = self.store.history("Alpha")[0]
        self.assertEqual(2, len(receipt["appointment_ids"]))

    def test_claim_survives_restart_without_consumption_or_replay(self):
        self.appointment()
        before = self.store.state("Alpha")["schedules"]
        delivery = self.store.claim("Alpha")
        self.assertIsNone(self.store.claim("Alpha"))
        self.assertIsNone(Heartbeats().due("Alpha"))
        self.assertEqual(before, Heartbeats().state("Alpha")["schedules"])
        self.assertEqual(delivery["id"], Heartbeats().state("Alpha")["delivery"]["id"])

    def test_only_one_concurrent_batch(self):
        self.appointment()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: Heartbeats().claim("Alpha"), range(2)))
        self.assertEqual(1, sum(c is not None for c in claims))

    def test_busy_never_steers_or_consumes(self):
        self.appointment()
        client = self.client("active")
        before = self.store.state("Alpha")["schedules"]
        self.assertFalse(self.deliver(client))
        self.assertEqual(["thread/read"], [c.args[0] for c in client.request.call_args_list])
        self.assertEqual(before, self.store.state("Alpha")["schedules"])
        self.assertFalse(self.store.state("Alpha")["heartbeat_held"])

    def test_preflight_failure_retains_pending_without_uncertainty(self):
        self.appointment()
        self.assertFalse(self.deliver(self.client(fail="thread/read")))
        self.assertTrue(self.store.state("Alpha")["schedules"])
        self.assertFalse(self.store.state("Alpha")["heartbeat_held"])
        self.now += 60
        self.assertTrue(self.deliver())

    def test_binding_mismatch_never_creates_thread(self):
        self.appointment()
        iam.write_atomic(iam.mailbox("Alpha") / ".codex-bridge-state.json", {"thread_id": "replacement"})
        client = self.client()
        self.assertFalse(self.deliver(client))
        client.connect.assert_not_called()
        self.assertTrue(self.store.state("Alpha")["schedules"])

    def test_unloaded_resumes_exact_thread_with_existing_policy(self):
        self.appointment()
        client = self.client()
        original = client.request.side_effect
        count = 0
        async def request(method, params):
            nonlocal count
            result = await original(method, params)
            if method == "thread/read":
                count += 1
                if count == 1:
                    result["thread"]["status"]["type"] = "notLoaded"
            return result
        client.request.side_effect = request
        self.assertTrue(self.deliver(client))
        methods = [c.args[0] for c in client.request.call_args_list]
        self.assertIn("thread/resume", methods)
        self.assertNotIn("thread/start", methods)
        params = next(c.args[1] for c in client.request.call_args_list if c.args[0] == "turn/start")
        self.assertEqual("exact", params["threadId"])
        self.assertEqual("on-request", params["approvalPolicy"])
        self.assertEqual("readOnly", params["sandboxPolicy"]["type"])

    def test_pause_or_cancel_at_last_read_wins(self):
        for action in ("pause", "cancel"):
            self.appointment()
            client = self.client()
            original = client.request.side_effect
            count = 0
            async def request(method, params):
                nonlocal count
                count += method == "thread/read"
                if count == 2:
                    if action == "pause":
                        self.store.configure("Alpha", "pause")
                    else:
                        self.store.cancel("Alpha", "changed my mind")
                return await original(method, params)
            client.request.side_effect = request
            self.assertFalse(self.deliver(client))
            self.assertNotIn("turn/start", [c.args[0] for c in client.request.call_args_list])
            self.now = 1000

    def test_timeout_retains_exact_appointments_and_never_retries(self):
        self.appointment()
        before = self.store.state("Alpha")["schedules"]
        self.assertFalse(self.deliver(self.client(fail="turn/start")))
        state = self.store.state("Alpha")
        self.assertTrue(state["heartbeat_held"])
        self.assertEqual(before, state["schedules"])
        client = self.client()
        self.now += 100000
        self.deliver(client)
        self.assertEqual(["thread/read"], [c.args[0] for c in client.request.call_args_list])
        self.assertEqual(before, self.store.state("Alpha")["schedules"])

    def test_late_verified_acceptance_consumes_atomically_and_respects_pause(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        self.store.hold("Alpha", delivery["id"])
        self.store.configure("Alpha", "pause")
        client = self.client(history=self.history_for(delivery))
        self.now = 5000
        result = asyncio.run(reconcile("Alpha", "url", lambda _: client))
        self.assertEqual("accepted", result["status"])
        state = self.store.state("Alpha")
        self.assertEqual({}, state["schedules"])
        self.assertFalse(state["heartbeat_held"])
        self.assertTrue(state["paused"])
        self.assertEqual(5900, state["earliest_next_wake"])
        self.assertEqual("verified-turn", state["last_turn_id"])

    def test_reconciliation_ignores_assistant_quotes_wrong_thread_or_changed_text(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        for kind, text, thread in (("agentMessage", None, "exact"),
                ("userMessage", "partial", "exact"), ("userMessage", None, "other")):
            result = {"thread": {"id": thread, "turns": self.history_for(delivery, kind, text)}}
            self.assertIsNone(acceptance_evidence(delivery, result))
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])

    def test_missing_or_compacted_history_never_releases_hold(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        self.assertIsNone(asyncio.run(reconcile("Alpha", "url", lambda _: self.client())))
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
        self.assertEqual(delivery["appointment_ids"], [w["id"] for w in self.store.state("Alpha")["schedules"].values()])

    def test_resident_renewal_during_delivery_is_not_consumed(self):
        self.appointment()
        client = self.client()
        original = client.request.side_effect
        async def request(method, params):
            if method == "turn/start":
                self.schedule("default", 4000, "next appointment")
            return await original(method, params)
        client.request.side_effect = request
        self.assertTrue(self.deliver(client))
        self.assertEqual("next appointment", self.store.state("Alpha")["next_wake"]["reason"])

    def test_owner_note_waits_survives_pause_and_uncertainty(self):
        self.appointment()
        self.store.owner_note("Alpha", "private surprise", "Liliana")
        self.assertNotIn("private surprise", str(self.store.state("Alpha")))
        before = self.store.state("Alpha")["schedules"]
        self.assertFalse(self.deliver(self.client(fail="turn/start")))
        self.assertEqual(before, self.store.state("Alpha")["schedules"])
        self.assertTrue(self.store.state("Alpha")["owner_note_pending"])
        delivery = self.store.held("Alpha", force=True)
        self.assertIn("A note from Liliana:\nprivate surprise", delivery["prompt"])
        client = self.client(history=self.history_for(delivery))
        asyncio.run(reconcile("Alpha", "url", lambda _: client, delivery_id=delivery["id"]))
        self.assertFalse(self.store.state("Alpha")["owner_note_pending"])
        self.assertNotIn("private surprise", str(self.store.history("Alpha")))
        self.assertNotIn("Reason for", str(self.store.history("Alpha")))

    def test_new_owner_note_is_preserved(self):
        self.appointment()
        self.store.owner_note("Alpha", "first", "Liliana")
        delivery = self.store.claim("Alpha")
        self.store.owner_note("Alpha", "second", "Liliana")
        self.resolve(delivery)
        self.assertTrue(self.store.state("Alpha")["owner_note_pending"])

    def test_resolution_is_idempotent(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        self.resolve(delivery)
        self.resolve(delivery)
        self.assertEqual(1, len(self.store.history("Alpha")))

    def test_acceptance_and_receipt_rollback_together(self):
        import sqlite3
        self.appointment()
        delivery = self.store.claim("Alpha")
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_acceptance BEFORE INSERT ON heartbeat_receipts BEGIN SELECT RAISE(ABORT, 'test fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.resolve(delivery)
        state = self.store.state("Alpha")
        self.assertTrue(state["heartbeat_held"])
        self.assertTrue(state["schedules"])
        self.assertEqual("submitting", state["delivery"]["status"])
        self.assertEqual([], self.store.history("Alpha"))

    def test_conflicting_resolution_cannot_overwrite_receipt(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        self.resolve(delivery)
        with self.assertRaises(IAMError):
            self.store.resolve("Alpha", delivery["id"], accepted=False, evidence={}, actor="test")
        self.assertEqual(1, len(self.store.history("Alpha")))

    def test_pause_resume_cannot_clear_uncertainty_or_rearm(self):
        self.appointment()
        before = self.store.state("Alpha")["schedules"]
        delivery = self.store.claim("Alpha")
        for action in ("pause", "resume", "disable", "enable"):
            self.store.configure("Alpha", action, "exact")
            state = self.store.state("Alpha")
            self.assertTrue(state["heartbeat_held"])
            self.assertEqual(delivery["id"], state["delivery"]["id"])
            self.assertEqual(before, state["schedules"])
            self.assertIsNone(self.store.due("Alpha"))

    def test_resident_can_cancel_held_appointment_without_releasing_hold(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        self.store.cancel("Alpha", "not again")
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
        self.assertEqual({}, self.store.state("Alpha")["schedules"])
        self.resolve(delivery)
        self.assertFalse(self.store.state("Alpha")["heartbeat_held"])

    def test_manual_acceptance_records_reviewer_and_requires_exact_turn(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        client = self.client(history=self.history_for(delivery))
        actor = {"os_user": "local-user", "reviewer": "Liliana"}
        result = asyncio.run(reconcile("Alpha", "url", lambda _: client,
            delivery_id=delivery["id"], actor=actor, turn_id="wrong-turn"))
        self.assertIsNone(result)
        result = asyncio.run(reconcile("Alpha", "url", lambda _: client,
            delivery_id=delivery["id"], actor=actor, turn_id="verified-turn"))
        self.assertEqual(actor, result["resolved_by"])
        self.assertEqual("proven_accepted", result["resolution"])
        self.assertEqual("item-1", result["evidence"]["item_id"])

    def test_missing_returned_turn_id_holds_instead_of_consuming(self):
        self.appointment()
        client = self.client()
        original = client.request.side_effect
        async def request(method, params):
            return {} if method == "turn/start" else await original(method, params)
        client.request.side_effect = request
        self.assertFalse(self.deliver(client))
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
        self.assertTrue(self.store.state("Alpha")["schedules"])

    def test_unknown_rpc_id_cannot_be_manually_released(self):
        self.appointment()
        delivery = self.store.claim("Alpha")
        path = iam.ROOT / "receipt.json"
        path.write_text(json.dumps(self.rejection_receipt(delivery)), encoding="utf-8")
        with self.assertRaises(IAMError):
            release_with_receipt("Alpha", delivery["id"], path, "Liliana")
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])

    def test_cli_refuses_blind_resolution_and_verifies_acceptance(self):
        from contextlib import redirect_stdout
        import io
        from iam_orchestrator import parser, cmd_heartbeat
        self.appointment()
        delivery = self.store.claim("Alpha")
        client = self.client(history=self.history_for(delivery))
        prefix = ["heartbeat", "resolve", str(iam.ROOT), "--delivery-id", delivery["id"]]
        with patch("iam_orchestrator.project_entry", return_value=("Alpha", iam.ROOT, self.projects["Alpha"])), \
             patch("iam_orchestrator.appserver_client", return_value=client), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(IAMError):
                cmd_heartbeat(parser().parse_args(prefix + ["--resolution", "retry"]))
            with self.assertRaises(IAMError):
                cmd_heartbeat(parser().parse_args(prefix + ["--resolution", "retry", "--verified-by", "Liliana"]))
            self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
            cmd_heartbeat(parser().parse_args(prefix + ["--resolution", "accepted", "--verified-by", "Liliana"]))
        receipt = json.loads(output.getvalue())
        self.assertEqual("Liliana", receipt["resolved_by"]["reviewer"])
        self.assertEqual("accepted", receipt["status"])

    def test_cli_positive_acceptance_overrules_supplied_rejection(self):
        from contextlib import redirect_stdout
        import io
        from iam_orchestrator import parser, cmd_heartbeat
        self.appointment()
        delivery = self.store.claim("Alpha", rpc_request_id=12)
        client = self.client(history=self.history_for(delivery))
        path = iam.ROOT / "receipt.json"
        path.write_text(json.dumps(self.rejection_receipt(delivery)), encoding="utf-8")
        args = parser().parse_args(["heartbeat", "resolve", str(iam.ROOT), "--delivery-id", delivery["id"],
            "--resolution", "retry", "--verified-by", "Liliana", "--evidence-file", str(path)])
        with patch("iam_orchestrator.project_entry", return_value=("Alpha", iam.ROOT, self.projects["Alpha"])), \
             patch("iam_orchestrator.appserver_client", return_value=client), redirect_stdout(io.StringIO()):
            cmd_heartbeat(args)
        self.assertEqual("accepted", self.store.history("Alpha")[0]["status"])
        self.assertEqual({}, self.store.state("Alpha")["schedules"])

    def rejection_receipt(self, delivery):
        return dict(kind="rpc_rejection", delivery_id=delivery["id"], thread_id="exact",
            client_message_id=delivery["client_message_id"], request_id=12, response_request_id=12, error_code=-32602)

    def test_manual_retry_requires_correlated_body_free_evidence_and_reviewer(self):
        self.appointment()
        delivery = self.store.claim("Alpha", rpc_request_id=12)
        path = iam.ROOT / "receipt.json"
        valid = self.rejection_receipt(delivery)
        for mutation in ({"kind": "timeout"}, {"error_code": -32603}, {"request_id": 15},
                         {"thread_id": "wrong"}, {"delivery_id": "wrong"}, {"body": "private"},
                         {"client_message_id": "wrong"}):
            path.write_text(json.dumps({**valid, **mutation}), encoding="utf-8")
            with self.assertRaises(IAMError):
                release_with_receipt("Alpha", delivery["id"], path, "Liliana")
            self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
        path.write_text(json.dumps(valid), encoding="utf-8")
        with self.assertRaises(IAMError):
            release_with_receipt("Alpha", delivery["id"], path, "")
        result = release_with_receipt("Alpha", delivery["id"], path, "Liliana")
        self.assertEqual("released_for_retry", result["status"])
        self.assertEqual("Liliana", result["resolved_by"]["reviewer"])
        self.assertIn("os_user", result["resolved_by"])
        self.assertIn("artifact_sha256", result["evidence"])
        self.assertTrue(self.store.state("Alpha")["schedules"])
        self.assertFalse(self.store.state("Alpha")["heartbeat_held"])
        self.now += 60
        self.assertTrue(self.deliver())

    def test_legacy_default_migration_preserves_id_reason_time(self):
        old = dict(owner="Alpha", enabled=True, paused=False, thread_id="exact", minimum_interval_seconds=900,
            next_wake=dict(id="old-id", timestamp=1900, reason="old reason"), outcome="scheduled")
        with self.store.transaction() as db:
            db.execute("INSERT INTO heartbeat VALUES (?,?)", ("Alpha", json.dumps(old)))
        state = self.store.state("Alpha")
        self.assertEqual("old-id", state["schedules"]["default"]["id"])
        self.assertEqual(1900, state["next_wake"]["timestamp"])
        self.assertEqual("old reason", state["next_wake"]["reason"])

    def test_legacy_uncertainty_is_not_rearmed(self):
        old = dict(owner="Alpha", enabled=True, paused=False, thread_id="exact", next_wake=None,
            outcome="failed_or_uncertain_dormant", last_wake={"id": "old-consumed", "timestamp": 1900, "reason": "old"})
        with self.store.transaction() as db:
            db.execute("INSERT INTO heartbeat VALUES (?,?)", ("Alpha", json.dumps(old)))
        self.assertTrue(self.store.state("Alpha")["heartbeat_held"])
        self.assertEqual({}, self.store.state("Alpha")["schedules"])
        self.assertIsNone(self.store.due("Alpha"))

    def test_linked_mailboxes_share_schedules(self):
        self.projects["Secondary"] = {"project_root": str(iam.ROOT / "Secondary")}
        iam.write_atomic(iam.CONFIG, {"projects": self.projects, "session_mailboxes": {"Secondary": "Alpha"}})
        self.appointment()
        self.assertEqual(self.store.state("Alpha"), self.store.state("Secondary"))
        self.store.cancel("Secondary", "done")
        self.assertIsNone(self.store.due("Alpha"))
