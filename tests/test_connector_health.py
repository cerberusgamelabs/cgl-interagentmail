import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import iam_webconnect as w

CONFIG = {'server_url': 'wss://example.invalid', 'node_id': 'test-node'}


class HealthTests(unittest.TestCase):
    def test_record_is_body_free_and_scoped_to_process_and_config(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(w, 'health_path', return_value=Path(folder) / 'health.json'), \
             patch.object(w.os, 'getpid', return_value=2), \
             patch.object(w.os, 'getppid', return_value=1), \
             patch.object(w.time, 'time', return_value=100):
            w.write_health(dict(CONFIG, node_token='SECRET'), 'ready')
            self.assertEqual('ready', w.connector_health(CONFIG, 1, True))
            self.assertEqual('ready', w.connector_health(CONFIG, 2, True))
            self.assertEqual('unverified', w.connector_health(CONFIG, 3, True))
            self.assertEqual('unverified', w.connector_health(dict(CONFIG, node_id='other'), 1, True))
            self.assertEqual('stopped', w.connector_health(CONFIG, 1, False))
            self.assertEqual('stale', w.connector_health(CONFIG, 1, True, now=146))
            self.assertEqual('stale', w.connector_health(CONFIG, 1, True, now=99))
            record = w.health_path().read_text()
            self.assertNotIn('SECRET', record)
            self.assertNotIn('wss:', record)
            w.write_health(CONFIG, 'disconnected')
            self.assertEqual('disconnected', w.connector_health(CONFIG, 1, True))

    def test_missing_or_malformed_record_is_not_healthy(self):
        with patch.object(w.iam, 'load', side_effect=OSError):
            self.assertEqual('unverified', w.connector_health(CONFIG, 1, True))
        for value in (None, [], {}, {'version': 1, 'pid': 1, 'identity': w.health_identity(CONFIG), 'checked_at': float('nan')}):
            with patch.object(w.iam, 'load', return_value=value):
                self.assertNotEqual('ready', w.connector_health(CONFIG, 1, True))


class RelayProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_pong_publishes_ready(self):
        pong = asyncio.get_running_loop().create_future()
        socket = AsyncMock()
        socket.ping.return_value = pong
        with patch.object(w, 'write_health') as write:
            task = asyncio.create_task(w.verify_relay(socket, CONFIG))
            await asyncio.sleep(0)
            write.assert_not_called()
            pong.set_result(None)
            await task
            write.assert_called_once_with(CONFIG, 'ready')

    async def test_ping_failure_never_publishes_ready(self):
        socket = AsyncMock()
        socket.ping.side_effect = OSError('unavailable')
        with patch.object(w, 'write_health') as write:
            with self.assertRaises(OSError):
                await w.verify_relay(socket, CONFIG)
            write.assert_not_called()

    async def test_fatal_startup_failure_is_recorded_without_retry(self):
        client = object.__new__(w.WebConnectClient)
        client.config = dict(CONFIG, enabled=True)
        client.run_once = AsyncMock(side_effect=ImportError('missing'))
        with patch.object(w, 'write_health') as write:
            with self.assertRaises(ImportError):
                await client.run_forever()
            self.assertEqual(['connecting', 'failed'], [c.args[1] for c in write.call_args_list])
        client.run_once.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
