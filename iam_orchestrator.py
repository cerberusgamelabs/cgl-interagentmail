"""Release-friendly setup and lifecycle commands for InterAgentMail agents."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import getpass
import hmac
import http.client
import importlib.metadata
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import interagentmail as iam
from iam_codex_bridge import (
    AppServerClient,
    AppServerError,
    DeliveryState,
    MailboxBridge,
    apply_registered_thread_policy,
    background_process_kwargs,
    thread_sandbox_mode,
    turn_sandbox_policy,
)
from iam_service import IAMService, IAMError, session_owner, session_workspace
from iam_web import (
    DEFAULT_PORT as DEFAULT_WEB_PORT,
    LAN_WARNING,
    WebError,
    configure_web,
    create_user_mailbox,
    list_user_mailboxes,
    load_web_config,
    public_web_config,
    update_password as update_web_password,
    web_config_path,
)
from iam_webconnect import ConnectorState, WebConnectClient, WebConnectError, load_config as load_webconnect_config, save_config as save_webconnect_config


LOG = logging.getLogger("iam.orchestrator")
MANAGED_ENDPOINT = "managed://"
DEFAULT_URL = MANAGED_ENDPOINT
MCP_BEGIN = "# BEGIN INTERAGENTMAIL (managed by `iam setup`)"
MCP_END = "# END INTERAGENTMAIL"
CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
INTEGRATION_SCHEMA_VERSION = "1.0"
ADDRESS_STRATEGY = "project-folder-basename"
SAFE_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
SAFE_APPROVAL_POLICIES = {"untrusted", "on-request", "never"}
CODEX_DAEMON_PRIVATE_DIRECTORY_ERROR = "socket directory is not private to the current user"
LOG_EVENT_PATTERNS = {
    "connection failures": re.compile(r"connection failed", re.IGNORECASE),
    "delivery failures": re.compile(r"delivery failed", re.IGNORECASE),
    "interactive requests rejected": re.compile(r"interactive (?:handling|request)", re.IGNORECASE),
    "thread resume failures": re.compile(r"no rollout found|thread/resume failed", re.IGNORECASE),
}


class IAMCommandError(SystemExit):
    """A stable machine-readable command failure that remains friendly in text mode."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        recoverable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}


def integration_envelope(data: Any) -> dict[str, Any]:
    return {"schema_version": INTEGRATION_SCHEMA_VERSION, "ok": True, "data": data}


def integration_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, IAMCommandError):
        error = {
            "code": exc.error_code,
            "message": exc.message,
            "recoverable": exc.recoverable,
        }
        if exc.details:
            error["details"] = exc.details
    elif isinstance(exc, SystemExit):
        message = str(exc.code) if exc.code not in (None, 1) else "The IAM command failed."
        error = {"code": "IAM_COMMAND_FAILED", "message": message, "recoverable": False}
    else:
        error = {
            "code": "IAM_COMMAND_FAILED",
            "message": str(exc) or type(exc).__name__,
            "recoverable": False,
        }
    return {"schema_version": INTEGRATION_SCHEMA_VERSION, "ok": False, "error": error}


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def package_version() -> str:
    try:
        return importlib.metadata.version("cgl-interagentmail")
    except importlib.metadata.PackageNotFoundError:
        return "development"


def load_config() -> dict[str, Any]:
    if not iam.CONFIG.exists():
        return {}
    try:
        data = iam.load(iam.CONFIG)
    except (OSError, ValueError) as exc:
        raise IAMCommandError(
            "IAM_CONFIG_INVALID",
            f"Invalid InterAgentMail configuration: {iam.CONFIG}: {exc}",
        ) from None
    if not isinstance(data, dict):
        raise IAMCommandError("IAM_CONFIG_INVALID", f"Invalid InterAgentMail configuration: {iam.CONFIG}")
    return data


def save_config(data: dict[str, Any]) -> None:
    iam.write_atomic(iam.CONFIG, data)


def configured_endpoint() -> str:
    """Opt-in Windows compatibility transport; default remains Codex-managed."""
    port = load_config().get("windows_hidden_console_port")
    if port is None:
        return DEFAULT_URL
    if os.name != "nt" or type(port) is not int or not 1024 <= port <= 65535:
        raise IAMError("Invalid Windows hidden-console configuration (port must be 1024..65535).")
    return f"ws://127.0.0.1:{port}"


def cmd_app_server_mode(args: argparse.Namespace) -> None:
    # Do not silently move an active supervisor to a different session backend.
    if pid_alive(read_pid("supervisor")) or pid_alive(read_pid("app-server")):
        raise IAMError("Stop IAM with `iam stop --all` before changing app-server mode. Saved sessions are preserved.")
    config = load_config()
    if args.mode == "hidden-console":
        if os.name != "nt":
            raise IAMError("Hidden-console compatibility mode is Windows-only.")
        if not 1024 <= args.port <= 65535:
            raise IAMError("Port must be 1024..65535.")
        config["windows_hidden_console_port"] = args.port
    else:
        config.pop("windows_hidden_console_port", None)
    save_config(config)
    print(f"App-server mode saved: {args.mode}. No processes were started or stopped.")
    print("Before switching backends, exit agent terminals and stop the old Codex daemon/server.")
    print("Then run `iam start` and reopen projects with `iam open` (not plain codex).")


def registered_projects() -> dict[str, dict[str, Any]]:
    projects = load_config().get("projects", {})
    if not isinstance(projects, dict):
        raise IAMCommandError("IAM_CONFIG_INVALID", "InterAgentMail configuration field `projects` must be an object.")
    return projects


