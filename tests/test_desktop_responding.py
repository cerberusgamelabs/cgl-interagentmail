import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock
import iam_desktop as desktop


class RespondingTests(unittest.IsolatedAsyncioTestCase):
    async def test_indicator_only_for_unanswered_desktop_request_in_exact_live_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            store = desktop.DesktopStore(Path(folder) / 'test.sqlite3')
            with patch.object(desktop, 'DesktopStore', return_value=store):
                backend = desktop.DesktopBackend()
            client = MagicMock(closed=asyncio.Event())
            backend.clients['One'] = (client, 'thread')
            backend.statuses['One'] = 'busy'
            backend.controls.turns['One'] = 'turn'
            with patch.object(backend, 'roster_config', return_value={'projects': {'One': {}}}), patch.object(desktop, 'session_owner', return_value='One'), patch.object(desktop.iam, 'display_name', return_value='Agent'):
                async def showing():
                    return (await backend.dispatch('snapshot', {}))['buddies'][0]['responding']
                self.assertFalse(await showing())  # private/unrelated active work
                rid = str(uuid.uuid4())
                store.create('One', 'Hello', rid)
                self.assertFalse(await showing())  # queued
                store.change(rid, 'accepted', thread_id='thread', turn_id='turn')
                self.assertTrue(await showing())
                backend.controls.turns['One'] = 'other'
                self.assertFalse(await showing())
                backend.controls.turns['One'] = 'turn'
                client.closed.set()
                self.assertFalse(await showing())
                client.closed.clear()
                backend.statuses['One'] = 'available'
                self.assertFalse(await showing())
                backend.statuses['One'] = 'busy'
                store.reply('One', rid, 'Hello back')
                self.assertFalse(await showing())
