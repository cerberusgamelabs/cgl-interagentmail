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
from datetime import datetime, timezone
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
CONFIG = ROOT / "config.json"


SAFE_MESSAGE_PREFIX = re.compile(r"^[A-Za-z0-9-]+$")


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
) -> dict:
    if not to:
        raise SystemExit("At least one --to recipient is required.")
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
        "attachments": attach or [],
        "references": names(refs),
        "signature": display_name(sender),
        "created_at": now,
        "read_at": None,
    }
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
        write_atomic(CONFIG, {"project_root": str(path)})
    root = configured_root()
    print(root if root else "No project root configured.")


def cmd_send(args: argparse.Namespace) -> None:
    sender = address(args.project_root)
    payload = send_message(
        sender,
        resolve_names(args.to),
        resolve_names(args.cc),
        args.subject,
        args.body,
        attachments(args.attach),
        args.thread,
        originator=args.originator,
        refs=args.ref,
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
        rows.append(f"{msg['id']} [{status}] {thread} from {sender} <{msg['from']}>: {msg['subject']}")
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
    if args.chat_command == "post":
        name = address(args.project_root)
        data = load_chat(args.channel, args.date)
        check_chat_access(data, args.project_root)
        if args.channel_type:
            data["channelType"] = args.channel_type
        if data["channelType"] == "private":
            if not data.get("participants") and not args.with_agent:
                raise SystemExit("New private channels require --with <agent>.")
            participants = data.get("participants") or [name, resolve_name(args.with_agent)]
            if name not in participants:
                raise SystemExit(f"{name} is not a participant in private channel {args.channel}.")
            if args.with_agent:
                other = resolve_name(args.with_agent)
                if other not in participants:
                    raise SystemExit(f"{other} is not a participant in private channel {args.channel}.")
            data["participants"] = participants
        stamp = now()
        data["messages"].insert(0, {
            "agent": display_name(name),
            "address": name,
            "timestamp": stamp,
            "message": args.message,
            "seen_by": {name: stamp},
        })
        save_chat(data)
        print(f"{display_name(name)}({stamp}): {args.message}")
        return

    if args.chat_command == "tail":
        data = load_chat(args.channel, args.date)
        check_chat_access(data, args.project_root)
        rows = []
        for msg in data["messages"][:args.lines]:
            seen = ", ".join(display_name(name) for name in msg.get("seen_by", {}))
            rows.append(f"{msg['agent']}({msg['timestamp']}): {msg['message']}" + (f"\n  Seen by: {seen}" if seen else ""))
        print("\n".join(rows) if rows else "No chat messages.")
        return

    if args.chat_command == "seen":
        name = address(args.project_root)
        data = load_chat(args.channel, args.date)
        check_chat_access(data, args.project_root)
        stamp = now()
        for msg in data["messages"][:args.lines]:
            msg.setdefault("seen_by", {})[name] = stamp
        save_chat(data)
        print(f"{display_name(name)} marked {min(args.lines, len(data['messages']))} seen.")
        return

    raise SystemExit(f"Unknown chat command: {args.chat_command}")


def cmd_reply(args: argparse.Namespace) -> None:
    sender = address(args.project_root)
    path = find_message(sender, args.id)
    original = load(path)
    originator = original.get("originator") or original["from"]
    default_to = original["from"] if originator == sender else originator
    payload = send_message(
        sender,
        resolve_names(args.to) or [default_to],
        resolve_names(args.cc),
        args.subject or f"Re: {original['subject']}",
        args.body,
        attachments(args.attach),
        original.get("thread") or f"thr-{original['id']}",
        original["id"],
        originator,
        args.ref,
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
            cmd_chat(argparse.Namespace(chat_command="post", channel="reviewers", project_root=str(a), message="Status update", date="2026-07-10", channel_type=None, with_agent=None))
            cmd_chat(argparse.Namespace(chat_command="seen", channel="reviewers", project_root=str(b), date="2026-07-10", lines=10))
            chat = load_chat("reviewers", "2026-07-10")
            assert chat["messages"][0]["message"] == "Status update"
            assert "NexusGuild" in chat["messages"][0]["seen_by"]
            assert "AegisGrid" in chat["messages"][0]["seen_by"]
            cmd_chat(argparse.Namespace(chat_command="post", channel="dm-nexus-aegis", project_root=str(a), message="Private note", date="2026-07-10", channel_type="private", with_agent="Aegis Grid"))
            private = load_chat("dm-nexus-aegis", "2026-07-10")
            assert private["channelType"] == "private"
            assert private["participants"] == ["NexusGuild", "AegisGrid"]
            cmd_chat(argparse.Namespace(chat_command="tail", channel="dm-nexus-aegis", project_root=str(b), lines=10, date="2026-07-10"))
            try:
                cmd_chat(argparse.Namespace(chat_command="tail", channel="dm-nexus-aegis", project_root=str(c), lines=10, date="2026-07-10"))
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
    post.add_argument("channel")
    post.add_argument("--project-root")
    post.add_argument("--message", required=True)
    post.add_argument("--date")
    post.add_argument("--type", choices=("public", "private"), dest="channel_type")
    post.add_argument("--with", dest="with_agent", help="Second participant for private channels.")
    post.set_defaults(func=cmd_chat)

    tail = chat_sub.add_parser("tail")
    tail.add_argument("channel")
    tail.add_argument("--project-root")
    tail.add_argument("--lines", type=int, default=10)
    tail.add_argument("--date")
    tail.set_defaults(func=cmd_chat)

    seen = chat_sub.add_parser("seen")
    seen.add_argument("channel")
    seen.add_argument("--project-root")
    seen.add_argument("--lines", type=int, default=10)
    seen.add_argument("--date")
    seen.set_defaults(func=cmd_chat)

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
