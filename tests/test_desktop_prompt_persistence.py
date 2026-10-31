import unittest
from iam_desktop_prompts import response, persistence_options
from iam_service import IAMError


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.params = {'mode': 'form', 'message': 'Allow tool?',
                       'requestedSchema': {'type': 'object', 'properties': {}},
                       '_meta': {'codex_approval_kind': 'mcp_tool_call', 'persist': ['session', 'always']}}

    def test_explicit_choices(self):
        for choice in ('session', 'always'):
            self.assertEqual(response(self.params, 'accept', {}, choice),
                             {'action': 'accept', 'content': {}, '_meta': {'persist': choice}})

    def test_default_once(self):
        self.assertNotIn('_meta', response(self.params, 'accept', {}))

    def test_decline_cannot_remember(self):
        with self.assertRaises(IAMError):
            response(self.params, 'decline', None, 'always')

    def test_unoffered_and_generic_form_rejected(self):
        self.params['_meta']['persist'] = 'session'
        self.assertEqual(persistence_options(self.params), ['session'])
        with self.assertRaises(IAMError):
            response(self.params, 'accept', {}, 'always')
        self.params['_meta']['codex_approval_kind'] = 'something_else'
        with self.assertRaises(IAMError):
            response(self.params, 'accept', {}, 'session')