def project_entry(project_root: str | Path) -> tuple[str, Path, dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    address = iam.address(str(root))
    entry = registered_projects().get(address)
    try:
        registered_root = Path(entry.get("project_root", "")).expanduser().resolve() if isinstance(entry, dict) else None
    except (OSError, TypeError, ValueError):
        registered_root = None
    if not isinstance(entry, dict) or registered_root != root:
        raise IAMCommandError(
            "IAM_PROJECT_NOT_REGISTERED",
            f"{root} is not registered. Run: iam setup \"{root}\"",
            recoverable=True,
            details={"project_root": str(root), "address": address},
        )
    return address, root, entry


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def mcp_python_executable() -> str:
    """Use Python's windowless launcher for stdio MCP servers on Windows."""
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


def mcp_block(project_root: Path) -> str:
    args = ["-m", "iam_mcp_server", "--project-root", str(project_root)]
    rendered_args = ", ".join(toml_string(item) for item in args)
    tool_approvals = "\n".join(
        f"\n[mcp_servers.interagentmail.tools.{tool}]\napproval_mode = \"approve\""
        for tool in (
            "iam_inbox",
            "iam_read",
            "iam_reply",
            "iam_archive",
            "iam_list_mailboxes",
            "iam_send",
            "iam_list_channels",
            "iam_chat_join",
            "iam_chat_leave",
            "iam_chat_subscriptions",
            "iam_chat_tail",
            "iam_chat_post",
            "iam_chat_seen",
            "iam_timer_set",
            "iam_timer_set_team",
            "iam_timer_set_for",
            "iam_timer_list",
            "iam_timer_clear",
            "iam_timer_cancel",
            "iam_timer_snooze",
            "schedule_next_wake",
            "cancel_next_wake",
            "get_heartbeat_state",
            "iam_team_add_member",
            "iam_team_remove_member",
        )
    )
    return (
        f"{MCP_BEGIN}\n"
        "[mcp_servers.interagentmail]\n"
        f"command = {toml_string(mcp_python_executable())}\n"
        f"args = [{rendered_args}]\n"
        f"required = true\n{tool_approvals}\n"
        f"{MCP_END}\n"
    )


def configure_project_mcp(project_root: Path) -> Path:
    config_path = project_root / ".codex" / "config.toml"
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = mcp_block(project_root)
    begin_count = current.count(MCP_BEGIN)
    end_count = current.count(MCP_END)
    if begin_count != end_count or begin_count > 1 or (begin_count == 1 and current.index(MCP_BEGIN) > current.index(MCP_END)):
        raise IAMCommandError(
            "IAM_MCP_CONFIG_INVALID",
            f"Cannot safely edit {config_path}: the managed InterAgentMail markers are incomplete, duplicated, or out of order.",
            recoverable=True,
            details={"mcp_config": str(config_path)},
        )
    if begin_count == 1:
        before, remainder = current.split(MCP_BEGIN, 1)
        _, after = remainder.split(MCP_END, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip("\r\n")
    elif "[mcp_servers.interagentmail]" in current:
        raise IAMCommandError(
            "IAM_MCP_CONFIG_CONFLICT",
            f"{config_path} already defines mcp_servers.interagentmail outside the managed setup block. "
            "Remove or rename that section, then run setup again.",
            recoverable=True,
            details={"mcp_config": str(config_path)},
        )
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + block
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(updated, encoding="utf-8")
    return config_path


def remove_project_mcp(project_root: Path) -> Path | None:
    """Remove only the MCP block managed by InterAgentMail setup."""
    config_path = project_root / ".codex" / "config.toml"
    if not config_path.exists():
        return None
    current = config_path.read_text(encoding="utf-8")
    if MCP_BEGIN not in current and MCP_END not in current:
        return config_path
    if (
        current.count(MCP_BEGIN) != 1
        or current.count(MCP_END) != 1
        or current.index(MCP_BEGIN) > current.index(MCP_END)
    ):
        raise IAMCommandError(
            "IAM_MCP_CONFIG_INVALID",
            f"Cannot safely edit {config_path}: the managed InterAgentMail markers are incomplete, duplicated, or out of order.",
            recoverable=True,
            details={"mcp_config": str(config_path)},
        )
    before, remainder = current.split(MCP_BEGIN, 1)
    _, after = remainder.split(MCP_END, 1)
    updated = before.rstrip() + ("\n\n" if before.strip() and after.strip() else "") + after.lstrip("\r\n")
    if updated and not updated.endswith("\n"):
        updated += "\n"
    config_path.write_text(updated, encoding="utf-8")
    return config_path


def setup_project(
    project_root: str | Path,
    *,
    display_name: str | None = None,
    sandbox: str = "workspace-write",
    approval_policy: str = "on-request",
    process_existing: bool = False,
    claim_empty_mailbox: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise IAMCommandError(
            "IAM_PROJECT_NOT_FOUND",
            f"Project directory does not exist: {root}",
            recoverable=True,
            details={"project_root": str(root)},
        )
    address = iam.address(str(root))
    config = load_config()
    config.setdefault("project_root", str(root.parent))
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise IAMCommandError("IAM_CONFIG_INVALID", "InterAgentMail configuration field `projects` must be an object.")

    existing = projects.get(address)
    if existing is not None:
        try:
            existing_root = Path(existing.get("project_root", "")).expanduser().resolve() if isinstance(existing, dict) else None
        except (OSError, TypeError, ValueError):
            raise IAMCommandError(
                "IAM_CONFIG_INVALID",
                f"Registration for {address} has an invalid project_root.",
                details={"address": address},
            ) from None
        if existing_root != root:
            raise IAMCommandError(
                "IAM_ADDRESS_COLLISION",
                f"Mailbox address {address} is already registered to another project.",
                recoverable=True,
                details={
                    "address": address,
                    "requested_project_root": str(root),
                    "registered_project_root": str(existing_root) if existing_root else "unavailable",
                },
            )
    for other_address, other_entry in projects.items():
        if other_address == address or not isinstance(other_entry, dict):
            continue
        try:
            other_root = Path(other_entry.get("project_root", "")).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            continue
        if other_root == root:
            raise IAMCommandError(
                "IAM_PROJECT_ALREADY_REGISTERED",
                f"{root} is already registered as {other_address}.",
                recoverable=True,
                details={"address": other_address, "project_root": str(root)},
            )

    box = iam.mailbox(address)
    box_existed = box.exists()
    profile_path = box / "profile.json"
    profile_before: dict[str, Any] = {}
    if profile_path.exists():
        try:
            loaded_profile = iam.load(profile_path)
        except (OSError, ValueError) as exc:
            raise IAMCommandError(
                "IAM_CONFIG_INVALID",
                f"Mailbox profile for {address} is invalid: {exc}",
                details={"address": address, "profile": str(profile_path)},
            ) from None
        if not isinstance(loaded_profile, dict):
            raise IAMCommandError(
                "IAM_CONFIG_INVALID",
                f"Mailbox profile for {address} must be a JSON object.",
                details={"address": address, "profile": str(profile_path)},
            )
        profile_before = loaded_profile
    profile_owner = str(profile_before.get("project_root", "")).strip()
    profile_kind = profile_before.get("kind")
    claimed_empty_mailbox = False
    if profile_kind == "user":
        raise IAMCommandError(
            "IAM_ADDRESS_COLLISION",
            f"Mailbox address {address} is owned by a human user and cannot become a project identity.",
            recoverable=True,
            details={"address": address, "requested_project_root": str(root)},
        )
    if box_existed and not profile_owner:
        # `InterAgentMail init` creates this exact, otherwise empty shape.  It
        # is safe to claim only when the caller makes that intent explicit;
        # never silently convert a human or previously used mailbox.
        allowed_entries = {"profile.json", "inbox", "sent", "archive"}
        unexpected_entries = [entry.name for entry in box.iterdir() if entry.name not in allowed_entries]
        folders_empty = all(
            (box / folder).is_dir() and not any((box / folder).iterdir())
            for folder in ("inbox", "sent", "archive")
        )
        default_profile = profile_before == {"address": address, "display_name": address}
        can_claim = default_profile and not unexpected_entries and folders_empty
        if not claim_empty_mailbox or not can_claim:
            raise IAMCommandError(
                "IAM_ADDRESS_COLLISION",
                f"Mailbox address {address} already exists without project ownership; IAM will not migrate it implicitly.",
                recoverable=True,
                details={
                    "address": address,
                    "requested_project_root": str(root),
                    "claim_empty_mailbox_available": can_claim,
                },
            )
        claimed_empty_mailbox = True
    if profile_owner:
        try:
            mailbox_root = Path(profile_owner).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            raise IAMCommandError(
                "IAM_CONFIG_INVALID",
                f"Mailbox profile for {address} has an invalid project_root.",
                details={"address": address, "profile": str(profile_path)},
            ) from None
        if mailbox_root != root:
            raise IAMCommandError(
                "IAM_ADDRESS_COLLISION",
                f"Mailbox address {address} belongs to another project.",
                recoverable=True,
                details={
                    "address": address,
                    "requested_project_root": str(root),
                    "mailbox_project_root": profile_owner,
                },
            )

    desired_entry = {
        "project_root": str(root),
        "sandbox": sandbox,
        "approval_policy": approval_policy,
    }
    managed_block_current = False
    config_path = root / ".codex" / "config.toml"
    if config_path.exists():
        try:
            managed_block_current = mcp_block(root).strip() in config_path.read_text(encoding="utf-8")
        except OSError:
            managed_block_current = False
    display_changed = bool(display_name and display_name != profile_before.get("display_name"))
    if existing is None:
        registration_status = "registered"
    elif existing != desired_entry or display_changed or not managed_block_current:
        registration_status = "updated"
    else:
        registration_status = "unchanged"

    iam.ensure_box(address)
    profile_data = iam.profile(address)
    profile_data["kind"] = "project"
    profile_data["project_root"] = str(root)
    if display_name:
        profile_data["display_name"] = display_name
    iam.save_profile(address, profile_data)

    projects[address] = desired_entry
    mcp_path = configure_project_mcp(root)
    save_config(config)

    state = DeliveryState.load(box / ".codex-bridge-state.json")
    state.initialize(IAMService(root).inbox(limit=1000), process_existing=process_existing)
    return {
        "status": registration_status,
        "address": address,
        "display_name": iam.display_name(address),
        "project_root": str(root),
        "mcp_config": str(mcp_path),
        "mailbox": str(box),
        "sandbox": sandbox,
        "approval_policy": approval_policy,
        "thread_state": "pinned" if state.thread_id else "pending",
        "thread_preserved": bool(state.thread_id),
        "legacy_mailbox_reused": claimed_empty_mailbox,
        "supervisor_reload": "automatic",
    }


def unregister_project(project_root: str | Path) -> dict[str, Any]:
    address, root, _entry = project_entry(project_root)
    links = load_config().get("session_mailboxes", {})
    if address in links or address in links.values():
        raise IAMError("Unlink shared-session mailboxes before unregistering this project")
    mcp_path = remove_project_mcp(root)
    config = load_config()
    projects = config.get("projects", {})
    if isinstance(projects, dict):
        projects.pop(address, None)
    save_config(config)
    return {
        "address": address,
        "project_root": str(root),
        "mcp_config": str(mcp_path) if mcp_path else None,
        "mailbox": str(iam.mailbox(address)),
    }


def runtime_dir(*, create: bool = True) -> Path:
    path = iam.ROOT / "run"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def pid_path(name: str, *, create: bool = True) -> Path:
    return runtime_dir(create=create) / f"{name}.pid"


def log_path(name: str, *, create: bool = True) -> Path:
    return runtime_dir(create=create) / f"{name}.log"


def web_instance_path(*, create: bool = True) -> Path:
    return runtime_dir(create=create) / "web.instance"


def read_web_instance() -> str | None:
    try:
        value = web_instance_path(create=False).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def read_pid(name: str) -> int | None:
    path = pid_path(name, create=False)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            # Windows can retain an openable process object after the process
            # itself has exited.  Treat only STILL_ACTIVE as a live process;
            # otherwise stale PID files prevent WebConnect and IAM services
            # from recovering after a reboot or clean exit.
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


def spawn_background(
    command: list[str], name: str, *, record_pid: bool = True, capture_output: bool = True,
    hidden_console: bool = False,
) -> int:
    log_file = log_path(name).open("a", encoding="utf-8") if capture_output else None
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file or subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if log_file else subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (
            subprocess.CREATE_NEW_CONSOLE if hidden_console else subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        if log_file:
            log_file.close()
    if record_pid:
        pid_path(name).write_text(str(process.pid) + "\n", encoding="utf-8")
    return process.pid


def is_managed_endpoint(url: str) -> bool:
    return url == MANAGED_ENDPOINT


def appserver_client(
    url: str, *, request_timeout: float = 30.0, experimental_api: bool = True
) -> AppServerClient:
    options = {
        "request_timeout": request_timeout,
        "experimental_api": experimental_api,
    }
    if is_managed_endpoint(url):
        if load_config().get("windows_hidden_console_port") is not None:
            raise IAMError("Hidden-console mode must not connect to the native daemon. Use the saved loopback endpoint or explicitly switch app-server mode.")
        return AppServerClient(
            command=[codex_executable(), "app-server", "proxy"],
            websocket_stdio=True,
            **options,
        )
    return AppServerClient(url=url, **options)


async def appserver_available(url: str, timeout: float = 2.0) -> bool:
    client = appserver_client(url, request_timeout=timeout)
    try:
        await client.connect()
        return True
    # A process can leave a listener behind while it is shutting down (or,
    # rarely on Windows, after it was forcibly terminated).  Connecting to
    # that listener times out during the WebSocket opening handshake.  Treat
    # it as unavailable so `iam start` can recover or provide a useful error.
    except (AppServerError, OSError, asyncio.TimeoutError):
        return False
    finally:
        await client.close()


def codex_executable() -> str:
    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        raise SystemExit("Codex was not found on PATH. Install Codex and run `codex login` first.")
    return executable


def repair_codex_daemon_state_acl() -> Path:
    """Restore Codex's Windows daemon state directory to a user-private DACL."""
    if os.name != "nt":
        raise OSError("Codex daemon ACL repair is only supported on Windows")
    state_dir = Path.home() / ".codex" / "app-server-daemon"
    if not state_dir.is_dir():
        raise OSError(f"Codex daemon state directory does not exist: {state_dir}")
    metadata = state_dir.lstat()
    if state_dir.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise OSError(f"Refusing to repair a reparse-point daemon directory: {state_dir}")

    hidden: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    hidden["startupinfo"] = startupinfo
    hidden["creationflags"] = subprocess.CREATE_NO_WINDOW
    identity = subprocess.run(["whoami"], **hidden)
    account = identity.stdout.strip()
    if identity.returncode != 0 or not account or "\\" not in account:
        detail = (identity.stderr or identity.stdout or "could not identify the current user").strip()
        raise OSError(f"Could not identify the Windows account for daemon ACL repair: {detail}")

    commands = (
        ["icacls", str(state_dir), "/reset"],
        ["icacls", str(state_dir), "/grant:r", f"{account}:(OI)(CI)(F)"],
        ["icacls", str(state_dir), "/inheritance:r"],
    )
    for command in commands:
        completed = subprocess.run(command, **hidden)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown icacls error").strip()
            raise OSError(f"Could not make the Codex daemon directory private: {detail}")
    return state_dir


def native_daemon_running() -> bool:
    """Passive, conservative check: never launch Codex to discover its daemon."""
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    record_path = codex_home / "app-server-daemon" / "daemon.pid"
    try:
        record = iam.load(record_path)
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        raise IAMError(f"Cannot inspect native daemon state safely: {exc}") from None
    if not isinstance(record, dict) or type(record.get("pid")) is not int or record["pid"] <= 0:
        raise IAMError("Native daemon PID record is invalid; inspect it before starting another backend.")
    # A recycled PID can conservatively block startup, but never authorizes
    # stopping that PID. All shutdown remains an explicit owner action.
    return pid_alive(record["pid"])


def compatibility_port_available(url: str) -> bool:
    """A failed protocol probe does not mean the listener's port is free."""
    endpoint = urlsplit(url)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((endpoint.hostname, endpoint.port))
        except OSError:
            return False
    return True


def ensure_appserver(url: str, wait_seconds: float = 15.0) -> str:
    hidden_console = (
        load_config().get("windows_hidden_console_port") is not None
        and url == configured_endpoint()
    )
    if hidden_console:
        with session_guard("app-server", "startup") as acquired:
            if not acquired:
                raise IAMError("Another IAM app-server startup is in progress; retry shortly.")
            return _ensure_appserver(url, wait_seconds)
    return _ensure_appserver(url, wait_seconds)


def _ensure_appserver(url: str, wait_seconds: float = 15.0) -> str:
    hidden_console = (
        load_config().get("windows_hidden_console_port") is not None
        and url == configured_endpoint()
    )
    if hidden_console and native_daemon_running():
        raise IAMError("The Codex managed daemon is still running. Exit its agent terminals, then run `codex app-server daemon stop` before `iam open` or `iam start`.")
    if asyncio.run(appserver_available(url)):
        if hidden_console:
            marker = iam.ROOT / "run" / "app-server-console-mode.json"
            saved = iam.load(marker) if marker.exists() else {}
            if (saved.get("url") != url or saved.get("pid") != read_pid("app-server")
                    or not pid_alive(saved.get("pid"))):
                raise IAMError("The compatibility port is occupied by an unverified server. Leave it running and choose another port.")
        return "already running"
    if hidden_console:
        if pid_alive(read_pid("app-server")):
            raise IAMError("The recorded compatibility server is still running but not responding. Its ownership record was preserved; retry shortly or inspect the app-server log.")
        if not compatibility_port_available(url):
            raise IAMError("The compatibility port is occupied but not responding. No replacement was launched and ownership records were preserved.")
    if is_managed_endpoint(url):
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "capture_output": True,
            "text": True,
            "timeout": wait_seconds,
            "check": False,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        command = [codex_executable(), "app-server", "daemon", "start"]
        try:
            completed = subprocess.run(command, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemExit(f"Could not start the Codex managed daemon: {exc}") from exc
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        if completed.returncode != 0 and CODEX_DAEMON_PRIVATE_DIRECTORY_ERROR in detail:
            try:
                repaired = repair_codex_daemon_state_acl()
                LOG.warning("Repaired stale Windows ACLs on Codex daemon state directory %s", repaired)
                completed = subprocess.run(command, **kwargs)
            except (OSError, subprocess.SubprocessError) as exc:
                raise SystemExit(
                    f"Could not start the Codex managed daemon: {detail}. "
                    f"IAM could not repair the private directory ACL: {exc}"
                ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise SystemExit(f"Could not start the Codex managed daemon: {detail}")
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if asyncio.run(appserver_available(url, timeout=1.0)):
                return "managed daemon started"
        raise SystemExit("Codex managed daemon started but its local proxy did not become available.")
    if not url.startswith("ws://127.0.0.1:") and not url.startswith("ws://localhost:"):
        raise SystemExit(f"Refusing to auto-start a non-loopback app-server: {url}")
    command = [codex_executable(), "app-server", "--listen", url]
    if hidden_console:
        command.extend(["--disable", "daemon_auto_start"])
        pid = spawn_background(command, "app-server", hidden_console=True, record_pid=False)
    else:
        pid = spawn_background(command, "app-server")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if hidden_console and not pid_alive(pid):
            break
        if asyncio.run(appserver_available(url, timeout=1.0)):
            if hidden_console:
                if not pid_alive(pid):
                    break
                # Publish only after this launch is alive and the endpoint is
                # ready. A failed duplicate must never replace healthy ownership.
                iam.write_atomic(iam.ROOT / "run" / "app-server-console-mode.json", {"pid": pid, "url": url})
                pid_path("app-server").write_text(str(pid) + "\n", encoding="utf-8")
            return f"started (pid {pid})"
        if not pid_alive(pid):
            break
    raise SystemExit(
        f"Codex app-server did not start. See {log_path('app-server')}. "
        "If the endpoint port is still occupied after Codex was force-closed, "
        "restart Windows to clear the stale listener, then run `iam start` again."
    )


def ensure_daemon(url: str) -> str:
    pid = read_pid("supervisor")
    if pid_alive(pid):
        return f"already running (pid {pid})"
    try:
        pid_path("supervisor").unlink()
    except FileNotFoundError:
        pass
    command = [
        sys.executable, "-m", "iam_orchestrator", "serve", "--url", url,
        "--log-file", str(log_path("supervisor")),
    ]
    spawn_background(command, "supervisor", record_pid=False, capture_output=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        time.sleep(0.1)
        pid = read_pid("supervisor")
        if pid_alive(pid):
            return f"started (pid {pid})"
    raise SystemExit(f"InterAgentMail supervisor did not start. See {log_path('supervisor')}")


def registry_signature(projects: dict[str, dict[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(
        (
            address,
            str(entry.get("project_root", "")),
            str(entry.get("sandbox", "workspace-write")),
            str(entry.get("approval_policy", "on-request")),
            session_owner(address),
        )
        for address, entry in projects.items()
    ))


@dataclass
class SupervisorMailbox:
    address: str
    service: IAMService
    state: DeliveryState
    sandbox: str
    approval_policy: str

    def pending_messages(self) -> list[dict[str, Any]]:
        messages = self.service.inbox(limit=1000)
        pending = [message for message in messages if not self.state.is_delivered(str(message["id"]))]
        return sorted(pending, key=lambda item: (str(item.get("created_at", "")), str(item["id"])))

    def pending_chat(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        return self.service.chat_updates()

    def pending_timers(self) -> list[dict[str, Any]]:
        return [timer for timer in self.service.timer_list(due_only=True)
                if not timer.get("delivery", {}).get("delivered_at")]


@dataclass
class ActiveDelivery:
    client: AppServerClient
    bridge: MailboxBridge


def supervisor_mailboxes(projects: dict[str, dict[str, Any]]) -> list[SupervisorMailbox]:
    watched: list[SupervisorMailbox] = []
    for address, entry in projects.items():
        root = Path(entry["project_root"]).resolve()
        if not root.is_dir():
            LOG.warning("Skipping %s; project directory is missing: %s", address, root)
            continue
        service = IAMService(root)
        state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
        guarded = any((iam.mailbox(name) / ".delivery-requirement.json").exists()
                      for name in (address, session_owner(address)))
        state.initialize(service.inbox(limit=1000), process_existing=guarded)
        watched.append(SupervisorMailbox(
            address=address,
            service=service,
            state=state,
            sandbox=str(entry.get("sandbox", "workspace-write")),
            approval_policy=str(entry.get("approval_policy", "on-request")),
        ))
    return watched


def delivery_requirement(address: str) -> dict[str, str] | None:
    path = iam.mailbox(address) / ".delivery-requirement.json"
    if not path.exists():
        return None
    value = iam.load(path)
    if (not isinstance(value, dict) or set(value) != {"session_owner", "thread_id"}
            or not all(isinstance(v, str) and v.strip() for v in value.values())):
        raise IAMError("invalid_delivery_requirement")
    return value


def check_delivery_requirement(address: str, actual_thread: str | None = None) -> dict[str, str] | None:
    """Read fresh policy/binding; never repair or replace either one."""
    owner = session_owner(address)
    requirements = [delivery_requirement(name) for name in dict.fromkeys((address, owner))]
    selected = None
    for requirement in requirements:
        if requirement is None:
            continue
        if requirement["session_owner"] != owner:
            raise IAMError("delivery_session_owner_mismatch")
        current = DeliveryState.load(iam.mailbox(owner) / ".codex-bridge-state.json").thread_id
        if not current:
            raise IAMError("delivery_thread_missing")
        if current != requirement["thread_id"]:
            raise IAMError("delivery_thread_mismatch")
        if actual_thread is not None and actual_thread != requirement["thread_id"]:
            raise IAMError("delivery_active_thread_mismatch")
        selected = requirement
    return selected


async def activate_delivery(mailbox: SupervisorMailbox, url: str, *, required_thread: str | None = None) -> ActiveDelivery:
    """Attach one mailbox to its own client connection only when mail requires it."""
    workspace_recorded = (iam.mailbox(mailbox.address) / ".session-workspace.json").exists()
    client = appserver_client(url, experimental_api=True)
    try:
        await client.connect()
        workspace = session_workspace(mailbox.address)
        root = Path(registered_projects()[workspace]["project_root"]).resolve()
        bridge = MailboxBridge(
            mailbox.service,
            mailbox.state,
            client,
            explicit_thread_id=required_thread or mailbox.state.thread_id,
            # A recorded mailbox thread is authoritative.  Never replace it
            # merely because a transient app-server failure makes it appear
            # unavailable; that destroys the user's chosen conversation.
            create_thread=not bool(required_thread or mailbox.state.thread_id),
            sandbox=mailbox.sandbox,
            approval_policy=mailbox.approval_policy,
            execution_root=root,
        )
        thread = await bridge.attach_thread()
        if workspace_recorded:
            actual = await client.request(
                "thread/resume",
                thread_resume_params(
                    str(thread["id"]),
                    root,
                    {"sandbox": mailbox.sandbox, "approval_policy": mailbox.approval_policy},
                    runtime_workspace_roots=True,
                ),
            )
            verify_session_workspace(actual, str(thread["id"]), root)
        elif not required_thread:
            await bridge.ensure_resumable(thread)
        LOG.info("Activated delivery for %s on isolated Codex connection and thread %s", mailbox.address, thread["id"])
        return ActiveDelivery(client=client, bridge=bridge)
    except BaseException:
        await client.close()
        raise


async def release_if_idle(active: ActiveDelivery) -> bool:
    """Release an idle mailbox's connection; active or approval-paused turns retain it."""
    thread_id = active.bridge.state.thread_id
    if not thread_id:
        return True
    result = await active.client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
    status = result["thread"].get("status") or {"type": "notLoaded"}
    return status.get("type") in {"idle", "notLoaded"}


async def close_active_delivery(address: str, active: ActiveDelivery) -> None:
    try:
        await active.client.close()
    except (AppServerError, OSError, ValueError) as exc:
        LOG.warning("Could not cleanly close delivery connection for %s: %s", address, exc)
    else:
        LOG.info("Released idle Codex delivery connection for %s", address)


async def serve_once(url: str, *, poll_seconds: float = 2.0, run_once: bool = False) -> None:
    projects = registered_projects()
    if not projects:
        raise AppServerError("No projects are registered; run `iam setup <project>` first")
    signature = registry_signature(projects)
    mailboxes = supervisor_mailboxes(projects)
    by_address = {mailbox.address: mailbox for mailbox in mailboxes}
    active: dict[str, ActiveDelivery] = {}
    retry_after: dict[str, float] = {}
    retry_delay: dict[str, float] = {}
    # Separate opt-in external attention lane. Never delays the mailbox scan
    # awaiting verifier IO and never inherits mail retry/new-thread semantics.
    from iam_fast_path import supervised_lane
    fast_path_task = asyncio.create_task(supervised_lane(url, appserver_client, session_guard))

    def defer(owner: str) -> float:
        delay = min(max(retry_delay.get(owner, poll_seconds), poll_seconds) * 2, 60.0)
        retry_delay[owner] = delay
        retry_after[owner] = time.monotonic() + delay
        return delay

    def recovered(owner: str) -> None:
        retry_after.pop(owner, None)
        retry_delay.pop(owner, None)

    try:
        while True:
            # An explicit iam open --thread-id may change a primary while
            # this supervisor holds an older in-memory delivery state.
            if registry_signature(registered_projects()) != signature or any(
                DeliveryState.load(mailbox.state.path).thread_id != mailbox.state.thread_id
                for mailbox in mailboxes
            ):
                LOG.info("Mailbox session configuration changed; reloading before delivery")
                return
            delivered_owners: set[str] = set()
            for mailbox in mailboxes:
                owner = session_owner(mailbox.address)
                if owner in delivered_owners:
                    continue
                if time.monotonic() < retry_after.get(owner, 0):
                    continue
                with session_guard(owner, "workspace") as available:
                    if not available:
                        continue
                    primary = by_address.get(owner)
                    if primary is None:
                        delay = defer(owner)
                        LOG.error("Shared session owner %s is unavailable; mail stays queued (retry in %.0fs)", owner, delay)
                        continue
                    try:
                        requirement = check_delivery_requirement(mailbox.address)
                    except (IAMError, OSError, ValueError) as exc:
                        delay = defer(owner)
                        LOG.warning("Delivery held for %s: %s (retry in %.0fs)", mailbox.address, exc, delay)
                        continue
                    session = active.get(owner)
                    try:
                        workspace = session_workspace(owner)
                        execution_root = Path(projects[workspace]["project_root"]).resolve()
                    except (IAMError, KeyError, OSError, ValueError) as exc:
                        delay = defer(owner)
                        LOG.error("Shared workspace unavailable for %s: %s (retry in %.0fs)", owner, exc, delay)
                        continue
                    if session and session.bridge.execution_root != execution_root:
                        await close_active_delivery(owner, session)
                        active.pop(owner, None)
                        session = None
                    pending = mailbox.pending_messages()
                    chat_state, pending_chat = mailbox.pending_chat()
                    pending_timers = mailbox.pending_timers()
                    # Heartbeats use their own acceptance/hold state, never mail's
                    # activation/retry/thread-creation path. The workspace lock
                    # above also serializes linked mailbox activations.
                    if not pending and not pending_chat and not pending_timers:
                        from iam_heartbeat import deliver_due
                        if await deliver_due(owner, url, projects, appserver_client):
                            delivered_owners.add(owner)
                            continue
                    if session is None:
                        if not pending and not pending_chat and not pending_timers:
                            continue
                        try:
                            if requirement:
                                session = await activate_delivery(primary, url, required_thread=requirement["thread_id"])
                            else:
                                session = await activate_delivery(primary, url)
                            active[owner] = session
                        except (AppServerError, OSError, KeyError, ValueError) as exc:
                            delay = defer(owner)
                            LOG.error("Could not activate delivery for %s: %s (retry in %.0fs)", mailbox.address, exc, delay)
                            continue
                    recovered(owner)

                    try:
                        check_delivery_requirement(mailbox.address, session.bridge.thread_id)
                        bridge = session.bridge if owner == mailbox.address else MailboxBridge(
                            mailbox.service, mailbox.state, session.client,
                            shared_thread_id=session.bridge.thread_id,
                            execution_root=execution_root,
                            sandbox=primary.sandbox, approval_policy=primary.approval_policy,
                            policy_turn_ids=session.bridge.policy_turn_ids,
                        )
                        bridge.delivery_check = lambda address=mailbox.address, target=bridge.thread_id: check_delivery_requirement(address, target)
                        if pending_timers:
                            timer = pending_timers[0]
                            if await bridge.deliver_timer(timer):
                                delivered_owners.add(owner)
                                mailbox.service.timer_mark_delivered(str(timer["id"]))
                                LOG.info("Delivered timer %s for %s", timer["id"], mailbox.address)
                                continue
                        if chat_state and pending_chat:
                            delivered_chat = await bridge.deliver_chat(chat_state["channel"], pending_chat[:10])
                            if delivered_chat:
                                delivered_owners.add(owner)
                                mailbox.service.chat_mark_delivered(chat_state, pending_chat[:10])
                                LOG.info("Delivered %d chat update(s) for %s", min(10, len(pending_chat)), mailbox.address)
                                continue
                        delivered = await bridge.deliver_once()
                        if delivered:
                            delivered_owners.add(owner)
                            # A turn can still report idle immediately after turn/start; retain its
                            # isolated connection until at least the following supervisor pass.
                            continue
                        _chat_state, remaining_chat = mailbox.pending_chat()
                        group_pending = any(
                            member.pending_messages() or member.pending_chat()[1] or member.pending_timers()
                            for member in mailboxes if session_owner(member.address) == owner
                        )
                        if not group_pending and await release_if_idle(session):
                            await close_active_delivery(owner, session)
                            active.pop(owner, None)
                    except (IAMError, AppServerError, OSError, KeyError, ValueError) as exc:
                        delay = defer(owner)
                        LOG.error("Delivery failed for %s: %s (retry in %.0fs)", mailbox.address, exc, delay)
                        await close_active_delivery(owner, session)
                        active.pop(owner, None)

            if registry_signature(registered_projects()) != signature:
                LOG.info("Project registry changed; reloading")
                return
            if run_once:
                return
            await asyncio.sleep(poll_seconds)
    finally:
        fast_path_task.cancel()
        await asyncio.gather(fast_path_task, return_exceptions=True)
        for address, session in list(active.items()):
            await close_active_delivery(address, session)


async def daemon_loop(url: str) -> None:
    while True:
        try:
            await serve_once(url)
        except (AppServerError, OSError, KeyError, ValueError) as exc:
            LOG.error("Supervisor connection failed: %s", exc)
        await asyncio.sleep(5)


def cmd_setup(args: argparse.Namespace) -> int:
    roots = args.projects or [str(Path.cwd())]
    if args.display_name and len(roots) != 1:
        raise IAMCommandError(
            "IAM_INVALID_ARGUMENT",
            "--display-name can only be used when registering one project.",
            recoverable=True,
        )
    sandbox = "danger-full-access" if args.full_access else "workspace-write"
    results = [
        setup_project(
            root,
            display_name=args.display_name,
            sandbox=sandbox,
            approval_policy=args.approval_policy,
            process_existing=args.process_existing,
            claim_empty_mailbox=args.claim_empty_mailbox,
        )
        for root in roots
    ]
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"projects": results}))
        return 0
    for item in results:
        print(f"Configured {item['display_name']} <{item['address']}> ({item['status']})")
        print(f"  Project: {item['project_root']}")
        print(f"  MCP:     {item['mcp_config']}")
        print(f"  Safety:  {item['sandbox']}, approvals {item['approval_policy']}")
    print("\nNext: run `iam start` once, then `iam open` from any registered project you want to view.")
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    roots = args.projects or [str(Path.cwd())]
    results = []
    for root in roots:
        item = unregister_project(root)
        item.update({"status": "unregistered", "mailbox_preserved": True, "supervisor_reload": "automatic"})
        results.append(item)
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"projects": results}))
        return 0
    for item in results:
        print(f"Unregistered {item['address']} ({item['project_root']})")
        if item["mcp_config"]:
            print(f"  Removed the managed MCP configuration from {item['mcp_config']}")
        print(f"  Mailbox data was preserved at {item['mailbox']}")
    print("The running supervisor will reload the project registry automatically.")
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    data = {
        "iam_version": package_version(),
        "integration_schema_version": INTEGRATION_SCHEMA_VERSION,
        "address_strategy": ADDRESS_STRATEGY,
        "commands": {
            "capabilities": {"json": True},
            "register": {"json": True, "multiple_projects": True, "display_name_optional": True},
            "unregister": {"json": True, "preserves_mailbox": True},
            "status": {"json": True},
            "doctor": {"json": True, "project_filter": True},
            "user": {"json": True, "mailbox_kind": "user"},
            "web": {"json": True, "standalone_process": True, "authentication_required": True},
            "hooks": {"preview": True, "wire_schema": "1.0", "standalone_process": True,
                      "loopback_only": True, "agent_execution": False},
        },
        "features": {
            "collision_detection": True,
            "idempotent_registration": True,
            "mailbox_ownership": True,
            "automatic_supervisor_reload": True,
            "human_identity_required": False,
            "sanitized_support_reports": True,
            "user_mailboxes": True,
            "authenticated_web_interface": True,
            "lan_access_opt_in": True,
            "lazy_isolated_delivery_connections": True,
            "urgent_mail_steering": True,
            "web_managed_by_iam_service": False,
            "generic_hooks_preview": True,
        },
    }
    if getattr(args, "json_output", False):
        print_json(integration_envelope(data))
    else:
        print(f"InterAgentMail {data['iam_version']} integration schema {INTEGRATION_SCHEMA_VERSION}")
        print(f"Address strategy: {ADDRESS_STRATEGY}")
        print("Automation commands: capabilities, register, unregister, status, doctor")
    return 0


def raise_web_command_error(exc: WebError) -> None:
    raise IAMCommandError(exc.code, exc.message, recoverable=exc.recoverable) from None


def prompt_web_password(*, password_stdin: bool = False, confirm: bool = True) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise IAMCommandError("IAM_WEB_PASSWORD_REQUIRED", "No password was received on standard input.", recoverable=True)
        return password
    password = getpass.getpass("Web application password: ")
    if confirm:
        repeated = getpass.getpass("Confirm web application password: ")
        if password != repeated:
            raise IAMCommandError("IAM_WEB_PASSWORD_MISMATCH", "The passwords did not match.", recoverable=True)
    return password


def cmd_user_create(args: argparse.Namespace) -> int:
    try:
        item = create_user_mailbox(args.address, args.display_name)
    except WebError as exc:
        raise_web_command_error(exc)
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"user_mailbox": item}))
    else:
        print(f"User mailbox {item['display_name']} <{item['address']}> is {item['status']}.")
        print(f"Mailbox data: {item['mailbox']}")
    return 0


def cmd_user_list(args: argparse.Namespace) -> int:
    rows = list_user_mailboxes()
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"user_mailboxes": rows}))
    elif rows:
        for row in rows:
            print(f"{row['display_name']} <{row['address']}>")
    else:
        print("No user mailboxes. Run `iam user create ADDRESS`.")
    return 0


