"""Port changes must follow IAM ownership, never arbitrary listeners."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import interagentmail as iam
import iam_orchestrator as o
from iam_service import IAMError


@unittest.skipUnless(os.name == "nt", "Windows compatibility transport")
class EndpointDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = iam.ROOT, iam.CONFIG
        iam.ROOT = Path(self.temp.name)
        iam.CONFIG = iam.ROOT / "config.json"
        iam.write_atomic(iam.CONFIG, {"windows_hidden_console_port": 4500})

    def tearDown(self):
        iam.ROOT, iam.CONFIG = self.old
        self.temp.cleanup()

    def marker(self, url="ws://127.0.0.1:4520", pid=1234):
        iam.write_atomic(iam.ROOT / "run" / "app-server-console-mode.json",
                         {"pid": pid, "url": url})

    def test_live_owned_port_overrides_launch_preference(self):
        self.marker()
        with patch.object(o, "read_pid", return_value=1234), patch.object(o, "pid_alive", return_value=True):
            self.assertEqual(o.configured_endpoint(), "ws://127.0.0.1:4520")
            self.marker("ws://127.0.0.1:4599")
            self.assertEqual(o.configured_endpoint(), "ws://127.0.0.1:4599")
        self.assertEqual(iam.load(iam.CONFIG)["windows_hidden_console_port"], 4500)

    def test_dead_or_mismatched_record_does_not_redirect(self):
        self.marker()
        for pid, alive in ((1234, False), (5678, True), (None, True)):
            with self.subTest(pid=pid, alive=alive), patch.object(o, "read_pid", return_value=pid), patch.object(o, "pid_alive", return_value=alive):
                self.assertEqual(o.configured_endpoint(), "ws://127.0.0.1:4500")

    def test_invalid_live_endpoint_fails_closed(self):
        for url in ("ws://example.com:4500", "ws://127.0.0.1:99999", "ws://127.0.0.1:80", "ws://localhost:4500/path", "managed://", None):
            self.marker(url)
            with self.subTest(url=url), patch.object(o, "read_pid", return_value=1234), patch.object(o, "pid_alive", return_value=True):
                with self.assertRaises(IAMError):
                    o.configured_endpoint()

    def test_managed_mode_ignores_compatibility_marker(self):
        self.marker()
        iam.write_atomic(iam.CONFIG, {})
        self.assertEqual(o.configured_endpoint(), "managed://")

    def test_configured_client_resolves_each_new_connection(self):
        with patch.object(o, "configured_endpoint", side_effect=["ws://127.0.0.1:4520", "ws://127.0.0.1:4599"]), patch.object(o, "AppServerClient") as client:
            o.appserver_client("configured://")
            o.appserver_client("configured://")
        self.assertEqual([c.kwargs["url"] for c in client.call_args_list],
                         ["ws://127.0.0.1:4520", "ws://127.0.0.1:4599"])

    def test_explicit_client_endpoint_remains_pinned(self):
        with patch.object(o, "configured_endpoint") as resolve, patch.object(o, "AppServerClient") as client:
            o.appserver_client("ws://127.0.0.1:4555")
        resolve.assert_not_called()
        self.assertEqual(client.call_args.kwargs["url"], "ws://127.0.0.1:4555")

    def test_foreground_supervisor_defaults_to_dynamic_resolution(self):
        with patch.object(o, "cmd_daemon", return_value=0) as daemon:
            o.main(["serve"])
            self.assertEqual(daemon.call_args.args[0].url, "configured://")
            o.main(["serve", "--url", "ws://127.0.0.1:4555"])
            self.assertEqual(daemon.call_args.args[0].url, "ws://127.0.0.1:4555")

    def test_supervisor_launch_uses_dynamic_endpoint_only_for_configured_server(self):
        for url, expected in (("ws://127.0.0.1:4500", "configured://"), ("ws://127.0.0.1:4555", "ws://127.0.0.1:4555")):
            with self.subTest(url=url), patch.object(o, "read_pid", side_effect=[None, 42]), patch.object(o, "pid_alive", side_effect=[False, True]), patch.object(o, "spawn_background") as spawn, patch.object(o.time, "sleep"):
                o.ensure_daemon(url)
            command = spawn.call_args.args[0]
            self.assertEqual(command[command.index("--url") + 1], expected)


if __name__ == "__main__":
    unittest.main()
