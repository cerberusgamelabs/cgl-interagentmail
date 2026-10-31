"""Exact-session lookup must not depend on recent-session list pagination."""
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import iam_orchestrator as orchestration
from iam_codex_bridge import AppServerError


class ThreadLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_validates_exact_thread_without_listing(self):
        root = Path.cwd()
        client = AsyncMock()
        client.request.return_value = {"thread": {"id": "saved", "cwd": str(root)}}
        with patch.object(orchestration, "appserver_client", return_value=client) as factory:
            self.assertTrue(await orchestration.thread_available("ws://127.0.0.1:4500", "saved", root))
        factory.assert_called_once_with("ws://127.0.0.1:4500", request_timeout=30.0)
        client.request.assert_awaited_once_with("thread/read", {"threadId": "saved", "includeTurns": False})
        client.close.assert_awaited_once()

    async def test_rejects_other_project_missing_cwd_and_wrong_id(self):
        root = Path.cwd()
        for thread in ({"id": "saved", "cwd": str(root / "other")},
                       {"id": "saved"}, {"id": "other", "cwd": str(root)}):
            with self.subTest(thread=thread):
                client = AsyncMock()
                client.request.return_value = {"thread": thread}
                with patch.object(orchestration, "appserver_client", return_value=client):
                    self.assertFalse(await orchestration.thread_available("ws://127.0.0.1:4500", "saved", root))
                client.close.assert_awaited_once()

    async def test_read_error_closes_connection(self):
        client = AsyncMock()
        client.request.side_effect = AppServerError("missing thread")
        with patch.object(orchestration, "appserver_client", return_value=client):
            self.assertFalse(await orchestration.thread_available("ws://127.0.0.1:4500", "saved", Path.cwd()))
        client.close.assert_awaited_once()