def web_available(port: int, timeout: float = 1.0, *, expected_instance: str | None = None) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        if response.status != 200 or payload.get("service") != "iam-web":
            return False
        if expected_instance is None:
            return True
        return hmac.compare_digest(str(payload.get("instance", "")), expected_instance)
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def web_status_data() -> dict[str, Any]:
    pid = read_pid("web")
    process_alive = pid_alive(pid)
    error: dict[str, Any] | None = None
    try:
        config = public_web_config(load_web_config()) if web_config_path().exists() else None
    except WebError as exc:
        config = None
        error = {"code": exc.code, "message": exc.message, "recoverable": exc.recoverable}
    instance = read_web_instance()
    if process_alive and config and not instance and error is None:
        error = {
            "code": "IAM_WEB_RUNTIME_IDENTITY_MISSING",
            "message": "The web process is live, but its runtime identity is missing.",
            "recoverable": True,
        }
    running = bool(
        process_alive
        and config
        and instance
        and web_available(int(config["port"]), expected_instance=instance)
    )
    if process_alive and config and instance and not running and error is None:
        error = {
            "code": "IAM_WEB_RUNTIME_IDENTITY_MISMATCH",
            "message": "The live process on the configured port does not match IAM's saved web instance.",
            "recoverable": True,
        }
    return {
        "configured": web_config_path().exists(),
        "running": running,
        "pid": pid if running else None,
        "config": config,
        "error": error,
    }


