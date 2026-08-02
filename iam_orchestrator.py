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
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import interagentmail as iam
from iam_codex_bridge import AppServerClient, AppServerError, DeliveryState, MailboxBridge
from iam_service import IAMService
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


LOG = logging.getLogger("iam.orchestrator")
DEFAULT_URL = "ws://127.0.0.1:4500"
MCP_BEGIN = "# BEGIN INTERAGENTMAIL (managed by `iam setup`)"
MCP_END = "# END INTERAGENTMAIL"
CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
INTEGRATION_SCHEMA_VERSION = "1.0"
ADDRESS_STRATEGY = "project-folder-basename"
SAFE_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
SAFE_APPROVAL_POLICIES = {"untrusted", "on-request", "never"}
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


def mcp_block(project_root: Path) -> str:
    args = ["-m", "iam_mcp_server", "--project-root", str(project_root)]
    rendered_args = ", ".join(toml_string(item) for item in args)
    return (
        f"{MCP_BEGIN}\n"
        "[mcp_servers.interagentmail]\n"
        f"command = {toml_string(sys.executable)}\n"
        f"args = [{rendered_args}]\n"
        "required = true\n"
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
    if box_existed and not profile_owner:
        raise IAMCommandError(
            "IAM_ADDRESS_COLLISION",
            f"Mailbox address {address} already exists without project ownership; IAM will not migrate it implicitly.",
            recoverable=True,
            details={"address": address, "requested_project_root": str(root)},
        )
    if profile_owner and profile_kind == "user":
        raise IAMCommandError(
            "IAM_ADDRESS_COLLISION",
            f"Mailbox address {address} is owned by a human user and cannot become a project identity.",
            recoverable=True,
            details={"address": address, "requested_project_root": str(root)},
        )
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
        "legacy_mailbox_reused": False,
        "supervisor_reload": "automatic",
    }


def unregister_project(project_root: str | Path) -> dict[str, Any]:
    address, root, _entry = project_entry(project_root)
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
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


def spawn_background(command: list[str], name: str, *, record_pid: bool = True) -> int:
    log_file = log_path(name).open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        log_file.close()
    if record_pid:
        pid_path(name).write_text(str(process.pid) + "\n", encoding="utf-8")
    return process.pid


async def appserver_available(url: str, timeout: float = 2.0) -> bool:
    client = AppServerClient(url=url, request_timeout=timeout)
    try:
        await client.connect()
        return True
    except (AppServerError, OSError):
        return False
    finally:
        await client.close()


def codex_executable() -> str:
    executable = shutil.which("codex.cmd") or shutil.which("codex")
    if not executable:
        raise SystemExit("Codex was not found on PATH. Install Codex and run `codex login` first.")
    return executable


def ensure_appserver(url: str, wait_seconds: float = 15.0) -> str:
    if asyncio.run(appserver_available(url)):
        return "already running"
    if not url.startswith("ws://127.0.0.1:") and not url.startswith("ws://localhost:"):
        raise SystemExit(f"Refusing to auto-start a non-loopback app-server: {url}")
    pid = spawn_background([codex_executable(), "app-server", "--listen", url], "app-server")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if asyncio.run(appserver_available(url, timeout=1.0)):
            return f"started (pid {pid})"
        if not pid_alive(pid):
            break
    raise SystemExit(f"Codex app-server did not start. See {log_path('app-server')}")


