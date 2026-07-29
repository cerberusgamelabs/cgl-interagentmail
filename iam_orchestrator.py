"""Release-friendly setup and lifecycle commands for InterAgentMail agents."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import importlib.metadata
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import interagentmail as iam
from iam_codex_bridge import AppServerClient, AppServerError, DeliveryState, MailboxBridge
from iam_service import IAMService


LOG = logging.getLogger("iam.orchestrator")
DEFAULT_URL = "ws://127.0.0.1:4500"
MCP_BEGIN = "# BEGIN INTERAGENTMAIL (managed by `iam setup`)"
MCP_END = "# END INTERAGENTMAIL"


def package_version() -> str:
    try:
        return importlib.metadata.version("cgl-interagentmail")
    except importlib.metadata.PackageNotFoundError:
        return "development"


def load_config() -> dict[str, Any]:
    if not iam.CONFIG.exists():
        return {}
    data = iam.load(iam.CONFIG)
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid InterAgentMail configuration: {iam.CONFIG}")
    return data


def save_config(data: dict[str, Any]) -> None:
    iam.write_atomic(iam.CONFIG, data)


def registered_projects() -> dict[str, dict[str, Any]]:
    projects = load_config().get("projects", {})
    return projects if isinstance(projects, dict) else {}


def project_entry(project_root: str | Path) -> tuple[str, Path, dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    address = iam.address(str(root))
    entry = registered_projects().get(address)
    if not entry or Path(entry.get("project_root", "")).resolve() != root:
        raise SystemExit(f"{root} is not registered. Run: iam setup \"{root}\"")
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
    if MCP_BEGIN in current and MCP_END in current:
        before, remainder = current.split(MCP_BEGIN, 1)
        _, after = remainder.split(MCP_END, 1)
        updated = before.rstrip() + "\n\n" + block + after.lstrip("\r\n")
    elif "[mcp_servers.interagentmail]" in current:
        raise SystemExit(
            f"{config_path} already defines mcp_servers.interagentmail outside the managed setup block. "
            "Remove or rename that section, then run setup again."
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
    if current.count(MCP_BEGIN) != 1 or current.count(MCP_END) != 1:
        raise SystemExit(
            f"Cannot safely edit {config_path}: the managed InterAgentMail markers are incomplete or duplicated."
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
        raise SystemExit(f"Project directory does not exist: {root}")
    address = iam.address(str(root))
    iam.ensure_box(address)
    if display_name:
        data = iam.profile(address)
        data["display_name"] = display_name
        iam.save_profile(address, data)

    config = load_config()
    config.setdefault("project_root", str(root.parent))
    projects = config.setdefault("projects", {})
    projects[address] = {
        "project_root": str(root),
        "sandbox": sandbox,
        "approval_policy": approval_policy,
    }
    mcp_path = configure_project_mcp(root)
    save_config(config)

    state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
    state.initialize(IAMService(root).inbox(limit=1000), process_existing=process_existing)
    return {
        "address": address,
        "display_name": iam.display_name(address),
        "project_root": str(root),
        "mcp_config": str(mcp_path),
        "sandbox": sandbox,
        "approval_policy": approval_policy,
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


def runtime_dir() -> Path:
    path = iam.ROOT / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_path(name: str) -> Path:
    return runtime_dir() / f"{name}.pid"


def log_path(name: str) -> Path:
    return runtime_dir() / f"{name}.log"


def read_pid(name: str) -> int | None:
    path = pid_path(name)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
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


def cmd_setup(args: argparse.Namespace) -> None:
    roots = args.projects or [str(Path.cwd())]
    if args.display_name and len(roots) != 1:
        raise SystemExit("--display-name can only be used when setting up one project.")
    sandbox = "danger-full-access" if args.full_access else "workspace-write"
    for root in roots:
        result = setup_project(
            root,
            display_name=args.display_name,
            sandbox=sandbox,
            approval_policy=args.approval_policy,
            process_existing=args.process_existing,
        )
        print(f"Configured {result['display_name']} <{result['address']}>")
        print(f"  Project: {result['project_root']}")
        print(f"  MCP:     {result['mcp_config']}")
        print(f"  Safety:  {result['sandbox']}, approvals {result['approval_policy']}")
    print("\nNext: run `iam start` once, then `iam open` from any registered project you want to view.")


def cmd_unregister(args: argparse.Namespace) -> None:
    roots = args.projects or [str(Path.cwd())]
    for root in roots:
        result = unregister_project(root)
        print(f"Unregistered {result['address']} ({result['project_root']})")
        if result["mcp_config"]:
            print(f"  Removed the managed MCP configuration from {result['mcp_config']}")
        print(f"  Mailbox data was preserved at {result['mailbox']}")
    print("The running supervisor will reload the project registry automatically.")


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


def cmd_status(args: argparse.Namespace) -> None:
    projects = registered_projects()
    print(f"App-server: {'reachable' if asyncio.run(appserver_available(args.url)) else 'stopped'} ({args.url})")
    supervisor_pid = read_pid("supervisor")
    print(f"Supervisor: {'running' if pid_alive(supervisor_pid) else 'stopped'}" + (f" (pid {supervisor_pid})" if supervisor_pid else ""))
    if not projects:
        print("Projects:   none; run `iam setup`")
        return
    print("Projects:")
    for address, entry in sorted(projects.items()):
        state = DeliveryState.load(iam.mailbox(address) / ".codex-bridge-state.json")
        print(f"  {address}: {entry['project_root']} (thread {state.thread_id or 'pending'})")


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

    setup = sub.add_parser("setup", help="Register projects, initialize mailboxes, and configure MCP.")
    setup.add_argument("projects", nargs="*", help="Project folders. Defaults to the current folder.")
    setup.add_argument("--display-name")
    setup.add_argument("--full-access", action="store_true", help="Use Codex danger-full-access for this project.")
    setup.add_argument("--approval-policy", choices=("untrusted", "on-request", "never"), default="on-request")
    setup.add_argument("--process-existing", action="store_true", help="Process inbox mail already present on first setup.")
    setup.set_defaults(func=cmd_setup)

    unregister = sub.add_parser(
        "unregister",
        help="Stop managing projects and remove only IAM's managed MCP configuration.",
    )
    unregister.add_argument("projects", nargs="*", help="Project folders. Defaults to the current folder.")
    unregister.set_defaults(func=cmd_unregister)

    for name, handler, help_text in (
        ("start", cmd_start, "Start the shared app-server and mail supervisor in the background."),
        ("status", cmd_status, "Show service and registered-project status."),
        ("restart", cmd_restart, "Restart both background services without visible windows."),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--url", default=DEFAULT_URL)
        command.set_defaults(func=handler)

    open_command = sub.add_parser("open", help="Open the registered project's pinned Codex session.")
    open_command.add_argument("project", nargs="?", help="Project folder. Defaults to the current folder.")
    open_command.add_argument("--url", default=DEFAULT_URL)
    open_command.set_defaults(func=cmd_open)

    stop = sub.add_parser("stop", help="Stop automatic mail delivery but leave Codex app-server running.")
    stop.add_argument("--all", action="store_true", help="Also stop the app-server started by IAM.")
    stop.set_defaults(func=cmd_stop)

    daemon = sub.add_parser("serve", help="Run the mail supervisor in the foreground.")
    daemon.add_argument("--url", default=DEFAULT_URL)
    daemon.set_defaults(func=cmd_daemon)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
