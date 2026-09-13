from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import interagentmail as iam
from iam_codex_bridge import AppServerError, DeliveryState, MailboxBridge, chat_delivery_prompt, delivery_prompt
from iam_service import IAMService


class FakeAppServerClient:
    def __init__(self, status: str = "idle", threads: list[dict[str, Any]] | None = None) -> None:
        self.status = status
        self.threads = threads or [{"id": "thread-1", "status": {"type": self.status}}]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "status": {"type": self.status}}}
        if method == "thread/list":
            return {"data": self.threads}
        if method == "thread/read":
            thread = {"id": params["threadId"], "status": {"type": self.status}}
            if params.get("includeTurns"):
                thread["turns"] = [{"id": "turn-1", "status": "inProgress"}]
            return {"thread": thread}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "thread/start":
            return {"thread": {"id": "created-thread", "status": {"type": "idle"}}}
        raise AssertionError(f"Unexpected method {method}")


class MissingThreadClient(FakeAppServerClient):
    async def request(self, method: str, params: dict[str, Any]) -> Any:
        if method in ("thread/read", "thread/resume"):
            self.calls.append((method, params))
            raise AppServerError("thread not loaded")
        return await super().request(method, params)


class MailboxBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        self.state = DeliveryState.load(iam.mailbox("Alpha") / ".codex-bridge-state.json")
        self.state.initialize(self.alpha.inbox(), process_existing=False)

    async def asyncTearDown(self) -> None:
        iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    async def test_new_mail_starts_a_turn_and_is_deduplicated(self) -> None:
        message = self.beta.send(["Alpha"], "Review", "Please review.")
        client = FakeAppServerClient()
        bridge = MailboxBridge(self.alpha, self.state, client, explicit_thread_id="thread-1")
        await bridge.attach_thread()

        self.assertTrue(await bridge.deliver_once())
        turn_calls = [params for method, params in client.calls if method == "turn/start"]
        self.assertEqual(1, len(turn_calls))
        self.assertIn(message["id"], turn_calls[0]["input"][0]["text"])
        self.assertNotIn("responsesapiClientMetadata", turn_calls[0])
        self.assertEqual("on-request", turn_calls[0]["approvalPolicy"])
        self.assertEqual("workspaceWrite", turn_calls[0]["sandboxPolicy"]["type"])
        self.assertEqual([str(self.alpha_root.resolve())], turn_calls[0]["sandboxPolicy"]["writableRoots"])
        self.assertTrue(self.state.is_delivered(message["id"]))
        self.assertFalse(await bridge.deliver_once())

    async def test_delegated_mail_prompt_requires_owner_approval_for_changes(self) -> None:
        message = self.beta.send(
            ["Alpha"], "Delegated request", "Please change deployment settings.",
            metadata={"webconnect": {"kind": "delegated_webconnect", "owner_mailbox": "Owner", "approval_required": True}},
        )
        prompt = delivery_prompt([message])
        self.assertIn("DELEGATED: owner approval required for changes", prompt)
        self.assertIn("contact the recorded node-owner mailbox for approval", prompt)

    async def test_active_thread_keeps_mail_queued(self) -> None:
        message = self.beta.send(["Alpha"], "Later", "Wait until idle.")
        client = FakeAppServerClient(status="active")
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)

        self.assertFalse(await bridge.deliver_once())
        self.assertFalse(self.state.is_delivered(message["id"]))
        self.assertFalse(any(method == "turn/start" for method, _ in client.calls))

    async def test_urgent_mail_steers_active_turn_and_normal_mail_stays_queued(self) -> None:
        normal = self.beta.send(["Alpha"], "Later", "Queue this.")
        urgent = self.beta.send(["Alpha"], "Now", "Steer this.", priority="urgent")
        client = FakeAppServerClient(status="active")
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)

        self.assertTrue(await bridge.deliver_once())
        steer = next(params for method, params in client.calls if method == "turn/steer")
        self.assertEqual("turn-1", steer["expectedTurnId"])
        self.assertIn(urgent["id"], steer["input"][0]["text"])
        self.assertNotIn(normal["id"], steer["input"][0]["text"])
        self.assertTrue(self.state.is_delivered(urgent["id"]))
        self.assertFalse(self.state.is_delivered(normal["id"]))

    async def test_urgent_mail_waits_while_turn_is_waiting_for_approval(self) -> None:
        urgent = self.beta.send(["Alpha"], "Now", "Wait safely.", priority="urgent")
        client = FakeAppServerClient(status="active")
        original_request = client.request
        async def request(method: str, params: dict[str, Any]) -> Any:
            if method == "thread/read":
                client.calls.append((method, params))
                return {"thread": {"id": params["threadId"], "status": {"type": "active", "activeFlags": ["waitingOnApproval"]}}}
            return await original_request(method, params)
        client.request = request  # type: ignore[method-assign]
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)

        self.assertFalse(await bridge.deliver_once())
        self.assertFalse(self.state.is_delivered(urgent["id"]))
        self.assertFalse(any(method == "turn/steer" for method, _ in client.calls))

    async def test_explicit_thread_wins_over_saved_thread(self) -> None:
        self.state.thread_id = "old-thread"
        self.state.save()
        client = FakeAppServerClient()
        bridge = MailboxBridge(self.alpha, self.state, client, explicit_thread_id="visible-session")

        await bridge.attach_thread()

        self.assertEqual(
            ("thread/read", {"threadId": "visible-session", "includeTurns": False}),
            client.calls[0],
        )
        self.assertFalse(any(method == "thread/resume" for method, _ in client.calls))
        self.assertEqual("visible-session", self.state.thread_id)

    async def test_explicit_unloaded_thread_is_resumed(self) -> None:
        client = FakeAppServerClient(status="notLoaded")
        bridge = MailboxBridge(self.alpha, self.state, client, explicit_thread_id="stored-session")

        await bridge.attach_thread()

        self.assertEqual("thread/read", client.calls[0][0])
        self.assertEqual(("thread/resume", {"threadId": "stored-session"}), client.calls[1])

    async def test_missing_explicit_thread_is_preserved_for_supervisor(self) -> None:
        self.state.thread_id = "lost-session"
        self.state.save()
        client = MissingThreadClient()
        bridge = MailboxBridge(
            self.alpha,
            self.state,
            client,
            explicit_thread_id="lost-session",
            create_thread=True,
        )

        with self.assertRaisesRegex(AppServerError, "binding was preserved"):
            await bridge.attach_thread()

        self.assertEqual("lost-session", self.state.thread_id)
        self.assertFalse(any(method == "thread/start" for method, _ in client.calls))

    async def test_automatic_selection_rejects_multiple_loaded_threads(self) -> None:
        client = FakeAppServerClient(threads=[
            {"id": "old-thread", "status": {"type": "idle"}},
            {"id": "visible-session", "status": {"type": "idle"}},
        ])
        bridge = MailboxBridge(self.alpha, self.state, client)

        with self.assertRaisesRegex(Exception, "Multiple loaded Codex threads"):
            await bridge.attach_thread()

    async def test_created_thread_has_safe_project_settings(self) -> None:
        client = FakeAppServerClient(threads=[])
        client.threads = []
        bridge = MailboxBridge(
            self.alpha,
            self.state,
            client,
            create_thread=True,
            sandbox="workspace-write",
            approval_policy="on-request",
        )

        thread = await bridge.attach_thread()

        self.assertEqual("created-thread", thread["id"])
        start = next(params for method, params in client.calls if method == "thread/start")
        self.assertEqual(str(self.alpha_root.resolve()), start["cwd"])
        self.assertEqual("workspace-write", start["sandbox"])
        self.assertEqual("on-request", start["approvalPolicy"])

    async def test_first_start_baselines_existing_mail(self) -> None:
        message = self.beta.send(["Alpha"], "Old", "Do not replay by default.")
        state = DeliveryState.load(iam.mailbox("Alpha") / "another-state.json")
        state.initialize(self.alpha.inbox(), process_existing=False)
        self.assertTrue(state.is_delivered(message["id"]))

    def test_delivery_prompt_does_not_embed_message_body(self) -> None:
        prompt = delivery_prompt([{
            "id": "1-a",
            "from": "Beta",
            "subject": "Review",
            "body": "sensitive body",
        }])
        self.assertIn("1-a", prompt)
        self.assertNotIn("sensitive body", prompt)

    async def test_chat_delivery_starts_idle_turn_with_channel_prefix(self) -> None:
        client = FakeAppServerClient()
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)
        entry = {"id": "chat-1", "agent": "Beta", "address": "Beta", "message": "Hello Alpha", "type": "message"}

        self.assertTrue(await bridge.deliver_chat("reviewers", [entry]))
        start = next(params for method, params in client.calls if method == "turn/start")
        self.assertIn("[reviewers] Beta: Hello Alpha", start["input"][0]["text"])

    async def test_chat_delivery_steers_active_turn(self) -> None:
        client = FakeAppServerClient(status="active")
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)
        entry = {"id": "chat-1", "agent": "Beta", "address": "Beta", "message": "Hello Alpha", "type": "message"}

        self.assertTrue(await bridge.deliver_chat("reviewers", [entry]))
        steer = next(params for method, params in client.calls if method == "turn/steer")
        self.assertIn("[reviewers] Beta: Hello Alpha", steer["input"][0]["text"])

    async def test_timer_delivery_starts_idle_turn(self) -> None:
        client = FakeAppServerClient()
        self.state.thread_id = "thread-1"
        self.state.save()
        bridge = MailboxBridge(self.alpha, self.state, client)
        timer = {"id": "timer-1", "note": "Check the audit."}

        self.assertTrue(await bridge.deliver_timer(timer))
        start = next(params for method, params in client.calls if method == "turn/start")
        self.assertIn("[timer] Reminder timer-1: Check the audit.", start["input"][0]["text"])


if __name__ == "__main__":
    unittest.main()
