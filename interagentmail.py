#!/usr/bin/env python3
"""Tiny file-backed mailboxes for local Codex agents."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def default_data_root() -> Path:
    configured = os.environ.get("INTERAGENTMAIL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    # Preserve portable/source checkouts that already contain mailbox data.
    if (PACKAGE_ROOT / "mailboxes").exists() or (PACKAGE_ROOT / "config.json").exists():
        return PACKAGE_ROOT
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "InterAgentMail"
    return Path.home() / ".local" / "share" / "interagentmail"


ROOT = default_data_root()
MAILBOXES = ROOT / "mailboxes"
CHATS = ROOT / "chats"
TEAMS = ROOT / "teams"
CONFIG = ROOT / "config.json"


SAFE_MESSAGE_PREFIX = re.compile(r"^[A-Za-z0-9-]+$")
MESSAGE_PRIORITIES = {"normal", "urgent"}


def safe_segment(value: str, label: str) -> str:
    """Reject path traversal while preserving human-readable mailbox names."""
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise SystemExit(f"Invalid {label}: {value!r}")
    return value


def address(project_root: str | None) -> str:
    root = Path(project_root or Path.cwd()).resolve()
    name = root.name
    if not name:
        raise SystemExit("Could not derive mailbox name from project root.")
    return name


def mailbox(name: str) -> Path:
    return MAILBOXES / safe_segment(name, "mailbox address")


def ensure_box(name: str) -> Path:
    box = mailbox(name)
    for child in ("inbox", "sent", "archive"):
        (box / child).mkdir(parents=True, exist_ok=True)
    profile = box / "profile.json"
    if not profile.exists():
        profile.write_text(json.dumps({"address": name, "display_name": name}, indent=2) + "\n", encoding="utf-8")
    else:
        data = load(profile)
        if "display_name" not in data:
            data["display_name"] = data.get("address", name)
            write_atomic(profile, data)
    return box


def message_path(folder: Path, msg_id: str) -> Path:
    return folder / f"{msg_id}.json"


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def names(values: list[str] | None) -> list[str]:
    seen = set()
    result = []
    for value in values or []:
        for name in value.split(","):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


def attachments(values: list[str] | None) -> list[dict]:
    return [{"path": path} for path in names(values)]


def profile(name: str) -> dict:
    ensure_box(name)
    path = mailbox(name) / "profile.json"
    data = load(path)
    data.setdefault("address", name)
    data.setdefault("display_name", data["address"])
    return data


def save_profile(name: str, data: dict) -> None:
    data["address"] = name
    data.setdefault("display_name", name)
    write_atomic(mailbox(name) / "profile.json", data)


def display_name(name: str) -> str:
    return profile(name).get("display_name") or name


def resolve_name(value: str) -> str:
    if mailbox(value).exists():
        return value
    matches = []
    if MAILBOXES.exists():
        for box in (path for path in MAILBOXES.iterdir() if path.is_dir()):
            if display_name(box.name).lower() == value.lower():
                matches.append(box.name)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"Display name is ambiguous: {value}")
    return value


def resolve_names(values: list[str] | None) -> list[str]:
    return names([resolve_name(name) for name in names(values)])


def configured_root() -> Path | None:
    if not CONFIG.exists():
        return None
    return Path(load(CONFIG).get("project_root", "")).expanduser().resolve()


def project_dir(name: str, sender_root: str | None) -> Path:
    base = configured_root() or (Path(sender_root).resolve().parent if sender_root else ROOT.parent)
    path = base / name
    if not path.exists():
        raise SystemExit(f"Could not find project folder for {name}: {path}")
    return path


def normalize_priority(priority: str | None) -> str:
    value = "normal" if priority is None else str(priority).strip().lower()
    if value not in MESSAGE_PRIORITIES:
        raise SystemExit("priority must be normal or urgent")
    return value


def send_message(
    sender: str,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attach: list[dict] | None = None,
    thread: str | None = None,
    reply_to: str | None = None,
    originator: str | None = None,
    refs: list[str] | None = None,
    priority: str | None = "normal",
    metadata: dict | None = None,
) -> dict:
    if not to:
        raise SystemExit("At least one --to recipient is required.")
    priority = normalize_priority(priority)
    for name in [sender, *to, *cc]:
        ensure_box(name)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    msg_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    payload = {
        "id": msg_id,
        "thread": thread or f"thr-{uuid.uuid4().hex[:8]}",
        "reply_to": reply_to,
        "originator": originator or sender,
        "from": sender,
        "from_display": display_name(sender),
        "to": to,
        "to_display": [display_name(name) for name in to],
        "cc": cc,
        "cc_display": [display_name(name) for name in cc],
        "subject": subject,
        "body": body,
        "priority": priority,
        "attachments": attach or [],
        "references": names(refs),
        "signature": display_name(sender),
        "created_at": now,
        "read_at": None,
    }
    if metadata:
        if not isinstance(metadata, dict):
            raise SystemExit("message metadata must be an object")
        payload["metadata"] = metadata
    for recipient in names([*to, *cc]):
        write_atomic(message_path(mailbox(recipient) / "inbox", msg_id), payload)
    write_atomic(message_path(mailbox(sender) / "sent", msg_id), payload)
    return payload


def cmd_init(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    ensure_box(name)
    print(name)


def cmd_whoami(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    print(f"{display_name(name)} <{name}>")


def cmd_profile(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    data = profile(name)
    if args.display_name:
        data["display_name"] = args.display_name
        save_profile(name, data)
    print(json.dumps(data, indent=2, sort_keys=True))


def cmd_root(args: argparse.Namespace) -> None:
    if args.path:
        path = Path(args.path).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Project root does not exist: {path}")
        # `config.json` also owns IAM's registered-project registry.  Root is
        # legacy request-command metadata, so changing it must never erase
        # active project registrations or their delivery configuration.
        try:
            data = load(CONFIG) if CONFIG.exists() else {}
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["project_root"] = str(path)
        write_atomic(CONFIG, data)
    root = configured_root()
    print(root if root else "No project root configured.")


def cmd_send(args: argparse.Namespace) -> None:
    from iam_service import IAMService
    payload = IAMService(args.project_root).send(
        args.to, args.subject, args.body, args.cc, args.attach, args.ref,
        args.thread, args.originator, getattr(args, "priority", "normal"),
    )
    print(payload["id"])


def replies(name: str, thread: str, sender: str) -> list[dict]:
    return [msg for msg in (load(path) for path in messages(name, "inbox")) if msg.get("thread") == thread and msg.get("from") != sender]


def cmd_request(args: argparse.Namespace) -> None:
    if os.environ.get("INTERAGENTMAIL_WORKER"):
        raise SystemExit("Refusing to spawn Codex from an InterAgentMail worker.")
    sender = address(args.project_root)
    recipients = resolve_names(args.to)
    if len(recipients) != 1:
        raise SystemExit("request requires exactly one --to recipient.")
    payload = send_message(
        sender,
        recipients,
        resolve_names(args.cc),
        args.subject,
        args.body,
        attachments(args.attach),
        args.thread,
        originator=args.originator,
        refs=args.ref,
        priority=getattr(args, "priority", "normal"),
    )
    recipient = recipients[0]
    recipient_root = project_dir(recipient, args.project_root)
    prompt = (
        f"Use the InterAgentMail skill. Read message {payload['id']} for project {recipient}. "
        "Do what the message asks, reply to it, then exit. "
        "Do not use InterAgentMail request or spawn another Codex instance."
    )
    env = os.environ.copy()
    env["INTERAGENTMAIL_WORKER"] = "1"
    codex = shutil.which("codex.cmd") or shutil.which("codex")
    if not codex:
        raise SystemExit("Could not find codex on PATH.")
    subprocess.run([codex, "exec", "--skip-git-repo-check", "-C", str(recipient_root), prompt], env=env, check=False)

    for attempt in range(args.tries):
        found = replies(sender, payload["thread"], sender)
        if found:
            for msg in found:
                print(f"{msg['id']} from {msg['from']}: {msg['subject']}")
            return
        if attempt < args.tries - 1:
            time.sleep(args.wait_seconds)
    print(f"No reply yet for {payload['id']}; ask me to check later.")


def messages(name: str, folder: str) -> list[Path]:
    ensure_box(name)
    return sorted((mailbox(name) / folder).glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_inbox(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    rows = []
    for path in messages(name, "inbox"):
        msg = load(path)
        status = "read" if msg.get("read_at") else "new"
        thread = msg.get("thread", "no-thread")
        sender = msg.get("from_display") or msg["from"]
        priority = msg.get("priority", "normal")
        rows.append(f"{msg['id']} [{status}] [{priority}] {thread} from {sender} <{msg['from']}>: {msg['subject']}")
    print("\n".join(rows) if rows else "No mail.")


def cmd_list(_: argparse.Namespace) -> None:
    if not MAILBOXES.exists():
        print("No mailboxes.")
        return
    rows = []
    for box in sorted(path for path in MAILBOXES.iterdir() if path.is_dir()):
        profile = box / "profile.json"
        name = box.name
        if profile.exists():
            try:
                data = load(profile)
                name = f"{data.get('display_name') or name} <{data.get('address') or name}>"
            except json.JSONDecodeError:
                pass
        rows.append(name)
    print("\n".join(rows) if rows else "No mailboxes.")


def find_message(name: str, msg_id: str) -> Path:
    if not SAFE_MESSAGE_PREFIX.fullmatch(msg_id):
        raise SystemExit(f"Invalid message id or prefix: {msg_id!r}")
    matches = list((mailbox(name) / "inbox").glob(f"{msg_id}*.json"))
    if len(matches) != 1:
        raise SystemExit(f"Expected one inbox message matching {msg_id}, found {len(matches)}.")
    return matches[0]


def cmd_read(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    path = find_message(name, args.id)
    msg = load(path)
    if not msg.get("read_at"):
        msg["read_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        write_atomic(path, msg)
    print(json.dumps(msg, indent=2, sort_keys=True))


def cmd_archive(args: argparse.Namespace) -> None:
    name = address(args.project_root)
    path = find_message(name, args.id)
    archive = mailbox(name) / "archive" / path.name
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(archive))
    print(archive)


def chat_path(channel: str, date: str | None = None) -> Path:
    safe_segment(channel, "chat channel")
    if date is not None:
        safe_segment(date, "chat date")
    day = date or datetime.now().astimezone().date().isoformat()
    return CHATS / channel / f"{day}.json"


def load_chat(channel: str, date: str | None = None) -> dict:
    path = chat_path(channel, date)
    if path.exists():
        data = load(path)
        data.setdefault("channelType", data.get("channel_type", "public"))
        data.setdefault("participants", [])
        data.setdefault("messages", [])
        return data
    return {"channel": channel, "channelType": "public", "participants": [], "date": path.stem, "messages": []}


def save_chat(data: dict) -> None:
    write_atomic(chat_path(data["channel"], data["date"]), data)


def check_chat_access(data: dict, project_root: str | None) -> str | None:
    if not project_root:
        return None
    name = address(project_root)
    if data.get("channelType") == "private" and name not in data.get("participants", []):
        raise SystemExit(f"{name} is not a participant in private channel {data['channel']}.")
    return name


def cmd_chat(args: argparse.Namespace) -> None:
    from iam_service import IAMService
    mailbox_address = getattr(args, "mailbox", None)
    service = IAMService.for_mailbox(mailbox_address) if mailbox_address else IAMService(args.project_root)
    if args.chat_command == "post":
        entry = service.chat_post(args.message, args.date)
        print(f"{entry['agent']}({entry['timestamp']}): {entry['message']}")
        return

    if args.chat_command in {"join", "leave", "subscriptions"}:
        if args.chat_command == "join":
            result = service.chat_join(args.channel, args.channel_type, args.with_agent)
            detail = f"; left {result['previous_channel']}" if result.get("previous_channel") else ""
            print(f"Subscribed {display_name(service.address)} to {result['channel']}{detail}.")
            return
        if args.chat_command == "leave":
            result = service.chat_leave()
            print(f"Unsubscribed {display_name(service.address)} from {result['channel']}.")
            return
        state = service.chat_subscription()
        print(state["channel"] if state else "No active chat subscription.")
        return

    if args.chat_command == "tail":
        data = service.chat_tail(args.channel, args.lines, args.date)
        rows = []
        for msg in data["messages"][:args.lines]:
            seen = ", ".join(display_name(name) for name in msg.get("seen_by", {}))
            rows.append(f"{msg['agent']}({msg['timestamp']}): {msg['message']}" + (f"\n  Seen by: {seen}" if seen else ""))
        print("\n".join(rows) if rows else "No chat messages.")
        return

    if args.chat_command == "seen":
        result = service.chat_seen(args.channel, args.lines, args.date)
        print(f"{display_name(service.address)} marked {result['marked_seen']} seen.")
        return

    raise SystemExit(f"Unknown chat command: {args.chat_command}")


def _timer_due_at(args: argparse.Namespace) -> str:
    if bool(getattr(args, "in_duration", None)) == bool(getattr(args, "at", None)):
        raise SystemExit("Specify exactly one of --in or --at.")
    if getattr(args, "at", None):
        value = args.at.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise SystemExit("--at must be ISO-8601 with a timezone, for example 2026-08-31T18:30:00-04:00") from exc
        if parsed.tzinfo is None:
            raise SystemExit("--at must include a timezone.")
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    match = re.fullmatch(r"(\d+)([smhd])", args.in_duration.strip(), re.IGNORECASE)
    if not match:
        raise SystemExit("--in must be a positive duration such as 15m, 2h, or 1d.")
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount < 1:
        raise SystemExit("--in must be positive.")
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def cmd_timer(args: argparse.Namespace) -> None:
    from iam_service import IAMService
    service = IAMService.for_mailbox(args.mailbox) if getattr(args, "mailbox", None) else IAMService(args.project_root)
    if args.timer_command == "set":
        due_at = _timer_due_at(args)
        if args.team and args.to:
            raise SystemExit("Use either --team or --to, not both.")
        if args.team:
            timers = service.timer_set_team(args.team, args.note, due_at)
            print("\n".join(f"{timer['target']}: {timer['id']} due {timer['due_at']}" for timer in timers))
        elif args.to:
            timer = service.timer_set_for(args.to, args.note, due_at)
            print(f"{timer['target']}: {timer['id']} due {timer['due_at']}")
        else:
            timer = service.timer_set(args.note, due_at)
            print(f"{timer['id']} due {timer['due_at']}")
    elif args.timer_command == "list":
        rows = service.timer_list(args.due_only)
        print("\n".join(f"{row['id']} | {row['due_at']} | {row['note']}" for row in rows) or "No outstanding timers.")
    elif args.timer_command == "clear":
        service.timer_clear(args.id)
        print(f"Cleared {args.id}.")
    elif args.timer_command == "cancel":
        service.timer_cancel(args.id)
        print(f"Cancelled {args.id}.")
    elif args.timer_command == "snooze":
        timer = service.timer_snooze(args.id, _timer_due_at(args))
        print(f"{timer['id']} due {timer['due_at']}")
    else:
        raise SystemExit(f"Unknown timer command: {args.timer_command}")


def cmd_team(args: argparse.Namespace) -> None:
    from iam_service import IAMService
    if args.team_command == "create":
        team = IAMService.team_create(args.name, args.member)
    elif args.team_command == "show":
        team = IAMService.team_show(args.name)
    elif args.team_command == "list":
        print("\n".join(team["name"] for team in IAMService.team_list()) or "No teams.")
        return
    elif args.team_command == "member-add":
        team = IAMService.team_add_member(args.name, args.member)
    elif args.team_command == "member-remove":
        team = IAMService.team_remove_member(args.name, args.member)
    elif args.team_command == "leader-add":
        team = IAMService.team_set_leader(args.name, args.member, True)
    elif args.team_command == "leader-remove":
        team = IAMService.team_set_leader(args.name, args.member, False)
    elif args.team_command == "grant":
        team = IAMService.team_grant(args.name, args.leader, args.capability)
    elif args.team_command == "delete":
        print(json.dumps(IAMService.team_delete(args.name), indent=2, sort_keys=True))
        return
    else:
        raise SystemExit(f"Unknown team command: {args.team_command}")
    print(json.dumps(team, indent=2, sort_keys=True))


def cmd_reply(args: argparse.Namespace) -> None:
    from iam_service import IAMService
    payload = IAMService(args.project_root).reply(
        args.id, args.body, args.to, args.cc, args.subject, args.attach, args.ref,
        getattr(args, "priority", "normal"),
    )
    print(payload["id"])


def cmd_self_test(_: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        global MAILBOXES
        global CONFIG
        global CHATS
        old = MAILBOXES
        old_config = CONFIG
        old_chats = CHATS
        MAILBOXES = Path(tmp) / "mailboxes"
        CONFIG = Path(tmp) / "config.json"
        CHATS = Path(tmp) / "chats"
        try:
            a = Path(tmp) / "NexusGuild"
            b = Path(tmp) / "AegisGrid"
            c = Path(tmp) / "Security"
            a.mkdir()
            b.mkdir()
            c.mkdir()
            cmd_root(argparse.Namespace(path=tmp))
            assert project_dir("AegisGrid", None) == b
            cmd_profile(argparse.Namespace(project_root=str(a), display_name="Nexus Guild"))
            cmd_profile(argparse.Namespace(project_root=str(b), display_name="Aegis Grid"))
            cmd_send(
                argparse.Namespace(
                    project_root=str(a),
                    to=["Aegis Grid"],
                    cc=["Security,Architecture"],
                    subject="Ping",
                    body="Hello",
                    attach=["reports/security.md"],
                    thread=None,
                    originator=None,
                    ref=["old-message-id"],
                )
            )
            inbox = messages("AegisGrid", "inbox")
            assert len(inbox) == 1
            msg = load(inbox[0])
            assert msg["from"] == "NexusGuild"
            assert msg["from_display"] == "Nexus Guild"
            assert msg["signature"] == "Nexus Guild"
            assert msg["originator"] == "NexusGuild"
            assert msg["to"] == ["AegisGrid"]
            assert msg["to_display"] == ["Aegis Grid"]
            assert msg["cc"] == ["Security", "Architecture"]
            assert msg["subject"] == "Ping"
            assert msg["attachments"] == [{"path": "reports/security.md"}]
            assert msg["references"] == ["old-message-id"]
            assert msg["thread"].startswith("thr-")
            assert (MAILBOXES / "NexusGuild" / "sent" / inbox[0].name).exists()
            cmd_reply(argparse.Namespace(project_root=str(b), id=msg["id"], to=["Security"], cc=None, subject=None, body="Can you review this?", attach=None, ref=["another-message-id"]))
            forwarded = next(item for item in (load(path) for path in messages("Security", "inbox")) if item.get("reply_to") == msg["id"])
            assert forwarded["to"] == ["Security"]
            assert forwarded["references"] == ["another-message-id"]
            assert forwarded["originator"] == "NexusGuild"
            assert forwarded["thread"] == msg["thread"]
            cmd_reply(argparse.Namespace(project_root=str(c), id=forwarded["id"], to=None, cc=None, subject=None, body="Looks clean.", attach=None, ref=None))
            reply = load(messages("NexusGuild", "inbox")[0])
            assert reply["to"] == ["NexusGuild"]
            assert reply["reply_to"] == forwarded["id"]
            assert reply["thread"] == msg["thread"]
            cmd_reply(argparse.Namespace(project_root=str(a), id=reply["id"], to=None, cc=None, subject=None, body="Thanks.", attach=None, ref=None))
            reply_back = next(item for item in (load(path) for path in messages("Security", "inbox")) if item.get("reply_to") == reply["id"])
            assert reply_back["to"] == ["Security"]
            assert reply_back["originator"] == "NexusGuild"
            assert reply_back["thread"] == msg["thread"]
            os.environ["INTERAGENTMAIL_WORKER"] = "1"
            try:
                try:
                    cmd_request(argparse.Namespace(project_root=str(a), to=["AegisGrid"], cc=None, subject="Loop", body="No", attach=None, thread=None, originator=None, tries=1, wait_seconds=0, ref=None))
                    raise AssertionError("worker request did not fail")
                except SystemExit:
                    pass
            finally:
                os.environ.pop("INTERAGENTMAIL_WORKER", None)
            cmd_chat(argparse.Namespace(chat_command="join", channel="reviewers", project_root=str(a), channel_type=None, with_agent=None))
            cmd_chat(argparse.Namespace(chat_command="post", project_root=str(a), message="Status update", date=None))
            cmd_chat(argparse.Namespace(chat_command="seen", channel="reviewers", project_root=str(b), date=None, lines=10))
            chat = load_chat("reviewers")
            assert chat["messages"][0]["message"] == "Status update"
            assert "NexusGuild" in chat["messages"][0]["seen_by"]
            assert "AegisGrid" in chat["messages"][0]["seen_by"]
            cmd_chat(argparse.Namespace(chat_command="join", channel="dm-nexus-aegis", project_root=str(a), channel_type="private", with_agent="Aegis Grid"))
            cmd_chat(argparse.Namespace(chat_command="post", project_root=str(a), message="Private note", date=None))
            private = load_chat("dm-nexus-aegis")
            assert private["channelType"] == "private"
            assert private["participants"] == ["NexusGuild", "AegisGrid"]
            cmd_chat(argparse.Namespace(chat_command="tail", channel="dm-nexus-aegis", project_root=str(b), lines=10, date=None))
            try:
                cmd_chat(argparse.Namespace(chat_command="tail", channel="dm-nexus-aegis", project_root=str(c), lines=10, date=None))
                raise AssertionError("private chat access did not fail")
            except SystemExit:
                pass
        finally:
            MAILBOXES = old
            CONFIG = old_config
            CHATS = old_chats
    print("ok")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="File-backed local mail for Codex project agents.")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("init", "whoami", "inbox"):
        sp = sub.add_parser(name)
        sp.add_argument("--project-root")
        sp.set_defaults(func=globals()[f"cmd_{name}"])

    sp = sub.add_parser("profile")
    sp.add_argument("--project-root")
    sp.add_argument("--display-name")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("list")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("root")
    sp.add_argument("path", nargs="?")
    sp.set_defaults(func=cmd_root)

    sp = sub.add_parser("chat")
    chat_sub = sp.add_subparsers(dest="chat_command", required=True)
    post = chat_sub.add_parser("post")
    post.add_argument("--project-root")
    post.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    post.add_argument("--message", required=True)
    post.add_argument("--date")
    post.set_defaults(func=cmd_chat)

    tail = chat_sub.add_parser("tail")
    tail.add_argument("channel")
    tail.add_argument("--project-root")
    tail.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    tail.add_argument("--lines", type=int, default=10)
    tail.add_argument("--date")
    tail.set_defaults(func=cmd_chat)

    seen = chat_sub.add_parser("seen")
    seen.add_argument("channel")
    seen.add_argument("--project-root")
    seen.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    seen.add_argument("--lines", type=int, default=10)
    seen.add_argument("--date")
    seen.set_defaults(func=cmd_chat)

    join = chat_sub.add_parser("join")
    join.add_argument("channel")
    join.add_argument("--project-root")
    join.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    join.add_argument("--type", choices=("public", "private"), dest="channel_type")
    join.add_argument("--with", dest="with_agent", help="Second participant when creating a private channel.")
    join.set_defaults(func=cmd_chat)

    leave = chat_sub.add_parser("leave")
    leave.add_argument("--project-root")
    leave.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    leave.set_defaults(func=cmd_chat)

    subscriptions = chat_sub.add_parser("subscriptions")
    subscriptions.add_argument("--project-root")
    subscriptions.add_argument("--mailbox", help="Use a human or project mailbox address instead of a project folder.")
    subscriptions.set_defaults(func=cmd_chat)

    sp = sub.add_parser("timer", help="Create and manage temporary IAM reminders.")
    timer_sub = sp.add_subparsers(dest="timer_command", required=True)
    for timer_name in ("set", "snooze"):
        timer = timer_sub.add_parser(timer_name)
        if timer_name == "set":
            timer.add_argument("--note", required=True)
            timer.add_argument("--team", help="Authorized team to expand into independent recipient timers.")
            timer.add_argument("--to", help="One explicitly authorized teammate mailbox or display name.")
        else:
            timer.add_argument("id")
        timer.add_argument("--in", dest="in_duration")
        timer.add_argument("--at")
        timer.add_argument("--project-root")
        timer.add_argument("--mailbox")
        timer.set_defaults(func=cmd_timer)
    timer = timer_sub.add_parser("list")
    timer.add_argument("--due-only", action="store_true")
    timer.add_argument("--project-root")
    timer.add_argument("--mailbox")
    timer.set_defaults(func=cmd_timer)
    timer = timer_sub.add_parser("clear")
    timer.add_argument("id")
    timer.add_argument("--project-root")
    timer.add_argument("--mailbox")
    timer.set_defaults(func=cmd_timer)
    timer = timer_sub.add_parser("cancel")
    timer.add_argument("id")
    timer.add_argument("--project-root")
    timer.add_argument("--mailbox")
    timer.set_defaults(func=cmd_timer)

    sp = sub.add_parser("team", help="Human-administered local IAM teams and leadership grants.")
    team_sub = sp.add_subparsers(dest="team_command", required=True)
    team = team_sub.add_parser("create")
    team.add_argument("name")
    team.add_argument("--member", action="append", default=[])
    team.set_defaults(func=cmd_team)
    team = team_sub.add_parser("show")
    team.add_argument("name")
    team.set_defaults(func=cmd_team)
    team = team_sub.add_parser("list")
    team.set_defaults(func=cmd_team)
    for command in ("member-add", "member-remove", "leader-add", "leader-remove"):
        team = team_sub.add_parser(command)
        team.add_argument("name")
        team.add_argument("member")
        team.set_defaults(func=cmd_team)
    team = team_sub.add_parser("grant")
    team.add_argument("name")
    team.add_argument("--leader", required=True)
    team.add_argument("--capability", required=True,
                      choices=("timer.schedule_team", "timer.schedule", "team.view", "team.coordinate", "team.manage_members"))
    team.set_defaults(func=cmd_team)
    team = team_sub.add_parser("delete")
    team.add_argument("name")
    team.set_defaults(func=cmd_team)

    sp = sub.add_parser("send")
    sp.add_argument("--project-root")
    sp.add_argument("--to", action="append", required=True, help="Recipient project name. Repeat or comma-separate.")
    sp.add_argument("--cc", action="append", help="CC project name. Repeat or comma-separate.")
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--attach", action="append", help="Referenced file path. Repeat or comma-separate.")
    sp.add_argument("--ref", action="append", help="Related message id. Repeat or comma-separate.")
    sp.add_argument("--thread", help="Existing thread id. Omit to create a new thread.")
    sp.add_argument("--originator", help="Original requester for an existing thread. Defaults to sender.")
    sp.add_argument("--priority", choices=("normal", "urgent"), default="normal")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("request")
    sp.add_argument("--project-root")
    sp.add_argument("--to", action="append", required=True, help="One recipient project name.")
    sp.add_argument("--cc", action="append", help="CC project name. Repeat or comma-separate.")
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--attach", action="append", help="Referenced file path. Repeat or comma-separate.")
    sp.add_argument("--ref", action="append", help="Related message id. Repeat or comma-separate.")
    sp.add_argument("--thread", help="Existing thread id. Omit to create a new thread.")
    sp.add_argument("--originator", help="Original requester for an existing thread. Defaults to sender.")
    sp.add_argument("--priority", choices=("normal", "urgent"), default="normal")
    sp.add_argument("--wait-seconds", type=int, default=30)
    sp.add_argument("--tries", type=int, default=3)
    sp.set_defaults(func=cmd_request)

    sp = sub.add_parser("reply")
    sp.add_argument("id")
    sp.add_argument("--project-root")
    sp.add_argument("--to", action="append", help="Override recipient. Defaults to original sender.")
    sp.add_argument("--cc", action="append", help="CC project name. Repeat or comma-separate.")
    sp.add_argument("--subject")
    sp.add_argument("--body", required=True)
    sp.add_argument("--attach", action="append", help="Referenced file path. Repeat or comma-separate.")
    sp.add_argument("--ref", action="append", help="Related message id. Repeat or comma-separate.")
    sp.add_argument("--priority", choices=("normal", "urgent"), default="normal")
    sp.set_defaults(func=cmd_reply)

    for name in ("read", "archive"):
        sp = sub.add_parser(name)
        sp.add_argument("id")
        sp.add_argument("--project-root")
        sp.set_defaults(func=globals()[f"cmd_{name}"])

    sp = sub.add_parser("self-test")
    sp.set_defaults(func=cmd_self_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
