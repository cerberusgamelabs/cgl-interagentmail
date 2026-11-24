"""Offline, review-first migration bundles and explicit pinned-session relocation.

Import deliberately stages data: it never registers projects or starts delivery.
Bundles contain private mail/session history and must be transferred privately.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import uuid
from urllib.parse import urlsplit
import zipfile

import interagentmail as iam
from iam_service import IAMError, session_owner

SCHEMA = 1
MAX_MANIFEST = 16 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".secrets", "secrets"}
SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".sqlite", ".sqlite3", ".db", ".wal", ".shm"}


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise IAMError("Invalid mailbox/resource name")
    safe_member(value)
    return value


def safe_member(value):
    if not isinstance(value, str) or "\\" in value or not value or value.startswith("/"):
        raise IAMError("Unsafe archive path")
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(p in {"", ".", ".."} or p.endswith((".", " ")) or
           any(ord(c) < 32 or c in ':<>"|?*' for c in p) or p.split(".")[0].upper() in reserved for p in parts):
        raise IAMError("Unsafe archive path")
    return value


def linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def files_under(root, *, workspace=False):
    """Never follow symlinks/junctions, and never package known credential stores."""
    root = Path(root)
    if not root.is_dir() or linked(root):
        raise IAMError(f"Expected a real directory, not a link: {root}")
    for folder, dirs, files in os.walk(root, followlinks=False):
        directory = Path(folder)
        dirs[:] = sorted(d for d in dirs if not linked(directory / d) and
                         (not workspace or d.casefold() not in SKIP_DIRS))
        for name in sorted(files):
            path = directory / name
            relative = path.relative_to(root).as_posix()
            lower = name.casefold()
            if linked(path) or not path.is_file():
                continue
            if workspace and (lower.startswith(".env") or "credential" in lower or
                              lower in {"auth.json", "id_rsa", "id_ed25519"} or
                              path.suffix.casefold() in SECRET_SUFFIXES or
                              lower.endswith(("-wal", "-shm")) or relative.casefold() == ".codex/config.toml"):
                continue
            yield safe_member(relative), path


def pack_file(archive, member, source, inventory):
    safe_member(member)
    before = source.stat()
    digest = hashlib.sha256()
    count = 0
    with source.open("rb") as src, archive.open(member, "w", force_zip64=True) as dest:
        while chunk := src.read(1024 * 1024):
            digest.update(chunk)
            count += len(chunk)
            dest.write(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or count != before.st_size:
        raise IAMError("Source changed during export; stop the selected agents and export again")
    inventory[member] = {"size": count, "sha256": digest.hexdigest()}


def pack_json(archive, member, value, inventory):
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    archive.writestr(member, data)
    inventory[member] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def export_bundle(root, mailboxes, output, *, workspaces=False, sessions=False, codex_home=None, resources=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    config = json.loads((root / "config.json").read_text(encoding="utf-8-sig"))
    selected = sorted(set(safe_name(name) for name in mailboxes))
    if not selected:
        raise IAMError("Select at least one mailbox")
    if len({name.casefold() for name in selected}) != len(selected):
        raise IAMError("Selected mailbox names collide on Windows")
    manifest = {"schema": SCHEMA, "projects": {}, "resources": {}, "files": {},
                "warnings": ["Private mail and session histories may contain sensitive information. This is not a secret scrubber.",
                             "No credentials, grants, heartbeat schedules, Codex indexes, or global runtime databases are intentionally migrated.",
                             "Project MCP configs are omitted; rebuild IAM config and review other MCP servers on destination."]}
    sources = []
    codex_home = Path(codex_home or os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    for name in selected:
        entry = config.get("projects", {}).get(name)
        if not isinstance(entry, dict):
            raise IAMError(f"Not a registered project: {name}")
        if session_owner(name, config) != name or any(owner == name for owner in config.get("session_mailboxes", {}).values()):
            raise IAMError(f"Shared-session mailbox {name} requires explicit shared-session migration; not supported yet")
        project = Path(entry["project_root"]).resolve()
        box = root / "mailboxes" / name
        profile = json.loads((box / "profile.json").read_text(encoding="utf-8-sig"))
        if Path(profile.get("project_root", "")).resolve() != project:
            raise IAMError(f"Registration/profile workspace mismatch: {name}")
        binding_path = box / ".codex-bridge-state.json"
        if not binding_path.is_file():
            raise IAMError(f"Missing pinned-session state for {name}; no replacement will be created")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        try:
            thread = str(uuid.UUID(binding.get("thread_id", "")))
        except (ValueError, TypeError, AttributeError):
            raise IAMError(f"Missing or invalid pinned thread for {name}") from None
        record = {"source_root": str(project), "thread_id": thread,
                  "sandbox": entry.get("sandbox", "workspace-write"),
                  "approval_policy": entry.get("approval_policy", "on-request"), "mcps_to_review": []}
        mcp = project / ".codex" / "config.toml"
        if mcp.is_file():
            record["mcps_to_review"] = sorted(set(re.findall(r"^\[mcp_servers\.([\w-]+)\]", mcp.read_text(encoding="utf-8-sig"), re.M)))
        manifest["projects"][name] = record
        # Exclude timers and runtime journals; carry continuity and mail only.
        for relative, path in files_under(box):
            if relative in {"profile.json", ".codex-bridge-state.json"} or relative.split("/")[0] in {"inbox", "sent", "archive"}:
                sources.append((f"mailboxes/{name}/{relative}", path))
        if workspaces:
            sources.extend((f"projects/{name}/{rel}", path) for rel, path in files_under(project, workspace=True))
        if sessions:
            matches = []
            for relative, path in files_under(codex_home / "sessions"):
                if path.name.endswith(f"-{thread}.jsonl"):
                    matches.append((relative, path))
            if len(matches) != 1:
                raise IAMError(f"Expected exactly one rollout for {name}; found {len(matches)}. No index or alternate thread guessed.")
            relative, path = matches[0]
            with path.open("rb") as stream:
                first = json.loads(stream.readline(1024 * 1024))
            if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != thread:
                raise IAMError(f"Rollout identity mismatch: {name}")
            member = f"sessions/{relative}"
            record["session_file"] = member
            sources.append((member, path))
    for name, directory in (resources or {}).items():
        safe_name(name)
        directory = Path(directory).resolve()
        manifest["resources"][name] = {"source_root": str(directory)}
        sources.extend((f"resources/{name}/{rel}", path) for rel, path in files_under(directory, workspace=True))
    if output.exists():
        raise IAMError("Export output already exists; choose a new file")
    if any(output == path.resolve() or output.is_relative_to(path.parent) for _, path in sources):
        # A bundle must never become part of its own source inventory.
        raise IAMError("Place the export outside selected mailbox/project/resource/session folders")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for member, source in sources:
            if member in manifest["files"]:
                raise IAMError("Duplicate archive member")
            pack_file(archive, member, source, manifest["files"])
        db = root / "run" / "desktop.sqlite3"
        if db.is_file():
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                for name in selected:
                    row = conn.execute("SELECT data FROM profiles WHERE mailbox=?", (name,)).fetchone()
                    if row:
                        pack_json(archive, f"messenger/{name}.json", json.loads(row[0]), manifest["files"])
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return {"bundle": str(output), "mailboxes": selected, "files": len(manifest["files"]), "state": "exported-private-bundle"}


def verified_manifest(archive, max_bytes):
    infos = archive.infolist()
    names = [safe_member(info.filename) for info in infos]
    folded = {name.casefold() for name in names}
    if len(folded) != len(names) or "manifest.json" not in names:
        raise IAMError("Duplicate members or missing manifest")
    for name in names:
        if any(str(parent).casefold() in folded for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise IAMError("Archive file/directory collision")
    if any(info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1 for info in infos):
        raise IAMError("Directories, links, or encrypted ZIP entries are unsupported")
    if archive.getinfo("manifest.json").file_size > MAX_MANIFEST or sum(i.file_size for i in infos) > max_bytes:
        raise IAMError("Bundle exceeds the configured uncompressed size limit")
    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("files"), dict):
        raise IAMError("Unsupported migration manifest")
    if set(names) != {"manifest.json", *manifest["files"]}:
        raise IAMError("Archive inventory mismatch")
    projects = manifest.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise IAMError("Missing selected projects")
    for name, entry in projects.items():
        safe_name(name)
        uuid.UUID(entry["thread_id"])
        if entry.get("sandbox") not in {"read-only", "workspace-write", "danger-full-access"} or entry.get("approval_policy") not in {"never", "on-request", "untrusted"}:
            raise IAMError("Invalid session policy")
        for required in (f"mailboxes/{name}/profile.json", f"mailboxes/{name}/.codex-bridge-state.json"):
            if required not in manifest["files"]:
                raise IAMError("Missing mailbox identity/binding")
        session = entry.get("session_file")
        if session:
            if not isinstance(session, str) or not session.startswith("sessions/") or session not in manifest["files"]:
                raise IAMError("Invalid session inventory reference")
            with archive.open(session) as stream:
                first = json.loads(stream.readline(1024 * 1024))
            if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != entry["thread_id"]:
                raise IAMError("Rollout identity does not match pinned thread")
    for name in manifest.get("resources", {}):
        safe_name(name)
    for name, metadata in manifest["files"].items():
        info = archive.getinfo(name)
        if metadata.get("size") != info.file_size:
            raise IAMError("Member size mismatch")
        digest = hashlib.sha256()
        with archive.open(name) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != metadata.get("sha256"):
            raise IAMError("Member checksum mismatch")
    return manifest


def parse_pairs(values):
    result = {}
    for value in values or []:
        name, separator, path = value.partition("=")
        safe_name(name)
        if not separator or not path or name in result or not Path(path).is_absolute():
            raise IAMError("Mappings must be unique NAME=absolute-path pairs")
        result[name] = str(Path(path).resolve())
    return result


def stage_bundle(bundle, staging, mappings, *, max_bytes=20 * 1024**3):
    staging = Path(staging).resolve()
    if staging.exists():
        raise IAMError("Staging folder already exists; choose a new empty destination")
    with zipfile.ZipFile(bundle) as archive:
        manifest = verified_manifest(archive, max_bytes)
        if set(mappings) != set(manifest["projects"]):
            raise IAMError("Supply one --map MAILBOX=absolute-workspace for every selected mailbox")
        if any(not isinstance(p, str) or not Path(p).is_absolute() for p in mappings.values()):
            raise IAMError("Destination workspace mappings must be absolute")
        if len({str(Path(p).resolve()).casefold() for p in mappings.values()}) != len(mappings):
            raise IAMError("Two mailboxes cannot target the same workspace")
        # Validate identities BEFORE creating the staging directory.
        for name, entry in manifest["projects"].items():
            profile = json.loads(archive.read(f"mailboxes/{name}/profile.json"))
            state = json.loads(archive.read(f"mailboxes/{name}/.codex-bridge-state.json"))
            if profile.get("address") != name or state.get("thread_id") != entry["thread_id"]:
                raise IAMError("Mailbox identity or pinned thread mismatch")
        staging.mkdir(parents=True)
        for member in manifest["files"]:
            target = staging / "payload" / member
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("xb") as dest:
                shutil.copyfileobj(src, dest, 1024 * 1024)
        # Keep extracted source bytes unchanged, and generate separate destination drafts.
        draft = {"projects": {}}
        from iam_orchestrator import mcp_block
        for name, entry in manifest["projects"].items():
            draft["projects"][name] = {"project_root": mappings[name], "sandbox": entry["sandbox"], "approval_policy": entry["approval_policy"]}
            folder = staging / "destination-drafts" / name
            folder.mkdir(parents=True)
            profile = json.loads((staging / "payload" / "mailboxes" / name / "profile.json").read_text(encoding="utf-8"))
            profile["project_root"] = mappings[name]
            write_json(folder / "profile.json", profile)
            with (folder / "iam-mcp.toml").open("x", encoding="utf-8") as stream:
                stream.write(mcp_block(Path(mappings[name])))
        write_json(staging / "registration-draft.json", draft)
        write_json(staging / "manifest.json", manifest)
        report = check_stage(staging)
        write_json(staging / "validation-report.json", report)
    return report


def check_stage(staging):
    staging = Path(staging)
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    draft = json.loads((staging / "registration-draft.json").read_text(encoding="utf-8"))
    checks = []
    for member, metadata in manifest["files"].items():
        safe_member(member)
        target = staging / "payload" / member
        digest = hashlib.sha256()
        if not target.is_file() or not target.resolve().is_relative_to((staging / "payload").resolve()):
            raise IAMError("Staged payload is missing or escapes staging")
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if target.stat().st_size != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
            raise IAMError("Staged payload changed; compare against the original bundle before proceeding")
    for name, entry in manifest["projects"].items():
        root = Path(draft["projects"][name]["project_root"])
        checks.append({"mailbox": name, "check": "destination-workspace", "status": "pass" if root.is_dir() else "blocked", "path": str(root)})
        checks.append({"mailbox": name, "check": "session-rollout-in-bundle", "status": "pass" if entry.get("session_file") else "blocked"})
        for server in entry.get("mcps_to_review", []):
            if server != "interagentmail":
                checks.append({"mailbox": name, "check": f"configure-and-test-MCP:{server}", "status": "blocked"})
    for executable in ("node", "codex", "npm"):
        found = shutil.which(executable) or shutil.which(executable + ".cmd")
        checks.append({"check": f"executable:{executable}", "status": "found" if found else "missing", "path": found})
    return {"state": "staged-not-activated", "checks": checks, "required_steps": [
        "Review private payload and destination drafts; back up destination state before applying anything.",
        "Copy workspaces and mailboxes selectively; merge registrations, never replace global config or desktop database.",
        "Restore selected Messenger profiles only; their avatars are in payload/messenger.",
        "Restore rollout files under the destination CODEX_HOME/sessions, not its global indexes or authentication.",
        "Recreate other MCP configurations, supply credentials privately, and run dependency/native-module checks (Node, npm ci/build, C++ toolchain if needed).",
        "Use migration relocate with the exact existing thread to reconcile the runtime workspace after the source agent is stopped.",
        "Verify tool initialization and message delivery before enabling destination automation; keep source originals and do not run both copies."],
        "note": "Found executables do not prove dependency compatibility. No runtime probe, grant, model turn, registration, or service activation was performed."}


async def relocate(mailbox, expected_thread, expected_old, destination):
    """Only an explicit idle-session move; no model input and no config rewriting."""
    from iam_orchestrator import appserver_client, configured_endpoint, session_guard, switch_session_workspace, read_pid, pid_alive
    name = safe_name(mailbox)
    config = iam.load(iam.CONFIG)
    if session_owner(name, config) != name:
        raise IAMError("Relocate the primary session owner; shared aliases are not supported here")
    entry = config.get("projects", {}).get(name, {})
    destination = Path(destination).resolve()
    profile = iam.load(iam.mailbox(name) / "profile.json")
    if not destination.is_dir() or Path(entry.get("project_root", "")).resolve() != destination or Path(profile.get("project_root", "")).resolve() != destination:
        raise IAMError("Destination must exist and match both registration and mailbox profile")
    def binding():
        return iam.load(iam.mailbox(name) / ".codex-bridge-state.json").get("thread_id")
    if binding() != expected_thread:
        raise IAMError("Pinned thread changed or does not match; no session replaced")
    if any(pid_alive(read_pid(service)) for service in ("supervisor", "connect")):
        raise IAMError("Destination supervisor/connector must be stopped by the owner before relocating; no service was stopped")
    endpoint = configured_endpoint()
    if endpoint != "managed://" and urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise IAMError("Migration requires the local configured app server")
    with session_guard(name, "workspace") as acquired:
        if not acquired:
            raise IAMError("Another migration is in progress")
        client = appserver_client(endpoint, request_timeout=30)
        try:
            await asyncio.wait_for(client.connect(), 15)
            result = await asyncio.wait_for(client.request("thread/read", {"threadId": expected_thread, "includeTurns": False}), 30)
            thread = result.get("thread", {})
            if thread.get("id") != expected_thread or Path(thread.get("cwd", "")).resolve() != Path(expected_old).resolve():
                raise IAMError("Session identity/old workspace differs from the explicit migration request")
            if thread.get("status", {}).get("type") not in {"idle", "notLoaded"} or binding() != expected_thread:
                raise IAMError("Session is busy or binding changed; no relocation attempted")
            await asyncio.wait_for(switch_session_workspace(client, expected_thread, destination, entry), 90)
            result = await asyncio.wait_for(client.request("thread/read", {"threadId": expected_thread, "includeTurns": False}), 30)
            thread = result.get("thread", {})
            if thread.get("id") != expected_thread or Path(thread.get("cwd", "")).resolve() != destination or binding() != expected_thread:
                raise IAMError("Post-relocation verification failed; do not enable delivery or retry blindly")
            return {"state": "workspace-verified", "mailbox": name, "thread_id": expected_thread, "workspace": str(destination)}
        finally:
            await asyncio.wait_for(client.close(), 5)


def command(args):
    try:
        if args.migration_action == "export":
            if not args.source_stopped:
                raise IAMError("Stop the selected source agents/tools first, then explicitly pass --source-stopped. IAM will not stop them for you.")
            result = export_bundle(iam.ROOT, args.mailbox, args.output, workspaces=args.include_workspaces,
                                   sessions=args.include_sessions, codex_home=args.codex_home, resources=parse_pairs(args.resource))
        elif args.migration_action == "import":
            result = stage_bundle(args.bundle, args.staging, parse_pairs(args.map), max_bytes=args.max_gib * 1024**3)
        elif args.migration_action == "check":
            result = check_stage(args.staging)
        else:
            if not args.source_stopped or not args.destination_quiescent:
                raise IAMError("Confirm source stopped and destination delivery/terminals quiescent; migration never stops services automatically")
            result = asyncio.run(relocate(args.mailbox, args.thread_id, args.from_path, args.to_path))
        print(json.dumps(result, indent=2))
    except IAMError:
        raise
    except Exception as error:
        # Native/MCP exceptions can embed private launch settings; do not print them.
        raise IAMError(f"Migration failed ({type(error).__name__}). Keep originals and any partial staging; no automatic retry or activation.") from None


def add_parser(sub):
    parser = sub.add_parser("migration", help="Private export, staged import, validation, and explicit session relocation.")
    actions = parser.add_subparsers(dest="migration_action", required=True)
    export = actions.add_parser("export", help="Copy selected agents; no live service changes. Treat bundle as private.")
    export.add_argument("--mailbox", action="append", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--include-workspaces", action="store_true")
    export.add_argument("--include-sessions", action="store_true")
    export.add_argument("--codex-home", type=Path)
    export.add_argument("--resource", action="append", help="Explicit extra source folder: NAME=absolute-path; excludes credentials/databases/build outputs.")
    export.add_argument("--source-stopped", action="store_true")
    imp = actions.add_parser("import", help="Stage ONLY; never activate or overwrite destination IAM state.")
    imp.add_argument("bundle", type=Path)
    imp.add_argument("--staging", type=Path, required=True)
    imp.add_argument("--map", action="append", required=True, help="MAILBOX=absolute-destination-workspace")
    imp.add_argument("--max-gib", type=int, default=20)
    check = actions.add_parser("check", help="Report staged blockers without running imported code.")
    check.add_argument("staging", type=Path)
    move = actions.add_parser("relocate", help="Reconcile an idle pinned session with already-correct destination registration/profile.")
    move.add_argument("--mailbox", required=True)
    move.add_argument("--thread-id", required=True)
    move.add_argument("--from", dest="from_path", type=Path, required=True)
    move.add_argument("--to", dest="to_path", type=Path, required=True)
    move.add_argument("--source-stopped", action="store_true")
    move.add_argument("--destination-quiescent", action="store_true")
    for item in (export, imp, check, move):
        item.set_defaults(func=command)
