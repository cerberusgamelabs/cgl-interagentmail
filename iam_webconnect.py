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
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, quote
from urllib.error import HTTPError
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


def health_path() -> Path:
    return iam.ROOT / 'run' / 'connect-health.json'


def health_identity(config):
    return hashlib.sha256(json.dumps([config['server_url'], config['node_id']],
                                    separators=(',', ':')).encode()).hexdigest()


def write_health(config, state):
    """Body-free process health, never a mail-delivery or agent-response receipt."""
    try:
        iam.write_atomic(health_path(), dict(version=1, pid=os.getpid(),
            parent_pid=os.getppid(), identity=health_identity(config),
            state=state, checked_at=time.time()))
    except OSError:
        LOG.warning('Connector health record could not be written.')


def connector_health(config, pid, running, now=None):
    if not running:
        return 'stopped'
    try:
        record = iam.load(health_path())
        checked = record.get('checked_at')
        if (record.get('version') != 1 or type(pid) is not int
                or pid not in (record.get('pid'), record.get('parent_pid'))
                or record.get('identity') != health_identity(config)):
            return 'unverified'
        if (type(checked) not in (int, float) or not math.isfinite(checked)
                or not 0 <= (time.time() if now is None else now) - checked <= 45):
            return 'stale'
        state = record.get('state')
        return state if state in {'connecting', 'ready', 'disconnected', 'failed', 'stopped'} else 'unverified'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'unverified'


async def verify_relay(socket, config):
    # A successful send is not enough: require the connected peer's pong.
    pong = await asyncio.wait_for(socket.ping(), timeout=5)
    await asyncio.wait_for(pong, timeout=5)
    write_health(config, 'ready')


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