def cmd_web_setup(args: argparse.Namespace) -> int:
    if pid_alive(read_pid("web")):
        raise IAMCommandError(
            "IAM_WEB_RUNNING",
            "Stop only the web companion with `iam web stop` before changing its configuration.",
            recoverable=True,
        )
    if args.lan and not args.acknowledge_network_risk:
        raise IAMCommandError(
            "IAM_WEB_LAN_ACK_REQUIRED",
            LAN_WARNING + " Re-run with --acknowledge-network-risk to enable LAN access.",
            recoverable=True,
        )
    try:
        mailbox = create_user_mailbox(args.address, args.display_name)
        config = configure_web(
            mailbox["address"],
            prompt_web_password(password_stdin=args.password_stdin),
            port=args.port,
            lan_enabled=args.lan,
        )
    except WebError as exc:
        raise_web_command_error(exc)
    data = {"user_mailbox": mailbox, "web": config}
    if getattr(args, "json_output", False):
        print_json(integration_envelope(data))
    else:
        print(f"Configured authenticated web access for {mailbox['display_name']} <{mailbox['address']}>.")
        print(f"Default URL: http://{'PC_PRIVATE_IP' if config['lan_enabled'] else '127.0.0.1'}:{config['port']}")
        if config["lan_enabled"]:
            print(f"WARNING: {LAN_WARNING}")
        print("Next: run `iam web start`. This does not restart IAM or Codex.")
    return 0


def cmd_web_password(args: argparse.Namespace) -> int:
    if pid_alive(read_pid("web")):
        raise IAMCommandError(
            "IAM_WEB_RUNNING",
            "Stop only the web companion with `iam web stop` before changing its password.",
            recoverable=True,
        )
    try:
        config = update_web_password(prompt_web_password(password_stdin=args.password_stdin))
    except WebError as exc:
        raise_web_command_error(exc)
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"web": config, "password_updated": True}))
    else:
        print("The IAM web application password was updated.")
    return 0


def cmd_web_start(args: argparse.Namespace) -> int:
    try:
        config = load_web_config()
    except WebError as exc:
        raise_web_command_error(exc)
    pid = read_pid("web")
    if pid_alive(pid):
        data = web_status_data()
        if not data["running"]:
            raise IAMCommandError(
                "IAM_WEB_PID_CONFLICT",
                "The saved web PID belongs to a live process, but the configured IAM web health check failed. "
                "IAM will not stop or replace an unidentified process.",
                recoverable=True,
            )
        if getattr(args, "json_output", False):
            print_json(integration_envelope({"web": data, "status": "already_running"}))
        else:
            print(f"IAM web companion is already running (pid {pid}).")
        return 0
    for stale_path in (pid_path("web"), web_instance_path()):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass

    lan_enabled = bool(config.get("lan_enabled"))
    host = "0.0.0.0" if lan_enabled else "127.0.0.1"
    port = int(config["port"])
    instance = secrets.token_urlsafe(32)
    command = [
        sys.executable,
        "-m",
        "iam_web",
        "--config",
        str(web_config_path()),
        "--host",
        host,
        "--port",
        str(port),
        "--instance-token",
        instance,
    ]
    pid = spawn_background(command, "web")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if web_available(port, expected_instance=instance):
            instance_file = web_instance_path()
            instance_file.write_text(instance + "\n", encoding="utf-8")
            if os.name != "nt":
                instance_file.chmod(0o600)
            data = {
                "configured": True,
                "running": True,
                "pid": pid,
                "config": {**public_web_config(config), "port": port, "lan_enabled": lan_enabled},
            }
            if getattr(args, "json_output", False):
                print_json(integration_envelope({"web": data, "status": "started"}))
            else:
                print(f"IAM web companion started (pid {pid}).")
                print(f"Open http://127.0.0.1:{port} on this PC.")
                if lan_enabled:
                    print(f"LAN devices may use this PC's private network address on port {port}.")
                    print(f"WARNING: {LAN_WARNING}")
                print("The IAM supervisor and Codex app-server were not restarted.")
            return 0
        if not pid_alive(pid):
            break
    if pid_alive(pid):
        stop_process(pid, tree=True)
    for stale_path in (pid_path("web", create=False), web_instance_path(create=False)):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass
    raise IAMCommandError(
        "IAM_WEB_START_FAILED",
        f"IAM web companion did not start. See {log_path('web')}",
        recoverable=True,
    )


def cmd_web_status(args: argparse.Namespace) -> int:
    data = web_status_data()
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"web": data}))
        return 0
    if not data["configured"]:
        print("IAM web companion is not configured. Run `iam web setup USER_MAILBOX`.")
        return 0
    if data["error"]:
        print(f"IAM web companion configuration is invalid: {data['error']['message']}")
        return 1
    config = data["config"]
    print(f"IAM web companion: {'running' if data['running'] else 'stopped'}")
    print(f"User mailbox:       {config['mailbox']}")
    print(f"Access:             {'local network' if config['lan_enabled'] else 'this PC only'}")
    print(f"Port:               {config['port']}")
    return 0


def cmd_web_stop(args: argparse.Namespace) -> int:
    pid = read_pid("web")
    if pid_alive(pid):
        status = web_status_data()
        if not status["running"]:
            raise IAMCommandError(
                "IAM_WEB_PID_CONFLICT",
                "The saved web PID belongs to a live process, but the configured IAM web health check failed. "
                "IAM will not terminate an unidentified process.",
                recoverable=True,
            )
        assert pid is not None
        stop_process(pid, tree=True)
        print(f"Stopped IAM web companion process tree {pid}.")
    else:
        print("IAM web companion is not running.")
    for stale_path in (pid_path("web", create=False), web_instance_path(create=False)):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass
    print("The IAM supervisor and Codex app-server were left running.")
    return 0


def cmd_connect_configure(args: argparse.Namespace) -> int:
    raw_mailboxes = args.human_mailbox if isinstance(args.human_mailbox, list) else [args.human_mailbox]
    mailboxes = list(dict.fromkeys(value.strip() for value in raw_mailboxes if isinstance(value, str) and value.strip()))
    data = {
        "schema_version": 1,
        "server_url": args.server_url.rstrip("/"),
        "node_id": args.node_id,
        "node_token": args.node_token,
        "human_mailbox": mailboxes[0] if mailboxes else "",
        "human_mailboxes": mailboxes,
        "enabled": True,
        "allow_insecure_development": bool(args.allow_insecure_development),
    }
    try:
        save_webconnect_config(data)
        state = ConnectorState.load()
        state.outbound.clear()
        WebConnectClient(data, state).baseline_existing_replies()
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_CONFIG_INVALID", str(exc), recoverable=True) from None
    print(f"Configured WebConnect node {args.node_id} for {args.server_url} as {', '.join(mailboxes)}.")
    return 0


def _restart_webconnect_if_running() -> int | None:
    """Reload only the outbound connector after a durable config mutation."""
    pid = read_pid("connect")
    if not pid_alive(pid):
        return None
    assert pid is not None
    stop_process(pid, tree=True)
    try:
        pid_path("connect", create=False).unlink()
    except FileNotFoundError:
        pass
    return spawn_background([sys.executable, "-m", "iam_webconnect"], "connect")


def cmd_connect_update(args: argparse.Namespace) -> int:
    try:
        data = load_webconnect_config()
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_NOT_CONFIGURED", str(exc), recoverable=True) from None
    if args.server_url is not None:
        data["server_url"] = args.server_url.rstrip("/")
    if args.node_id is not None:
        data["node_id"] = args.node_id
    if args.node_token is not None:
        data["node_token"] = args.node_token
    if args.allow_insecure_development is not None:
        data["allow_insecure_development"] = args.allow_insecure_development
    additions = args.human_mailbox or []
    data["human_mailboxes"] = list(dict.fromkeys([
        *data["human_mailboxes"], *(value.strip() for value in additions if value and value.strip()),
    ]))
    data["human_mailbox"] = data["human_mailbox"] if data["human_mailbox"] in data["human_mailboxes"] else data["human_mailboxes"][0]
    try:
        save_webconnect_config(data)
        state = ConnectorState.load()
        WebConnectClient(data, state).baseline_existing_replies()
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_CONFIG_INVALID", str(exc), recoverable=True) from None
    reloaded = _restart_webconnect_if_running()
    print(f"Updated WebConnect node {data['node_id']} as {', '.join(data['human_mailboxes'])}.")
    print(f"WebConnect client reloaded (pid {reloaded})." if reloaded else "WebConnect client is stopped; run `iam connect start` when ready.")
    return 0


def cmd_connect_removeuser(args: argparse.Namespace) -> int:
    try:
        data = load_webconnect_config()
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_NOT_CONFIGURED", str(exc), recoverable=True) from None
    mailbox = args.human_mailbox.strip()
    if mailbox not in data["human_mailboxes"]:
        raise IAMCommandError("IAM_CONNECT_MAILBOX_NOT_FOUND", f"{mailbox} is not configured on this WebConnect connector.", recoverable=True)
    remaining = [value for value in data["human_mailboxes"] if value != mailbox]
    if not remaining:
        raise IAMCommandError("IAM_CONNECT_LAST_MAILBOX", "A WebConnect connector must retain at least one human mailbox.", recoverable=True)
    data["human_mailboxes"] = remaining
    if data["human_mailbox"] == mailbox:
        data["human_mailbox"] = remaining[0]
    try:
        save_webconnect_config(data)
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_CONFIG_INVALID", str(exc), recoverable=True) from None
    reloaded = _restart_webconnect_if_running()
    print(f"Removed {mailbox} from the local WebConnect connector.")
    print("Revoke that account in WebConnect Settings too; otherwise the relay will safely reject this connector's mailbox list.")
    print(f"WebConnect client reloaded (pid {reloaded})." if reloaded else "WebConnect client is stopped; run `iam connect start` when ready.")
    return 0


