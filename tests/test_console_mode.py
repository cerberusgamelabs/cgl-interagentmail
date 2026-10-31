"""Compatibility mode must be opt-in, loopback-only, and non-disruptive."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

import interagentmail as iam
import iam_orchestrator as orchestration
from iam_service import IAMError


class ConsoleModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = iam.ROOT, iam.CONFIG
        self.daemon_check = patch.object(orchestration, "native_daemon_running", return_value=False)
        self.daemon_check.start()
        self.port_check = patch.object(orchestration, "compatibility_port_available", return_value=True)
        self.port_check.start()
        iam.ROOT = Path(self.temp.name)
        iam.CONFIG = iam.ROOT / "config.json"

    def tearDown(self):
        self.daemon_check.stop()
        self.port_check.stop()
        iam.ROOT, iam.CONFIG = self.old
        self.temp.cleanup()

    def test_default_unchanged(self):
        self.assertEqual("managed://", orchestration.configured_endpoint())

    def test_mode_change_refuses_running_service(self):
        with patch.object(orchestration, "pid_alive", return_value=True):
            with self.assertRaises(IAMError):
                orchestration.cmd_app_server_mode(argparse.Namespace(mode="hidden-console", port=4500))
        self.assertFalse(iam.CONFIG.exists())

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_mode_persists_and_reverts_without_touching_projects(self):
        original = {"projects": {"Example": {"sandbox": "read-only"}}}
        iam.write_atomic(iam.CONFIG, original)
        with patch.object(orchestration, "pid_alive", return_value=False), redirect_stdout(io.StringIO()):
            orchestration.cmd_app_server_mode(argparse.Namespace(mode="hidden-console", port=4521))
            self.assertEqual("ws://127.0.0.1:4521", orchestration.configured_endpoint())
            orchestration.cmd_app_server_mode(argparse.Namespace(mode="managed", port=4500))
        self.assertEqual(original, iam.load(iam.CONFIG))

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_cli_uses_saved_endpoint_but_explicit_url_wins(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4521})
        with patch.object(orchestration, "cmd_status", return_value=0) as status:
            orchestration.main(["status"])
            self.assertEqual("ws://127.0.0.1:4521", status.call_args.args[0].url)
            orchestration.main(["status", "--url", "managed://"])
            self.assertEqual("managed://", status.call_args.args[0].url)

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_new_console_is_hidden_and_not_no_window(self):
        with patch.object(orchestration.subprocess, "Popen") as popen:
            popen.return_value.pid = 9876
            orchestration.spawn_background(["codex", "app-server"], "probe", hidden_console=True)
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE)
        self.assertFalse(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(subprocess.SW_HIDE, kwargs["startupinfo"].wShowWindow)

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_refuses_unverified_existing_listener(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        with patch.object(orchestration, "appserver_available", new=AsyncMock(return_value=True)), \
             patch.object(orchestration, "spawn_background") as spawn:
            with self.assertRaisesRegex(IAMError, "unverified"):
                orchestration.ensure_appserver("ws://127.0.0.1:4500")
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_refuses_parallel_managed_daemon(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        with patch.object(orchestration, "native_daemon_running", return_value=True), \
             patch.object(orchestration, "appserver_available", new=AsyncMock()) as available, \
             patch.object(orchestration, "spawn_background") as spawn:
            with self.assertRaisesRegex(IAMError, "managed daemon is still running"):
                orchestration.ensure_appserver("ws://127.0.0.1:4500")
        spawn.assert_not_called()
        available.assert_not_awaited()

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_launch_uses_hidden_console_after_native_daemon_stops(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        with patch.object(orchestration, "appserver_available", new=AsyncMock(side_effect=[False, True])) as available, \
             patch.object(orchestration, "spawn_background", return_value=9876) as spawn, \
             patch.object(orchestration, "pid_alive", side_effect=lambda pid: pid == 9876), \
             patch.object(orchestration, "codex_executable", return_value="codex"), \
             patch.object(orchestration.time, "sleep"):
            orchestration.ensure_appserver("ws://127.0.0.1:4500")
        self.assertTrue(spawn.call_args.kwargs["hidden_console"])
        self.assertFalse(spawn.call_args.kwargs["record_pid"])
        self.assertEqual(9876, orchestration.read_pid("app-server"))
        self.assertEqual(["codex", "app-server", "--listen", "ws://127.0.0.1:4500", "--disable", "daemon_auto_start"], spawn.call_args.args[0])
        self.assertTrue(all(call.args[0] == "ws://127.0.0.1:4500" for call in available.await_args_list))

    def test_hidden_mode_rejects_native_proxy_without_spawning(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        with patch.object(orchestration, "AppServerClient") as client:
            with self.assertRaisesRegex(IAMError, "must not connect"):
                orchestration.appserver_client("managed://")
        client.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_busy_startup_does_not_probe_or_spawn(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        with orchestration.session_guard("app-server", "startup"), \
             patch.object(orchestration, "appserver_available", new=AsyncMock()) as available, \
             patch.object(orchestration, "spawn_background") as spawn:
            with self.assertRaisesRegex(IAMError, "startup is in progress"):
                orchestration.ensure_appserver("ws://127.0.0.1:4500")
        available.assert_not_called()
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_failed_launch_preserves_previous_ownership(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        marker = iam.ROOT / "run" / "app-server-console-mode.json"
        old = {"pid": 1234, "url": "ws://127.0.0.1:4500"}
        iam.write_atomic(marker, old)
        orchestration.pid_path("app-server").write_text("1234\n")
        with patch.object(orchestration, "appserver_available", new=AsyncMock(return_value=False)), \
             patch.object(orchestration, "spawn_background", return_value=9876), \
             patch.object(orchestration, "codex_executable", return_value="codex"), \
             patch.object(orchestration, "pid_alive", return_value=False), \
             patch.object(orchestration.time, "sleep"):
            with self.assertRaises(SystemExit):
                orchestration.ensure_appserver(old["url"])
        self.assertEqual(old, iam.load(marker))
        self.assertEqual(1234, orchestration.read_pid("app-server"))

    @unittest.skipUnless(os.name == "nt", "Windows compatibility mode")
    def test_unresponsive_live_server_or_occupied_port_never_spawns(self):
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})
        for live in (True, False):
            with self.subTest(recorded_process_alive=live), \
                 patch.object(orchestration, "appserver_available", new=AsyncMock(return_value=False)), \
                 patch.object(orchestration, "pid_alive", return_value=live), \
                 patch.object(orchestration, "compatibility_port_available", return_value=False), \
                 patch.object(orchestration, "spawn_background") as spawn:
                with self.assertRaisesRegex(IAMError, "preserved"):
                    orchestration.ensure_appserver("ws://127.0.0.1:4500")
                spawn.assert_not_called()

    def test_invalid_saved_port_fails_closed(self):
        for value in (True, "4500", 80, 65536):
            iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": value})
            with self.assertRaises(IAMError):
                orchestration.configured_endpoint()