def email_registration(service: IAMService, action: str, local: str | None = None) -> dict[str, Any]:
    """Use the configured node credential; identity comes from the bound service."""
    if action not in {"available", "register", "status"}:
        raise WebConnectError("Unsupported email registration action")
    config = load_config()
    parsed = urlparse(config["server_url"])
    origin = ("https" if parsed.scheme == "wss" else "http") + "://" + parsed.netloc
    payload = json.dumps({"mailbox": service.address, "action": action, "local": local}).encode("utf-8")
    request = Request(origin + "/v1/node-email/" + quote(config["node_id"], safe=""), data=payload,
                      headers={"Authorization": "Bearer " + config["node_token"], "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("message", "Email registration was rejected")
        except (ValueError, AttributeError):
            message = "Email registration endpoint unavailable"
        raise WebConnectError(message) from None


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
        self._attachment_retry_at: dict[str, float] = {}
        self._invalid_sender_reported: set[str] = set()

    def relay_sender_allowed(self, sender: Any, message_id: str) -> bool:
        """Hold incompatible mail locally without changing its identity or delivery state."""
        if isinstance(sender, str) and re.fullmatch(r"[A-Za-z0-9._-]+", sender):
            self._invalid_sender_reported.discard(message_id)
            return True
        if message_id not in self._invalid_sender_reported:
            # Log an ID only: sender/content may be private or contain control text.
            safe_id = message_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", message_id) else "invalid-id"
            LOG.warning("Relay export held for message %s: incompatible sender; original retained locally.", safe_id)
            self._invalid_sender_reported.add(message_id)
        return False

    def relay_outbound(self) -> list[dict[str, Any]]:
        """Filter persisted frames too; never acknowledge or discard held frames."""
        return [frame for identifier, frame in self.state.outbound.items()
                if self.relay_sender_allowed(frame.get("payload", {}).get("from"), identifier)]

    def outbound_attachments(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        entries = message.get("attachments") or []
        resolved = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
                raise WebConnectError("Invalid attachment path.")
            source = Path(entry["path"]).expanduser()
            if not source.is_absolute():
                projects = iam.load(iam.CONFIG).get("projects", {})
                project = projects.get(str(message.get("from") or ""), {})
                root = project.get("project_root")
                if not root:
                    raise WebConnectError("Relative attachment requires a registered sender project.")
                root = Path(root).resolve()
                source = (root / source).resolve()
                if not source.is_relative_to(root):
                    raise WebConnectError("Relative attachment escapes the sender project.")
            if not source.is_file():
                raise WebConnectError("Attachment path does not exist.")
            resolved.append({**entry, "path": str(source)})
        return [self.upload_attachment(entry) for entry in resolved]

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
        if payload.get("origin") == "external_email" and not isinstance(payload.get("external_email"), dict):
            raise WebConnectError("External email is missing its relay identity.")
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
                    external = payload["external_email"]
                    claimed_from = str(external.get("from") or "Unknown external sender")
                    claimed_from = re.sub(r"[\x00-\x1f\x7f]", " ", claimed_from)[:320]
                    metadata["external_email"] = {
                        "id": external_id, "from": claimed_from,
                        "reply_to": str(external.get("reply_to") or ""),
                        "address": str(external.get("address") or ""),
                        "routing_owner": sender_address, "approval_required": True,
                    }
                else:
                    raise WebConnectError("External email has an invalid relay identity.")
            sender.send(to, str(payload.get("subject") or ""), str(payload.get("body") or ""),
                        priority=str(payload.get("priority") or "normal"), thread=payload.get("thread"),
                        attachments=attachments, references=[f"webconnect:{message_id}"],
                        metadata=metadata or None)
        self.state.remember_received(message_id)
        return {"protocol": PROTOCOL, "version": VERSION, "type": "ack", "id": message_id}

    def queue_replies(self) -> None:
        for human in self.humans.values():
            sent_by_id = {str(item.get("id") or ""): item for item in human.list_messages("sent", limit=1000)}
            address_key = human.address.casefold()
            for message in human.inbox(limit=1000):
                message_id = str(message.get("id") or "")
                if not message_id or message_id in self.state._observed or str(message.get("from") or "").casefold() == address_key:
                    continue
                recipients = {value.casefold() for value in [*message.get("to", []), *message.get("cc", [])]
                              if isinstance(value, str)}
                if address_key not in recipients:
                    continue
                if not self.relay_sender_allowed(message.get("from"), message_id):
                    continue
                frame_id = str(uuid.uuid4())
                external_id = None
                for reference in [message.get("reply_to"), *(message.get("references") or [])]:
                    original = sent_by_id.get(str(reference or ""))
                    candidate = (original or {}).get("metadata", {}).get("external_email", {}).get("id")
                    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9-]{8,128}", candidate):
                        external_id = candidate
                        break
                if time.monotonic() < self._attachment_retry_at.get(message_id, 0):
                    continue
                try:
                    attachments = self.outbound_attachments(message)
                except (OSError, ValueError) as exc:
                    # Keep the original unobserved and retry later, without taking
                    # down the socket or starving unrelated mailboxes/messages.
                    self._attachment_retry_at[message_id] = time.monotonic() + 60
                    LOG.warning("Attachment export deferred for message %s (%s); other mail continues.", message_id, type(exc).__name__)
                    continue
                self._attachment_retry_at.pop(message_id, None)
                payload = {"from": message.get("from"), "to": [human.address], "subject": message.get("subject"), "body": message.get("body"), "priority": message.get("priority", "normal"), "thread": message.get("thread"), "attachments": attachments}
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

    def mailbox_directory(self) -> dict[str, Any]:
        addresses = self.project_mailboxes()
        names = {}
        for address in [*addresses, *self.humans]:
            try:
                name = iam.profile(address).get("display_name")
                if isinstance(name, str) and name.strip() and len(name) <= 200 and not any(ord(c) < 32 or ord(c) == 127 for c in name):
                    names[address] = name.strip()
            except (OSError, ValueError):
                continue
        return {"protocol": PROTOCOL, "version": VERSION, "type": "mailboxes", "mailboxes": addresses, "display_names": names}

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
            directory = self.mailbox_directory()
            LOG.info("Publishing %d registered project mailbox(es).", len(directory["mailboxes"]))
            await socket.send(json.dumps(directory))
            next_directory_check = time.monotonic() + 30
            next_push_check = 0
            from iam_messenger_stream import ForegroundStreams
            foreground_streams=ForegroundStreams()
            next_stream_check=0
            for frame in self.relay_outbound():
                await socket.send(json.dumps(frame))
            next_health_check = 0
            while True:
                if time.monotonic() >= next_health_check:
                    await verify_relay(socket, self.config)
                    next_health_check = time.monotonic() + 10
                if foreground_streams.leases and time.monotonic()>=next_stream_check:
                    from iam_messenger_pairing import Pairings
                    from iam_desktop import DesktopStore
                    try:
                        for update in foreground_streams.updates(DesktopStore(iam.ROOT/'run'/'desktop.sqlite3'),
                                Pairings(iam.ROOT/'run'/'messenger-pairings.db'),self.config,int(time.time())):
                            await socket.send(json.dumps(update))
                    except (OSError,sqlite3.Error,ValueError):
                        for stream_id,lease in foreground_streams.leases.items():
                            await socket.send(json.dumps(dict(protocol=PROTOCOL,version=VERSION,type='messenger_stream_state',
                                stream_id=stream_id,account_id=lease['principal'].account_id,device_id=lease['principal'].device_id,
                                revision=lease['revision']+1,state='ended')))
                        foreground_streams.leases.clear()
                        LOG.warning('Foreground stream state unavailable; leases dropped.')
                    next_stream_check=time.monotonic()+1
                if time.monotonic() >= next_push_check and self.config.get('messenger',{}).get('notifications_enabled') is True:
                    from iam_messenger_pairing import Pairings
                    from iam_messenger_reads import push_states
                    from iam_desktop import DesktopStore
                    try:
                        states=push_states(DesktopStore(iam.ROOT/'run'/'desktop.sqlite3'),
                            Pairings(iam.ROOT/'run'/'messenger-pairings.db'),self.config,int(time.time()))
                        for update in states: await socket.send(json.dumps(update))
                    except (OSError,sqlite3.Error,ValueError):
                        LOG.warning('Messenger notification state unavailable; no content logged.')
                    next_push_check=time.monotonic()+10
                if time.monotonic() >= next_directory_check:
                    updated = self.mailbox_directory()
                    if updated != directory:
                        await socket.send(json.dumps(updated))
                        directory = updated
                    next_directory_check = time.monotonic() + 30
                try:
                    frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=1))
                except asyncio.TimeoutError:
                    self.queue_replies()
                    for frame in self.relay_outbound(): await socket.send(json.dumps(frame))
                    continue
                if frame.get('type') == 'messenger_friend_changed':
                    from iam_messenger_friend_delivery import FriendQueue
                    from iam_orchestrator import registered_projects
                    config=load_config()
                    names=frame.get('mailboxes')
                    if (config.get('enabled') is True and config.get('messenger',{}).get('friendships_enabled') is True
                            and frame.get('protocol')==PROTOCOL and frame.get('version')==VERSION
                            and isinstance(names,list) and len(names)<=500 and all(isinstance(n,str) for n in names)):
                        known=registered_projects();queue=FriendQueue()
                        for mailbox in set(names):
                            if mailbox in known:queue.signal(mailbox)
                elif frame.get('type') == 'messenger_friend_policy':
                    from iam_messenger_friends import policy_request
                    from iam_desktop import DesktopStore
                    from iam_orchestrator import registered_projects
                    # Fresh local owner policy, not cached connector startup state.
                    try:
                        result=policy_request(frame,load_config(),DesktopStore(),registered_projects())
                    except (OSError,ValueError,sqlite3.Error):
                        result=dict(protocol=PROTOCOL,version=VERSION,type='messenger_friend_policy_result',id=frame.get('id'),error='friend_policy_unavailable')
                    await socket.send(json.dumps(result))
                elif frame.get("type") in ("messenger_pairing_request", "messenger_pairing_status", "messenger_pairing_claim",
                                         "messenger_history_challenge", "messenger_history_read",
                                         "messenger_events_challenge", "messenger_events_sync",
                                         "messenger_send_challenge", "messenger_send_submit",
                                         "messenger_attachment_challenge", "messenger_attachment_transfer"):
                    from iam_messenger_connector import pairing_request, history_request, send_request, attachment_request, events_request
                    # Never create state while the feature is disabled.
                    policy = self.config.get('messenger', {})
                    store = None
                    if isinstance(policy, dict) and policy.get('enabled') is True:
                        from iam_messenger_pairing import Pairings
                        (iam.ROOT / 'run').mkdir(parents=True, exist_ok=True)
                        store = Pairings(iam.ROOT / 'run' / 'messenger-pairings.db')
                    if frame['type'].startswith('messenger_events_'):
                        result = events_request(frame,self.config,store,iam.ROOT / 'run' / 'desktop.sqlite3',streams=foreground_streams)
                    elif frame['type'].startswith('messenger_attachment_'):
                        result = attachment_request(frame,self.config,store,iam.ROOT / 'run' / 'desktop.sqlite3')
                    elif frame['type'].startswith('messenger_send_'):
                        result = send_request(frame,self.config,store,iam.ROOT / 'run' / 'desktop.sqlite3')
                    elif frame['type'].startswith('messenger_history_'):
                        result = history_request(frame,self.config,store,iam.ROOT / 'run' / 'desktop.sqlite3')
                    else:
                        result = pairing_request(frame,self.config,store)
                    await socket.send(json.dumps(result,ensure_ascii=False))
                elif frame.get("type") == "mail":
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
            write_health(self.config, 'connecting')
            try:
                await self.run_once()
            except asyncio.CancelledError:
                write_health(self.config, 'stopped')
                raise
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException, WebConnectError) as exc:
                write_health(self.config, 'disconnected')
                LOG.warning("WebConnect connection ended: %s", exc)
                await asyncio.sleep(5)
            except Exception:
                write_health(self.config, 'failed')
                raise
            else:
                write_health(self.config, 'disconnected')
                await asyncio.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(WebConnectClient(load_config()).run_forever())


if __name__ == "__main__":
    main()
