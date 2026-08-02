"""Authenticated browser interface for a human InterAgentMail mailbox."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import interagentmail as iam
from iam_service import IAMError, IAMService


LOG = logging.getLogger("iam.web")
WEB_SCHEMA_VERSION = 1
PASSWORD_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_REQUEST_BYTES = 1_048_576
SESSION_IDLE_SECONDS = 60 * 60
SESSION_LIFETIME_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 256
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_MAX_FAILURES = 5
DEFAULT_PORT = 8787
SESSION_COOKIE = "iam_session"
LAN_WARNING = (
    "LAN access exposes this interface to other devices on the network. IAM's HTTP traffic is not "
    "end-to-end encrypted, so use only a trusted, password-protected WPA2/WPA3 network, keep the "
    "application password private, and never forward this port to the public internet. Anyone who "
    "gains access can read messages, send instructions to agents, and may cause them to modify or "
    "corrupt projects."
)


class WebError(ValueError):
    def __init__(self, code: str, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


def web_config_path() -> Path:
    return iam.ROOT / "web.json"


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    iam.write_atomic(path, payload)
    if os.name != "nt":
        path.chmod(0o600)


def _registered_projects() -> dict[str, Any]:
    if not iam.CONFIG.exists():
        return {}
    try:
        config = iam.load(iam.CONFIG)
    except (OSError, ValueError) as exc:
        raise WebError("IAM_CONFIG_INVALID", f"Invalid InterAgentMail configuration: {exc}", recoverable=False) from None
    projects = config.get("projects", {}) if isinstance(config, dict) else {}
    if not isinstance(projects, dict):
        raise WebError("IAM_CONFIG_INVALID", "InterAgentMail projects configuration must be an object.", recoverable=False)
    return projects


def create_user_mailbox(address: str, display_name: str | None = None) -> dict[str, Any]:
    try:
        address = iam.safe_segment(address.strip(), "mailbox address")
    except SystemExit as exc:
        raise WebError("IAM_USER_ADDRESS_INVALID", str(exc)) from None
    if address in _registered_projects():
        raise WebError(
            "IAM_ADDRESS_COLLISION",
            f"{address} is already owned by a registered project and cannot become a user mailbox.",
        )

    box = iam.mailbox(address)
    profile_path = box / "profile.json"
    existed = box.exists()
    before: dict[str, Any] = {}
    if existed and not profile_path.exists():
        raise WebError(
            "IAM_ADDRESS_COLLISION",
            f"Mailbox {address} already exists without explicit user ownership.",
        )
    if profile_path.exists():
        try:
            loaded = iam.load(profile_path)
        except (OSError, ValueError) as exc:
            raise WebError("IAM_CONFIG_INVALID", f"Mailbox profile for {address} is invalid: {exc}", recoverable=False) from None
        if not isinstance(loaded, dict):
            raise WebError("IAM_CONFIG_INVALID", f"Mailbox profile for {address} must be an object.", recoverable=False)
        before = loaded
        if before.get("address") not in (None, address):
            raise WebError(
                "IAM_CONFIG_INVALID",
                f"Mailbox profile for {address} declares a different address.",
                recoverable=False,
            )
        if before.get("project_root") or before.get("kind") != "user":
            raise WebError(
                "IAM_ADDRESS_COLLISION",
                f"Mailbox {address} is not an unowned user mailbox; IAM will not migrate its identity implicitly.",
            )

    iam.ensure_box(address)
    profile = iam.profile(address)
    profile["address"] = address
    profile["kind"] = "user"
    if display_name:
        profile["display_name"] = display_name.strip() or address
    profile.setdefault("display_name", address)
    profile.setdefault("created_at", iam.now())
    iam.save_profile(address, profile)
    if not existed:
        status = "created"
    elif profile == before:
        status = "unchanged"
    else:
        status = "updated"
    return {
        "status": status,
        "address": address,
        "display_name": str(profile.get("display_name") or address),
        "kind": "user",
        "mailbox": str(box),
    }


def list_user_mailboxes() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not iam.MAILBOXES.exists():
        return rows
    for box in sorted(path for path in iam.MAILBOXES.iterdir() if path.is_dir()):
        try:
            profile = iam.load(box / "profile.json")
        except (OSError, ValueError):
            continue
        if isinstance(profile, dict) and profile.get("kind") == "user":
            rows.append({
                "address": str(profile.get("address") or box.name),
                "display_name": str(profile.get("display_name") or box.name),
            })
    return rows


def password_record(password: str) -> dict[str, Any]:
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise WebError(
            "IAM_WEB_PASSWORD_WEAK",
            f"The web password must contain between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters.",
        )
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": PASSWORD_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }


def password_parameters(record: dict[str, Any]) -> tuple[int, bytes, bytes] | None:
    try:
        if record.get("algorithm") != "pbkdf2-sha256":
            return None
        iterations = int(record["iterations"])
        salt = base64.b64decode(str(record["salt"]), validate=True)
        expected = base64.b64decode(str(record["digest"]), validate=True)
    except (KeyError, TypeError, ValueError):
        return None
    if iterations < 100_000 or iterations > 2_000_000 or len(salt) < 16 or len(expected) != 32:
        return None
    return iterations, salt, expected


def verify_password(password: str, record: dict[str, Any]) -> bool:
    parameters = password_parameters(record)
    if parameters is None:
        return False
    iterations, salt, expected = parameters
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def load_user_profile(mailbox_address: str) -> dict[str, Any]:
    try:
        mailbox_address = iam.safe_segment(mailbox_address, "mailbox address")
        profile_path = iam.mailbox(mailbox_address) / "profile.json"
    except SystemExit as exc:
        raise WebError("IAM_USER_NOT_FOUND", str(exc)) from None
    if not profile_path.exists():
        raise WebError("IAM_USER_NOT_FOUND", f"{mailbox_address} is not a user mailbox. Run `iam user create` first.")
    try:
        profile = iam.load(profile_path)
    except (OSError, ValueError) as exc:
        raise WebError("IAM_CONFIG_INVALID", f"Mailbox profile for {mailbox_address} is invalid: {exc}", recoverable=False) from None
    if not isinstance(profile, dict) or profile.get("kind") != "user":
        raise WebError("IAM_USER_NOT_FOUND", f"{mailbox_address} is not a user mailbox. Run `iam user create` first.")
    if profile.get("project_root"):
        raise WebError(
            "IAM_CONFIG_INVALID",
            f"User mailbox {mailbox_address} is also marked as a project identity.",
            recoverable=False,
        )
    if profile.get("address") != mailbox_address:
        raise WebError(
            "IAM_CONFIG_INVALID",
            f"Mailbox profile for {mailbox_address} declares a different address.",
            recoverable=False,
        )
    return profile


def configure_web(
    mailbox_address: str,
    password: str,
    *,
    port: int = DEFAULT_PORT,
    lan_enabled: bool = False,
) -> dict[str, Any]:
    if port < 1 or port > 65535:
        raise WebError("IAM_WEB_PORT_INVALID", "Web port must be between 1 and 65535.")
    load_user_profile(mailbox_address)
    config = {
        "schema_version": WEB_SCHEMA_VERSION,
        "mailbox": mailbox_address,
        "port": port,
        "lan_enabled": bool(lan_enabled),
        "password": password_record(password),
    }
    _write_private_json(web_config_path(), config)
    return public_web_config(config)


def load_web_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve() if path else web_config_path()
    if not config_path.exists():
        raise WebError("IAM_WEB_NOT_CONFIGURED", "The web interface is not configured. Run `iam web setup USER_MAILBOX`.")
    try:
        config = iam.load(config_path)
    except (OSError, ValueError) as exc:
        raise WebError("IAM_WEB_CONFIG_INVALID", f"Invalid web configuration: {exc}", recoverable=False) from None
    if not isinstance(config, dict) or config.get("schema_version") != WEB_SCHEMA_VERSION:
        raise WebError("IAM_WEB_CONFIG_INVALID", "Unsupported or malformed web configuration.", recoverable=False)
    mailbox_address = str(config.get("mailbox", ""))
    try:
        load_user_profile(mailbox_address)
    except WebError as exc:
        raise WebError("IAM_WEB_CONFIG_INVALID", f"Configured user mailbox is unavailable: {exc}", recoverable=False) from None
    password = config.get("password")
    if not isinstance(password, dict) or password_parameters(password) is None:
        raise WebError("IAM_WEB_CONFIG_INVALID", "Web configuration does not reference a valid user mailbox and password.", recoverable=False)
    port = config.get("port")
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise WebError("IAM_WEB_CONFIG_INVALID", "Web configuration contains an invalid port.", recoverable=False)
    return config


def public_web_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": int(config.get("schema_version", WEB_SCHEMA_VERSION)),
        "mailbox": str(config.get("mailbox", "")),
        "port": int(config.get("port", DEFAULT_PORT)),
        "lan_enabled": bool(config.get("lan_enabled", False)),
        "authentication": "password",
    }


def update_password(password: str, path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve() if path else web_config_path()
    config = load_web_config(config_path)
    config["password"] = password_record(password)
    _write_private_json(config_path, config)
    return public_web_config(config)


def local_hostnames() -> set[str]:
    names = {"localhost", "127.0.0.1", "::1", "0.0.0.0", socket.gethostname().lower()}
    try:
        _canonical, aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
        names.update(alias.lower() for alias in aliases)
        names.update(address.lower() for address in addresses)
    except OSError:
        pass
    return names


class WebApplication:
    def __init__(self, config: dict[str, Any], *, host: str, instance_token: str | None = None) -> None:
        self.config = config
        self.host = host
        self.instance_token = instance_token or secrets.token_urlsafe(32)
        self.service = IAMService.for_mailbox(str(config["mailbox"]))
        self.sessions: dict[str, dict[str, Any]] = {}
        self.login_failures: dict[str, list[float]] = {}
        self._state_lock = threading.RLock()
        self._password_slots = threading.BoundedSemaphore(value=4)
        self.allowed_hosts = local_hostnames()
        if host not in {"0.0.0.0", "::"}:
            self.allowed_hosts.add(host.lower())

    @property
    def lan_enabled(self) -> bool:
        return self.host in {"0.0.0.0", "::"} or bool(self.config.get("lan_enabled"))

    def authenticate(self, password: str, client_ip: str) -> dict[str, str]:
        now = time.time()
        with self._state_lock:
            failures = [
                stamp for stamp in self.login_failures.get(client_ip, [])
                if now - stamp < LOGIN_WINDOW_SECONDS
            ]
            self.login_failures[client_ip] = failures
            if len(failures) >= LOGIN_MAX_FAILURES:
                raise WebError("IAM_WEB_LOGIN_THROTTLED", "Too many failed sign-in attempts. Try again in a few minutes.")
        if len(password) > MAX_PASSWORD_LENGTH:
            valid = False
        elif not self._password_slots.acquire(blocking=False):
            raise WebError("IAM_WEB_BUSY", "Too many sign-in attempts are being processed. Try again shortly.")
        else:
            try:
                valid = verify_password(password, self.config["password"])
            finally:
                self._password_slots.release()
        with self._state_lock:
            if not valid:
                self.login_failures.setdefault(client_ip, []).append(now)
                raise WebError("IAM_WEB_LOGIN_FAILED", "Incorrect password.")
            self.login_failures.pop(client_ip, None)
            expired = [
                item for item, session in self.sessions.items()
                if now - session["last_seen"] > SESSION_IDLE_SECONDS
                or now - session["created"] > SESSION_LIFETIME_SECONDS
            ]
            for item in expired:
                self.sessions.pop(item, None)
            while len(self.sessions) >= MAX_SESSIONS:
                oldest = min(self.sessions, key=lambda item: self.sessions[item]["created"])
                self.sessions.pop(oldest, None)
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            self.sessions[token] = {"created": now, "last_seen": now, "csrf": csrf, "client_ip": client_ip}
        return {"token": token, "csrf": csrf}

    def session(self, token: str | None, client_ip: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._state_lock:
            session = self.sessions.get(token)
            if not session or not hmac.compare_digest(str(session["client_ip"]), client_ip):
                return None
            now = time.time()
            if now - session["last_seen"] > SESSION_IDLE_SECONDS or now - session["created"] > SESSION_LIFETIME_SECONDS:
                self.sessions.pop(token, None)
                return None
            session["last_seen"] = now
            return session

    def logout(self, token: str | None) -> None:
        if token:
            with self._state_lock:
                self.sessions.pop(token, None)


class IAMWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: WebApplication) -> None:
        self.app = app
        super().__init__(server_address, IAMWebHandler)


class IAMWebHandler(BaseHTTPRequestHandler):
    server: IAMWebServer
    server_version = "IAMWeb/1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format_string: str, *args: Any) -> None:
        LOG.info("%s - %s", self.client_address[0], format_string % args)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any], *, cookie: str | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _host_allowed(self) -> bool:
        value = self.headers.get("Host", "")
        if not value:
            return False
        if value.startswith("["):
            host = value[1:].split("]", 1)[0]
        else:
            host = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
        lowered = host.lower()
        if lowered in self.server.app.allowed_hosts:
            return True
        try:
            address = ipaddress.ip_address(lowered)
        except ValueError:
            return False
        return address.is_loopback or address.is_private or address.is_link_local

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.netloc.lower() == self.headers.get("Host", "").lower()

    def _cookie_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except ValueError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _require_session(self, *, csrf: bool = False) -> tuple[str, dict[str, Any]] | None:
        token = self._cookie_token()
        session = self.server.app.session(token, self.client_address[0])
        if not token or not session:
            self._error(HTTPStatus.UNAUTHORIZED, "IAM_WEB_AUTH_REQUIRED", "Sign in is required.")
            return None
        if csrf and not hmac.compare_digest(str(session["csrf"]), self.headers.get("X-IAM-CSRF", "")):
            self._error(HTTPStatus.FORBIDDEN, "IAM_WEB_CSRF_INVALID", "The request security token is missing or invalid.")
            return None
        return token, session

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise WebError("IAM_WEB_REQUEST_INVALID", "Invalid Content-Length.") from None
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise WebError("IAM_WEB_REQUEST_INVALID", "Request body is empty or too large.")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise WebError("IAM_WEB_REQUEST_INVALID", "Requests must use application/json.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebError("IAM_WEB_REQUEST_INVALID", "Request body is not valid JSON.") from None
        if not isinstance(payload, dict):
            raise WebError("IAM_WEB_REQUEST_INVALID", "Request body must be a JSON object.")
        return payload

    def _route_guard(self) -> bool:
        if not self._host_allowed():
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "IAM_WEB_HOST_INVALID", "The request Host is not a local interface.")
            return False
        if not self._origin_allowed():
            self._error(HTTPStatus.FORBIDDEN, "IAM_WEB_ORIGIN_INVALID", "Cross-origin requests are not allowed.")
            return False
        return True

    def do_GET(self) -> None:
        if not self._route_guard():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "service": "iam-web", "instance": self.server.app.instance_token})
            return
        if parsed.path == "/":
            self._send(HTTPStatus.OK, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/app.css":
            self._send(HTTPStatus.OK, APP_CSS.encode("utf-8"), "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send(HTTPStatus.OK, APP_JS.encode("utf-8"), "text/javascript; charset=utf-8")
            return
        auth = self._require_session()
        if not auth:
            return
        _token, session = auth
        try:
            if parsed.path == "/api/session":
                profile = self.server.app.service.whoami()
                self._json(HTTPStatus.OK, {"ok": True, "data": {
                    "mailbox": profile,
                    "csrf": session["csrf"],
                    "lan_enabled": self.server.app.lan_enabled,
                    "network_warning": LAN_WARNING if self.server.app.lan_enabled else None,
                }})
                return
            if parsed.path == "/api/mailboxes":
                rows = [row for row in self.server.app.service.list_mailboxes() if row["address"] != self.server.app.service.address]
                self._json(HTTPStatus.OK, {"ok": True, "data": {"mailboxes": rows}})
                return
            if parsed.path == "/api/messages":
                query = parse_qs(parsed.query)
                folder = query.get("folder", ["inbox"])[0]
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError:
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Message limit must be a number.") from None
                if limit < 1 or limit > 200:
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Message limit must be between 1 and 200.")
                rows = self.server.app.service.list_messages(folder, limit)
                self._json(HTTPStatus.OK, {"ok": True, "data": {"folder": folder, "messages": rows}})
                return
        except (IAMError, WebError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, getattr(exc, "code", "IAM_WEB_OPERATION_FAILED"), str(exc))
            return
        self._error(HTTPStatus.NOT_FOUND, "IAM_WEB_NOT_FOUND", "Not found.")

    def do_POST(self) -> None:
        if not self._route_guard():
            return
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/login":
                password = payload.get("password")
                if not isinstance(password, str):
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Password is required.")
                session = self.server.app.authenticate(password, self.client_address[0])
                cookie = (
                    f"{SESSION_COOKIE}={session['token']}; HttpOnly; SameSite=Strict; "
                    f"Path=/; Max-Age={SESSION_LIFETIME_SECONDS}"
                )
                self._json(HTTPStatus.OK, {"ok": True, "data": {"csrf": session["csrf"]}}, cookie=cookie)
                return

            auth = self._require_session(csrf=True)
            if not auth:
                return
            token, _session = auth
            if parsed.path == "/api/logout":
                self.server.app.logout(token)
                self._json(
                    HTTPStatus.OK,
                    {"ok": True},
                    cookie=f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
                )
                return
            if parsed.path == "/api/send":
                recipients = payload.get("to")
                subject = payload.get("subject")
                body = payload.get("body")
                if (
                    not isinstance(recipients, list)
                    or not recipients
                    or len(recipients) > 20
                    or not all(isinstance(item, str) for item in recipients)
                ):
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Choose between 1 and 20 recipients.")
                if not isinstance(subject, str) or not subject.strip() or len(subject) > 300:
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Subject must contain between 1 and 300 characters.")
                if not isinstance(body, str) or not body.strip() or len(body) > 100_000:
                    raise WebError("IAM_WEB_REQUEST_INVALID", "Message must contain between 1 and 100000 characters.")
                resolved_recipients = iam.resolve_names(recipients)
                missing = [recipient for recipient in resolved_recipients if not iam.mailbox(recipient).is_dir()]
                if missing:
                    raise WebError(
                        "IAM_WEB_RECIPIENT_NOT_FOUND",
                        "Unknown mailbox recipient: " + ", ".join(missing),
                    )
                message = self.server.app.service.send(resolved_recipients, subject.strip(), body.strip())
                self._json(HTTPStatus.CREATED, {"ok": True, "data": {"message": message}})
                return
            if parsed.path == "/api/reply":
                message_id = payload.get("message_id")
                body = payload.get("body")
                if not isinstance(message_id, str) or not isinstance(body, str) or not body.strip() or len(body) > 100_000:
                    raise WebError("IAM_WEB_REQUEST_INVALID", "A valid message ID and reply body are required.")
                message = self.server.app.service.reply(message_id, body.strip())
                self._json(HTTPStatus.CREATED, {"ok": True, "data": {"message": message}})
                return
            if parsed.path == "/api/read":
                message_id = payload.get("message_id")
                if not isinstance(message_id, str):
                    raise WebError("IAM_WEB_REQUEST_INVALID", "A valid message ID is required.")
                message = self.server.app.service.read(message_id)
                self._json(HTTPStatus.OK, {"ok": True, "data": {"message": message}})
                return
            if parsed.path == "/api/archive":
                message_id = payload.get("message_id")
                if not isinstance(message_id, str):
                    raise WebError("IAM_WEB_REQUEST_INVALID", "A valid message ID is required.")
                archived = self.server.app.service.archive(message_id)
                self._json(HTTPStatus.OK, {"ok": True, "data": archived})
                return
        except WebError as exc:
            if exc.code == "IAM_WEB_LOGIN_THROTTLED":
                status = HTTPStatus.TOO_MANY_REQUESTS
            elif exc.code == "IAM_WEB_BUSY":
                status = HTTPStatus.SERVICE_UNAVAILABLE
            elif exc.code == "IAM_WEB_LOGIN_FAILED":
                status = HTTPStatus.UNAUTHORIZED
            else:
                status = HTTPStatus.BAD_REQUEST
            self._error(status, exc.code, exc.message)
            return
        except IAMError as exc:
            self._error(HTTPStatus.BAD_REQUEST, "IAM_WEB_OPERATION_FAILED", str(exc))
            return
        except Exception:
            LOG.exception("Unhandled web request failure")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "IAM_WEB_INTERNAL_ERROR", "The request could not be completed.")
            return
        self._error(HTTPStatus.NOT_FOUND, "IAM_WEB_NOT_FOUND", "Not found.")


INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>InterAgentMail</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="shell">
    <section id="login" class="login-card">
      <div class="brand-mark">IAM</div>
      <p class="eyebrow">Cerberus Game Labs</p>
      <h1>Your agents are waiting.</h1>
      <p class="muted">Sign in to your private InterAgentMail mailbox.</p>
      <form id="login-form">
        <label for="password">Application password</label>
        <input id="password" type="password" minlength="12" autocomplete="current-password" required autofocus>
        <button type="submit">Open mailbox</button>
      </form>
      <p id="login-error" class="error" role="alert"></p>
    </section>

    <section id="app" class="app" hidden>
      <header>
        <div><p class="eyebrow">InterAgentMail</p><h1 id="identity">Mailbox</h1></div>
        <div class="header-actions"><span id="network-mode" class="badge"></span><button id="logout" class="quiet">Sign out</button></div>
      </header>
      <div id="network-warning" class="warning" hidden></div>
      <div class="workspace">
        <aside>
          <button class="compose active" data-view="compose">New request</button>
          <nav>
            <button data-folder="inbox">Inbox <span id="unread-count"></span></button>
            <button data-folder="sent">Sent</button>
            <button data-folder="archive">Archive</button>
          </nav>
          <p class="trust-note">Keep this interface on a trusted private network. Never expose its port to the internet.</p>
        </aside>

        <section id="compose-view" class="panel">
          <p class="eyebrow">New message</p><h2>Ask an agent</h2>
          <form id="compose-form">
            <label for="to">Recipients</label>
            <select id="to" multiple required></select>
            <small>Hold Ctrl or Command to select multiple agents.</small>
            <label for="subject">Subject</label>
            <input id="subject" maxlength="300" required>
            <label for="body">Request</label>
            <textarea id="body" rows="12" maxlength="100000" required></textarea>
            <div class="form-actions"><button type="submit">Send request</button><span id="send-status" role="status"></span></div>
          </form>
        </section>

        <section id="mail-view" class="mail-view" hidden>
          <div class="message-list">
            <div class="list-heading"><p class="eyebrow" id="folder-name">Inbox</p><button id="refresh" class="quiet">Refresh</button></div>
            <div id="messages"></div>
          </div>
          <article id="reader" class="reader"><div class="empty-state">Select a message to read it.</div></article>
        </section>
      </div>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>'''


APP_CSS = r''':root{color-scheme:dark;--bg:#0a0d12;--panel:#111720;--panel2:#171f2b;--line:#293444;--text:#f3f6fa;--muted:#93a2b7;--accent:#e35235;--accent2:#ff7658;--warn:#f1b84b}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at 15% 0,#1b2430 0,transparent 34rem),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}
.shell{min-height:100vh}
.login-card{width:min(440px,calc(100% - 32px));margin:10vh auto;padding:42px;background:rgba(17,23,32,.96);border:1px solid var(--line);border-radius:20px;box-shadow:0 30px 80px #0008}
.brand-mark{display:grid;place-items:center;width:58px;height:58px;border-radius:15px;background:linear-gradient(145deg,var(--accent2),var(--accent));font-weight:900;letter-spacing:-2px}
.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:11px;font-weight:800;color:var(--accent2);margin:18px 0 5px}
h1,h2{line-height:1.1;margin:0 0 12px}h1{font-size:clamp(28px,4vw,42px)}h2{font-size:28px}.muted,small{color:var(--muted)}
label{display:block;font-weight:700;margin:18px 0 7px}
input,textarea,select{width:100%;background:#0d1219;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:12px;font:inherit}
select{min-height:150px}input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);border-color:transparent}
button{border:0;border-radius:10px;background:var(--accent);color:white;padding:11px 16px;font:inherit;font-weight:800;cursor:pointer}button:hover{filter:brightness(1.12)}form>button{width:100%;margin-top:20px}.error{min-height:22px;color:#ff8870}
.app header{height:84px;padding:16px 28px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:#0d1219e8}.app header .eyebrow{margin:0}.app header h1{font-size:24px;margin:2px 0}
.header-actions{display:flex;align-items:center;gap:12px}.quiet{background:transparent;border:1px solid var(--line);padding:8px 12px}.badge{font-size:12px;font-weight:800;background:#203044;padding:6px 9px;border-radius:99px}
.warning{padding:12px 28px;background:#3a2b11;color:#ffe2a3;border-bottom:1px solid #70551f}.workspace{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 84px)}
aside{border-right:1px solid var(--line);padding:22px;background:#0d1219}.compose{width:100%;margin-bottom:22px}nav{display:grid;gap:6px}nav button{background:transparent;text-align:left;color:var(--muted)}nav button:hover,nav button.active{background:var(--panel2);color:var(--text)}.trust-note{color:var(--muted);font-size:12px;margin-top:36px}
.panel{padding:42px;max-width:850px}.panel form{margin-top:28px}.form-actions{display:flex;align-items:center;gap:15px;margin-top:20px}.form-actions button{min-width:160px}
.mail-view{display:grid;grid-template-columns:minmax(300px,38%) 1fr;min-width:0}.message-list{border-right:1px solid var(--line);min-width:0}.list-heading{height:60px;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}.list-heading .eyebrow{margin:0}
.message-row{display:block;width:100%;padding:15px 18px;border-radius:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;color:var(--text)}.message-row:hover,.message-row.active{background:var(--panel2);filter:none}.message-row.unread{border-left:3px solid var(--accent)}.message-row strong,.message-row span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.message-row span{color:var(--muted);font-weight:400;font-size:13px}
.reader{padding:34px;min-width:0}.reader-meta{color:var(--muted);margin-bottom:24px}.reader-body{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--panel);padding:22px;border-radius:12px;border:1px solid var(--line)}.reader-actions{display:flex;gap:10px;margin-top:20px}.reply-box{margin-top:18px}.empty-state{color:var(--muted);display:grid;place-items:center;min-height:50vh}
@media(max-width:800px){.workspace{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}aside nav{grid-template-columns:repeat(3,1fr)}.trust-note{display:none}.panel{padding:24px}.mail-view{grid-template-columns:1fr}.message-list{border-right:0}.reader{border-top:1px solid var(--line)}.app header{padding:14px 18px}.badge{display:none}}'''


APP_JS = r'''let csrf="",folder="inbox",selected=null,poll=null;
const $=id=>document.getElementById(id);
async function api(path,options={}){options.credentials="same-origin";options.headers={"Content-Type":"application/json",...(csrf?{"X-IAM-CSRF":csrf}:{}),...(options.headers||{})};const response=await fetch(path,options);const data=await response.json();if(!response.ok)throw new Error(data.error?.message||"Request failed");return data.data||data}
function showLogin(message=""){$("login").hidden=false;$("app").hidden=true;$("login-error").textContent=message}
async function boot(){try{const data=await api("/api/session");csrf=data.csrf;$("login").hidden=true;$("app").hidden=false;$("identity").textContent=`${data.mailbox.display_name} <${data.mailbox.address}>`;$("network-mode").textContent=data.lan_enabled?"LAN access":"This PC only";if(data.network_warning){$("network-warning").hidden=false;$("network-warning").textContent=data.network_warning}await loadMailboxes();showCompose();if(!poll)poll=setInterval(()=>{if(folder==="inbox"&&!$("mail-view").hidden)loadMessages(false)},10000)}catch(error){showLogin()}}
async function loadMailboxes(){const data=await api("/api/mailboxes");const select=$("to");select.replaceChildren();for(const box of data.mailboxes){const option=document.createElement("option");option.value=box.address;option.textContent=`${box.display_name} <${box.address}>`;select.append(option)}}
function showCompose(){selected=null;$("compose-view").hidden=false;$("mail-view").hidden=true;document.querySelectorAll("[data-folder]").forEach(button=>button.classList.remove("active"))}
async function openFolder(name){folder=name;selected=null;$("compose-view").hidden=true;$("mail-view").hidden=false;$("folder-name").textContent=name[0].toUpperCase()+name.slice(1);document.querySelectorAll("[data-folder]").forEach(button=>button.classList.toggle("active",button.dataset.folder===name));await loadMessages()}
async function loadMessages(reset=true){try{const data=await api(`/api/messages?folder=${encodeURIComponent(folder)}&limit=200`);const container=$("messages");container.replaceChildren();let unread=0;for(const message of data.messages){if(!message.read_at&&folder==="inbox")unread++;const button=document.createElement("button");button.className=`message-row${!message.read_at&&folder==="inbox"?" unread":""}`;const sender=document.createElement("strong");sender.textContent=folder==="sent"?`To: ${(message.to_display||message.to||[]).join(", ")}`:(message.from_display||message.from);const subject=document.createElement("span");subject.textContent=message.subject;const date=document.createElement("span");date.textContent=new Date(message.created_at).toLocaleString();button.append(sender,subject,date);button.onclick=()=>readMessage(message,button);container.append(button)}$("unread-count").textContent=unread?`(${unread})`:"";if(reset||!selected)$("reader").replaceChildren(Object.assign(document.createElement("div"),{className:"empty-state",textContent:data.messages.length?"Select a message to read it.":"No messages here."}))}catch(error){$("messages").textContent=error.message}}
async function readMessage(message,button){selected=message;document.querySelectorAll(".message-row").forEach(row=>row.classList.remove("active"));button.classList.add("active");if(folder==="inbox"&&!message.read_at){try{const data=await api("/api/read",{method:"POST",body:JSON.stringify({message_id:message.id})});message=data.message;button.classList.remove("unread")}catch(error){console.error(error)}}const reader=$("reader");reader.replaceChildren();const title=document.createElement("h2");title.textContent=message.subject;const meta=document.createElement("div");meta.className="reader-meta";meta.textContent=`From ${message.from_display||message.from} <${message.from}> · ${new Date(message.created_at).toLocaleString()}`;const body=document.createElement("div");body.className="reader-body";body.textContent=message.body;reader.append(title,meta,body);if(folder==="inbox"){const reply=document.createElement("textarea");reply.className="reply-box";reply.rows=6;reply.placeholder="Write a reply…";const actions=document.createElement("div");actions.className="reader-actions";const send=document.createElement("button");send.textContent="Send reply";send.onclick=async()=>{if(!reply.value.trim())return;try{await api("/api/reply",{method:"POST",body:JSON.stringify({message_id:message.id,body:reply.value})});reply.value="";send.textContent="Reply sent"}catch(error){send.textContent=error.message}};const archive=document.createElement("button");archive.className="quiet";archive.textContent="Archive";archive.onclick=async()=>{try{await api("/api/archive",{method:"POST",body:JSON.stringify({message_id:message.id})});await loadMessages()}catch(error){archive.textContent=error.message}};actions.append(send,archive);reader.append(reply,actions)}}
$("login-form").onsubmit=async event=>{event.preventDefault();try{const data=await api("/api/login",{method:"POST",body:JSON.stringify({password:$("password").value})});csrf=data.csrf;$("password").value="";await boot()}catch(error){showLogin(error.message)}};
$("compose-form").onsubmit=async event=>{event.preventDefault();const to=[...$("to").selectedOptions].map(option=>option.value);try{await api("/api/send",{method:"POST",body:JSON.stringify({to,subject:$("subject").value,body:$("body").value})});$("compose-form").reset();$("send-status").textContent="Request sent."}catch(error){$("send-status").textContent=error.message}};
document.querySelector("[data-view=compose]").onclick=showCompose;
document.querySelectorAll("[data-folder]").forEach(button=>button.onclick=()=>openFolder(button.dataset.folder));
$("refresh").onclick=()=>loadMessages();
$("logout").onclick=async()=>{try{await api("/api/logout",{method:"POST",body:"{}"})}finally{csrf="";if(poll){clearInterval(poll);poll=null}showLogin()}};
boot();'''


def create_server(
    config: dict[str, Any],
    host: str,
    port: int,
    *,
    instance_token: str | None = None,
) -> IAMWebServer:
    return IAMWebServer((host, port), WebApplication(config, host=host, instance_token=instance_token))


def serve(
    config_path: str | Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    instance_token: str | None = None,
) -> None:
    config = load_web_config(config_path)
    selected_host = host or ("0.0.0.0" if config.get("lan_enabled") else "127.0.0.1")
    selected_port = port or int(config["port"])
    server = create_server(config, selected_host, selected_port, instance_token=instance_token)
    LOG.info("InterAgentMail web interface listening on http://%s:%s", selected_host, server.server_port)
    if selected_host in {"0.0.0.0", "::"}:
        LOG.warning(LAN_WARNING)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Serve the authenticated InterAgentMail web companion.")
    result.add_argument("--config")
    result.add_argument("--host")
    result.add_argument("--port", type=int)
    result.add_argument("--instance-token", help=argparse.SUPPRESS)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(args.config, host=args.host, port=args.port, instance_token=args.instance_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
