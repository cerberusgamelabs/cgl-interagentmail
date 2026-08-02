from __future__ import annotations

import http.client
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import interagentmail as iam
from iam_service import IAMService
from iam_web import (
    LAN_WARNING,
    MAX_SESSIONS,
    WebApplication,
    WebError,
    configure_web,
    create_server,
    create_user_mailbox,
    load_user_profile,
    load_web_config,
    password_record,
    verify_password,
)


class WebFixture(unittest.TestCase):
    PASSWORD = "correct horse battery staple"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "iam-home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"
        self.agent_root = self.root / "SecurityReviewer"
        self.agent_root.mkdir()
        self.agent = IAMService(self.agent_root)
        create_user_mailbox("Human", "Adrian")
        self.config = configure_web("Human", self.PASSWORD)
        self.full_config = load_web_config()
        self.server = create_server(self.full_config, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.cookie: str | None = None
        self.csrf: str | None = None

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        csrf: bool = False,
        host: str | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict, dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        if csrf and self.csrf:
            headers["X-IAM-CSRF"] = self.csrf
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, json.loads(raw.decode("utf-8")), response_headers

    def login(self) -> None:
        status, payload, headers = self.request("POST", "/api/login", {"password": self.PASSWORD})
        self.assertEqual(200, status)
        self.cookie = headers["set-cookie"].split(";", 1)[0]
        self.csrf = payload["data"]["csrf"]


class UserMailboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = (iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG)
        iam.ROOT = self.root / "iam-home"
        iam.MAILBOXES = iam.ROOT / "mailboxes"
        iam.CHATS = iam.ROOT / "chats"
        iam.CONFIG = iam.ROOT / "config.json"

    def tearDown(self) -> None:
        iam.ROOT, iam.MAILBOXES, iam.CHATS, iam.CONFIG = self.old_paths
        self.temp.cleanup()

    def test_user_mailbox_is_idempotent_and_has_no_project_identity(self) -> None:
        first = create_user_mailbox("Human", "Adrian")
        second = create_user_mailbox("Human", "Adrian")

        self.assertEqual("created", first["status"])
        self.assertEqual("unchanged", second["status"])
        profile = iam.profile("Human")
        self.assertEqual("user", profile["kind"])
        self.assertNotIn("project_root", profile)
        self.assertIsNone(IAMService.for_mailbox("Human").whoami()["project_root"])

    def test_user_mailbox_cannot_claim_registered_project_address(self) -> None:
        iam.write_atomic(iam.CONFIG, {"projects": {"Human": {"project_root": str(self.root / "Human")}}})

        with self.assertRaises(WebError) as raised:
            create_user_mailbox("Human")

        self.assertEqual("IAM_ADDRESS_COLLISION", raised.exception.code)
        self.assertFalse(iam.mailbox("Human").exists())

    def test_user_mailbox_does_not_claim_a_legacy_generic_mailbox(self) -> None:
        iam.ensure_box("Human")

        with self.assertRaises(WebError) as raised:
            create_user_mailbox("Human")

        self.assertEqual("IAM_ADDRESS_COLLISION", raised.exception.code)
        self.assertNotEqual("user", iam.profile("Human").get("kind"))

    def test_user_loader_rejects_a_user_profile_with_project_ownership(self) -> None:
        iam.ensure_box("Human")
        iam.write_atomic(iam.mailbox("Human") / "profile.json", {
            "address": "Human",
            "display_name": "Adrian",
            "kind": "user",
            "project_root": str(self.root / "Human"),
        })

        with self.assertRaises(WebError) as raised:
            load_user_profile("Human")

        self.assertEqual("IAM_CONFIG_INVALID", raised.exception.code)

    def test_user_mailbox_rejects_a_profile_that_declares_another_address(self) -> None:
        iam.ensure_box("Human")
        iam.write_atomic(iam.mailbox("Human") / "profile.json", {
            "address": "AnotherMailbox",
            "display_name": "Adrian",
            "kind": "user",
        })

        with self.assertRaises(WebError) as raised:
            create_user_mailbox("Human")

        self.assertEqual("IAM_CONFIG_INVALID", raised.exception.code)

    def test_password_is_salted_hashed_and_minimum_length_is_enforced(self) -> None:
        first = password_record("this is a strong password")
        second = password_record("this is a strong password")

        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertTrue(verify_password("this is a strong password", first))
        self.assertFalse(verify_password("incorrect password", first))
        self.assertNotIn("this is a strong password", json.dumps(first))
        with self.assertRaises(WebError) as raised:
            password_record("too short")
        self.assertEqual("IAM_WEB_PASSWORD_WEAK", raised.exception.code)


class WebAuthenticationTests(WebFixture):
    def test_api_requires_authentication_and_rejects_wrong_password(self) -> None:
        status, payload, _headers = self.request("GET", "/api/session")
        self.assertEqual(401, status)
        self.assertEqual("IAM_WEB_AUTH_REQUIRED", payload["error"]["code"])

        status, payload, _headers = self.request("POST", "/api/login", {"password": "wrong password value"})
        self.assertEqual(401, status)
        self.assertEqual("IAM_WEB_LOGIN_FAILED", payload["error"]["code"])

    def test_login_is_throttled_after_repeated_failures(self) -> None:
        for _attempt in range(5):
            status, _payload, _headers = self.request(
                "POST", "/api/login", {"password": "wrong password value"}
            )
            self.assertEqual(401, status)

        status, payload, _headers = self.request(
            "POST", "/api/login", {"password": self.PASSWORD}
        )
        self.assertEqual(429, status)
        self.assertEqual("IAM_WEB_LOGIN_THROTTLED", payload["error"]["code"])

    def test_successful_login_evicts_the_oldest_session_at_the_bound(self) -> None:
        now = 1_000_000.0
        for index in range(MAX_SESSIONS):
            self.server.app.sessions[str(index)] = {
                "created": now + index,
                "last_seen": now + index,
                "csrf": "csrf",
                "client_ip": "127.0.0.1",
            }

        with (
            patch("iam_web.time.time", return_value=now + MAX_SESSIONS + 1),
            patch("iam_web.verify_password", return_value=True),
        ):
            self.server.app.authenticate(self.PASSWORD, "127.0.0.1")

        self.assertEqual(MAX_SESSIONS, len(self.server.app.sessions))
        self.assertNotIn("0", self.server.app.sessions)

    def test_login_sets_hardened_cookie_and_security_headers(self) -> None:
        status, payload, headers = self.request("POST", "/api/login", {"password": self.PASSWORD})

        self.assertEqual(200, status)
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertIn("SameSite=Strict", headers["set-cookie"])
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertEqual("same-origin", headers["cross-origin-resource-policy"])
        self.assertNotIn("Python", headers["server"])
        self.assertIn("csrf", payload["data"])

    def test_mutations_require_csrf_and_same_origin(self) -> None:
        self.login()
        status, payload, _headers = self.request(
            "POST",
            "/api/send",
            {"to": ["SecurityReviewer"], "subject": "Audit", "body": "Review this."},
        )
        self.assertEqual(403, status)
        self.assertEqual("IAM_WEB_CSRF_INVALID", payload["error"]["code"])

        status, payload, _headers = self.request(
            "POST",
            "/api/send",
            {"to": ["SecurityReviewer"], "subject": "Audit", "body": "Review this."},
            csrf=True,
            origin="http://malicious.example",
        )
        self.assertEqual(403, status)
        self.assertEqual("IAM_WEB_ORIGIN_INVALID", payload["error"]["code"])

    def test_health_identifies_the_serving_process(self) -> None:
        status, payload, _headers = self.request("GET", "/healthz")

        self.assertEqual(200, status)
        self.assertEqual("iam-web", payload["service"])
        self.assertEqual(self.server.app.instance_token, payload["instance"])

    def test_unrecognized_host_is_rejected(self) -> None:
        status, payload, _headers = self.request("GET", "/healthz", host="malicious.example")

        self.assertEqual(421, status)
        self.assertEqual("IAM_WEB_HOST_INVALID", payload["error"]["code"])


class WebMessagingTests(WebFixture):
    def test_message_listing_enforces_the_browser_response_limit(self) -> None:
        self.login()

        status, payload, _headers = self.request("GET", "/api/messages?folder=inbox&limit=201")

        self.assertEqual(400, status)
        self.assertEqual("IAM_WEB_REQUEST_INVALID", payload["error"]["code"])

    def test_send_rejects_unknown_mailbox_without_creating_it(self) -> None:
        self.login()

        status, payload, _headers = self.request(
            "POST",
            "/api/send",
            {"to": ["MadeUpReviewer"], "subject": "Audit", "body": "Review this project."},
            csrf=True,
        )

        self.assertEqual(400, status)
        self.assertEqual("IAM_WEB_RECIPIENT_NOT_FOUND", payload["error"]["code"])
        self.assertFalse(iam.mailbox("MadeUpReviewer").exists())

    def test_authenticated_user_can_send_receive_reply_read_and_archive(self) -> None:
        self.login()
        status, sent_payload, _headers = self.request(
            "POST",
            "/api/send",
            {"to": ["SecurityReviewer"], "subject": "Audit", "body": "Review this project."},
            csrf=True,
        )
        self.assertEqual(201, status)
        sent = sent_payload["data"]["message"]
        self.assertEqual("Human", sent["from"])
        self.assertEqual(sent["id"], self.agent.inbox()[0]["id"])

        reply = self.agent.reply(sent["id"], "No findings.")
        status, inbox_payload, _headers = self.request("GET", "/api/messages?folder=inbox")
        self.assertEqual(200, status)
        self.assertEqual(reply["id"], inbox_payload["data"]["messages"][0]["id"])

        status, read_payload, _headers = self.request(
            "POST",
            "/api/read",
            {"message_id": reply["id"]},
            csrf=True,
        )
        self.assertEqual(200, status)
        self.assertIsNotNone(read_payload["data"]["message"]["read_at"])

        status, _archive_payload, _headers = self.request(
            "POST",
            "/api/archive",
            {"message_id": reply["id"]},
            csrf=True,
        )
        self.assertEqual(200, status)
        self.assertEqual([], IAMService.for_mailbox("Human").inbox())
        self.assertEqual(reply["id"], IAMService.for_mailbox("Human").list_messages("archive")[0]["id"])

    def test_lan_mode_discloses_network_warning_after_login(self) -> None:
        app = WebApplication({**self.full_config, "lan_enabled": True}, host="0.0.0.0")
        self.assertTrue(app.lan_enabled)
        self.assertIn("WPA2/WPA3", LAN_WARNING)
        self.assertIn("corrupt projects", LAN_WARNING)


if __name__ == "__main__":
    unittest.main()
