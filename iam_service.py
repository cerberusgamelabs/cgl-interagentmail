"""Structured application service for InterAgentMail.

The original CLI is intentionally kept as a thin, stable user interface.  MCP
and event delivery use this module so they return Python data instead of
printing command-oriented text to stdout.
"""

from __future__ import annotations

import shutil
import uuid
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
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with translated_errors():
            return iam.send_message(
                self.address,
                self.resolve_mail_recipients(to),
                self.resolve_mail_recipients(_as_list(cc)) if cc else [],
                subject,
                body,
                iam.attachments(_as_list(attachments)),
                thread,
                originator=originator,
                refs=_as_list(references),
                priority=priority,
                metadata=metadata,
            )

    def resolve_mail_recipients(self, values: list[str]) -> list[str]:
        """Resolve mailbox names plus member-scoped `team:` and `leader:` aliases."""
        resolved: list[str] = []
        for value in values:
            for candidate in value.split(","):
                candidate = candidate.strip()
                prefix, separator, team_name = candidate.partition(":")
                if separator and prefix.lower() in {"team", "leader"}:
                    team = self.team_show(team_name)
                    if self.address not in team.get("members", []):
                        raise IAMError(f"Only members of {team_name} may address that team.")
                    targets = team.get("members", []) if prefix.lower() == "team" else team.get("leaders", [])
                    if not targets:
                        raise IAMError(f"Team {team_name} has no active leaders.")
                    resolved.extend(str(target) for target in targets if target != self.address)
                else:
                    resolved.append(iam.resolve_name(candidate))
        result = list(dict.fromkeys(resolved))
        if not result:
            raise IAMError("The resolved recipient list is empty.")
        return result

    def reply(
        self,
        message_id: str,
        body: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        attachments: list[str] | None = None,
        references: list[str] | None = None,
        priority: str = "normal",
    ) -> dict[str, Any]:
        with translated_errors():
            original = self.peek(message_id)
            originator = original.get("originator") or original["from"]
            default_to = original["from"] if originator == self.address else originator
            return iam.send_message(
                self.address,
                self.resolve_mail_recipients(_as_list(to)) if to else [default_to],
                self.resolve_mail_recipients(_as_list(cc)) if cc else [],
                subject or f"Re: {original['subject']}",
                body,
                iam.attachments(_as_list(attachments)),
                original.get("thread") or f"thr-{original['id']}",
                original["id"],
                originator,
                _as_list(references),
                priority,
            )

    def archive(self, message_id: str) -> dict[str, str]:
        with translated_errors():
            source = iam.find_message(self.address, message_id)
            destination = iam.mailbox(self.address) / "archive" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        return {"id": destination.stem, "archived_to": str(destination)}

    def _timer_dir(self) -> Path:
        path = iam.mailbox(self.address) / "timers"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _timer_path(self, timer_id: str) -> Path:
        safe_id = iam.safe_segment(timer_id, "timer id")
        return self._timer_dir() / f"{safe_id}.json"

    @staticmethod
    def _parse_due_at(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IAMError("due_at must be an ISO-8601 timestamp with a timezone.") from exc
        if parsed.tzinfo is None:
            raise IAMError("due_at must include a timezone.")
        return parsed.astimezone(timezone.utc)

    def timer_set(self, note: str, due_at: str, *, created_by: str | None = None, authorization: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(note, str) or not note.strip():
            raise IAMError("timer note is required")
        if len(note) > 8000:
            raise IAMError("timer note must be at most 8000 characters")
        due = self._parse_due_at(due_at)
        timer_id = f"timer-{uuid.uuid4()}"
        timer = {
            "id": timer_id,
            "target": self.address,
            "created_by": iam.safe_segment(created_by or self.address, "creator mailbox"),
            "created_at": iam.now(),
            "due_at": due.replace(microsecond=0).isoformat(),
            "note": note.strip(),
            "status": "scheduled",
            "authorization": authorization or {"kind": "self"},
            "delivery": {"attempts": 0, "last_attempt_at": None, "delivered_at": None},
        }
        iam.write_atomic(self._timer_path(timer_id), timer)
        return timer

    def timer_list(self, due_only: bool = False) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        for path in sorted(self._timer_dir().glob("timer-*.json")):
            try:
                timer = iam.load(path)
                due = self._parse_due_at(str(timer.get("due_at", "")))
            except (OSError, ValueError, IAMError):
                continue
            if timer.get("target") != self.address or timer.get("status") not in {"scheduled", "due"}:
                continue
            if due_only and due > now:
                continue
            if due <= now:
                timer["status"] = "due"
            rows.append(timer)
        return sorted(rows, key=lambda row: (str(row["due_at"]), str(row["id"])))

    def timer_clear(self, timer_id: str) -> dict[str, str]:
        path = self._timer_path(timer_id)
        if not path.exists():
            raise IAMError(f"Timer {timer_id} was not found.")
        path.unlink()
        return {"id": timer_id, "cleared": "true"}

    def timer_cancel(self, timer_id: str, *, actor: str | None = None) -> dict[str, str]:
        """Cancel before delivery; targets may always cancel their own timer, creators may cancel theirs."""
        path = self._timer_path(timer_id)
        if not path.exists():
            raise IAMError(f"Timer {timer_id} was not found.")
        timer = iam.load(path)
        caller = iam.safe_segment(actor or self.address, "actor mailbox")
        if caller not in {self.address, timer.get("created_by")}:
            raise IAMError("Only the timer recipient or creator may cancel this timer.")
        path.unlink()
        return {"id": timer_id, "cancelled": "true"}

    def timer_snooze(self, timer_id: str, due_at: str) -> dict[str, Any]:
        path = self._timer_path(timer_id)
        if not path.exists():
            raise IAMError(f"Timer {timer_id} was not found.")
        timer = iam.load(path)
        due = self._parse_due_at(due_at)
        timer["due_at"] = due.replace(microsecond=0).isoformat()
        timer["status"] = "scheduled"
        timer["delivery"] = {"attempts": 0, "last_attempt_at": None, "delivered_at": None}
        iam.write_atomic(path, timer)
        return timer

    def timer_mark_delivered(self, timer_id: str) -> None:
        path = self._timer_path(timer_id)
        if not path.exists():
            return
        timer = iam.load(path)
        delivery = timer.setdefault("delivery", {})
        delivery["attempts"] = int(delivery.get("attempts", 0)) + 1
        delivery["last_attempt_at"] = iam.now()
        delivery["delivered_at"] = iam.now()
        timer["status"] = "due"
        iam.write_atomic(path, timer)

    @staticmethod
    def _team_path(name: str) -> Path:
        return iam.TEAMS / f"{iam.safe_segment(name, 'team name')}.json"

    @staticmethod
    def team_list() -> list[dict[str, Any]]:
        if not iam.TEAMS.is_dir():
            return []
        return [iam.load(path) for path in sorted(iam.TEAMS.glob("*.json"))]

    @staticmethod
    def team_show(name: str) -> dict[str, Any]:
        path = IAMService._team_path(name)
        if not path.exists():
            raise IAMError(f"Team {name} was not found.")
        return iam.load(path)

    @staticmethod
    def team_create(name: str, members: list[str] | None = None) -> dict[str, Any]:
        path = IAMService._team_path(name)
        if path.exists():
            raise IAMError(f"Team {name} already exists.")
        normalized = list(dict.fromkeys(iam.resolve_names(members or [])))
        for member in normalized:
            if not iam.mailbox(member).is_dir():
                raise IAMError(f"Mailbox {member} does not exist.")
        team = {"id": f"team-{uuid.uuid4()}", "name": name, "members": normalized,
                "leaders": [], "grants": [], "created_at": iam.now(), "updated_at": iam.now()}
        iam.write_atomic(path, team)
        return team

    @staticmethod
    def team_add_member(name: str, member: str) -> dict[str, Any]:
        team = IAMService.team_show(name)
        address = iam.resolve_name(member)
        if not iam.mailbox(address).is_dir():
            raise IAMError(f"Mailbox {address} does not exist.")
        team["members"] = list(dict.fromkeys([*team.get("members", []), address]))
        team["updated_at"] = iam.now()
        iam.write_atomic(IAMService._team_path(name), team)
        return team

    @staticmethod
    def team_remove_member(name: str, member: str) -> dict[str, Any]:
        team = IAMService.team_show(name)
        address = iam.resolve_name(member)
        if address not in team.get("members", []):
            raise IAMError(f"{address} is not a member of {name}.")
        team["members"] = [item for item in team["members"] if item != address]
        team["leaders"] = [item for item in team.get("leaders", []) if item != address]
        team["grants"] = [item for item in team.get("grants", []) if item.get("subject") != address]
        team["updated_at"] = iam.now()
        iam.write_atomic(IAMService._team_path(name), team)
        return team

    @staticmethod
    def team_delete(name: str) -> dict[str, str]:
        path = IAMService._team_path(name)
        if not path.exists():
            raise IAMError(f"Team {name} was not found.")
        path.unlink()
        return {"name": name, "deleted": "true"}

    @staticmethod
    def team_set_leader(name: str, member: str, active: bool = True) -> dict[str, Any]:
        team = IAMService.team_show(name)
        address = iam.resolve_name(member)
        if address not in team.get("members", []):
            raise IAMError("A team leader must already be a team member.")
        leaders = set(team.get("leaders", []))
        if active:
            leaders.add(address)
        else:
            leaders.discard(address)
        team["leaders"] = sorted(leaders)
        team["updated_at"] = iam.now()
        iam.write_atomic(IAMService._team_path(name), team)
        return team

    @staticmethod
    def team_grant(name: str, leader: str, capability: str) -> dict[str, Any]:
        if capability not in {
            "timer.schedule_team", "timer.schedule", "team.view", "team.coordinate", "team.manage_members",
        }:
            raise IAMError("Unsupported team capability.")
        team = IAMService.team_show(name)
        address = iam.resolve_name(leader)
        if address not in team.get("leaders", []):
            raise IAMError("Only an active team leader can receive a team capability grant.")
        grant = {"subject": address, "capability": capability}
        team["grants"] = [row for row in team.get("grants", []) if row != grant] + [grant]
        team["updated_at"] = iam.now()
        iam.write_atomic(IAMService._team_path(name), team)
        return team

    def may_schedule_team(self, name: str) -> bool:
        team = self.team_show(name)
        return self.address in team.get("leaders", []) and {"subject": self.address, "capability": "timer.schedule_team"} in team.get("grants", [])

    def may_manage_team_members(self, name: str) -> bool:
        team = self.team_show(name)
        return self.address in team.get("leaders", []) and {
            "subject": self.address, "capability": "team.manage_members",
        } in team.get("grants", [])

    def team_add_member_as_leader(self, name: str, member: str) -> dict[str, Any]:
        if not self.may_manage_team_members(name):
            raise IAMError(f"{self.address} is not authorized to manage members of team {name}.")
        return self.team_add_member(name, member)

    def team_remove_member_as_leader(self, name: str, member: str) -> dict[str, Any]:
        if not self.may_manage_team_members(name):
            raise IAMError(f"{self.address} is not authorized to manage members of team {name}.")
        # A delegated manager cannot remove themselves and thereby invalidate
        # the provenance of an in-flight administrative operation.
        target = iam.resolve_name(member)
        if target == self.address:
            raise IAMError("A delegated leader cannot remove themselves; ask a human administrator.")
        if target in self.team_show(name).get("leaders", []):
            raise IAMError("A delegated leader cannot remove another active leader; ask a human administrator.")
        return self.team_remove_member(name, member)

    def timer_set_for(self, target: str, note: str, due_at: str) -> dict[str, Any]:
        recipient = iam.resolve_name(target)
        if not iam.mailbox(recipient).is_dir():
            raise IAMError(f"Mailbox {recipient} does not exist.")
        for team in self.team_list():
            grant = {"subject": self.address, "capability": "timer.schedule"}
            if (recipient in team.get("members", []) and self.address in team.get("leaders", [])
                    and grant in team.get("grants", [])):
                return IAMService.for_mailbox(recipient).timer_set(
                    note, due_at, created_by=self.address,
                    authorization={"kind": "direct", "team_id": team["id"], "team_name": team["name"]},
                )
        raise IAMError(f"{self.address} is not authorized to schedule a timer for {recipient}.")

    def timer_set_team(self, name: str, note: str, due_at: str) -> list[dict[str, Any]]:
        team = self.team_show(name)
        if not self.may_schedule_team(name):
            raise IAMError(f"{self.address} is not authorized to schedule timers for team {name}.")
        members = list(team.get("members", []))
        if not members:
            raise IAMError(f"Team {name} has no members.")
        # Validate before mutating so an invalid roster cannot leave a partial fan-out.
        for member in members:
            if not iam.mailbox(member).is_dir():
                raise IAMError(f"Team {name} has an unavailable mailbox: {member}.")
        authorization = {"kind": "team", "team_id": team["id"], "team_name": team["name"]}
        return [IAMService.for_mailbox(member).timer_set(note, due_at, created_by=self.address, authorization=authorization)
                for member in members]

    def list_channels(self) -> list[str]:
        if not iam.CHATS.exists():
            return []
        return sorted(path.name for path in iam.CHATS.iterdir() if path.is_dir())

    def _check_chat_access(self, data: dict[str, Any]) -> None:
        if data.get("channelType") == "private" and self.address not in data.get("participants", []):
            raise IAMError(f"{self.address} is not a participant in private channel {data['channel']}.")

    def chat_tail(self, channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
        if lines < 1 or lines > 1000:
            raise IAMError("lines must be between 1 and 1000")
        with translated_errors():
            data = iam.load_chat(channel, date)
            self._check_chat_access(data)
        return {**data, "messages": data["messages"][:lines]}

    def chat_post(
        self,
        message: str,
        date: str | None = None,
    ) -> dict[str, Any]:
        with translated_errors():
            state = self.chat_subscription()
            if not state:
                raise IAMError("No active chat channel. Join a channel before posting.")
            data = iam.load_chat(state["channel"], date)
            self._check_chat_access(data)
            entry = self._chat_entry(message)
            data["messages"].insert(0, entry)
            iam.save_chat(data)
        return entry

    def _chat_entry(self, message: str, *, entry_type: str = "message") -> dict[str, Any]:
        stamp = iam.now()
        return {
            "id": f"chat-{uuid.uuid4()}",
            "type": entry_type,
            "agent": iam.display_name(self.address),
            "address": self.address,
            "timestamp": stamp,
            "message": message,
            "seen_by": {self.address: stamp},
        }

    def _chat_subscription_path(self) -> Path:
        return iam.mailbox(self.address) / ".chat-subscription.json"

    def chat_subscription(self) -> dict[str, Any] | None:
        path = self._chat_subscription_path()
        if not path.exists():
            return None
        with translated_errors():
            state = iam.load(path)
        if not isinstance(state, dict) or not isinstance(state.get("channel"), str):
            raise IAMError("Chat subscription state is invalid.")
        state.setdefault("delivered", [])
        return state

    def _save_chat_subscription(self, state: dict[str, Any]) -> None:
        iam.write_atomic(self._chat_subscription_path(), state)

    def _chat_entries(self, channel: str) -> list[dict[str, Any]]:
        directory = iam.CHATS / channel
        entries: list[dict[str, Any]] = []
        if not directory.is_dir():
            return entries
        for path in sorted(directory.glob("*.json")):
            data = iam.load(path)
            self._check_chat_access(data)
            # Files store newest-first; delivery is chronological.
            entries.extend(reversed(data.get("messages", [])))
        return [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)]

    def chat_join(
        self,
        channel: str,
        channel_type: str | None = None,
        with_agent: str | None = None,
    ) -> dict[str, Any]:
        if channel_type not in (None, "public", "private"):
            raise IAMError("channel_type must be public or private")
        with translated_errors():
            data = iam.load_chat(channel)
            if channel_type:
                if data.get("messages") or data.get("participants"):
                    if data.get("channelType") != channel_type:
                        raise IAMError(f"Chat channel {channel} already exists as {data['channelType']}.")
                else:
                    data["channelType"] = channel_type
            if data["channelType"] == "private":
                if not data.get("participants") and not with_agent:
                    raise IAMError("Joining a new private channel requires with_agent.")
                participants = data.get("participants") or [self.address, iam.resolve_name(str(with_agent))]
                if self.address not in participants:
                    raise IAMError(f"{self.address} is not a participant in private channel {channel}.")
                if with_agent:
                    other = iam.resolve_name(with_agent)
                    if other not in participants:
                        raise IAMError(f"{other} is not a participant in private channel {channel}.")
                data["participants"] = participants
            self._check_chat_access(data)
            previous = self.chat_subscription()
            if previous and previous["channel"] == channel:
                return {"channel": channel, "changed": False, "previous_channel": None}
            if previous:
                prior = iam.load_chat(previous["channel"])
                self._check_chat_access(prior)
                prior["messages"].insert(0, self._chat_entry(f"{iam.display_name(self.address)} has left the chat.", entry_type="system"))
                iam.save_chat(prior)
            data["messages"].insert(0, self._chat_entry(f"{iam.display_name(self.address)} has joined the chat.", entry_type="system"))
            iam.save_chat(data)
            delivered = [str(entry["id"]) for entry in self._chat_entries(channel)]
            self._save_chat_subscription({
                "channel": channel,
                "subscribed_at": iam.now(),
                "delivered": delivered[-1000:],
            })
        return {"channel": channel, "changed": True, "previous_channel": previous["channel"] if previous else None}

    def chat_leave(self) -> dict[str, Any]:
        with translated_errors():
            state = self.chat_subscription()
            if not state:
                raise IAMError("No active chat channel. Join a channel before leaving it.")
            channel = state["channel"]
            data = iam.load_chat(channel)
            self._check_chat_access(data)
            data["messages"].insert(0, self._chat_entry(f"{iam.display_name(self.address)} has left the chat.", entry_type="system"))
            iam.save_chat(data)
            self._chat_subscription_path().unlink(missing_ok=True)
        return {"channel": channel, "left": True}

    def chat_updates(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        state = self.chat_subscription()
        if not state:
            return None, []
        with translated_errors():
            entries = self._chat_entries(state["channel"])
        delivered = {str(value) for value in state.get("delivered", [])}
        own_ids = [str(entry["id"]) for entry in entries if entry.get("address") == self.address]
        if own_ids:
            state["delivered"] = list(dict.fromkeys([*state.get("delivered", []), *own_ids]))[-1000:]
            self._save_chat_subscription(state)
            delivered.update(own_ids)
        return state, [entry for entry in entries if str(entry["id"]) not in delivered]

    def chat_mark_delivered(self, state: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        state["delivered"] = list(dict.fromkeys([
            *state.get("delivered", []), *(str(entry["id"]) for entry in entries),
        ]))[-1000:]
        self._save_chat_subscription(state)

    def chat_seen(self, channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
        if lines < 1 or lines > 1000:
            raise IAMError("lines must be between 1 and 1000")
        with translated_errors():
            data = iam.load_chat(channel, date)
            self._check_chat_access(data)
            stamp = iam.now()
            count = min(lines, len(data["messages"]))
            for message in data["messages"][:lines]:
                message.setdefault("seen_by", {})[self.address] = stamp
            iam.save_chat(data)
        return {"channel": channel, "address": self.address, "marked_seen": count, "seen_at": stamp}
