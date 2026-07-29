"""Push newly received IAM messages into a running Codex app-server thread."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

import interagentmail as iam
from iam_service import IAMService


LOG = logging.getLogger("iam.codex_bridge")


class AppServerError(RuntimeError):
    """The Codex app-server connection or request failed."""


class AppServerClient:
    """Small JSONL client for Codex app-server or its managed-daemon proxy."""

    def __init__(
        self,
        command: list[str] | None = None,
        url: str | None = None,
        bearer_token: str | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        if bool(command) == bool(url):
            raise ValueError("Provide exactly one of command or url")
        self.command = command
        self.url = url
        self.bearer_token = bearer_token
        self.request_timeout = request_timeout
        self.process: asyncio.subprocess.Process | None = None
        self.websocket: Any = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.next_id = 1
        self.write_lock = asyncio.Lock()
        self.closed = asyncio.Event()
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        if self.url:
            LOG.info("Connecting to Codex app-server at %s", self.url)
            headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else None
            try:
                self.websocket = await websocket_connect(self.url, additional_headers=headers)
            except (OSError, WebSocketException) as exc:
                raise AppServerError(f"Could not connect to app-server WebSocket: {exc}") from exc
            self.reader_task = asyncio.create_task(self._read_websocket())
        else:
            assert self.command
            LOG.info("Connecting to Codex app-server with: %s", " ".join(self.command))
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise AppServerError(f"Could not start app-server client command: {exc}") from exc
            self.reader_task = asyncio.create_task(self._read_stdout())
            self.stderr_task = asyncio.create_task(self._read_stderr())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "interagentmail",
                    "title": "InterAgentMail delivery bridge",
                    "version": "1.0.0",
                }
            },
        )
        await self.notify("initialized", {})

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    LOG.warning("Ignoring non-JSON app-server output: %r", line.decode(errors="replace").strip())
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        finally:
            self._mark_closed()

    async def _read_websocket(self) -> None:
        try:
            async for payload in self.websocket:
                try:
                    message = json.loads(payload)
                except json.JSONDecodeError:
                    LOG.warning("Ignoring non-JSON app-server WebSocket message")
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            pass
        finally:
            self._mark_closed()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if message_id is not None and ("result" in message or "error" in message):
            future = self.pending.pop(message_id, None)
            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    future.set_exception(AppServerError(f"{error.get('message', error)}"))
                else:
                    future.set_result(message.get("result"))
            return
        if message_id is not None and message.get("method"):
            await self._reject_server_request(message)
            return
        method = message.get("method")
        if method in ("turn/completed", "thread/status/changed"):
            LOG.debug("Codex event %s", method)

    def _mark_closed(self) -> None:
        error = AppServerError("Codex app-server connection closed")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()
        self.closed.set()

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while line := await self.process.stderr.readline():
                LOG.info("codex: %s", line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise

    async def _reject_server_request(self, message: dict[str, Any]) -> None:
        """Fail closed instead of silently approving an unattended request."""
        method = str(message.get("method"))
        LOG.warning("Codex requested interactive handling for %s; rejecting safely", method)
        await self._write({
            "id": message["id"],
            "error": {
                "code": -32601,
                "message": f"InterAgentMail bridge cannot handle interactive request {method}",
            },
        })

    async def _write(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"))
        async with self.write_lock:
            if self.websocket is not None:
                try:
                    await self.websocket.send(payload)
                except ConnectionClosed as exc:
                    raise AppServerError("Codex app-server WebSocket is closed") from exc
                return
            if not self.process or not self.process.stdin or self.process.returncode is not None:
                raise AppServerError("Codex app-server is not connected")
            self.process.stdin.write((payload + "\n").encode("utf-8"))
            await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.next_id
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._write({"method": method, "id": request_id, "params": params})
        except Exception:
            self.pending.pop(request_id, None)
            future.cancel()
            raise
        try:
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError as exc:
            self.pending.pop(request_id, None)
            raise AppServerError(f"Timed out waiting for {method}") from exc

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
        if self.process and self.process.stdin:
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader_task, self.stderr_task):
            if task:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=2)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    pass


@dataclass
class DeliveryState:
    path: Path
    delivered: list[str] = field(default_factory=list)
    thread_id: str | None = None
    initialized_at: str | None = None
    _delivered_set: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def load(cls, path: Path) -> "DeliveryState":
        if not path.exists():
            return cls(path=path)
        data = iam.load(path)
        state = cls(
            path=path,
            delivered=list(data.get("delivered", [])),
            thread_id=data.get("thread_id"),
            initialized_at=data.get("initialized_at"),
        )
        state._delivered_set = set(state.delivered)
        return state

    def initialize(self, messages: list[dict[str, Any]], process_existing: bool) -> None:
        if self.initialized_at is not None:
            return
        self.initialized_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
        if not process_existing:
            self.mark_delivered([str(message["id"]) for message in messages])
        else:
            self.save()

    def mark_delivered(self, message_ids: list[str]) -> None:
        for message_id in message_ids:
            if message_id not in self._delivered_set:
                self.delivered.append(message_id)
                self._delivered_set.add(message_id)
        if len(self.delivered) > 5000:
            self.delivered = self.delivered[-5000:]
            self._delivered_set = set(self.delivered)
        self.save()

    def is_delivered(self, message_id: str) -> bool:
        return message_id in self._delivered_set

    def save(self) -> None:
        iam.write_atomic(self.path, {
            "delivered": self.delivered,
            "initialized_at": self.initialized_at,
            "thread_id": self.thread_id,
        })


def delivery_prompt(messages: list[dict[str, Any]]) -> str:
    rows = []
    for message in messages:
        sender = message.get("from_display") or message.get("from") or "unknown"
        rows.append(f"- {message['id']} from {sender}: {message.get('subject', '(no subject)')}")
    return (
        "InterAgentMail received new mail for this project:\n\n"
        + "\n".join(rows)
        + "\n\nUse the InterAgentMail MCP tools to read each message, carry out what it asks "
        "within your current authorization, reply when appropriate, and archive it only after it "
        "is fully handled. Do not send receipt, thanks, or acknowledgment-only replies; reply only "
        "when the sender requested a response or you have a substantive result or question. If the "
        "MCP tools are unavailable, use the InterAgentMail skill/CLI."
    )


def turn_sandbox_policy(mode: str, project_root: Path) -> dict[str, Any]:
    if mode == "danger-full-access":
        return {"type": "dangerFullAccess"}
    if mode == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(project_root)],
        "networkAccess": False,
    }


class MailboxBridge:
    def __init__(
        self,
        service: IAMService,
        state: DeliveryState,
        client: AppServerClient,
        explicit_thread_id: str | None = None,
        create_thread: bool = False,
        batch_size: int = 10,
        sandbox: str = "workspace-write",
        approval_policy: str = "on-request",
    ) -> None:
        self.service = service
        self.state = state
        self.client = client
        self.explicit_thread_id = explicit_thread_id
        self.create_thread = create_thread
        self.batch_size = batch_size
        self.sandbox = sandbox
        self.approval_policy = approval_policy

    async def attach_thread(self) -> dict[str, Any]:
        if self.explicit_thread_id:
            try:
                result = await self.client.request(
                    "thread/read",
                    {"threadId": self.explicit_thread_id, "includeTurns": False},
                )
                thread = result["thread"]
                status = thread.get("status") or {"type": "notLoaded"}
                if status.get("type") == "notLoaded":
                    result = await self.client.request("thread/resume", {"threadId": self.explicit_thread_id})
                    thread = result["thread"]
                thread_id = str(thread["id"])
                self._remember_thread(thread_id)
                LOG.info(
                    "Attached mailbox %s to explicitly selected Codex thread %s",
                    self.service.address,
                    thread_id,
                )
                return thread
            except AppServerError as read_error:
                try:
                    result = await self.client.request("thread/resume", {"threadId": self.explicit_thread_id})
                    thread = result["thread"]
                    thread_id = str(thread["id"])
                    self._remember_thread(thread_id)
                    LOG.info(
                        "Resumed mailbox %s on Codex thread %s",
                        self.service.address,
                        thread_id,
                    )
                    return thread
                except AppServerError as resume_error:
                    if not self.create_thread:
                        raise resume_error from read_error
                    LOG.warning(
                        "Codex thread %s for %s is unavailable; creating a replacement",
                        self.explicit_thread_id,
                        self.service.address,
                    )
                    self.explicit_thread_id = None
                    self.state.thread_id = None
                    self.state.save()
                    return await self._start_thread()

        result = await self.client.request(
            "thread/list",
            {
                "cwd": str(self.service.project_root),
                "limit": 20,
                "sortKey": "recency_at",
                "sortDirection": "desc",
            },
        )
        candidates = result.get("data", [])
        loaded = [
            item
            for item in candidates
            if (item.get("status") or {}).get("type") not in (None, "notLoaded")
        ]
        if len(loaded) > 1:
            loaded_ids = ", ".join(str(item["id"]) for item in loaded)
            raise AppServerError(
                f"Multiple loaded Codex threads match {self.service.project_root}: {loaded_ids}. "
                "Run /status in the intended Codex session, then restart IAMBridge with "
                "--thread-id SESSION_ID."
            )
        if loaded:
            thread_id = str(loaded[0]["id"])
            resumed = await self.client.request("thread/resume", {"threadId": thread_id})
            thread = resumed["thread"]
            self._remember_thread(thread_id)
            LOG.info("Attached mailbox %s to the only loaded Codex thread %s", self.service.address, thread_id)
            return thread

        preferred = self.state.thread_id
        if preferred:
            try:
                result = await self.client.request("thread/resume", {"threadId": preferred})
                thread = result["thread"]
                self._remember_thread(str(thread["id"]))
                LOG.info("Attached mailbox %s to saved Codex thread %s", self.service.address, preferred)
                return thread
            except AppServerError:
                LOG.warning("Saved Codex thread %s is unavailable; selecting another", preferred)

        if candidates:
            candidate = candidates[0]
            thread_id = str(candidate["id"])
            resumed = await self.client.request("thread/resume", {"threadId": thread_id})
            thread = resumed["thread"]
            self._remember_thread(thread_id)
            LOG.info("Attached mailbox %s to newest matching Codex thread %s", self.service.address, thread_id)
            return thread

        if not self.create_thread:
            raise AppServerError(
                f"No Codex thread found for {self.service.project_root}. Start the agent first or pass --create-thread."
            )
        return await self._start_thread()

    async def _start_thread(self) -> dict[str, Any]:
        started = await self.client.request(
            "thread/start",
            {
                "cwd": str(self.service.project_root),
                "sandbox": self.sandbox,
                "approvalPolicy": self.approval_policy,
            },
        )
        thread = started["thread"]
        thread_id = str(thread["id"])
        self._remember_thread(thread_id)
        LOG.info("Attached mailbox %s to new Codex thread %s", self.service.address, thread_id)
        return thread

    async def ensure_resumable(self, thread: dict[str, Any]) -> bool:
        """Persist an empty app-server thread so the Codex TUI can resume it later."""
        thread_id = str(thread["id"])
        listed = await self.client.request(
            "thread/list",
            {
                "cwd": str(self.service.project_root),
                "limit": 100,
                "sortKey": "recency_at",
                "sortDirection": "desc",
            },
        )
        if any(str(item.get("id")) == thread_id for item in listed.get("data", [])):
            return True
        status = thread.get("status") or {"type": "notLoaded"}
        if status.get("type") == "active":
            return False
        await self.client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{
                    "type": "text",
                    "text": (
                        "InterAgentMail has initialized automatic delivery for this project. "
                        "Do not call tools or perform project work. Reply with exactly: InterAgentMail ready."
                    ),
                }],
                "clientUserMessageId": f"iam-bootstrap-{thread_id}",
                "approvalPolicy": self.approval_policy,
                "sandboxPolicy": turn_sandbox_policy(self.sandbox, self.service.project_root),
            },
        )
        LOG.info("Started one-time persistence turn for Codex thread %s", thread_id)
        return False

    def _remember_thread(self, thread_id: str) -> None:
        if self.state.thread_id != thread_id:
            self.state.thread_id = thread_id
            self.state.save()

    def pending_messages(self) -> list[dict[str, Any]]:
        messages = self.service.inbox(limit=1000)
        pending = [message for message in messages if not self.state.is_delivered(str(message["id"]))]
        return sorted(pending, key=lambda item: (str(item.get("created_at", "")), str(item["id"])))

    async def deliver_once(self) -> bool:
        pending = self.pending_messages()
        if not pending:
            return False
        thread_id = self.state.thread_id
        if not thread_id:
            raise AppServerError("No Codex thread is attached")
        result = await self.client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        status = result["thread"].get("status") or {"type": "notLoaded"}
        if status.get("type") == "active":
            LOG.debug("Codex thread %s is active; %d mail message(s) remain queued", thread_id, len(pending))
            return False
        if status.get("type") == "notLoaded":
            await self.client.request("thread/resume", {"threadId": thread_id})
        elif status.get("type") != "idle":
            LOG.warning(
                "Codex thread %s has status %s; mail remains queued",
                thread_id,
                status.get("type"),
            )
            return False
        batch = pending[: self.batch_size]
        message_ids = [str(message["id"]) for message in batch]
        digest = hashlib.sha256("\n".join(message_ids).encode("utf-8")).hexdigest()[:24]
        await self.client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": delivery_prompt(batch)}],
                "clientUserMessageId": f"iam-{digest}",
                "approvalPolicy": self.approval_policy,
                "sandboxPolicy": turn_sandbox_policy(self.sandbox, self.service.project_root),
            },
        )
        self.state.mark_delivered(message_ids)
        LOG.info("Delivered %d IAM message(s) to Codex thread %s", len(batch), thread_id)
        return True

    async def run(self, poll_seconds: float) -> None:
        await self.attach_thread()
        while not self.client.closed.is_set():
            await self.deliver_once()
            try:
                await asyncio.wait_for(self.client.closed.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass


def codex_executable(value: str | None) -> str:
    if value:
        return value
    found = shutil.which("codex.cmd") or shutil.which("codex")
    if not found:
        raise AppServerError("Could not find codex on PATH; pass --codex with its full path.")
    return found


async def run_bridge(args: argparse.Namespace) -> None:
    service = IAMService(args.project_root)
    state_path = iam.mailbox(service.address) / ".codex-bridge-state.json"
    state = DeliveryState.load(state_path)
    current = service.inbox(limit=1000)
    state.initialize(current, args.process_existing)
    LOG.info("Watching mailbox %s for project %s", service.address, service.project_root)

    executable = codex_executable(args.codex)
    url = args.app_server_url
    command: list[str] | None = None
    if args.standalone:
        command = [executable, "app-server"]
        url = None
    elif args.proxy:
        command = [executable, "app-server", "proxy"]
        url = None
    elif not url:
        if os.name == "nt":
            url = "ws://127.0.0.1:4500"
        else:
            command = [executable, "app-server", "proxy"]
    bearer_token = os.environ.get(args.app_server_token_env) if args.app_server_token_env else None
    while True:
        client = AppServerClient(
            command=command,
            url=url,
            bearer_token=bearer_token,
            request_timeout=args.request_timeout,
        )
        try:
            await client.connect()
            bridge = MailboxBridge(
                service,
                state,
                client,
                explicit_thread_id=args.thread_id,
                create_thread=args.create_thread,
                batch_size=args.batch_size,
            )
            await bridge.run(args.poll_seconds)
        except (AppServerError, OSError, KeyError, ValueError) as exc:
            LOG.error("Bridge connection failed: %s", exc)
        finally:
            await client.close()
        if args.once:
            return
        await asyncio.sleep(args.reconnect_seconds)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Push IAM mail into a running Codex app-server thread.")
    result.add_argument("--project-root", required=True, help="Project root whose mailbox should be watched.")
    result.add_argument(
        "--thread-id",
        help="Session id from /status. Required when more than one loaded thread matches the project.",
    )
    result.add_argument("--codex", help="Full path to codex/codex.cmd when it is not on PATH.")
    transport = result.add_mutually_exclusive_group()
    transport.add_argument(
        "--standalone",
        action="store_true",
        help="Start an isolated app-server instead of attaching to a shared running server.",
    )
    transport.add_argument("--proxy", action="store_true", help="Attach through the Unix managed-daemon proxy.")
    transport.add_argument(
        "--app-server-url",
        help="Shared app-server WebSocket URL. Windows defaults to ws://127.0.0.1:4500.",
    )
    result.add_argument(
        "--app-server-token-env",
        help="Environment variable containing a bearer token for an authenticated WebSocket.",
    )
    result.add_argument("--create-thread", action="store_true", help="Create a thread if no matching project thread exists.")
    result.add_argument(
        "--process-existing",
        action="store_true",
        help="Deliver inbox messages present on the bridge's first run instead of using them as its baseline.",
    )
    result.add_argument("--poll-seconds", type=float, default=1.0)
    result.add_argument("--reconnect-seconds", type=float, default=5.0)
    result.add_argument("--request-timeout", type=float, default=30.0)
    result.add_argument("--batch-size", type=int, default=10)
    result.add_argument("--once", action="store_true", help="Attempt one connection, then exit (diagnostics/tests).")
    result.add_argument("--verbose", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 100:
        raise SystemExit("--batch-size must be between 1 and 100")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run_bridge(args))
    except KeyboardInterrupt:
        LOG.info("Bridge stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
