"""Outbound WebConnect transport for InterAgentMail.

This module deliberately talks only to the canonical IAM mailbox service.  It
does not expose an HTTP listener, Codex, or local filesystem to the relay.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import websockets

import interagentmail as iam
from iam_service import IAMService

PROTOCOL = "iam-webconnect"
VERSION = 1
MAX_FRAME_BYTES = 150_000
MAX_ATTACHMENT_BYTES = 10_000_000
MAX_TRACKED_IDS = 5_000
LOG = logging.getLogger("iam.webconnect")


class WebConnectError(ValueError):
    pass


def config_path() -> Path:
    return iam.ROOT / "webconnect.json"


def state_path() -> Path:
    return iam.ROOT / "webconnect-state.json"


def attachment_root() -> Path:
    return iam.ROOT / "attachments" / "webconnect"


def validate_config(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise WebConnectError("WebConnect configuration is missing or invalid.")
    url = str(data.get("server_url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"wss", "ws"} or not parsed.netloc or parsed.path not in {"", "/"}:
        raise WebConnectError("server_url must be a WebSocket origin such as wss://relay.example.")
    if parsed.scheme == "ws" and not data.get("allow_insecure_development"):
        raise WebConnectError("ws:// is allowed only with explicit development configuration.")
    for key in ("node_id", "node_token"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise WebConnectError(f"WebConnect configuration requires {key}.")
    values = data.get("human_mailboxes")
    if values is None:
        values = [data.get("human_mailbox")]
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise WebConnectError("WebConnect configuration requires one or more human_mailboxes.")
    mailboxes = list(dict.fromkeys(value.strip() for value in values))
    if any(not re.fullmatch(r"[A-Za-z0-9._-]+", value) for value in mailboxes):
        raise WebConnectError("human_mailboxes must contain valid IAM mailbox addresses.")
    data["human_mailboxes"] = mailboxes
    data["human_mailbox"] = str(data.get("human_mailbox") or mailboxes[0]).strip()
    if data["human_mailbox"] not in mailboxes:
        raise WebConnectError("human_mailbox must be included in human_mailboxes.")
    if "enabled" not in data:
        # Existing configurations predate the persisted lifecycle switch.
        data["enabled"] = True
    if not isinstance(data["enabled"], bool):
        raise WebConnectError("WebConnect configuration requires enabled to be true or false.")
    for mailbox in mailboxes:
        profile = iam.profile(mailbox)
        if profile.get("kind") != "user":
            raise WebConnectError("Every human_mailbox must be an IAM user mailbox.")
    return data


def load_config() -> dict[str, Any]:
    try:
        return validate_config(iam.load(config_path()))
    except FileNotFoundError as exc:
        raise WebConnectError("WebConnect is not configured. Run `iam connect configure`.") from exc


def save_config(data: dict[str, Any]) -> None:
    validate_config(data)
    iam.write_atomic(config_path(), data)
    if os.name != "nt":
        os.chmod(config_path(), 0o600)


@dataclass
class ConnectorState:
    received: list[str] = field(default_factory=list)
    outbound: dict[str, dict[str, Any]] = field(default_factory=dict)
    observed_local: list[str] = field(default_factory=list)
    _received: set[str] = field(default_factory=set, repr=False)
    _observed: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def load(cls) -> "ConnectorState":
        if not state_path().exists():
            return cls()
        data = iam.load(state_path())
        state = cls(
            received=list(data.get("received", [])),
            outbound=dict(data.get("outbound", {})),
            observed_local=list(data.get("observed_local", [])),
        )
        state._received = set(state.received)
        state._observed = set(state.observed_local)
        return state

    def save(self) -> None:
        iam.write_atomic(state_path(), {"received": self.received, "outbound": self.outbound, "observed_local": self.observed_local})

    def remember_received(self, message_id: str) -> None:
        if message_id not in self._received:
            self.received.append(message_id); self._received.add(message_id)
            if len(self.received) > MAX_TRACKED_IDS:
                self.received = self.received[-MAX_TRACKED_IDS:]; self._received = set(self.received)
        self.save()

    def remember_local(self, message_id: str) -> None:
        if message_id not in self._observed:
            self.observed_local.append(message_id); self._observed.add(message_id)
            if len(self.observed_local) > MAX_TRACKED_IDS:
                self.observed_local = self.observed_local[-MAX_TRACKED_IDS:]; self._observed = set(self.observed_local)
        self.save()


class WebConnectClient:
    def __init__(self, config: dict[str, Any], state: ConnectorState | None = None) -> None:
        self.config = validate_config(config)
        self.state = state or ConnectorState.load()
        self.human = IAMService.for_mailbox(self.config["human_mailbox"])
        self.humans = {address: IAMService.for_mailbox(address) for address in self.config["human_mailboxes"]}

    @property
    def url(self) -> str:
        return self.config["server_url"].rstrip("/") + "/v1/connect"

    def attachment_url(self, attachment_id: str | None = None) -> str:
        parsed = urlparse(self.config["server_url"])
        scheme = "https" if parsed.scheme == "wss" else "http"
        suffix = f"/v1/nodes/{self.config['node_id']}/attachments"
        if attachment_id:
            suffix += f"/{attachment_id}"
        return f"{scheme}://{parsed.netloc}{suffix}"

    def upload_attachment(self, entry: dict[str, Any]) -> dict[str, Any]:
        value = str(entry.get("path") or "")
        source = Path(value).expanduser()
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.is_file():
            raise WebConnectError(f"Attachment path does not exist: {value}")
        data = source.read_bytes()
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise WebConnectError(f"Attachment must contain 1-{MAX_ATTACHMENT_BYTES} bytes: {source.name}")
        digest = hashlib.sha256(data).hexdigest()
        request = Request(self.attachment_url(), data=data, method="POST", headers={"Authorization": f"Bearer {self.config['node_token']}", "Content-Type": mimetypes.guess_type(source.name)[0] or "application/octet-stream", "X-IAM-Attachment-Name": source.name, "X-IAM-Attachment-SHA256": digest})
        with urlopen(request, timeout=30) as response:
            manifest = json.loads(response.read().decode("utf-8"))
        if manifest.get("sha256") != digest or manifest.get("size") != len(data):
            raise WebConnectError("WebConnect attachment upload integrity check failed.")
        return manifest

    def download_attachment(self, manifest: dict[str, Any], message_id: str) -> str:
        attachment_id = str(manifest.get("id") or "")
        name = str(manifest.get("name") or "attachment")
        size = manifest.get("size")
        digest = manifest.get("sha256")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id)
                or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", attachment_id)
                or not name or name in {".", ".."} or name.endswith((".", " "))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
                or type(size) is not int or not 1 <= size <= MAX_ATTACHMENT_BYTES
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise WebConnectError("WebConnect attachment manifest is invalid.")
        root = attachment_root().resolve()
        destination = (root / message_id / name).resolve()
        if not destination.is_relative_to(root):
            raise WebConnectError("WebConnect attachment destination escapes its storage directory.")
        request = Request(self.attachment_url(attachment_id), headers={"Authorization": f"Bearer {self.config['node_token']}"})
        with urlopen(request, timeout=30) as response:
            data = response.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise WebConnectError("WebConnect attachment download integrity check failed.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return str(destination)

    async def receive_mail(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        if frame.get("id") in self.state._received:
            return None
        if frame.get("node") != self.config["node_id"] or frame.get("type") != "mail":
            raise WebConnectError("WebConnect server sent mail for an unexpected node.")
        payload = frame.get("payload") or {}
        sender_address = payload.get("from")
        if sender_address not in self.humans:
            raise WebConnectError("WebConnect server sent mail with an unexpected human identity.")
        sender = self.humans[sender_address]
        to = payload.get("to")
        if not isinstance(to, list) or not to or any(not isinstance(name, str) for name in to):
            raise WebConnectError("WebConnect mail recipients are invalid.")
        message_id = str(frame["id"])
        # The reference makes recovery after a crash between persistence and
        # state-save detectable without changing normal IAM semantics.
        existing = [item for item in sender.list_messages("sent", limit=1000) if f"webconnect:{message_id}" in item.get("references", [])]
        attachments = [self.download_attachment(item, message_id) for item in payload.get("attachments", [])]
        if not existing:
            metadata: dict[str, Any] = {}
            if isinstance(payload.get("delegation"), dict):
                metadata["webconnect"] = payload["delegation"]
            if payload.get("origin") == "external_email" and isinstance(payload.get("external_email"), dict):
                external_id = str(payload["external_email"].get("id") or "")
                if re.fullmatch(r"[A-Za-z0-9-]{8,128}", external_id):
                    metadata["external_email"] = {"id": external_id}
            sender.send(to, str(payload.get("subject") or ""), str(payload.get("body") or ""),
                        priority=str(payload.get("priority") or "normal"), thread=payload.get("thread"),
                        attachments=attachments, references=[f"webconnect:{message_id}"],
                        metadata=metadata or None)
        self.state.remember_received(message_id)
        return {"protocol": PROTOCOL, "version": VERSION, "type": "ack", "id": message_id}

    def queue_replies(self) -> None:
        for human in self.humans.values():
            sent_by_id = {str(item.get("id") or ""): item for item in human.list_messages("sent", limit=1000)}
            for message in human.inbox(limit=1000):
                message_id = str(message.get("id") or "")
                if not message_id or message_id in self.state._observed or message.get("from") == human.address:
                    continue
                if human.address not in [*message.get("to", []), *message.get("cc", [])]:
                    continue
                frame_id = str(uuid.uuid4())
                external_id = None
                for reference in [message.get("reply_to"), *(message.get("references") or [])]:
                    original = sent_by_id.get(str(reference or ""))
                    candidate = (original or {}).get("metadata", {}).get("external_email", {}).get("id")
                    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9-]{8,128}", candidate):
                        external_id = candidate
                        break
                payload = {"from": message.get("from"), "to": [human.address], "subject": message.get("subject"), "body": message.get("body"), "priority": message.get("priority", "normal"), "thread": message.get("thread"), "attachments": [self.upload_attachment(item) for item in message.get("attachments", [])]}
                if external_id:
                    payload["external_email_id"] = external_id
                self.state.outbound[frame_id] = {
                    "protocol": PROTOCOL, "version": VERSION, "type": "mail", "id": frame_id,
                    "payload": payload,
                }
                self.state.remember_local(message_id)

    def project_mailboxes(self) -> list[str]:
        """Return routable IAM projects currently registered on this machine."""
        try:
            projects = iam.load(iam.CONFIG).get("projects", {})
        except (OSError, ValueError):
            return []
        if not isinstance(projects, dict):
            return []
        return sorted(address for address in projects if isinstance(address, str) and re.fullmatch(r"[A-Za-z0-9._-]+", address))

    def baseline_existing_replies(self) -> None:
        """Do not export mail that predates the connector's configuration."""
        for human in self.humans.values():
            for message in human.inbox(limit=1000):
                message_id = str(message.get("id") or "")
                if message_id:
                    self.state.remember_local(message_id)

    async def run_once(self) -> None:
        self.queue_replies()
        async with websockets.connect(self.url, max_size=MAX_FRAME_BYTES, ping_interval=20, ping_timeout=20) as socket:
            await socket.send(json.dumps({"protocol": PROTOCOL, "version": VERSION, "type": "register", "node": self.config["node_id"], "token": self.config["node_token"]}))
            registered = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            registered_mailboxes = registered.get("human_mailboxes") or [registered.get("human_mailbox")]
            if registered.get("type") != "registered" or set(registered_mailboxes) != set(self.humans):
                raise WebConnectError("WebConnect server rejected the configured node or human mailbox.")
            LOG.info("WebConnect node %s registered with %s.", self.config["node_id"], self.url)
            mailboxes = self.project_mailboxes()
            LOG.info("Publishing %d registered project mailbox(es).", len(mailboxes))
            await socket.send(json.dumps({"protocol": PROTOCOL, "version": VERSION, "type": "mailboxes", "mailboxes": mailboxes}))
            for frame in self.state.outbound.values():
                await socket.send(json.dumps(frame))
            while True:
                try:
                    frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=1))
                except asyncio.TimeoutError:
                    self.queue_replies()
                    for frame in self.state.outbound.values(): await socket.send(json.dumps(frame))
                    continue
                if frame.get("type") == "mail":
                    acknowledgement = await self.receive_mail(frame)
                    if acknowledgement: await socket.send(json.dumps(acknowledgement))
                elif frame.get("type") == "ack" and frame.get("id") in self.state.outbound:
                    self.state.outbound.pop(frame["id"], None); self.state.save()
                elif frame.get("type") == "error":
                    raise WebConnectError(str(frame.get("message") or "WebConnect server rejected a frame."))

    async def run_forever(self) -> None:
        if not self.config["enabled"]:
            LOG.info("WebConnect is disabled in local configuration.")
            return
        while True:
            try: await self.run_once()
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException, WebConnectError) as exc:
                LOG.warning("WebConnect connection ended: %s", exc); await asyncio.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(WebConnectClient(load_config()).run_forever())


if __name__ == "__main__":
    main()
