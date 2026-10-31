import argparse
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import iam_orchestrator as o


class ConnectorPreflightTests(unittest.TestCase):
    def test_import_probe_is_isolated_and_bounded(self):
        with patch.object(o.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            o.check_connector_installation()
        command = run.call_args.args[0]
        self.assertEqual(command[:4], [o.sys.executable, '-I', '-B', '-c'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_import_failure_and_timeout_are_safe_errors(self):
        for outcome in [subprocess.CompletedProcess([], 1, '', 'sensitive detail'),
                        subprocess.TimeoutExpired('probe', 30)]:
            with self.subTest(outcome=type(outcome).__name__):
                kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
                with patch.object(o.subprocess, 'run', **kwargs):
                    with self.assertRaises(o.IAMCommandError) as raised:
                        o.check_connector_installation()
                self.assertNotIn('sensitive detail', str(raised.exception))

    def test_failed_probe_never_enables_or_spawns(self):
        config = {'enabled': False, 'server_url': 'wss://example.invalid'}
        with patch.object(o, 'load_webconnect_config', return_value=config), \
             patch.object(o, 'read_pid', return_value=None), \
             patch.object(o, 'pid_alive', return_value=False), \
             patch.object(o, 'check_connector_installation', side_effect=o.IAMCommandError('FAILED', 'failed')), \
             patch.object(o, 'save_webconnect_config') as save, \
             patch.object(o, 'spawn_background') as spawn:
            with self.assertRaises(o.IAMCommandError):
                o.cmd_connect_start(argparse.Namespace())
        save.assert_not_called()
        spawn.assert_not_called()
        self.assertFalse(config['enabled'])

    def test_launch_is_not_reported_as_registered(self):
        config = {'enabled': True, 'server_url': 'wss://example.invalid'}
        out = io.StringIO()
        with patch.object(o, 'load_webconnect_config', return_value=config), \
             patch.object(o, 'read_pid', return_value=None), \
             patch.object(o, 'pid_alive', return_value=False), \
             patch.object(o, 'check_connector_installation') as probe, \
             patch.object(o, 'spawn_background', return_value=123) as spawn, redirect_stdout(out):
            o.cmd_connect_start(argparse.Namespace())
        probe.assert_called_once()
        spawn.assert_called_once()
        self.assertIn('registration is not yet verified', out.getvalue())


if __name__ == '__main__':
    unittest.main()
