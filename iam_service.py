"""Structured application service for InterAgentMail.

The original CLI is intentionally kept as a thin, stable user interface.  MCP
and event delivery use this module so they return Python data instead of
printing command-oriented text to stdout.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import interagentmail as iam


class IAMError(ValueError):
    """A user-facing IAM operation error."""


@contextmanager
def translated_errors() -> Iterator[None]:
    try:
        yield
    except SystemExit as exc:
        raise IAMError(str(exc)) from None


def _as_list(values: list[str] | None) -> list[str]:
    return values or []


class IAMService:
    """IAM operations bound to one trusted project or application mailbox.

    Binding identity at startup prevents an MCP or browser caller from
    impersonating another mailbox through request parameters.
    """

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        mailbox_address: str | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve() if mailbox_address is None else None
        with translated_errors():
            self.address = iam.address(str(self.project_root)) if mailbox_address is None else iam.safe_segment(
                mailbox_address,
                "mailbox address",
            )
            iam.ensure_box(self.address)

    @classmethod
    def for_mailbox(cls, mailbox_address: str) -> "IAMService":
        """Bind trusted local application code to a non-project mailbox."""
        return cls(mailbox_address=mailbox_address)

    def whoami(self) -> dict[str, Any]:
        with translated_errors():
            data = iam.profile(self.address)
        return {**data, "project_root": str(self.project_root) if self.project_root else None}

    def list_mailboxes(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if not iam.MAILBOXES.exists():
            return result
        for box in sorted(path for path in iam.MAILBOXES.iterdir() if path.is_dir()):
            try:
                data = iam.load(box / "profile.json") if (box / "profile.json").exists() else {}
            except (OSError, ValueError):
                data = {}
            result.append({
                "address": str(data.get("address") or box.name),
                "display_name": str(data.get("display_name") or box.name),
                "kind": str(data.get("kind") or ("project" if data.get("project_root") else "mailbox")),
            })
        return result

    def list_messages(self, folder: str = "inbox", limit: int = 100) -> list[dict[str, Any]]:
        if folder not in {"inbox", "sent", "archive"}:
            raise IAMError("folder must be inbox, sent, or archive")
        if limit < 1 or limit > 1000:
            raise IAMError("limit must be between 1 and 1000")
        rows: list[dict[str, Any]] = []
        with translated_errors():
            for path in reversed(iam.messages(self.address, folder)):
                rows.append(iam.load(path))
                if len(rows) >= limit:
                    break
        return rows

    def inbox(self, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise IAMError("limit must be between 1 and 1000")
        rows: list[dict[str, Any]] = []
        with translated_errors():
            paths = reversed(iam.messages(self.address, "inbox"))
            for path in paths:
                message = iam.load(path)
                if unread_only and message.get("read_at"):
                    continue
                rows.append(message)
                if len(rows) >= limit:
                    break
        return rows

    def peek(self, message_id: str) -> dict[str, Any]:
        """Read a message without changing its read state."""
        with translated_errors():
            path = iam.find_message(self.address, message_id)
            return iam.load(path)

    def read(self, message_id: str, mark_read: bool = True) -> dict[str, Any]:
        with translated_errors():
            path = iam.find_message(self.address, message_id)
            message = iam.load(path)
            if mark_read and not message.get("read_at"):
                message["read_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                iam.write_atomic(path, message)
            return message

    def send(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        attachments: list[str] | None = None,
        references: list[str] | None = None,
        thread: str | None = None,
        originator: str | None = None,
    ) -> dict[str, Any]:
        with translated_errors():
            return iam.send_message(
                self.address,
                iam.resolve_names(to),
                iam.resolve_names(_as_list(cc)),
                subject,
                body,
                iam.attachments(_as_list(attachments)),
                thread,
                originator=originator,
                refs=_as_list(references),
            )

    def reply(
        self,
        message_id: str,
        body: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        attachments: list[str] | None = None,
        references: list[str] | None = None,
    ) -> dict[str, Any]:
        with translated_errors():
            original = self.peek(message_id)
            originator = original.get("originator") or original["from"]
            default_to = original["from"] if originator == self.address else originator
            return iam.send_message(
                self.address,
                iam.resolve_names(_as_list(to)) or [default_to],
                iam.resolve_names(_as_list(cc)),
                subject or f"Re: {original['subject']}",
                body,
                iam.attachments(_as_list(attachments)),
                original.get("thread") or f"thr-{original['id']}",
                original["id"],
                originator,
                _as_list(references),
            )

    def archive(self, message_id: str) -> dict[str, str]:
        with translated_errors():
            source = iam.find_message(self.address, message_id)
            destination = iam.mailbox(self.address) / "archive" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        return {"id": destination.stem, "archived_to": str(destination)}

    def list_channels(self) -> list[str]:
        if not iam.CHATS.exists():
            return []
        return sorted(path.name for path in iam.CHATS.iterdir() if path.is_dir())

    def chat_tail(self, channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
        if lines < 1 or lines > 1000:
            raise IAMError("lines must be between 1 and 1000")
        with translated_errors():
            data = iam.load_chat(channel, date)
            iam.check_chat_access(data, str(self.project_root))
        return {**data, "messages": data["messages"][:lines]}

    def chat_post(
        self,
        channel: str,
        message: str,
        channel_type: str | None = None,
        with_agent: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        if channel_type not in (None, "public", "private"):
            raise IAMError("channel_type must be public or private")
        with translated_errors():
            data = iam.load_chat(channel, date)
            iam.check_chat_access(data, str(self.project_root))
            if channel_type:
                data["channelType"] = channel_type
            if data["channelType"] == "private":
                if not data.get("participants") and not with_agent:
                    raise IAMError("New private channels require with_agent.")
                participants = data.get("participants") or [self.address, iam.resolve_name(str(with_agent))]
                if self.address not in participants:
                    raise IAMError(f"{self.address} is not a participant in private channel {channel}.")
                if with_agent:
                    other = iam.resolve_name(with_agent)
                    if other not in participants:
                        raise IAMError(f"{other} is not a participant in private channel {channel}.")
                data["participants"] = participants
            stamp = iam.now()
            entry = {
                "agent": iam.display_name(self.address),
                "address": self.address,
                "timestamp": stamp,
                "message": message,
                "seen_by": {self.address: stamp},
            }
            data["messages"].insert(0, entry)
            iam.save_chat(data)
        return entry

    def chat_seen(self, channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
        if lines < 1 or lines > 1000:
            raise IAMError("lines must be between 1 and 1000")
        with translated_errors():
            data = iam.load_chat(channel, date)
            iam.check_chat_access(data, str(self.project_root))
            stamp = iam.now()
            count = min(lines, len(data["messages"]))
            for message in data["messages"][:lines]:
                message.setdefault("seen_by", {})[self.address] = stamp
            iam.save_chat(data)
        return {"channel": channel, "address": self.address, "marked_seen": count, "seen_at": stamp}
