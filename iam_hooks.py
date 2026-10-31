"""Opt-in local event broker. No agent execution, mailbox access or callbacks."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
MAX_BODY = 8192
TTL = 900
LEASE = 60
RETENTION = 7 * 86400
ACTIVE = ("queued", "leased")


class HookError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(condition, code="invalid_request", status=400):
    if not condition:
        raise HookError(code, status)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), "invalid_identifier")
    return value


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional), "invalid_fields")


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value), "invalid_timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise HookError("invalid_timestamp") from None


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _windows_directory_principals(path):
    """Read numeric owner/DACL SIDs, without shell-specific SDDL aliases."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    out_pointer = ctypes.POINTER(pointer)
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.DWORD, out_pointer, out_pointer, out_pointer, out_pointer, out_pointer]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.GetAce.argtypes = [pointer, wintypes.DWORD, out_pointer]
    api.GetAce.restype = wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer

    def sid_string(value):
        require(bool(value), "invalid_state_directory_sid", 403)
        result = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(value, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return result.value
        finally:
            kernel.LocalFree(ctypes.cast(result, pointer))

    owner, acl, descriptor = pointer(), pointer(), pointer()
    # SE_FILE_OBJECT; OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
    error = api.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                    ctypes.byref(acl), None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        require(bool(acl.value), "state_directory_has_other_principals", 403)
        # ACL header: BYTE revision/reserved, WORD size/count/reserved.
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        principals = []
        for index in range(count):
            ace = pointer()
            if not api.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Simple allow/deny ACEs have a four-byte header, DWORD mask, SID.
            # Unknown/object/callback ACE layouts fail closed rather than guess.
            kind = ctypes.c_ubyte.from_address(ace.value).value
            size = ctypes.c_ushort.from_address(ace.value + 2).value
            require(kind in (0, 1) and size >= 16, "unsupported_state_directory_ace", 403)
            principals.append(sid_string(ace.value + 8))
        return sid_string(owner), principals
    finally:
        kernel.LocalFree(descriptor)


def private_directory(path):
    """Apply ACLs only to this dedicated directory, never the workspace root."""
    path = Path(path).absolute()
    require(not path.is_symlink() and (not path.exists() or not
            (getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)),
            "unsafe_state_directory")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
    except FileExistsError:
        created = False
        require(path.is_dir(), "unsafe_state_directory")
    require(not (getattr(path.lstat(), "st_file_attributes", 0)
                 & stat.FILE_ATTRIBUTE_REPARSE_POINT), "unsafe_state_directory")
    if os.name == "nt":
        output = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
        sid = next(csv.reader([output.strip()]))[1]
        require(bool(re.fullmatch(r"S-1-[0-9-]+", sid)), "cannot_identify_owner")
        if created:
            # Only our newly created, still-empty directory can be initialized.
            # Windows/Python may add explicit Administrators/Owner Rights ACEs.
            # Never reset an existing directory's explicit grants or ownership.
            subprocess.run(["icacls", str(path), "/setowner", f"*{sid}"],
                           check=True, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            subprocess.run(["icacls", str(path), "/reset"],
                           check=True, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        # /grant:r does not remove pre-existing explicit grants to other users.
        # Refuse such a directory rather than silently claiming it is private.
        owner, principals = _windows_directory_principals(path)
        require(owner in (sid, "S-1-5-18") and principals
                and all(principal in (sid, "S-1-5-18") for principal in principals),
                "state_directory_has_other_principals", 403)
    else:
        os.chmod(path, 0o700)
    return path


class Broker:
    def __init__(self, directory, clock=time.time):
        self.directory = private_directory(directory)
        self.path = self.directory / "events.sqlite3"
        require(not self.path.is_symlink(), "unsafe_state_file")
        self.clock = clock
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS principals (
                  id TEXT PRIMARY KEY, role TEXT NOT NULL, target TEXT,
                  token_hash TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS targets (
                  id TEXT PRIMARY KEY, policy TEXT NOT NULL, policy_hash TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS grants (
                  producer TEXT, target TEXT, type TEXT, enabled INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(producer,target,type));
                CREATE TABLE IF NOT EXISTS events (
                  receipt TEXT PRIMARY KEY, producer TEXT NOT NULL, event_id TEXT NOT NULL,
                  target TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL,
                  payload_hash TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL,
                  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  lease_hash TEXT, lease_until REAL, available REAL NOT NULL,
                  outcome TEXT, UNIQUE(producer,event_id));
                CREATE TABLE IF NOT EXISTS rates (producer TEXT PRIMARY KEY, tokens REAL, updated REAL);
                CREATE INDEX IF NOT EXISTS events_target_state ON events(target,state);
                CREATE INDEX IF NOT EXISTS events_producer_state ON events(producer,state);
                CREATE INDEX IF NOT EXISTS events_state_expiry ON events(state,expires);
            """)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def create_producer(self, name):
        return self._principal(identifier(name), "producer", None)

    def _principal(self, name, role, target):
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            require(not db.execute("SELECT 1 FROM principals WHERE id=?", (name,)).fetchone(), "already_exists", 409)
            db.execute("INSERT INTO principals(id,role,target,token_hash) VALUES(?,?,?,?)",
                       (name, role, target, digest(token)))
        return token

    def create_target(self, name, policy):
        identifier(name)
        fields(policy, ("schema_version", "runtime_id", "capabilities", "isolation", "public_docs"))
        require(policy["schema_version"] == "1.0", "unsupported_schema")
        identifier(policy["runtime_id"])
        require(isinstance(policy["capabilities"], list) and policy["capabilities"]
                and all(isinstance(x, str) for x in policy["capabilities"])
                and set(policy["capabilities"]) <= {"conversation.read", "conversation.reply", "public_docs.read"}, "invalid_capabilities")
        require(isinstance(policy["isolation"], str) and 1 <= len(policy["isolation"]) <= 2048, "invalid_isolation_description")
        require(isinstance(policy["public_docs"], list) and len(policy["public_docs"]) <= 50
                and all(isinstance(x, str) and 1 <= len(x) <= 256 for x in policy["public_docs"]), "invalid_docs")
        encoded = canonical(policy)
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            require(not db.execute("SELECT 1 FROM targets WHERE id=?", (name,)).fetchone(), "already_exists", 409)
            require(not db.execute("SELECT 1 FROM principals WHERE id=?", ("consumer-" + name,)).fetchone(), "already_exists", 409)
            db.execute("INSERT INTO targets(id,policy,policy_hash) VALUES(?,?,?)", (name, encoded, digest(encoded)))
            db.execute("INSERT INTO principals(id,role,target,token_hash) VALUES(?,?,?,?)",
                       ("consumer-" + name, "consumer", name, digest(token)))
        return token, digest(encoded)

    def grant(self, producer, target, event_type, enabled=False):
        require(isinstance(event_type, str) and TYPE.fullmatch(event_type), "invalid_event_type")
        with self.transaction() as db:
            require(db.execute("SELECT 1 FROM principals WHERE id=? AND role='producer'", (producer,)).fetchone(), "producer_not_found", 404)
            require(db.execute("SELECT 1 FROM targets WHERE id=?", (target,)).fetchone(), "target_not_found", 404)
            db.execute("INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(producer,target,type) DO UPDATE SET enabled=excluded.enabled",
                       (producer, target, event_type, int(enabled)))
            if not enabled:
                db.execute("UPDATE events SET state='cancelled',outcome='revoked' WHERE producer=? AND target=? AND type=? AND state IN ('queued','leased')", (producer, target, event_type))

    def enable(self, kind, name, enabled, reviewed=False):
        require(kind in ("producer", "consumer", "target"), "invalid_kind")
        with self.transaction() as db:
            if kind == "target":
                row = db.execute("SELECT * FROM targets WHERE id=?", (name,)).fetchone()
                require(row, "not_found", 404)
                require(not enabled or reviewed, "isolation_review_required", 403)
                db.execute("UPDATE targets SET enabled=?,reviewed=? WHERE id=?", (int(enabled), int(reviewed), name))
                column, value = "target", name
            else:
                row = db.execute("SELECT * FROM principals WHERE id=? AND role=?", (name, kind)).fetchone()
                require(row, "not_found", 404)
                db.execute("UPDATE principals SET enabled=? WHERE id=?", (int(enabled), name))
                column, value = ("producer", name) if kind == "producer" else ("target", row["target"])
            if not enabled:
                db.execute(f"UPDATE events SET state='cancelled',outcome='revoked' WHERE {column}=? AND state IN ('queued','leased')", (value,))

    def rotate(self, name):
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM principals WHERE id=?", (name,)).fetchone()
            require(row, "not_found", 404)
            db.execute("UPDATE principals SET token_hash=? WHERE id=?", (digest(token), name))
            if row["role"] == "consumer":
                db.execute("UPDATE events SET state='queued',lease_hash=NULL,lease_until=NULL WHERE target=? AND state='leased'", (row["target"],))
        return token

    def authenticate(self, db, token, role):
        require(isinstance(token, str) and 32 <= len(token) <= 128, "unauthorized", 401)
        row = db.execute("SELECT * FROM principals WHERE token_hash=? AND role=? AND enabled=1", (digest(token), role)).fetchone()
        require(row, "unauthorized", 401)
        return row

    def authorized(self, db, producer, target, event_type):
        return db.execute("""SELECT 1 FROM grants g JOIN principals p ON p.id=g.producer
          JOIN targets t ON t.id=g.target WHERE g.producer=? AND g.target=? AND g.type=?
          AND g.enabled=1 AND p.enabled=1 AND p.role='producer' AND t.enabled=1 AND t.reviewed=1""",
                          (producer, target, event_type)).fetchone() is not None

    def sweep(self, db):
        now = self.clock()
        db.execute("UPDATE events SET state='expired',outcome='expired' WHERE state IN ('queued','leased') AND expires<=?", (now,))
        db.execute("UPDATE events SET state=CASE WHEN attempts>=5 THEN 'failed' ELSE 'queued' END, available=?,lease_hash=NULL WHERE state='leased' AND lease_until<=?", (now + 1, now))
        db.execute("DELETE FROM events WHERE state NOT IN ('queued','leased') AND expires<?", (now - RETENTION,))

    def view(self, row):
        return {"receipt_id": row["receipt"], "event_id": row["event_id"], "state": row["state"],
                "attempts": row["attempts"], "expires_at": utc(row["expires"]), "outcome": row["outcome"]}

    def submit(self, token, event):
        fields(event, ("schema_version", "event_id", "target", "type", "occurred_at", "expires_at"), ("refs",))
        require(event["schema_version"] == "1.0", "unsupported_schema")
        identifier(event["event_id"]); identifier(event["target"])
        require(isinstance(event["type"], str) and TYPE.fullmatch(event["type"]), "invalid_event_type")
        refs = event.get("refs", {})
        fields(refs, (), ("stream_id", "cursor"))
        for value in refs.values():
            identifier(value)
        occurred, expires = timestamp(event["occurred_at"]), timestamp(event["expires_at"])
        encoded = canonical(event)
        require(len(encoded.encode()) <= MAX_BODY, "payload_too_large", 413)
        with self.transaction() as db:
            caller = self.authenticate(db, token, "producer")
            self.sweep(db)
            require(self.authorized(db, caller["id"], event["target"], event["type"]), "event_not_authorized", 403)
            prior = db.execute("SELECT * FROM events WHERE producer=? AND event_id=?", (caller["id"], event["event_id"])).fetchone()
            if prior:
                require(prior["payload_hash"] == digest(encoded), "event_id_conflict", 409)
                return self.view(prior)
            now = self.clock()
            require(now - TTL <= occurred <= now + 30 and occurred < expires <= occurred + TTL
                    and now < expires <= now + TTL, "invalid_expiry")
            count = db.execute("SELECT count(*) FROM events WHERE producer=? AND state IN ('queued','leased')", (caller["id"],)).fetchone()[0]
            target_count = db.execute("SELECT count(*) FROM events WHERE target=? AND state IN ('queued','leased')", (event["target"],)).fetchone()[0]
            require(count < 1000 and target_count < 100, "queue_full", 429)
            rate = db.execute("SELECT * FROM rates WHERE producer=?", (caller["id"],)).fetchone()
            tokens = min(10, rate["tokens"] + max(0, now - rate["updated"]) * .5) if rate else 10
            require(tokens >= 1, "rate_limited", 429)
            db.execute("INSERT OR REPLACE INTO rates VALUES(?,?,?)", (caller["id"], tokens - 1, now))
            receipt = secrets.token_hex(16)
            db.execute("INSERT INTO events(receipt,producer,event_id,target,type,payload,payload_hash,expires,created,state,available) VALUES(?,?,?,?,?,?,?,?,?,'queued',?)",
                       (receipt, caller["id"], event["event_id"], event["target"], event["type"], encoded, digest(encoded), expires, now, now))
            return self.view(db.execute("SELECT * FROM events WHERE receipt=?", (receipt,)).fetchone())

    def status(self, token, event_id):
        identifier(event_id)
        with self.transaction() as db:
            caller = self.authenticate(db, token, "producer")
            self.sweep(db)
            row = db.execute("SELECT * FROM events WHERE producer=? AND event_id=?", (caller["id"], event_id)).fetchone()
            require(row, "not_found", 404)
            return self.view(row)

    def lease(self, token, policy_hash):
        with self.transaction() as db:
            caller = self.authenticate(db, token, "consumer")
            target = db.execute("SELECT * FROM targets WHERE id=? AND enabled=1 AND reviewed=1", (caller["target"],)).fetchone()
            require(target, "target_unavailable", 403)
            require(policy_hash == target["policy_hash"], "policy_mismatch", 409)
            self.sweep(db)
            if db.execute("SELECT 1 FROM events WHERE target=? AND state='leased'", (target["id"],)).fetchone():
                return None
            rows = db.execute("SELECT * FROM events WHERE target=? AND state='queued' AND available<=? ORDER BY created,receipt", (target["id"], self.clock())).fetchall()
            for row in rows:
                if not self.authorized(db, row["producer"], row["target"], row["type"]):
                    db.execute("UPDATE events SET state='cancelled',outcome='revoked' WHERE receipt=?", (row["receipt"],))
                    continue
                lease_token = secrets.token_urlsafe(32)
                until = min(row["expires"], self.clock() + LEASE)
                db.execute("UPDATE events SET state='leased',attempts=attempts+1,lease_hash=?,lease_until=? WHERE receipt=?",
                           (digest(lease_token), until, row["receipt"]))
                return {"delivery_id": row["receipt"], "producer_id": row["producer"],
                        "event": json.loads(row["payload"]), "policy_sha256": target["policy_hash"],
                        "lease_token": lease_token, "lease_expires_at": utc(until)}
            return None

    def finish(self, token, delivery, lease_token, outcome=None):
        identifier(delivery)
        require(isinstance(lease_token, str) and 32 <= len(lease_token) <= 128, "invalid_lease", 409)
        require(outcome in (None, "completed", "failed", "retry"), "invalid_outcome")
        with self.transaction() as db:
            caller = self.authenticate(db, token, "consumer")
            self.sweep(db)
            row = db.execute("SELECT * FROM events WHERE receipt=? AND target=?", (delivery, caller["target"])).fetchone()
            require(row, "not_found", 404)
            require(self.authorized(db, row["producer"], row["target"], row["type"]), "event_not_authorized", 403)
            require(row["lease_hash"] and secrets.compare_digest(row["lease_hash"], digest(lease_token)), "invalid_lease", 409)
            if outcome in ("completed", "failed") and row["state"] == outcome:
                return self.view(row)
            require(row["state"] == "leased" and row["lease_until"] > self.clock(), "stale_lease", 409)
            if outcome is None:
                db.execute("UPDATE events SET lease_until=? WHERE receipt=?", (min(row["expires"], self.clock() + LEASE), delivery))
            elif outcome == "retry":
                state = "failed" if row["attempts"] >= 5 else "queued"
                db.execute("UPDATE events SET state=?,outcome='retry',lease_hash=NULL,available=? WHERE receipt=?", (state, self.clock() + min(30, 2 ** row["attempts"]) + secrets.randbelow(3), delivery))
            else:
                db.execute("UPDATE events SET state=?,outcome=? WHERE receipt=?", (outcome, outcome, delivery))
            updated = db.execute("SELECT * FROM events WHERE receipt=?", (delivery,)).fetchone()
            result = self.view(updated)
            if outcome is None:
                result["lease_expires_at"] = utc(updated["lease_until"])
            return result


class HookServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, broker, port):
        self.broker = broker
        self.slots = threading.BoundedSemaphore(16)
        self.poll_slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "IAMHooks/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # Never log request bodies, credentials, paths or lease tokens.

    def respond(self, code, value):
        body = canonical(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if code == 429:
            self.send_header("Retry-After", "2")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            port = self.server.server_address[1]
            require(self.headers.get_all("Host", []) in ([f"127.0.0.1:{port}"], [f"localhost:{port}"]), "invalid_host", 421)
            require(not self.headers.get_all("Origin"), "browser_requests_forbidden", 403)
            require(not self.headers.get_all("Transfer-Encoding"), "invalid_framing")
            parsed = urlsplit(self.path)
            require(not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment, "invalid_path")
            path = parsed.path
            require(self.headers.get_all("Authorization") and len(self.headers.get_all("Authorization")) == 1, "unauthorized", 401)
            auth = self.headers.get("Authorization", "")
            require(auth.startswith("Bearer "), "unauthorized", 401)
            token = auth[7:]
            data = {}
            if self.command == "POST":
                lengths = self.headers.get_all("Content-Length", [])
                require(len(lengths) == 1 and lengths[0].isdigit(), "invalid_framing")
                size = int(lengths[0])
                require(0 < size <= MAX_BODY, "payload_too_large", 413)
                require(self.headers.get_content_type() == "application/json", "unsupported_media_type", 415)
                raw = self.rfile.read(size)
                require(len(raw) == size, "incomplete_body")
                def pairs(items):
                    obj = {}
                    for key, value in items:
                        require(key not in obj, "duplicate_json_key")
                        obj[key] = value
                    return obj
                data = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(HookError("invalid_json")))
                require(isinstance(data, dict), "invalid_fields")
            broker = self.server.broker
            if self.command == "POST" and path == "/v1/events":
                result = broker.submit(token, data)
                return self.respond(202, {"schema_version": "1.0", "ok": True, "data": result})
            if self.command == "GET" and re.fullmatch(r"/v1/events/[A-Za-z0-9_-]{1,128}", path):
                result = broker.status(token, path.rsplit("/", 1)[1])
            elif self.command == "POST" and path == "/v1/consumer/leases":
                fields(data, ("policy_sha256",), ("wait_seconds",))
                require(isinstance(data["policy_sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", data["policy_sha256"]), "invalid_policy_digest")
                wait = data.get("wait_seconds", 0)
                require(type(wait) is int and 0 <= wait <= 20, "invalid_wait")
                deadline = time.monotonic() + wait
                acquired = self.server.poll_slots.acquire(blocking=False) if wait else False
                require(not wait or acquired, "poll_capacity", 429)
                try:
                    while True:
                        result = broker.lease(token, data["policy_sha256"])
                        if result or time.monotonic() >= deadline:
                            break
                        time.sleep(.25)
                finally:
                    if acquired:
                        self.server.poll_slots.release()
            elif self.command == "POST" and re.fullmatch(r"/v1/consumer/leases/[A-Za-z0-9_-]{1,128}/(complete|renew)", path):
                renew = path.endswith("/renew")
                fields(data, ("lease_token",) if renew else ("lease_token", "outcome"))
                result = broker.finish(token, path.split("/")[-2], data["lease_token"], None if renew else data["outcome"])
            else:
                raise HookError("not_found", 404)
            self.respond(200, {"schema_version": "1.0", "ok": True, "data": result})
        except HookError as exc:
            self.respond(exc.status, {"schema_version": "1.0", "ok": False, "error": {"code": exc.code}})
        except (ValueError, UnicodeError, RecursionError):
            self.respond(400, {"schema_version": "1.0", "ok": False, "error": {"code": "invalid_json"}})
        except (TimeoutError, ConnectionError, OSError):
            self.close_connection = True
        except Exception:
            self.respond(503, {"schema_version": "1.0", "ok": False, "error": {"code": "temporarily_unavailable"}})


def store_secret(broker, token):
    directory = private_directory(broker.directory / "secrets")
    path = directory / (secrets.token_hex(12) + ".token")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(token + "\n")
    return str(path)


def run_cli(args):
    import interagentmail as iam
    try:
        broker = Broker(args.state_dir or iam.ROOT / "hooks")
        action = args.hook_action
        result = {"ok": True}
        if action == "producer-create":
            result.update(id=args.name, enabled=False, token_file=store_secret(broker, broker.create_producer(args.name)))
        elif action == "target-create":
            token, policy_hash = broker.create_target(args.name, json.loads(Path(args.policy_file).read_text(encoding="utf-8")))
            result.update(id=args.name, consumer_id="consumer-" + args.name, enabled=False,
                          policy_sha256=policy_hash, token_file=store_secret(broker, token))
        elif action == "grant":
            broker.grant(args.producer, args.target, args.event_type, args.enable)
        elif action in ("enable", "revoke"):
            broker.enable(args.kind, args.name, action == "enable", args.reviewed_isolation)
        elif action == "rotate":
            result["token_file"] = store_secret(broker, broker.rotate(args.name))
        elif action == "status":
            with broker.transaction() as db:
                broker.sweep(db)
                result["principals"] = [dict(r) for r in db.execute("SELECT id,role,target,enabled FROM principals")]
                result["targets"] = [dict(r) for r in db.execute("SELECT id,policy_hash,enabled,reviewed FROM targets")]
                result["grants"] = [dict(r) for r in db.execute("SELECT * FROM grants")]
                result["events"] = [dict(r) for r in db.execute("SELECT state,count(*) AS count FROM events GROUP BY state")]
        elif action == "serve":
            require(1024 <= args.port <= 65535, "invalid_port")
            server = HookServer(broker, args.port)
            print(f"IAM hooks listening only on http://127.0.0.1:{args.port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        print(canonical(result))
        return 0
    except (HookError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(canonical({"ok": False, "error": {"code": exc.code if isinstance(exc, HookError) else "local_configuration_error"}}))
        return 1


def add_parser(subparsers):
    parser = subparsers.add_parser("hooks", help="Optional local generic event broker; no agent execution.")
    parser.add_argument("--state-dir", type=Path, help="Dedicated private broker state directory.")
    commands = parser.add_subparsers(dest="hook_action", required=True)
    for action in ("producer-create", "target-create", "rotate"):
        command = commands.add_parser(action)
        command.add_argument("name")
        if action == "target-create":
            command.add_argument("--policy-file", required=True)
        command.set_defaults(func=run_cli)
    grant = commands.add_parser("grant", help="Create/update exact grant; omit --enable to revoke/disable.")
    grant.add_argument("producer")
    grant.add_argument("--target", required=True)
    grant.add_argument("--event-type", required=True)
    grant.add_argument("--enable", action="store_true")
    grant.set_defaults(func=run_cli)
    for action in ("enable", "revoke"):
        command = commands.add_parser(action)
        command.add_argument("kind", choices=("producer", "consumer", "target"))
        command.add_argument("name")
        command.add_argument("--reviewed-isolation", action="store_true")
        command.set_defaults(func=run_cli)
    commands.add_parser("status").set_defaults(func=run_cli)
    serve = commands.add_parser("serve", help="Run foreground on loopback; never started by iam start.")
    serve.add_argument("--port", type=int, default=8790)
    serve.set_defaults(func=run_cli)