def check_connector_installation() -> None:
    """Probe fresh-process imports without checkout/PYTHONPATH masking defects.

    Imports only: no connector, database, thread or network session is started.
    Keep failures body-free; subprocess stderr may contain local configuration.
    """
    probe = (
        "import iam_webconnect, iam_desktop, iam_messenger_reads, "
        "iam_messenger_stream, iam_messenger_connector, iam_messenger_pairing"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", probe],
            capture_output=True, text=True, timeout=30,
            **background_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise IAMCommandError(
            "IAM_CONNECT_INSTALLATION_CHECK_FAILED",
            "Could not verify installed connector modules. No connector was launched; check the IAM installation.",
            recoverable=True,
        ) from None
    if result.returncode:
        raise IAMCommandError(
            "IAM_CONNECT_INSTALLATION_INCOMPLETE",
            "Installed connector imports failed. Refresh the IAM installation and verify imports outside the checkout. No connector was launched.",
            recoverable=True,
        )


def cmd_connect_start(args: argparse.Namespace) -> int:
    try:
        config = load_webconnect_config()
    except WebConnectError as exc:
        raise IAMCommandError("IAM_CONNECT_NOT_CONFIGURED", str(exc), recoverable=True) from None
    pid = read_pid("connect")
    if pid_alive(pid):
        print(f"WebConnect process is already running (pid {pid}); relay connectivity is not verified.")
        return 0
    check_connector_installation()
    if not config["enabled"]:
        config["enabled"] = True
        save_webconnect_config(config)
    pid = spawn_background([sys.executable, "-m", "iam_webconnect"], "connect")
    print(f"WebConnect process launched (pid {pid}) for {config['server_url']}; relay registration is not yet verified.")
    return 0


def cmd_connect_status(args: argparse.Namespace) -> int:
    try:
        config = load_webconnect_config()
    except WebConnectError as exc:
        print(f"WebConnect: not configured ({exc})")
        return 0
    pid = read_pid("connect")
    status = "running" if pid_alive(pid) else ("stopped" if config["enabled"] else "disabled")
    print(f"WebConnect: {status}")
    from iam_webconnect import connector_health
    health = connector_health(config, pid, status == 'running')
    description = 'registered connection; recent relay pong verified' if health == 'ready' else health
    print(f"Connection: {description}")
    print("Delivery:   not verified by connection health; agent replies are separate")
    print(f"Server:     {config['server_url']}")
    print(f"Node:       {config['node_id']}")
    print(f"Humans:     {', '.join(config['human_mailboxes'])}")
    return 0


def cmd_connect_stop(args: argparse.Namespace) -> int:
    pid = read_pid("connect")
    if pid_alive(pid):
        assert pid is not None
        stop_process(pid, tree=True)
        print(f"Stopped WebConnect client process tree {pid}.")
    else:
        print("WebConnect client is not running.")
    try:
        config = load_webconnect_config()
        config["enabled"] = False
        save_webconnect_config(config)
    except WebConnectError:
        pass
    try:
        pid_path("connect", create=False).unlink()
    except FileNotFoundError:
        pass
    return 0


def cmd_start(args: argparse.Namespace) -> None:
    if not registered_projects():
        raise SystemExit("No projects are registered. Run `iam setup` in a project first.")
    print(f"Codex app-server: {ensure_appserver(args.url)}")
    print(f"IAM supervisor:   {ensure_daemon(args.url)}")
    print("Mail delivery is now running in the background for every registered project.")
    from iam_desktop import enabled as desktop_enabled, start_desktop
    if desktop_enabled():
        print(f"IAM desktop:      {start_desktop()}")


async def thread_available(url: str, thread_id: str, project_root: Path) -> bool:
    client = appserver_client(url, request_timeout=30.0)
    try:
        await client.connect()
        result = await client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread", {})
        # Validate this exact thread's project, not membership in a slow,
        # filtered and paginated list of recent sessions.
        cwd = thread.get("cwd")
        return (str(thread.get("id")) == thread_id
                and isinstance(cwd, str) and bool(cwd)
                and Path(cwd).resolve() == project_root.resolve())
    except (AppServerError, OSError):
        return False
    finally:
        await client.close()


def command_version(executable: str) -> tuple[str | None, str | None]:
    """Return a command's version line without invoking a shell."""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            **background_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    if completed.returncode != 0:
        return None, output[0] if output else f"exit code {completed.returncode}"
    return (output[0] if output else "version not reported"), None


def managed_mcp_status(project_root: Path) -> tuple[str, str]:
    config_path = project_root / ".codex" / "config.toml"
    try:
        content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CHECK_FAIL, f"missing {config_path}"
    except OSError as exc:
        return CHECK_FAIL, f"cannot read {config_path}: {exc}"
    if content.count(MCP_BEGIN) != 1 or content.count(MCP_END) != 1:
        return CHECK_FAIL, "managed InterAgentMail MCP markers are missing or duplicated"
    begin = content.find(MCP_BEGIN)
    end_marker = content.find(MCP_END)
    if end_marker < begin:
        return CHECK_FAIL, "managed InterAgentMail MCP markers are out of order"
    end = end_marker + len(MCP_END)
    block = content[begin:end]
    required = (
        "[mcp_servers.interagentmail]",
        "iam_mcp_server",
        "--project-root",
        toml_string(str(project_root)),
    )
    if not all(value in block for value in required):
        return CHECK_FAIL, "managed InterAgentMail MCP block is incomplete or points elsewhere"
    return CHECK_PASS, "managed MCP configuration is present"


def mailbox_counts(address: str) -> dict[str, int]:
    box = iam.mailbox(address)
    result: dict[str, int] = {}
    for folder in ("inbox", "sent", "archive", "timers"):
        path = box / folder
        result[folder] = sum(1 for item in path.glob("*.json") if item.is_file()) if path.is_dir() else 0
    return result


def read_log_tail(path: Path, lines: int, *, max_bytes: int = 4 * 1024 * 1024) -> list[str]:
    if lines <= 0:
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        remaining = min(end, max_bytes)
        chunks: list[bytes] = []
        newline_count = 0
        while remaining > 0 and newline_count <= lines:
            size = min(64 * 1024, remaining)
            handle.seek(-size, os.SEEK_CUR)
            chunk = handle.read(size)
            handle.seek(-size, os.SEEK_CUR)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
            remaining -= size
    return b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()[-lines:]


def summarize_log(name: str, lines: int = 200) -> dict[str, Any]:
    """Summarize a log without copying potentially private log text."""
    path = log_path(name, create=False)
    if not path.exists():
        return {"exists": False, "size_bytes": 0, "sampled_lines": 0, "levels": {}, "events": {}}
    try:
        stat = path.stat()
        tail = read_log_tail(path, lines)
    except OSError:
        return {"exists": True, "size_bytes": 0, "sampled_lines": 0, "levels": {"read errors": 1}, "events": {}}
    levels: Counter[str] = Counter()
    events: Counter[str] = Counter()
    for line in tail:
        upper = line.upper()
        if "ERROR" in upper:
            levels["errors"] += 1
        if "WARNING" in upper or " WARN " in upper:
            levels["warnings"] += 1
        for label, pattern in LOG_EVENT_PATTERNS.items():
            if pattern.search(line):
                events[label] += 1
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
        "sampled_lines": len(tail),
        "levels": dict(levels),
        "events": dict(events),
    }


def collect_diagnostics(
    url: str = DEFAULT_URL, *, log_lines: int = 200, project_root: str | Path | None = None
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append({"status": status, "name": name, "detail": detail})

    add(CHECK_PASS, "InterAgentMail", f"version {package_version()}")
    python_version = platform.python_version()
    if sys.version_info >= (3, 10):
        add(CHECK_PASS, "Python", f"{python_version} is supported")
    else:
        add(CHECK_FAIL, "Python", f"{python_version} is unsupported; Python 3.10 or newer is required")

    codex = shutil.which("codex.cmd") or shutil.which("codex")
    codex_version: str | None = None
    if not codex:
        add(CHECK_FAIL, "Codex CLI", "not found on PATH")
    else:
        codex_version, error = command_version(codex)
        if error:
            add(CHECK_FAIL, "Codex CLI", f"found at {codex}, but `codex --version` failed: {error}")
        else:
            add(CHECK_PASS, "Codex CLI", str(codex_version))

    if not iam.ROOT.exists():
        add(CHECK_FAIL, "Data directory", f"missing {iam.ROOT}; run `iam setup` first")
    elif not os.access(iam.ROOT, os.R_OK | os.W_OK):
        add(CHECK_FAIL, "Data directory", f"not readable and writable: {iam.ROOT}")
    else:
        add(CHECK_PASS, "Data directory", f"readable and writable: {iam.ROOT}")

    config_error: str | None = None
    try:
        projects = registered_projects()
    except (OSError, ValueError, SystemExit) as exc:
        projects = {}
        config_error = str(exc)
    if config_error:
        add(CHECK_FAIL, "Configuration", config_error)
    elif not iam.CONFIG.exists():
        add(CHECK_FAIL, "Configuration", f"missing {iam.CONFIG}; run `iam setup` first")
    elif not projects:
        add(CHECK_FAIL, "Configuration", "no projects are registered; run `iam setup` in a project")
    else:
        add(CHECK_PASS, "Configuration", f"{len(projects)} project(s) registered")

    target_root: Path | None = None
    if project_root is not None:
        target_root = Path(project_root).expanduser().resolve()
        selected: dict[str, dict[str, Any]] = {}
        for address, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            try:
                candidate = Path(entry.get("project_root", "")).expanduser().resolve()
            except (OSError, ValueError):
                continue
            if candidate == target_root:
                selected[address] = entry
        if selected:
            projects = selected
            add(CHECK_PASS, "Doctor target", f"registered project selected: {target_root}")
        else:
            projects = {}
            add(CHECK_FAIL, "Doctor target", f"project is not registered: {target_root}")

    try:
        server_reachable = asyncio.run(appserver_available(url))
    except (OSError, RuntimeError, ValueError):
        server_reachable = False
    add(
        CHECK_PASS if server_reachable else CHECK_WARN,
        "Codex app-server",
        f"reachable at {url}" if server_reachable else f"not reachable at {url}; run `iam start` when delivery is needed",
    )
    supervisor_pid = read_pid("supervisor")
    supervisor_running = pid_alive(supervisor_pid)
    if supervisor_running:
        add(CHECK_PASS, "IAM supervisor", "running")
    elif supervisor_pid:
        add(CHECK_WARN, "IAM supervisor", "stopped; a stale PID file is present")
    else:
        add(CHECK_WARN, "IAM supervisor", "stopped; run `iam start` when delivery is needed")

    web_configured = web_config_path().exists()
    web_running = False
    web_lan_enabled = False
    if web_configured:
        try:
            web_config = load_web_config()
            web_lan_enabled = bool(web_config.get("lan_enabled"))
            web_state = web_status_data()
            web_running = bool(web_state["running"])
            if web_state["error"]:
                add(CHECK_FAIL, "IAM web companion", f"runtime is invalid: {web_state['error']['message']}")
            elif web_lan_enabled:
                add(
                    CHECK_WARN,
                    "IAM web companion",
                    "LAN access is enabled; use only a trusted WPA2/WPA3 network and never forward the port",
                )
            elif web_running:
                add(CHECK_PASS, "IAM web companion", "authenticated local-only interface is running")
            else:
                add(CHECK_WARN, "IAM web companion", "configured but stopped; run `iam web start` when browser access is needed")
        except WebError as exc:
            add(CHECK_FAIL, "IAM web companion", f"configuration is invalid: {exc}")
    else:
        add(CHECK_PASS, "IAM web companion", "not configured (optional)")

    project_rows: list[dict[str, Any]] = []
    for address, entry in sorted(projects.items()):
        if not isinstance(entry, dict):
            project_rows.append({
                "address": address,
                "project_root": "unavailable",
                "sandbox": "unavailable",
                "approval_policy": "unavailable",
                "mailbox": {"inbox": 0, "sent": 0, "archive": 0},
            })
            add(CHECK_FAIL, f"Project {address}", "registration entry is not an object")
            continue
        root_value = str(entry.get("project_root", "")).strip()
        if not root_value:
            project_rows.append({
                "address": address,
                "project_root": "unavailable",
                "sandbox": str(entry.get("sandbox", "workspace-write")),
                "approval_policy": str(entry.get("approval_policy", "on-request")),
                "mailbox": {"inbox": 0, "sent": 0, "archive": 0},
            })
            add(CHECK_FAIL, f"Project {address}", "registration has no project_root")
            continue
        try:
            root = Path(root_value).expanduser().resolve()
            counts = mailbox_counts(address)
        except (OSError, ValueError, SystemExit) as exc:
            project_rows.append({
                "address": address,
                "project_root": root_value,
                "sandbox": str(entry.get("sandbox", "workspace-write")),
                "approval_policy": str(entry.get("approval_policy", "on-request")),
                "mailbox": {"inbox": 0, "sent": 0, "archive": 0},
            })
            add(CHECK_FAIL, f"Project {address}", f"registration cannot be inspected: {exc}")
            continue
        row: dict[str, Any] = {
            "address": address,
            "project_root": str(root),
            "sandbox": str(entry.get("sandbox", "workspace-write")),
            "approval_policy": str(entry.get("approval_policy", "on-request")),
            "mailbox": counts,
        }
        project_rows.append(row)
        if not root.is_dir():
            add(CHECK_FAIL, f"Project {address}", f"directory is missing: {root}")
            continue
        add(CHECK_PASS, f"Project {address}", f"directory exists: {root}")

        mcp_level, mcp_detail = managed_mcp_status(root)
        row["mcp"] = mcp_level == CHECK_PASS
        add(mcp_level, f"MCP {address}", mcp_detail)

        box = iam.mailbox(address)
        missing_folders = [name for name in ("inbox", "sent", "archive") if not (box / name).is_dir()]
        if missing_folders:
            add(CHECK_FAIL, f"Mailbox {address}", "missing folders: " + ", ".join(missing_folders))
        else:
            counts = row["mailbox"]
            add(
                CHECK_PASS,
                f"Mailbox {address}",
                f"inbox {counts['inbox']}, sent {counts['sent']}, archive {counts['archive']}",
            )

        sandbox = row["sandbox"]
        approval = row["approval_policy"]
        if sandbox not in SAFE_SANDBOXES or approval not in SAFE_APPROVAL_POLICIES:
            add(CHECK_FAIL, f"Safety {address}", f"unrecognized policy: {sandbox}, approvals {approval}")
        elif sandbox == "danger-full-access":
            add(CHECK_WARN, f"Safety {address}", f"full access enabled, approvals {approval}")
        else:
            add(CHECK_PASS, f"Safety {address}", f"{sandbox}, approvals {approval}")

        state_path = box / ".codex-bridge-state.json"
        try:
            state = DeliveryState.load(state_path)
        except (OSError, ValueError) as exc:
            row["thread_pinned"] = False
            add(CHECK_FAIL, f"Thread {address}", f"cannot read bridge state: {exc}")
            continue
        row["thread_pinned"] = bool(state.thread_id)
        row["delivered_count"] = len(state.delivered)
        if not state.thread_id:
            add(CHECK_WARN, f"Thread {address}", "not pinned yet; the first delivery will create one")
        elif server_reachable:
            try:
                resumable = asyncio.run(thread_available(url, state.thread_id, root))
            except (OSError, RuntimeError, ValueError):
                resumable = False
            add(
                CHECK_PASS if resumable else CHECK_FAIL,
                f"Thread {address}",
                "pinned thread is resumable" if resumable else "pinned thread is not available from the app-server",
            )
        else:
            add(CHECK_WARN, f"Thread {address}", "pinned, but resumability was not checked while the app-server is stopped")

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "environment": {
            "iam_version": package_version(),
            "python_version": python_version,
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "codex_version": codex_version or "unavailable",
            "data_root": str(iam.ROOT),
            "custom_data_root": bool(os.environ.get("INTERAGENTMAIL_HOME")),
        },
        "services": {
            "url": url,
            "appserver_reachable": server_reachable,
            "supervisor_running": supervisor_running,
            "web_configured": web_configured,
            "web_running": web_running,
            "web_lan_enabled": web_lan_enabled,
        },
        "coordination": {
            "teams": len(IAMService.team_list()),
            "outstanding_timers": sum(row["mailbox"].get("timers", 0) for row in project_rows),
        },
        "target_project": str(target_root) if target_root else None,
        "projects": project_rows,
        "checks": checks,
        "logs": {
            "supervisor": summarize_log("supervisor", log_lines),
            "app-server": summarize_log("app-server", log_lines),
            "web": summarize_log("web", log_lines),
        },
    }