def ensure_daemon(url: str) -> str:
    pid = read_pid("supervisor")
    if pid_alive(pid):
        return f"already running (pid {pid})"
    try:
        pid_path("supervisor").unlink()
    except FileNotFoundError:
        pass
    command = [sys.executable, "-m", "iam_orchestrator", "serve", "--url", url]
    spawn_background(command, "supervisor", record_pid=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        time.sleep(0.1)
        pid = read_pid("supervisor")
        if pid_alive(pid):
            return f"started (pid {pid})"
    raise SystemExit(f"InterAgentMail supervisor did not start. See {log_path('supervisor')}")


def registry_signature(projects: dict[str, dict[str, Any]]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(sorted(
        (
            address,
            str(entry.get("project_root", "")),
            str(entry.get("sandbox", "workspace-write")),
            str(entry.get("approval_policy", "on-request")),
        )
        for address, entry in projects.items()
    ))


async def serve_once(url: str) -> None:
    projects = registered_projects()
    if not projects:
        raise AppServerError("No projects are registered; run `iam setup <project>` first")
    signature = registry_signature(projects)
    client = AppServerClient(url=url)
    await client.connect()
    bridges: list[MailboxBridge] = []
    try:
        for address, entry in projects.items():
            root = Path(entry["project_root"]).resolve()
            if not root.is_dir():
                LOG.warning("Skipping %s; project directory is missing: %s", address, root)
                continue
            service = IAMService(root)
            state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
            state.initialize(service.inbox(limit=1000), process_existing=False)
            bridge = MailboxBridge(
                service,
                state,
                client,
                explicit_thread_id=state.thread_id,
                create_thread=True,
                sandbox=str(entry.get("sandbox", "workspace-write")),
                approval_policy=str(entry.get("approval_policy", "on-request")),
            )
            thread = await bridge.attach_thread()
            await bridge.ensure_resumable(thread)
            LOG.info("Watching %s at %s on thread %s", address, root, thread["id"])
            bridges.append(bridge)

        while not client.closed.is_set():
            for bridge in bridges:
                try:
                    await bridge.deliver_once()
                except (AppServerError, KeyError, ValueError) as exc:
                    LOG.error("Delivery failed for %s: %s", bridge.service.address, exc)
            if registry_signature(registered_projects()) != signature:
                LOG.info("Project registry changed; reloading")
                return
            try:
                await asyncio.wait_for(client.closed.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
    finally:
        await client.close()


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
            "web_managed_by_iam_service": False,
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


def cmd_start(args: argparse.Namespace) -> None:
    if not registered_projects():
        raise SystemExit("No projects are registered. Run `iam setup` in a project first.")
    print(f"Codex app-server: {ensure_appserver(args.url)}")
    print(f"IAM supervisor:   {ensure_daemon(args.url)}")
    print("Mail delivery is now running in the background for every registered project.")


async def thread_available(url: str, thread_id: str, project_root: Path) -> bool:
    client = AppServerClient(url=url, request_timeout=2.0)
    try:
        await client.connect()
        await client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        listed = await client.request(
            "thread/list",
            {
                "cwd": str(project_root),
                "limit": 100,
                "sortKey": "recency_at",
                "sortDirection": "desc",
            },
        )
        return any(str(item.get("id")) == thread_id for item in listed.get("data", []))
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
    for folder in ("inbox", "sent", "archive"):
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
            add(CHECK_WARN, f"Thread {address}", "not pinned yet; `iam start` will create one")
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
            f"- Mailbox counts: inbox {counts['inbox']}, sent {counts['sent']}, archive {counts['archive']}",
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


def cmd_open(args: argparse.Namespace) -> None:
    root_value = args.project or str(Path.cwd())
    try:
        address, root, entry = project_entry(root_value)
    except SystemExit:
        setup_project(root_value)
        address, root, entry = project_entry(root_value)
    ensure_appserver(args.url)
    ensure_daemon(args.url)
    thread_id = wait_for_thread(address, args.url, root)
    command = [
        codex_executable(),
        "resume",
        "--remote", args.url,
        "-C", str(root),
        "-s", str(entry.get("sandbox", "workspace-write")),
        "-a", str(entry.get("approval_policy", "on-request")),
        "--include-non-interactive",
        thread_id,
    ]
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
            state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
            thread_state = "pinned" if state.thread_id else "pending"
            thread_id = state.thread_id
        except (OSError, ValueError):
            thread_state = "invalid"
            thread_id = None
        project_rows.append({
            "address": address,
            "display_name": iam.display_name(address),
            "project_root": str(entry.get("project_root", "")),
            "sandbox": str(entry.get("sandbox", "workspace-write")),
            "approval_policy": str(entry.get("approval_policy", "on-request")),
            "thread_state": thread_state,
            "thread_id": thread_id,
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
    return 0


def stop_process(pid: int, *, tree: bool = False) -> None:
    if os.name == "nt" and tree:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    os.kill(pid, signal.SIGTERM)


def cmd_stop(args: argparse.Namespace) -> None:
    pid = read_pid("supervisor")
    if pid_alive(pid):
        assert pid is not None
        stop_process(pid)
        print(f"Stopped IAM supervisor process {pid}.")
    else:
        print("IAM supervisor is not running.")
    if args.all:
        server_pid = read_pid("app-server")
        if pid_alive(server_pid):
            assert server_pid is not None
            stop_process(server_pid, tree=True)
            print(f"Stopped managed Codex app-server process tree {server_pid}.")
        else:
            print("Managed Codex app-server is not running.")
    else:
        print("The shared Codex app-server was left running. Use `iam stop --all` to stop it too.")


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(argparse.Namespace(all=True))
    time.sleep(0.5)
    cmd_start(args)


def cmd_daemon(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    existing = read_pid("supervisor")
    if pid_alive(existing) and existing != os.getpid():
        raise SystemExit(f"IAM supervisor is already running (pid {existing}).")
    pid_path("supervisor").write_text(str(os.getpid()) + "\n", encoding="utf-8")
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

    def add_registration_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("projects", nargs="*", help="Project folders. Defaults to the current folder.")
        command.add_argument("--display-name", help="Optional human-facing mailbox label; defaults to the address.")
        command.add_argument("--full-access", action="store_true", help="Use Codex danger-full-access for this project.")
        command.add_argument("--approval-policy", choices=("untrusted", "on-request", "never"), default="on-request")
        command.add_argument("--process-existing", action="store_true", help="Process inbox mail already present on first setup.")
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

    start = sub.add_parser("start", help="Start the shared app-server and mail supervisor in the background.")
    start.add_argument("--url", default=DEFAULT_URL)
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="Show service and registered-project status.")
    status.add_argument("--url", default=DEFAULT_URL)
    status.add_argument("--json", action="store_true", dest="json_output")
    status.set_defaults(func=cmd_status)

    restart = sub.add_parser("restart", help="Restart both background services without visible windows.")
    restart.add_argument("--url", default=DEFAULT_URL)
    restart.set_defaults(func=cmd_restart)

    open_command = sub.add_parser("open", help="Open the registered project's pinned Codex session.")
    open_command.add_argument("project", nargs="?", help="Project folder. Defaults to the current folder.")
    open_command.add_argument("--url", default=DEFAULT_URL)
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

    stop = sub.add_parser("stop", help="Stop automatic mail delivery but leave Codex app-server running.")
    stop.add_argument("--all", action="store_true", help="Also stop the app-server started by IAM.")
    stop.set_defaults(func=cmd_stop)

    daemon = sub.add_parser("serve", help="Run the mail supervisor in the foreground.")
    daemon.add_argument("--url", default=DEFAULT_URL)
    daemon.set_defaults(func=cmd_daemon)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.func(args)
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
