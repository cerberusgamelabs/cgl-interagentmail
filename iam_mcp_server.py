"""MCP stdio server for InterAgentMail."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from iam_service import IAMService


PROJECT_ROOT_ENV = "IAM_PROJECT_ROOT"


def service() -> IAMService:
    """Resolve identity lazily so imports remain safe for MCP tooling."""
    return IAMService(os.environ.get(PROJECT_ROOT_ENV) or Path.cwd())


mcp = MCPServer(
    "InterAgentMail",
    version="1.0.0",
    instructions=(
        "Local project-to-project mail and chat. This server is bound to one project identity. "
        "Check iam_inbox when asked about mail; use iam_read before acting, iam_reply for thread "
        "continuity, and iam_archive only after a message is fully handled. Attachments are path "
        "references, not embedded file contents. Do not send acknowledgment-only replies; reply "
        "only when a response was requested or you have a substantive result or question."
    ),
)


@mcp.tool()
def iam_whoami() -> dict[str, Any]:
    """Return the mailbox identity bound to this MCP server."""
    return service().whoami()


@mcp.tool()
def iam_list_mailboxes() -> list[dict[str, str]]:
    """List known IAM mailbox addresses and display names."""
    return service().list_mailboxes()


@mcp.tool()
def iam_inbox(unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """List newest inbox messages for this project without marking them read."""
    return service().inbox(unread_only=unread_only, limit=limit)


@mcp.tool()
def iam_read(message_id: str, mark_read: bool = True) -> dict[str, Any]:
    """Read one inbox message by full id or unique prefix."""
    return service().read(message_id, mark_read=mark_read)


@mcp.tool()
def iam_send(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[str] | None = None,
    references: list[str] | None = None,
    priority: str = "normal",
) -> dict[str, Any]:
    """Send a new message. Priority is normal or urgent; urgent may steer an active Codex turn."""
    return service().send(to, subject, body, cc, attachments, references, priority=priority)


@mcp.tool()
def iam_reply(
    message_id: str,
    body: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    subject: str | None = None,
    attachments: list[str] | None = None,
    references: list[str] | None = None,
    priority: str = "normal",
) -> dict[str, Any]:
    """Send a substantive reply in an existing thread, preserving originator and routing."""
    return service().reply(message_id, body, to, cc, subject, attachments, references, priority=priority)


@mcp.tool()
def iam_archive(message_id: str) -> dict[str, str]:
    """Archive one fully handled inbox message."""
    return service().archive(message_id)


@mcp.tool()
def iam_timer_set(note: str, due_at: str) -> dict[str, Any]:
    """Set a one-shot reminder for this mailbox at an ISO-8601 timezone-aware time."""
    return service().timer_set(note, due_at)


@mcp.tool()
def iam_timer_set_team(team: str, note: str, due_at: str) -> list[dict[str, Any]]:
    """Schedule independent timers for an authorized team. Only an explicitly granted active leader may use this."""
    return service().timer_set_team(team, note, due_at)


@mcp.tool()
def iam_timer_set_for(target: str, note: str, due_at: str) -> dict[str, Any]:
    """Schedule a timer for one authorized teammate; explicit timer.schedule permission is required."""
    return service().timer_set_for(target, note, due_at)


@mcp.tool()
def iam_timer_list(due_only: bool = False) -> list[dict[str, Any]]:
    """List this mailbox's outstanding temporary reminders."""
    return service().timer_list(due_only)


@mcp.tool()
def iam_timer_clear(timer_id: str) -> dict[str, str]:
    """Clear a handled timer permanently. Cleared timers are not archived."""
    return service().timer_clear(timer_id)


@mcp.tool()
def iam_timer_cancel(timer_id: str) -> dict[str, str]:
    """Cancel an outstanding timer created by or targeted to this mailbox."""
    return service().timer_cancel(timer_id)


@mcp.tool()
def iam_timer_snooze(timer_id: str, due_at: str) -> dict[str, Any]:
    """Move one outstanding timer to a new ISO-8601 timezone-aware due time."""
    return service().timer_snooze(timer_id, due_at)


@mcp.tool()
def iam_team_add_member(team: str, member: str) -> dict[str, Any]:
    """Add one mailbox to this leader's team when team.manage_members was explicitly granted."""
    return service().team_add_member_as_leader(team, member)


@mcp.tool()
def iam_team_remove_member(team: str, member: str) -> dict[str, Any]:
    """Remove a non-leader mailbox from this leader's team when team.manage_members was granted."""
    return service().team_remove_member_as_leader(team, member)


@mcp.tool()
def iam_list_channels() -> list[str]:
    """List known IAM public and private chat channel names."""
    return service().list_channels()


@mcp.tool()
def iam_chat_tail(channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
    """Read recent chat messages, enforcing private-channel membership."""
    return service().chat_tail(channel, lines, date)


@mcp.tool()
def iam_chat_join(
    channel: str,
    channel_type: str | None = None,
    with_agent: str | None = None,
) -> dict[str, Any]:
    """Join one live channel and make it active; joining another leaves the prior one. Use channel_type=private and with_agent only to create a new DM."""
    return service().chat_join(channel, channel_type, with_agent)


@mcp.tool()
def iam_chat_leave() -> dict[str, Any]:
    """Leave this mailbox's active live chat channel and stop its supervisor notifications."""
    return service().chat_leave()


@mcp.tool()
def iam_chat_subscriptions() -> dict[str, Any] | None:
    """Return this mailbox's active live chat subscription, if any."""
    return service().chat_subscription()


@mcp.tool()
def iam_chat_post(
    message: str,
    date: str | None = None,
) -> dict[str, Any]:
    """Post to this mailbox's active chat channel. Join a channel first; its channel cannot be supplied here."""
    return service().chat_post(message, date)


@mcp.tool()
def iam_chat_seen(channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
    """Mark recent chat messages as seen by this project."""
    return service().chat_seen(channel, lines, date)


@mcp.resource("iam://identity")
def identity_resource() -> str:
    """The identity and project root bound to this server."""
    return json.dumps(service().whoami(), indent=2, sort_keys=True)


@mcp.resource("iam://inbox")
def inbox_resource() -> str:
    """The current inbox without read-state side effects."""
    return json.dumps(service().inbox(limit=100), indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve InterAgentMail over MCP stdio.")
    parser.add_argument(
        "--project-root",
        help=f"Project identity root. Defaults to ${PROJECT_ROOT_ENV}, then the current directory.",
    )
    args = parser.parse_args(argv)
    if args.project_root:
        os.environ[PROJECT_ROOT_ENV] = str(Path(args.project_root).resolve())
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
