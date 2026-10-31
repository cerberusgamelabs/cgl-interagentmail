import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock

import iam_desktop as desktop


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = desktop.DesktopStore(Path(self.temp.name) / 'test.db')
        with patch.object(desktop, 'DesktopStore', return_value=store):
            self.backend = desktop.DesktopBackend()

    async def test_transient_read_retries_and_snapshot_reads_config_once(self):
        config = {'projects': {'One': {}, 'Two': {}}}
        with patch.object(desktop.iam, 'load', side_effect=[PermissionError(), config]) as load, \
             patch.object(desktop.iam, 'display_name', side_effect=lambda m: m):
            result = await self.backend.dispatch('snapshot', {})
        self.assertEqual(load.call_count, 2)
        self.assertEqual(len(result['buddies']), 2)

    async def test_persistent_failure_is_bounded_and_next_request_recovers(self):
        with patch.object(desktop.iam, 'load', side_effect=PermissionError()) as load:
            with self.assertRaises(PermissionError):
                await self.backend.dispatch('snapshot', {})
        self.assertEqual(load.call_count, 3)
        with patch.object(desktop.iam, 'load', return_value={'projects': {}}):
            self.assertEqual((await self.backend.dispatch('snapshot', {}))['buddies'], [])

    async def test_bad_config_does_not_become_empty_success(self):
        with patch.object(desktop.iam, 'load', return_value={'projects': []}):
            with self.assertRaises(desktop.IAMError):
                await self.backend.dispatch('snapshot', {})

    async def test_cli_exit_error_is_contained_without_leaking_details(self):
        error = desktop.IAMCommandError('IAM_CONFIG_INVALID', 'private path')
        with patch.object(self.backend, 'dispatch', side_effect=[error, {'ok': True}]), \
             patch.object(desktop, 'record_failure'):
            failed = await self.backend.handle_message({'id': 1, 'action': 'open'})
            recovered = await self.backend.handle_message({'id': 2, 'action': 'snapshot'})
        self.assertIn('error', failed)
        self.assertNotIn('private path', str(failed))
        self.assertEqual(recovered['result'], {'ok': True})

    async def test_pump_survives_owner_read_failure(self):
        self.backend.store.create('One', 'test', str(uuid.uuid4()))
        attempted = asyncio.Event()
        async def delivered(row):
            attempted.set()
        real_sleep = asyncio.sleep
        async def short_sleep(delay):
            await real_sleep(0.01)
        with patch.object(desktop, 'session_owner', side_effect=[PermissionError(), 'One', 'One']), \
             patch.object(self.backend, 'deliver_once', side_effect=delivered), \
             patch.object(desktop, 'record_failure'), \
             patch.object(desktop.asyncio, 'sleep', side_effect=short_sleep):
            task = asyncio.create_task(self.backend.pump())
            try:
                await asyncio.wait_for(attempted.wait(), 1)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
