import unittest
from unittest.mock import AsyncMock

from iam_codex_bridge import AppServerClient, AppServerError, AppServerRpcError


class PermissionHandoffTests(unittest.IsolatedAsyncioTestCase):
    def client(self, mode="danger-full-access", allowed=True):
        client = AppServerClient(url="ws://127.0.0.1:4500")
        profile, sandbox = {
            "danger-full-access": (":danger-full-access", "dangerFullAccess"),
            "workspace-write": (":workspace", "workspaceWrite"),
            "read-only": (":read-only", "readOnly"),
        }[mode]
        response = {"thread": {"id": "same-thread"}, "approvalPolicy": "never",
                    "sandbox": {"type": sandbox}, "activePermissionProfile": {"id": profile}}
        client._request_raw = AsyncMock(side_effect=[
            {"data": [{"id": profile, "allowed": allowed}]}, response])
        params = {"threadId": "same-thread", "cwd": "E:/Example",
                  "sandbox": mode, "approvalPolicy": "never"}
        return client, params, response

    async def test_all_registered_modes_select_their_own_profile(self):
        for mode in ("danger-full-access", "workspace-write", "read-only"):
            with self.subTest(mode=mode):
                client, params, response = self.client(mode)
                self.assertEqual(response, await client.request("thread/resume", params))
                sent = client._request_raw.call_args.args[1]
                self.assertNotIn("sandbox", sent)
                self.assertEqual(response["activePermissionProfile"]["id"], sent["permissions"])
                self.assertEqual("same-thread", sent["threadId"])
                self.assertIn("sandbox", params)  # Caller data not mutated.

    async def test_denied_profile_never_resumes_or_falls_back(self):
        client, params, _ = self.client(allowed=False)
        with self.assertRaisesRegex(AppServerError, "denied"):
            await client.request("thread/resume", params)
        self.assertEqual(1, client._request_raw.await_count)

    async def test_wrong_effective_sandbox_or_approval_or_profile_fails(self):
        for field, value in (("sandbox", {"type": "workspaceWrite"}),
                             ("approvalPolicy", "on-request"),
                             ("activePermissionProfile", None)):
            client, params, response = self.client()
            response[field] = value
            with self.assertRaisesRegex(AppServerError, "unverifiable"):
                await client.request("thread/resume", params)

    async def test_timeout_does_not_fallback(self):
        client, params, _ = self.client()
        client._request_raw.side_effect = AppServerError("Timed out")
        with self.assertRaisesRegex(AppServerError, "Timed out"):
            await client.request("thread/resume", params)
        self.assertEqual(1, client._request_raw.await_count)

    async def test_diagnostics_identify_only_failed_fields_without_returned_values(self):
        fields = {"approvalPolicy": "approvalPolicy",
                  "sandbox": "sandbox.type",
                  "activePermissionProfile": "activePermissionProfile.id"}
        for field, label in fields.items():
            with self.subTest(field=field):
                client, params, response = self.client()
                response[field] = "PRIVATE-SERVER-VALUE"
                with self.assertRaises(AppServerError) as raised:
                    await client.request("thread/resume", params)
                message = str(raised.exception)
                self.assertIn(label + " missing or mismatched", message)
                self.assertNotIn("PRIVATE-SERVER-VALUE", message)
                self.assertIn("not a rollback", message)
                for other in fields.values():
                    if other != label:
                        self.assertNotIn(other, message)
                self.assertEqual(2, client._request_raw.await_count)

    async def test_malformed_policy_result_fails_closed_without_retry(self):
        client, params, _ = self.client()
        client._request_raw.side_effect = [
            {"data": [{"id": ":danger-full-access", "allowed": True}]}, None]
        with self.assertRaisesRegex(AppServerError, "sandbox.type missing or mismatched"):
            await client.request("thread/resume", params)
        self.assertEqual(2, client._request_raw.await_count)

    async def test_legacy_only_on_method_not_found(self):
        client, params, response = self.client()
        response.pop("activePermissionProfile")
        client._request_raw.side_effect = [AppServerRpcError("unsupported", -32601, 1), response]
        await client.request("thread/resume", params)
        self.assertEqual(params, client._request_raw.call_args.args[1])

    async def test_full_access_delivery_selects_profile_without_extra_turn(self):
        client, _, _ = self.client()
        client._profile_support = True
        client._request_raw.side_effect = [{"turn": {"id": "accepted"}}]
        await client.request("turn/start", {"threadId": "same-thread", "input": [],
                             "approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}})
        self.assertEqual(["turn/start"],
                         [c.args[0] for c in client._request_raw.call_args_list])
        sent = client._request_raw.call_args.args[1]
        self.assertNotIn("sandboxPolicy", sent)
        self.assertEqual(":danger-full-access", sent["permissions"])

    async def test_unnegotiated_turn_adds_no_discovery_inside_submission_deadline(self):
        client, _, _ = self.client()
        client._request_raw.side_effect = [{"turn": {"id": "accepted"}}]
        params = {"sandboxPolicy": {"type": "dangerFullAccess"}}
        await client.request("turn/start", params)
        client._request_raw.assert_awaited_once_with("turn/start", params)

    async def test_custom_turn_policy_is_not_broadened(self):
        client, _, _ = self.client()
        client._request_raw.side_effect = [{"turn": {"id": "ok"}}]
        params = {"sandboxPolicy": {"type": "workspaceWrite", "writableRoots": ["E:/One"]}}
        await client.request("turn/start", params)
        client._request_raw.assert_awaited_once_with("turn/start", params)

    async def test_rejection_after_profile_selection_is_not_retried(self):
        client, params, _ = self.client()
        client._request_raw.side_effect = [
            {"data": [{"id": ":danger-full-access", "allowed": True}]},
            AppServerRpcError("denied", -32602, 2)]
        with self.assertRaises(AppServerRpcError):
            await client.request("thread/resume", params)
        self.assertEqual(2, client._request_raw.await_count)


if __name__ == "__main__":
    unittest.main()