def redaction_replacements(projects: list[dict[str, Any]]) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    for index, project in enumerate(projects, start=1):
        placeholder = f"<PROJECT_{index}>"
        project_root = str(project.get("project_root", ""))
        if project_root and project_root != "unavailable":
            replacements.append((project_root, placeholder))
        replacements.append((str(project.get("address", "")), placeholder))
        try:
            profile_path = iam.mailbox(str(project.get("address", ""))) / "profile.json"
            profile_data = iam.load(profile_path)
            replacements.append((str(profile_data.get("display_name", "")), placeholder))
        except (OSError, ValueError, SystemExit):
            pass
    replacements.extend(((str(iam.ROOT), "<IAM_DATA>"), (str(Path.home()), "<HOME>")))
    unique = {(source, replacement) for source, replacement in replacements if source}
    return sorted(unique, key=lambda item: len(item[0]), reverse=True)


def sanitize_report(text: str, replacements: list[tuple[str, str]]) -> str:
    result = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    for source, replacement in replacements:
        result = re.sub(re.escape(source), replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\/\s]+", r"\1<USER>", result)
    result = re.sub(r"(?i)(/home/|/Users/)[^/\s]+", r"\1<USER>", result)
    result = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<EMAIL>", result)
    result = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<ID>", result)
    result = re.sub(r"\b\d{10}-[0-9a-fA-F]{8}\b", "<MESSAGE_ID>", result)
    result = re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,})\b", "<TOKEN>", result)
    result = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1<TOKEN>", result)
    result = re.sub(
        r"(?i)\b((?:api[_-]?key|token|password|secret)\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1<REDACTED>",
        result,
    )
    return "".join(character for character in result if character in "\n\r\t" or ord(character) >= 32)


def render_report(diagnostics: dict[str, Any]) -> str:
    environment = diagnostics["environment"]
    services = diagnostics["services"]
    lines = [
        "# InterAgentMail diagnostic report",
        "",
        f"Generated: {diagnostics['generated_at']}",
        "",
        "> This report is automatically sanitized, but review it before sharing. It never includes mailbox or chat contents, environment variables, thread IDs, raw configuration, or raw app-server logs.",
        "",
        "## Environment",
        "",
        f"- InterAgentMail: {environment['iam_version']}",
        f"- Codex CLI: {environment['codex_version']}",
        f"- Python: {environment['python_implementation']} {environment['python_version']}",
        f"- Operating system: {environment['operating_system']} {environment['os_release']} ({environment['architecture']})",
        f"- Data directory: {environment['data_root']}",
        f"- Custom data directory configured: {'yes' if environment['custom_data_root'] else 'no'}",
        "",
        "## Services",
        "",
        f"- App-server endpoint: {services['url'] if services['url'].startswith(('ws://127.0.0.1:', 'ws://localhost:')) else '<CUSTOM_APP_SERVER_URL>'}",
        f"- App-server reachable: {'yes' if services['appserver_reachable'] else 'no'}",
        f"- Supervisor running: {'yes' if services['supervisor_running'] else 'no'}",
        f"- Web companion configured: {'yes' if services.get('web_configured') else 'no'}",
        f"- Web companion running: {'yes' if services.get('web_running') else 'no'}",
        f"- Web companion LAN access: {'yes' if services.get('web_lan_enabled') else 'no'}",
        "",
        "## Health checks",
        "",
    ]
    for check in diagnostics["checks"]:
        lines.append(f"- {check['status']} - {check['name']}: {check['detail']}")

    lines.extend(("", "## Registered projects", ""))
    if not diagnostics["projects"]:
        lines.append("No projects are registered.")
    for index, project in enumerate(diagnostics["projects"], start=1):
        counts = project["mailbox"]
        lines.extend((
            f"### Project {index}",
            "",
            f"- Address: {project['address']}",
            f"- Directory: {project['project_root']}",
            f"- Safety: {project['sandbox']}, approvals {project['approval_policy']}",
            f"- Managed MCP configured: {'yes' if project.get('mcp') else 'no'}",
            f"- Thread pinned: {'yes' if project.get('thread_pinned') else 'no'}",
            f"- Delivered-message IDs recorded: {project.get('delivered_count', 0)}",
            f"- Mailbox counts: inbox {counts['inbox']}, sent {counts['sent']}, archive {counts['archive']}, timers {counts.get('timers', 0)}",
            "",
        ))

    coordination = diagnostics.get("coordination", {})
    lines.extend((
        "## Coordination health",
        "",
        f"- Teams: {coordination.get('teams', 0)}",
        f"- Outstanding timers: {coordination.get('outstanding_timers', 0)}",
        "- Team names, memberships, timer notes, and timer IDs are intentionally omitted.",
        "",
    ))

    lines.extend((
        "## Recent log summary",
        "",
        "Raw log lines are intentionally omitted because Codex app-server logs can contain project source or private instructions.",
        "",
    ))
    for name, summary in diagnostics["logs"].items():
        if not summary["exists"]:
            lines.append(f"- {name}: not present")
            continue
        levels = summary.get("levels", {})
        events = summary.get("events", {})
        lines.append(
            f"- {name}: {summary['size_bytes']} bytes; sampled {summary['sampled_lines']} trailing lines; "
            f"errors {levels.get('errors', 0)}; warnings {levels.get('warnings', 0)}"
        )
        for event, count in sorted(events.items()):
            lines.append(f"  - {event}: {count}")
    lines.extend(("", "## Reporter notes", "", "Add reproduction steps and the behavior you expected before sharing this report.", ""))
    rendered = "\n".join(lines)
    return sanitize_report(rendered, redaction_replacements(diagnostics["projects"]))


