import tempfile
import unittest
from pathlib import Path
from iam_desktop import DesktopStore
from iam_desktop_prompts import fields, response
from iam_service import IAMError


class NoticesTests(unittest.TestCase):
    def test_notice_is_deduplicated_scoped_and_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DesktopStore(Path(folder) / 'test.sqlite3')
            first = store.notify('One', 'Please review the blocker.', 'blocker')
            self.assertEqual(first, store.notify('One', 'Please review the blocker.', 'blocker'))
            self.assertNotEqual(first, store.notify('Two', 'Please review the blocker.', 'blocker'))
            with self.assertRaises(IAMError): store.notify('One', 'different', 'blocker')
            with self.assertRaises(IAMError): store.notify('One', 'x' * 4001, 'large')
            for i in range(19): store.notify('One', 'update', str(i))
            with self.assertRaises(IAMError): store.notify('One', 'update', 'over-limit')
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)

    def test_confirmation_requires_explicit_valid_values(self):
        params = {'mode': 'form', 'message': 'Allow?', 'requestedSchema': {'type': 'object',
            'properties': {'allow': {'type': 'boolean'}, 'scope': {'type': 'string', 'enum': ['once']}},
            'required': ['allow', 'scope']}}
        self.assertEqual(len(fields(params)), 2)
        self.assertEqual(response(params, 'accept', {'allow': True, 'scope': 'once'}),
                         {'action': 'accept', 'content': {'allow': True, 'scope': 'once'}})
        for content in ({}, {'allow': 'true', 'scope': 'once'}, {'allow': True, 'scope': 'always'}, {'allow': True, 'scope': 'once', 'extra': 1}):
            with self.assertRaises(IAMError): response(params, 'accept', content)
        self.assertEqual(response(params, 'decline', None), {'action': 'decline', 'content': None})

    def test_unknown_forms_are_never_approved(self):
        for schema in ({'type': 'object', 'oneOf': []}, {'type': 'object', 'properties': {'secret': {'type': 'string'}}},
                       {'type': 'object', 'properties': {'yes': {'type': 'boolean', 'const': True}}}):
            with self.assertRaises(IAMError): fields({'mode': 'form', 'message': 'Prompt', 'requestedSchema': schema})
        with self.assertRaises(IAMError): fields({'mode': 'url', 'message': 'Login'})

    def test_notify_tool_uses_bound_mailbox(self):
        from unittest.mock import patch, MagicMock
        import iam_mcp_server as mcp
        with patch.object(mcp, 'service', return_value=MagicMock(address='Bound')), patch('iam_desktop.DesktopStore') as store:
            mcp.iam_desktop_notify('Important', 'stable', 'Bound')
        store.return_value.notify.assert_called_once_with('Bound', 'Important', 'stable', images=[], files=[])


if __name__ == '__main__': unittest.main()
