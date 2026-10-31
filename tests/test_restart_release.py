import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import iam_orchestrator as o


class RestartReleaseTests(unittest.TestCase):
    def test_waits_until_port_is_released(self):
        with patch.object(o, 'compatibility_port_available', side_effect=[False, False, True]), patch.object(o.time, 'sleep') as sleep:
            o.wait_compatibility_port_release('ws://127.0.0.1:4500')
        self.assertEqual(sleep.call_count, 2)

    def test_timeout_is_distinct_shutdown_error(self):
        with patch.object(o, 'compatibility_port_available', return_value=False), patch.object(o, 'read_pid', return_value=123):
            with self.assertRaisesRegex(o.IAMCommandError, 'shutdown is incomplete'):
                o.wait_compatibility_port_release('ws://127.0.0.1:4500', wait_seconds=0)

    def test_failed_shutdown_does_not_start_replacement(self):
        with patch.object(o, 'cmd_stop', side_effect=o.IAMCommandError('TEST', 'blocked')), patch.object(o, 'cmd_start') as start:
            with self.assertRaises(o.IAMCommandError):
                o.cmd_restart(argparse.Namespace())
        start.assert_not_called()

    def test_stop_preserves_record_until_endpoint_is_released(self):
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'app-server.pid'
            record.write_text('123\n')
            with (
                patch('iam_desktop.control', return_value=False),
                patch.object(o, 'read_pid', return_value=123),
                patch.object(o, 'pid_alive', return_value=False),
                patch.object(o, 'configured_endpoint', return_value='ws://127.0.0.1:4500'),
                patch.object(o, 'load_config', return_value={'windows_hidden_console_port': 4500}),
                patch.object(o, 'appserver_available', new=AsyncMock(return_value=False)),
                patch.object(o, 'pid_path', return_value=record),
                patch.object(o, 'stop_process') as stop,
                patch.object(o, 'wait_compatibility_port_release', side_effect=o.IAMCommandError('TEST', 'blocked')) as release,
            ):
                with self.assertRaises(o.IAMCommandError):
                    o.cmd_stop(argparse.Namespace(all=True))
                self.assertEqual(record.read_text(), '123\n')
                stop.assert_not_called()
                release.side_effect = None
                o.cmd_stop(argparse.Namespace(all=True))
                self.assertFalse(record.exists())
