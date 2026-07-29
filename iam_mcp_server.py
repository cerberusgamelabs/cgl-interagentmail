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
) -> dict[str, Any]:
    """Send a new message. Addresses may also be unique display names."""
    return service().send(to, subject, body, cc, attachments, references)


@mcp.tool()
def iam_reply(
    message_id: str,
    body: str,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    subject: str | None = None,
    attachments: list[str] | None = None,
    references: list[str] | None = None,
) -> dict[str, Any]:
    """Send a substantive reply in an existing thread, preserving originator and routing."""
    return service().reply(message_id, body, to, cc, subject, attachments, references)


@mcp.tool()
def iam_archive(message_id: str) -> dict[str, str]:
    """Archive one fully handled inbox message."""
    return service().archive(message_id)


@mcp.tool()
def iam_list_channels() -> list[str]:
    """List known IAM public and private chat channel names."""
    return service().list_channels()


@mcp.tool()
def iam_chat_tail(channel: str, lines: int = 10, date: str | None = None) -> dict[str, Any]:
    """Read recent chat messages, enforcing private-channel membership."""
    return service().chat_tail(channel, lines, date)


@mcp.tool()
def iam_chat_post(
    channel: str,
    message: str,
    channel_type: str | None = None,
    with_agent: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """Post to a chat; set channel_type=private and with_agent for a new DM."""
    return service().chat_post(channel, message, channel_type, with_agent, date)


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