def cmd_doctor(args: argparse.Namespace) -> int:
    diagnostics = collect_diagnostics(args.url, project_root=getattr(args, "project", None))
    counts = Counter(check["status"] for check in diagnostics["checks"])
    summary = {
        "passed": counts[CHECK_PASS],
        "warnings": counts[CHECK_WARN],
        "failed": counts[CHECK_FAIL],
        "healthy": counts[CHECK_FAIL] == 0,
    }
    if getattr(args, "json_output", False):
        print_json(integration_envelope({"summary": summary, "diagnostics": diagnostics}))
        return 1 if counts[CHECK_FAIL] else 0
    print("InterAgentMail doctor")
    print()
    for check in diagnostics["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    print()
    print(
        f"Summary: {counts[CHECK_PASS]} passed, {counts[CHECK_WARN]} warning(s), "
        f"{counts[CHECK_FAIL]} failed."
    )
    if counts[CHECK_FAIL]:
        print("Run `iam report` to create a sanitized support report.")
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if args.log_lines < 0 or args.log_lines > 10000:
        raise SystemExit("--log-lines must be between 0 and 10000")
    report = render_report(collect_diagnostics(args.url, log_lines=args.log_lines))
    if args.stdout or args.output == "-":
        print(report, end="")
        return 0
    if args.output:
        destination = Path(args.output).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = iam.ROOT / "reports" / f"iam-report-{stamp}.md"
    if destination.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing report: {destination}. Use --force to replace it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    if os.name != "nt":
        destination.chmod(0o600)
    print(f"Sanitized report written to {destination}")
    print("Review the report before attaching it to a public issue.")
    return 0


def wait_for_thread(address: str, url: str, project_root: Path, timeout: float = 60.0) -> str:
    path = iam.mailbox(address) / ".codex-bridge-state.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = DeliveryState.load(path)
        if state.thread_id and asyncio.run(thread_available(url, state.thread_id, project_root)):
            return state.thread_id
        time.sleep(0.5)
    raise SystemExit(f"No Codex thread was created for {address}. See {log_path('supervisor')}")


def cmd_email(args: argparse.Namespace) -> None:
    from iam_service import mailbox_service
    from iam_webconnect import email_registration
    selected = mailbox_service(os.environ.get("IAM_PROJECT_ROOT") or Path.cwd(), args.mailbox)
    print(json.dumps(email_registration(selected, args.action, args.local), indent=2))


def cmd_fast_path(args: argparse.Namespace) -> None:
    from iam_fast_path import configuration, configurations, registration_directory, register, set_enabled, public_config, Ledger
    selected = project_entry(args.project)[0] if args.project else None
    if selected is None and args.action in {"enable", "disable"}:
        selected = project_entry(str(Path.cwd()))[0]
    cfg = configuration(selected) if selected else configuration()
    if args.action == "register":
        if not args.resident or not args.verifier_url:
            raise IAMError("Registration requires --resident and --verifier-url; no secrets on the command line")
        address, _root, _entry = project_entry(args.project or str(Path.cwd()))
        owner = session_owner(address)
        root = Path(registered_projects()[session_workspace(owner)]["project_root"]).resolve()
        thread_id = DeliveryState.load(iam.mailbox(owner) / ".codex-bridge-state.json").thread_id
        with session_guard(owner, "workspace") as acquired:
            if not acquired or not thread_id or not asyncio.run(thread_available(configured_endpoint(), thread_id, root)):
                raise IAMError("Cannot verify the existing pinned session; no registration created")
            with session_guard("fast-path", "registration") as registration_lock:
                if not registration_lock:
                    raise IAMError("Another fast-path registration is in progress")
                result = register(mailbox=address, owner=owner, root=root, thread_id=thread_id,
                                  resident=args.resident, verifier_url=args.verifier_url, listen_port=args.port)
        print(json.dumps(result, indent=2))
        print(f"Disabled registration created. Private handoff: {registration_directory(result) / 'credentials.json'}. Never transfer through mail. No grant or wake was enabled.")
        return
    if args.action in {"enable", "disable"}:
        if cfg is None:
            raise IAMError("No fast-path registration exists")
        if args.action == "enable":
            # Validate current binding without briefly enabling a live listener.
            from iam_service import session_workspace as workspace
            if (session_owner(cfg["mailbox"]) != cfg["owner"] or
                DeliveryState.load(iam.mailbox(cfg["owner"]) / ".codex-bridge-state.json").thread_id != cfg["threadBinding"] or
                Path(registered_projects()[workspace(cfg["owner"])]["project_root"]).resolve() != Path(cfg["root"]).resolve()):
                raise IAMError("Fast-path registration no longer matches the current session")
        result = set_enabled(args.action == "enable", selected)
        print(json.dumps(result, indent=2))
        print("Restart only the IAM supervisor to load listener changes. This does not enable any cDiscord attention record.")
        return
    rows = [cfg] if selected and cfg else ([] if selected else configurations())
    print(json.dumps({"registrations": [{"registration": public_config(item),
                      "private_handoff_directory": str(registration_directory(item)),
                      "deliveries": Ledger(registration_directory(item) / "deliveries.sqlite3").summary()}
                     for item in rows], "steering_available": False}, indent=2))


def cmd_heartbeat(args: argparse.Namespace) -> None:
    from iam_heartbeat import Heartbeats
    address, root, _entry = project_entry(args.project or str(Path.cwd()))
    store = Heartbeats()
    if args.action == "status":
        result = store.state(address)
    elif args.action == "history":
        result = store.history(address)
    elif args.action == "resolve":
        from iam_heartbeat import reconcile, release_with_receipt, reviewer_identity
        if not args.delivery_id or not args.resolution or not args.verified_by:
            raise IAMError("Resolution requires --delivery-id, --resolution and --verified-by; no blind retry")
        actor = reviewer_identity(args.verified_by)
        owner = session_owner(address)
        with session_guard(owner, "workspace") as acquired:
            if not acquired:
                raise IAMError("Session delivery is busy; resolve during an idle window")
            if args.resolution == "accepted":
                result = asyncio.run(reconcile(owner, configured_endpoint(), appserver_client,
                    delivery_id=args.delivery_id,
                    actor=actor, turn_id=args.turn_id))
                if not result:
                    raise IAMError("Acceptance could not be verified in thread history; heartbeat hold remains")
            else:
                if not args.evidence_file:
                    raise IAMError("Retry release requires --evidence-file with a verified protocol rejection receipt")
                # Positive acceptance wins over a contradictory supplied rejection.
                result = asyncio.run(reconcile(owner, configured_endpoint(), appserver_client,
                    delivery_id=args.delivery_id,
                    actor=actor))
                if not result:
                    result = release_with_receipt(address, args.delivery_id, args.evidence_file, args.verified_by)
    elif args.action == "note":
        if not args.message or not args.sender:
            raise IAMError("Use --message and --from-name for an owner heartbeat note")
        result = store.owner_note(address, args.message, args.sender)
    elif args.action == "clear-note":
        result = store.owner_note(address)
    else:
        owner = session_owner(address)
        thread_id = DeliveryState.load(iam.mailbox(owner) / ".codex-bridge-state.json").thread_id
        if args.action == "enable":
            primary_root = Path(registered_projects()[session_workspace(owner)]["project_root"])
            if not thread_id or not asyncio.run(thread_available(configured_endpoint(), thread_id, primary_root)):
                raise IAMError("Cannot enable heartbeat: existing pinned thread could not be verified. No thread was created.")
        result = store.configure(address, args.action, thread_id)
    print(json.dumps(result, indent=2))


def cmd_session(args: argparse.Namespace) -> None:
    address, _root, _entry = project_entry(args.project or str(Path.cwd()))
    if args.session_command in {"require-existing", "allow-replacement"}:
        owner = session_owner(address)
        with session_guard(owner, "workspace") as available:
            if not available:
                raise IAMError("Session delivery is busy; retry shortly")
            path = iam.mailbox(address) / ".delivery-requirement.json"
            if args.session_command == "allow-replacement":
                if path.exists():
                    path.unlink()
                print(f"Removed {address}'s delivery requirement; ordinary session selection applies.")
                return
            if args.session_owner and args.session_owner != owner:
                raise IAMError("Expected session owner does not match the current owner")
            current = DeliveryState.load(iam.mailbox(owner) / ".codex-bridge-state.json").thread_id
            if not current or current != args.thread_id:
                raise IAMError("An existing matching pinned thread is required; binding left unchanged")
            iam.write_atomic(path, {"session_owner": owner, "thread_id": current})
            print(f"{address}: delivery requires existing session {owner}/{current}; binding left unchanged.")
            return
    config = load_config()
    links = config.setdefault("session_mailboxes", {})
    if args.session_command == "link":
        owner, _owner_root, _owner_entry = project_entry(args.primary)
        if address == owner or owner in links or address in links.values():
            raise IAMError("Cannot link a primary with members, link to a secondary, or link to itself")
        if address in links and links[address] != owner:
            raise IAMError("Unlink this mailbox before choosing another primary")
        links[address] = owner
        session_owner(address, config)
        save_config(config)
        print(f"{address} now shares {owner}'s session and execution settings. Mail stays separate.")
    elif args.session_command == "unlink":
        if address not in links:
            raise IAMError("This project is not a secondary mailbox")
        links.pop(address)
        save_config(config)
        print(f"Unlinked {address}. Its previous independent thread binding is retained; use iam open --thread-id to change it.")
    else:
        owner = session_owner(address, config)
        members = [name for name in config.get("projects", {}) if session_owner(name, config) == owner]
        print(f"Primary: {owner}; mailboxes: {', '.join(sorted(members))}")


@contextmanager
def session_guard(owner: str, purpose: str):
    """OS-held locks survive process crashes without stale PID ownership guesses."""
    directory = runtime_dir() / "shared-sessions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{iam.safe_segment(owner, 'mailbox')}.{purpose}.lock"
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def thread_resume_params(
    thread_id: str,
    root: Path,
    entry: dict[str, Any],
    *,
    runtime_workspace_roots: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "cwd": str(root),
        "excludeTurns": True,
    }
    if runtime_workspace_roots:
        params["runtimeWorkspaceRoots"] = [str(root)]
    return params


async def apply_thread_policy(
    url: str,
    thread_id: str,
    root: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Resume a thread with its registered unattended policy before opening a terminal."""
    client = appserver_client(url)
    try:
        await client.connect()
        result = await client.request("thread/resume", thread_resume_params(thread_id, root, entry))
        if str(result.get("thread", {}).get("id", "")) != thread_id:
            raise IAMError("App-server did not confirm the requested thread; no terminal was opened")
        return await apply_registered_thread_policy(
            client,
            thread_id,
            str(entry.get("sandbox", "workspace-write")),
            str(entry.get("approval_policy", "on-request")),
            root,
        )
    except AppServerError as exc:
        raise IAMError(
            f"Could not apply the registered approval and sandbox policy to thread {thread_id}: {exc}. "
            "No terminal was opened and the IAM binding was left unchanged."
        ) from None
    finally:
        await client.close()


async def switch_session_workspace(client: AppServerClient, thread_id: str, root: Path, entry: dict[str, Any]) -> None:
    """Change actual runtime cwd, workspace roots, and policy; never start a model turn."""
    result = await client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
    status = result["thread"].get("status", {}).get("type")
    if status not in {"idle", "notLoaded"}:
        raise IAMError("Shared session is busy or awaiting approval; wait for its turn to finish")
    confirmed = await client.request(
        "thread/resume",
        thread_resume_params(thread_id, root, entry, runtime_workspace_roots=True),
    )
    if confirmed["thread"].get("status", {}).get("type") != "idle":
        raise IAMError("Shared session is not idle; workspace was not switched")
    verify_session_workspace(confirmed, thread_id, root)
    await apply_registered_thread_policy(
        client,
        thread_id,
        str(entry.get("sandbox", "workspace-write")),
        str(entry.get("approval_policy", "on-request")),
        root,
    )


def verify_session_workspace(confirmed: dict[str, Any], thread_id: str, root: Path) -> None:
    roots = confirmed.get("runtimeWorkspaceRoots")
    if (confirmed.get("thread", {}).get("id") != thread_id
            or not confirmed.get("cwd") or Path(confirmed["cwd"]).resolve() != root
            or not isinstance(roots, list) or [Path(p).resolve() for p in roots] != [root]):
        raise IAMError("App-server did not confirm the requested cwd and workspace roots; shared delivery remains paused")


def cmd_open_shared(args: argparse.Namespace, address: str, root: Path, owner: str) -> None:
    entry = registered_projects()[owner]
    state = DeliveryState.load(iam.mailbox(owner) / ".codex-bridge-state.json")
    requested = getattr(args, "thread_id", None)
    if requested and address != owner:
        raise IAMError("Change the shared thread from the primary project, not a secondary mailbox")
    thread_id = requested or state.thread_id
    if not thread_id:
        raise IAMError("Pin an existing thread from the primary with iam open --thread-id before opening a shared workspace")
    with session_guard(owner, "terminal") as terminal:
        if not terminal:
            raise IAMError("This shared session already has an IAM terminal open; exit it before opening another workspace")
        with session_guard(owner, "workspace") as switching:
            if not switching:
                raise IAMError("Shared-session delivery or a workspace switch is in progress; retry shortly")
            ensure_appserver(args.url)
            marker = iam.mailbox(owner) / ".session-workspace.json"

            async def switch():
                client = appserver_client(args.url, experimental_api=True)
                try:
                    await client.connect()
                    # Do not quarantine a busy thread: its current workspace is still valid.
                    read = await client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
                    if read["thread"].get("status", {}).get("type") not in {"idle", "notLoaded"}:
                        raise IAMError("Shared session is busy or awaiting approval; wait for its turn to finish")
                    iam.write_atomic(marker, {"mailbox": address, "verified": False})
                    await switch_session_workspace(client, thread_id, root, entry)
                    iam.write_atomic(marker, {"mailbox": address, "verified": True})
                except AppServerError as exc:
                    raise IAMError(f"Could not verify the shared workspace: {exc}. No terminal was opened; retry after resolving the app-server error.") from None
                finally:
                    await client.close()

            asyncio.run(switch())
            if requested:
                state.thread_id = requested
                state.save()
        ensure_daemon(args.url)
        command = [codex_executable(), "resume"]
        if not is_managed_endpoint(args.url):
            command.extend(["--remote", args.url, "--disable", "daemon_auto_start"])
        else:
            command.extend(["-s", str(entry.get("sandbox", "workspace-write")),
                            "-a", str(entry.get("approval_policy", "on-request"))])
        # Remote resume rejects permission CLI overrides; the workspace switch
        # above already supplies the primary's policy through thread/resume.
        command.extend(["-C", str(root), "--include-non-interactive", thread_id])
        print(f"Opening {owner}'s shared session in {root}; mailbox identities remain separate.")
        raise SystemExit(subprocess.run(command, cwd=str(root), check=False).returncode)


def cmd_open(args: argparse.Namespace) -> None:
    root_value = args.project or str(Path.cwd())
    try:
        address, root, entry = project_entry(root_value)
    except SystemExit:
        setup_project(root_value)
        address, root, entry = project_entry(root_value)
    owner = session_owner(address)
    links = load_config().get("session_mailboxes", {})
    if address in links or address in links.values():
        return cmd_open_shared(args, address, root, owner)
    ensure_appserver(args.url)
    ensure_daemon(args.url)
    state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
    base_command = [codex_executable()]
    if not is_managed_endpoint(args.url):
        base_command.extend(["--remote", args.url, "--disable", "daemon_auto_start"])
    base_command.extend([
        "-C", str(root),
        "-s", str(entry.get("sandbox", "workspace-write")),
        "-a", str(entry.get("approval_policy", "on-request")),
    ])
    command = base_command
    requested_thread_id = getattr(args, "thread_id", None)
    if requested_thread_id:
        if not asyncio.run(thread_available(args.url, requested_thread_id, root)):
            raise SystemExit(
                f"Codex thread {requested_thread_id} is not available for {root}. "
                "The existing IAM thread binding was left unchanged."
            )
        thread_id = requested_thread_id
    elif state.thread_id:
        thread_id = wait_for_thread(address, args.url, root)
    else:
        thread_id = None
    if thread_id:
        asyncio.run(apply_thread_policy(args.url, thread_id, root, entry))
        if requested_thread_id and state.thread_id != requested_thread_id:
            state.thread_id = requested_thread_id
            state.save()
            print(f"Pinned {address} to explicitly selected Codex thread {requested_thread_id}.")
        command = [codex_executable(), "resume"]
        if not is_managed_endpoint(args.url):
            command.extend(["--remote", args.url, "--disable", "daemon_auto_start"])
        else:
            command.extend(["-s", str(entry.get("sandbox", "workspace-write")),
                            "-a", str(entry.get("approval_policy", "on-request"))])
        # Remote resume must use the server-side policy applied above, not
        # CLI overrides (which Codex explicitly rejects for remote tasks).
        command.extend(["-C", str(root), "--include-non-interactive", thread_id])
    raise SystemExit(subprocess.run(command, check=False).returncode)


def collect_status(url: str = DEFAULT_URL) -> dict[str, Any]:
    projects = registered_projects()
    try:
        appserver_reachable = asyncio.run(appserver_available(url))
    except (OSError, RuntimeError, ValueError):
        appserver_reachable = False
    supervisor_pid = read_pid("supervisor")
    supervisor_running = pid_alive(supervisor_pid)
    web = web_status_data()
    project_rows: list[dict[str, Any]] = []
    for address, entry in sorted(projects.items()):
        if not isinstance(entry, dict):
            project_rows.append({"address": address, "status": "invalid"})
            continue
        try:
            state = DeliveryState.load(iam.mailbox(session_owner(address)) / ".codex-bridge-state.json")
            thread_state = "pinned" if state.thread_id else "pending"
            thread_id = state.thread_id
        except (OSError, ValueError):
            thread_state = "invalid"
            thread_id = None
        try:
            requirement = check_delivery_requirement(address)
            delivery_policy = {"state": "ready" if requirement else "unrestricted", "requirement": requirement}
        except (IAMError, OSError, ValueError) as exc:
            delivery_policy = {"state": "held", "reason": str(exc)}
        project_rows.append({
            "address": address,
            "display_name": iam.display_name(address),
            "project_root": str(entry.get("project_root", "")),
            "sandbox": str(entry.get("sandbox", "workspace-write")),
            "approval_policy": str(entry.get("approval_policy", "on-request")),
            "thread_state": thread_state,
            "thread_id": thread_id,
            "session_owner": session_owner(address),
            "delivery_policy": delivery_policy,
        })
    return {
        "services": {
            "appserver": {"reachable": appserver_reachable, "url": url},
            "supervisor": {"running": supervisor_running, "pid": supervisor_pid if supervisor_running else None},
            "web": web,
        },
        "projects": project_rows,
    }


def cmd_status(args: argparse.Namespace) -> int:
    status = collect_status(args.url)
    if getattr(args, "json_output", False):
        print_json(integration_envelope(status))
        return 0
    services = status["services"]
    print(f"App-server: {'reachable' if services['appserver']['reachable'] else 'stopped'} ({args.url})")
    supervisor = services["supervisor"]
    print(
        f"Supervisor: {'running' if supervisor['running'] else 'stopped'}"
        + (f" (pid {supervisor['pid']})" if supervisor["pid"] else "")
    )
    web = services["web"]
    if web["error"]:
        print(f"Web:        invalid configuration ({web['error']['code']})")
    else:
        print(
            f"Web:        {'running' if web['running'] else 'stopped' if web['configured'] else 'not configured'}"
            + (f" ({'LAN' if web['config']['lan_enabled'] else 'this PC only'}, port {web['config']['port']})" if web["config"] else "")
        )
    if not status["projects"]:
        print("Projects:   none; run `iam setup`")
        return 0
    print("Projects:")
    for project in status["projects"]:
        if project.get("status") == "invalid":
            print(f"  {project['address']}: invalid registration")
        else:
            print(f"  {project['address']}: {project['project_root']} (thread {project['thread_id'] or 'pending'})")
            if project["delivery_policy"]["state"] == "held":
                print(f"    Delivery held: {project['delivery_policy']['reason']}")
    return 0


def stop_process(pid: int, *, tree: bool = False) -> None:
    if os.name == "nt" and tree:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if completed.returncode != 0 and pid_alive(pid):
            detail = (completed.stderr or completed.stdout or "taskkill failed").strip()
            raise IAMCommandError(
                "IAM_PROCESS_STOP_FAILED",
                f"Could not stop process tree {pid}: {detail}",
                recoverable=True,
                details={"pid": pid},
            )
    else:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if pid_alive(pid):
        raise IAMCommandError(
            "IAM_PROCESS_STOP_FAILED",
            f"Process {pid} did not exit after it was asked to stop.",
            recoverable=True,
            details={"pid": pid},
        )


def wait_compatibility_port_release(url: str, *, wait_seconds: float = 10.0) -> None:
    """Launcher exit alone does not prove Windows released its listening socket."""
    deadline = time.monotonic() + wait_seconds
    while not compatibility_port_available(url):
        if time.monotonic() >= deadline:
            raise IAMCommandError(
                "IAM_APPSERVER_SHUTDOWN_INCOMPLETE",
                f"Codex shutdown is incomplete: {url} is still occupied after "
                f"{wait_seconds:g} seconds. No replacement was launched; ownership "
                "records were preserved. Inspect the port owner and app-server log "
                "before retrying. Do not stop an unverified process.",
                recoverable=True,
                details={"endpoint": url, "recorded_pid": read_pid("app-server")},
            )
        time.sleep(0.25)


def cmd_stop(args: argparse.Namespace) -> None:
    from iam_desktop import control as desktop_control
    if desktop_control('stop'):
        print('Stopped IAM desktop companion.')
    pid = read_pid("supervisor")
    if pid_alive(pid):
        assert pid is not None
        stop_process(pid, tree=os.name == "nt")
        print(f"Stopped IAM supervisor process {pid}.")
    else:
        print("IAM supervisor is not running.")
    if args.all:
        endpoint = configured_endpoint()
        compatibility = load_config().get("windows_hidden_console_port") is not None
        server_pid = read_pid("app-server")
        if pid_alive(server_pid):
            assert server_pid is not None
            stop_process(server_pid, tree=True)
            print(f"IAM-owned Codex app-server launcher {server_pid} exited.")
        else:
            print("No legacy IAM-owned Codex app-server is running.")
            try:
                if not is_managed_endpoint(endpoint) and asyncio.run(appserver_available(endpoint)):
                    print(
                        "An unmanaged Codex app-server is still reachable and was left running. "
                        "Stop it from the terminal that started it, or close that Codex process."
                    )
            except (OSError, RuntimeError, ValueError):
                pass
        if compatibility:
            # Check even with a dead/missing launcher PID: an orphaned listener
            # must not erase the remaining evidence or trigger a replacement.
            wait_compatibility_port_release(endpoint)
            print("Compatibility endpoint released.")
        try:
            pid_path("app-server", create=False).unlink()
        except FileNotFoundError:
            pass
    else:
        print("The shared Codex app-server was left running. `iam stop --all` stops IAM-owned servers, not the Codex-managed daemon.")


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(argparse.Namespace(all=True))
    cmd_start(args)


def cmd_daemon(args: argparse.Namespace) -> None:
    log_file = getattr(args, "log_file", None)
    if log_file:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[handler],
            force=True,
        )
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    existing = read_pid("supervisor")
    if pid_alive(existing) and existing != os.getpid():
        raise SystemExit(f"IAM supervisor is already running (pid {existing}).")
    pid_path("supervisor").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    LOG.info("IAM supervisor started on %s with pid %s", args.url, os.getpid())
    try:
        asyncio.run(daemon_loop(args.url))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            pid_path("supervisor").unlink()
        except FileNotFoundError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="iam",
        description="Set up and run automatic InterAgentMail delivery for Codex projects.",
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    sub = result.add_subparsers(dest="command", required=True)
    from iam_desktop import command as desktop_command
    desktop = sub.add_parser('desktop', help='Manage the optional local desktop buddy list.')
    desktop.add_argument('action', choices=['enable', 'disable', 'start', 'stop', 'status', 'reply'])
    desktop.add_argument('--request-id')
    desktop_body = desktop.add_mutually_exclusive_group()
    desktop_body.add_argument('--body', help='Single-line reply text; use --body-file for multiline text on Windows')
    desktop_body.add_argument('--body-file', help='UTF-8 file containing the exact reply body (safe with Windows .cmd launchers)')
    desktop.add_argument('--mailbox')
    desktop.add_argument('--file', action='append', help='Explicit document attachment for desktop reply; repeat for multiple files')
    desktop.add_argument('--image', action='append', help='Explicit PNG/JPEG/WebP attachment; repeat up to four images')
    desktop.set_defaults(func=desktop_command)
    email = sub.add_parser("email", help="Choose a mailbox-bound external email address after owner enrollment.")
    email.add_argument("action", choices=["available", "register", "status"])
    email.add_argument("local", nargs="?")
    email.add_argument("--mailbox", help="Only the bound or explicitly linked mailbox may be selected.")
    email.set_defaults(func=cmd_email)
    fast_path = sub.add_parser("fast-path", help="Owner controls for disabled-by-default external attention delivery.")
    fast_path.add_argument("action", choices=["register", "enable", "disable", "status"])
    fast_path.add_argument("project", nargs="?")
    fast_path.add_argument("--resident")
    fast_path.add_argument("--verifier-url")
    fast_path.add_argument("--port", type=int, default=43200)
    fast_path.set_defaults(func=cmd_fast_path)

    heartbeat = sub.add_parser("heartbeat", help="Owner controls for opt-in one-shot session heartbeats.")
    heartbeat.add_argument("action", choices=["enable", "disable", "pause", "resume", "status", "note", "clear-note", "history", "resolve"])
    heartbeat.add_argument("project", nargs="?")
    heartbeat.add_argument("--message", help="One-time owner note for the next heartbeat; replaces any pending note.")
    heartbeat.add_argument("--from-name", dest="sender", help="Owner's display name for the note.")
    heartbeat.add_argument("--delivery-id", help="Exact held delivery to resolve.")
    heartbeat.add_argument("--resolution", choices=["accepted", "retry"])
    heartbeat.add_argument("--verified-by", help="Identity of the human reviewing acceptance/rejection evidence.")
    heartbeat.add_argument("--turn-id", help="Optional known turn ID to constrain acceptance verification.")
    heartbeat.add_argument("--evidence-file", help="Body-free, correlated protocol rejection receipt; never a timeout or missing-history assertion.")
    heartbeat.set_defaults(func=cmd_heartbeat)
    from iam_hooks import add_parser as add_hooks_parser
    add_hooks_parser(sub)

    capabilities = sub.add_parser("capabilities", help="Describe IAM's stable automation interface.")
    capabilities.add_argument("--json", action="store_true", dest="json_output")
    capabilities.set_defaults(func=cmd_capabilities)

    user = sub.add_parser("user", help="Create and inspect human IAM mailboxes.")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    user_create = user_sub.add_parser("create", help="Create or update a human mailbox.")
    user_create.add_argument("address", help="Unique mailbox address used as the sender identity.")
    user_create.add_argument("--display-name", help="Optional human-facing name; defaults to the address.")
    user_create.add_argument("--json", action="store_true", dest="json_output")
    user_create.set_defaults(func=cmd_user_create)
    user_list = user_sub.add_parser("list", help="List human IAM mailboxes.")
    user_list.add_argument("--json", action="store_true", dest="json_output")
    user_list.set_defaults(func=cmd_user_list)

    web = sub.add_parser("web", help="Manage the standalone authenticated browser interface.")
    web_sub = web.add_subparsers(dest="web_command", required=True)
    web_setup = web_sub.add_parser("setup", help="Create a user mailbox and configure authenticated web access.")
    web_setup.add_argument("address", help="User mailbox address to create or use.")
    web_setup.add_argument("--display-name", help="Optional human-facing name; defaults to the address.")
    web_setup.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    web_setup.add_argument("--lan", action="store_true", help="Allow access from the local network instead of only this PC.")
    web_setup.add_argument("--acknowledge-network-risk", action="store_true", help="Confirm the LAN security warning.")
    web_setup.add_argument("--password-stdin", action="store_true", help="Read the password from standard input for automation.")
    web_setup.add_argument("--json", action="store_true", dest="json_output")
    web_setup.set_defaults(func=cmd_web_setup)

    web_start = web_sub.add_parser("start", help="Start only the configured standalone web companion in the background.")
    web_start.add_argument("--json", action="store_true", dest="json_output")
    web_start.set_defaults(func=cmd_web_start)

    web_status = web_sub.add_parser("status", help="Show standalone web companion status.")
    web_status.add_argument("--json", action="store_true", dest="json_output")
    web_status.set_defaults(func=cmd_web_status)
    web_stop = web_sub.add_parser("stop", help="Stop only the standalone web companion.")
    web_stop.set_defaults(func=cmd_web_stop)
    web_password = web_sub.add_parser("password", help="Change the web application password while the companion is stopped.")
    web_password.add_argument("--password-stdin", action="store_true", help="Read the password from standard input for automation.")
    web_password.add_argument("--json", action="store_true", dest="json_output")
    web_password.set_defaults(func=cmd_web_password)

    connect = sub.add_parser("connect", help="Manage the outbound WebConnect transport client.")
    connect_sub = connect.add_subparsers(dest="connect_command", required=True)
    connect_configure = connect_sub.add_parser("configure", help="Configure a compatible WebConnect server and bound human mailbox.")
    connect_configure.add_argument("--server-url", required=True)
    connect_configure.add_argument("--node-id", required=True)
    connect_configure.add_argument("--node-token", required=True)
    connect_configure.add_argument(
        "--human-mailbox",
        action="append",
        required=True,
        help="Local IAM user mailbox. Repeat to authorize separate human identities such as Echo.",
    )
    connect_configure.add_argument("--allow-insecure-development", action="store_true")
    connect_configure.set_defaults(func=cmd_connect_configure)
    connect_update = connect_sub.add_parser("update", help="Persistently update selected WebConnect settings or add human mailboxes.")
    connect_update.add_argument("--server-url")
    connect_update.add_argument("--node-id")
    connect_update.add_argument("--node-token")
    connect_update.add_argument("--human-mailbox", action="append", help="Add one local IAM user mailbox without removing existing ones. Repeat as needed.")
    connect_update.add_argument("--allow-insecure-development", action=argparse.BooleanOptionalAction, default=None)
    connect_update.set_defaults(func=cmd_connect_update)
    connect_removeuser = connect_sub.add_parser("removeuser", help="Remove one human mailbox from the local WebConnect connector.")
    connect_removeuser.add_argument("--human-mailbox", required=True)
    connect_removeuser.set_defaults(func=cmd_connect_removeuser)
    connect_start = connect_sub.add_parser("start", help="Start the configured outbound WebConnect client.")
    connect_start.set_defaults(func=cmd_connect_start)
    connect_status = connect_sub.add_parser("status", help="Show configured WebConnect client status.")
    connect_status.set_defaults(func=cmd_connect_status)
    connect_stop = connect_sub.add_parser("stop", help="Stop only the WebConnect client.")
    connect_stop.set_defaults(func=cmd_connect_stop)

    def add_registration_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("projects", nargs="*", help="Project folders. Defaults to the current folder.")
        command.add_argument("--display-name", help="Optional human-facing mailbox label; defaults to the address.")
        command.add_argument("--full-access", action="store_true", help="Use Codex danger-full-access for this project.")
        command.add_argument("--approval-policy", choices=("untrusted", "on-request", "never"), default="on-request")
        command.add_argument("--process-existing", action="store_true", help="Process inbox mail already present on first setup.")
        command.add_argument(
            "--claim-empty-mailbox",
            action="store_true",
            help="Claim only an untouched mailbox created by `InterAgentMail init`; never migrates mail or human identities.",
        )
        command.add_argument("--json", action="store_true", dest="json_output")
        command.set_defaults(func=cmd_setup)

    setup = sub.add_parser("setup", help="Register projects, initialize mailboxes, and configure MCP.")
    add_registration_arguments(setup)
    register = sub.add_parser("register", help="Automation-friendly alias for setup with stable JSON output.")
    add_registration_arguments(register)

    unregister = sub.add_parser(
        "unregister",
        help="Stop managing projects and remove only IAM's managed MCP configuration.",
    )
    unregister.add_argument("projects", nargs="*", help="Project folders. Defaults to the current folder.")
    unregister.add_argument("--json", action="store_true", dest="json_output")
    unregister.set_defaults(func=cmd_unregister)

    session = sub.add_parser("session", help="Manage explicit shared-session mailboxes (local administrator only).")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    for action in ("link", "unlink", "show", "require-existing", "allow-replacement"):
        command = session_sub.add_parser(action)
        command.add_argument("project", nargs="?", help="Registered project folder; defaults to the current directory.")
        if action == "link":
            command.add_argument("--primary", required=True, help="Registered project folder that owns the shared session.")
        if action == "require-existing":
            command.add_argument("--thread-id", required=True, help="Exact already-pinned thread required for delivery.")
            command.add_argument("--session-owner", help="Assert the effective primary mailbox address.")
        command.set_defaults(func=cmd_session)

    start = sub.add_parser("start", help="Start the Codex managed daemon and mail supervisor in the background.")
    start.add_argument("--url", default=DEFAULT_URL, help="Use managed:// (default) or an explicit legacy ws:// endpoint.")
    start.set_defaults(func=cmd_start)

    mode = sub.add_parser("app-server-mode", help="Choose managed or Windows hidden-console compatibility mode (services must be stopped).")
    mode.add_argument("mode", choices=("managed", "hidden-console"))
    mode.add_argument("--port", type=int, default=4500, help="Loopback-only compatibility port; default 4500.")
    mode.set_defaults(func=cmd_app_server_mode)

    status = sub.add_parser("status", help="Show service and registered-project status.")
    status.add_argument("--url", default=DEFAULT_URL)
    status.add_argument("--json", action="store_true", dest="json_output")
    status.set_defaults(func=cmd_status)

    restart = sub.add_parser("restart", help="Restart IAM delivery without opening visible windows.")
    restart.add_argument("--url", default=DEFAULT_URL)
    restart.set_defaults(func=cmd_restart)

    open_command = sub.add_parser("open", help="Open the registered project's pinned Codex session.")
    open_command.add_argument("project", nargs="?", help="Project folder. Defaults to the current folder.")
    open_command.add_argument("--url", default=DEFAULT_URL)
    open_command.add_argument(
        "--thread-id",
        help="Explicitly validate, pin, and open this Codex thread instead of the current IAM binding.",
    )
    open_command.set_defaults(func=cmd_open)

    doctor = sub.add_parser("doctor", help="Run read-only health checks for IAM, Codex, and registered projects.")
    doctor.add_argument("--url", default=DEFAULT_URL)
    doctor.add_argument("--project", help="Limit project checks to one registered project folder.")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.set_defaults(func=cmd_doctor)

    report = sub.add_parser("report", help="Create a privacy-sanitized support report.")
    report.add_argument("--url", default=DEFAULT_URL)
    report.add_argument("--output", help="Report path. Defaults to the IAM data directory; use - for stdout.")
    report.add_argument("--stdout", action="store_true", help="Print the report instead of writing a file.")
    report.add_argument("--force", action="store_true", help="Replace an existing --output file.")
    report.add_argument("--log-lines", type=int, default=200, help="Trailing log lines to summarize without copying their contents.")
    report.set_defaults(func=cmd_report)

    stop = sub.add_parser("stop", help="Stop automatic mail delivery; leave the Codex managed daemon running.")
    stop.add_argument("--all", action="store_true", help="Also stop an obsolete legacy app-server started by IAM.")
    stop.set_defaults(func=cmd_stop)

    daemon = sub.add_parser("serve", help="Run the mail supervisor in the foreground.")
    daemon.add_argument("--url", default=DEFAULT_URL)
    daemon.add_argument("--log-file", help=argparse.SUPPRESS)
    daemon.set_defaults(func=cmd_daemon)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        # Resolve at invocation time, not import time; direct --url always wins.
        supplied = sys.argv[1:] if argv is None else argv
        if hasattr(args, "url") and not any(item == "--url" or item.startswith("--url=") for item in supplied):
            args.url = configured_endpoint()
        result = args.func(args)
    except IAMError as exc:
        if not getattr(args, "json_output", False):
            raise SystemExit(str(exc)) from None
        print_json(integration_error(exc))
        return 1
    except SystemExit as exc:
        if not getattr(args, "json_output", False):
            raise
        print_json(integration_error(exc))
        return exc.code if isinstance(exc.code, int) and exc.code != 0 else 1
    except Exception as exc:
        if not getattr(args, "json_output", False):
            raise
        print_json(integration_error(exc))
        return 1
    return int(result or 0)

if __name__ == "__main__":
    raise SystemExit(main())
