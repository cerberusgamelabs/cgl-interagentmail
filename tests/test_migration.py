import asyncio
from contextlib import closing, nullcontext
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

import iam_migration as migration
from iam_service import IAMError

THREAD = "01a1987c-781f-7662-9e0b-906711c125b2"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "iam"
        self.project = self.base / "workspace" / "Maeve"
        self.project.mkdir(parents=True)
        (self.project / "README.md").write_text("work")
        (self.project / ".env").write_text("never-package")
        (self.project / ".codex").mkdir()
        (self.project / ".codex" / "config.toml").write_text('[mcp_servers.cdiscord]\ncommand="old-node"\n')
        box = self.root / "mailboxes" / "Maeve"
        box.mkdir(parents=True)
        self.box = box
        self.config = {"projects": {"Maeve": {"project_root": str(self.project), "sandbox": "danger-full-access", "approval_policy": "never"}}, "unrelated": "keep"}
        (self.root / "config.json").write_text(json.dumps(self.config))
        (box / "profile.json").write_text(json.dumps({"address": "Maeve", "project_root": str(self.project)}))
        (box / ".codex-bridge-state.json").write_text(json.dumps({"thread_id": THREAD, "delivered": ["old"]}))
        (box / "inbox").mkdir()
        (box / "inbox" / "message.json").write_text('{"body":"private mail"}')
        (box / "timers").mkdir()
        (box / "timers" / "timer.json").write_text("{}")
        self.home = self.base / "codex"
        sessions = self.home / "sessions" / "2026" / "11" / "01"
        sessions.mkdir(parents=True)
        self.rollout = sessions / f"rollout-2026-11-01T14-00-56-{THREAD}.jsonl"
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": THREAD, "cwd": "old"}}) + "\n")
        (self.root / "run").mkdir()
        with closing(sqlite3.connect(self.root / "run" / "desktop.sqlite3")) as db, db:
            db.execute("CREATE TABLE profiles(mailbox TEXT PRIMARY KEY, data TEXT)")
            db.execute("INSERT INTO profiles VALUES(?,?)", ("Maeve", json.dumps({"avatar": "embedded-picture", "bio": "bio"})))
            db.execute("INSERT INTO profiles VALUES(?,?)", ("Other", '{"bio":"not selected"}'))
        self.bundle = self.base / "transfer.zip"

    def export(self, **kwargs):
        return migration.export_bundle(self.root, ["Maeve"], self.bundle, codex_home=self.home, **kwargs)

    def test_round_trip_stages_only_and_preserves_source(self):
        before = (self.root / "config.json").read_bytes()
        result = self.export(workspaces=True, sessions=True)
        self.assertEqual(result["mailboxes"], ["Maeve"])
        dest = self.base / "destination" / "Maeve"
        stage = self.base / "staging"
        report = migration.stage_bundle(self.bundle, stage, {"Maeve": str(dest)})
        self.assertEqual(report["state"], "staged-not-activated")
        self.assertFalse(dest.exists())
        self.assertEqual(before, (self.root / "config.json").read_bytes())
        self.assertEqual((stage / "payload" / "projects" / "Maeve" / "README.md").read_text(), "work")
        self.assertFalse((stage / "payload" / "projects" / "Maeve" / ".env").exists())
        self.assertFalse((stage / "payload" / "projects" / "Maeve" / ".codex" / "config.toml").exists())
        self.assertFalse((stage / "payload" / "mailboxes" / "Maeve" / "timers").exists())
        self.assertEqual(json.loads((stage / "payload" / "messenger" / "Maeve.json").read_text())["avatar"], "embedded-picture")
        self.assertFalse((stage / "payload" / "messenger" / "Other.json").exists())
        self.assertIn(str(dest).replace("\\", "\\\\"), (stage / "destination-drafts" / "Maeve" / "iam-mcp.toml").read_text())
        self.assertEqual(json.loads((stage / "destination-drafts" / "Maeve" / "profile.json").read_text())["project_root"], str(dest))

    def test_existing_output_refused(self):
        self.bundle.write_text("keep")
        with self.assertRaises(IAMError): self.export()
        self.assertEqual(self.bundle.read_text(), "keep")

    def test_profile_mismatch_refused(self):
        (self.box / "profile.json").write_text('{"address":"Maeve","project_root":"wrong"}')
        with self.assertRaises(IAMError): self.export()
        self.assertFalse(self.bundle.exists())

    def test_missing_rollout_refused(self):
        self.rollout.unlink()
        with self.assertRaises(IAMError): self.export(sessions=True)

    def test_wrong_rollout_identity_refused(self):
        self.rollout.write_text('{"type":"session_meta","payload":{"id":"wrong"}}')
        with self.assertRaises(IAMError): self.export(sessions=True)

    def test_shared_session_refused(self):
        self.config["session_mailboxes"] = {"Alias": "Maeve"}
        (self.root / "config.json").write_text(json.dumps(self.config))
        with self.assertRaises(IAMError): self.export()

    def test_mapping_required_and_existing_stage_refused(self):
        self.export()
        stage = self.base / "staging"
        with self.assertRaises(IAMError): migration.stage_bundle(self.bundle, stage, {})
        self.assertFalse(stage.exists())
        stage.mkdir()
        with self.assertRaises(IAMError): migration.stage_bundle(self.bundle, stage, {"Maeve": str(self.project)})

    def test_archive_traversal_rejected_before_extraction(self):
        for member in ("../escape", "C:/escape", "a\\b", "CON", "a/../b", "a:stream", "a./b"):
            with self.subTest(member=member), self.assertRaises(IAMError): migration.safe_member(member)

    def test_unlisted_member_rejected(self):
        self.export()
        with zipfile.ZipFile(self.bundle, "a") as archive: archive.writestr("extra.txt", "bad")
        with self.assertRaises(IAMError): migration.stage_bundle(self.bundle, self.base / "staging", {"Maeve": str(self.project)})
        self.assertFalse((self.base / "staging").exists())

    def test_case_collision_rejected(self):
        self.export()
        with zipfile.ZipFile(self.bundle, "a") as archive: archive.writestr("MANIFEST.JSON", "{}")
        with self.assertRaises(IAMError): migration.stage_bundle(self.bundle, self.base / "staging", {"Maeve": str(self.project)})

    def test_size_limit_rejected(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as archive:
            with self.assertRaises(IAMError): migration.verified_manifest(archive, 1)

    def test_hash_tampering_rejected(self):
        self.export()
        altered = self.base / "altered.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(altered, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name.endswith("message.json"): data = b"x" * len(data)
                target.writestr(name, data)
        with self.assertRaises(IAMError): migration.stage_bundle(altered, self.base / "staging", {"Maeve": str(self.project)})

    def test_parser_and_explicit_source_ack(self):
        from iam_orchestrator import parser
        args = parser().parse_args(["migration", "export", "--mailbox", "Maeve", "--output", "out.zip"])
        with self.assertRaises(IAMError): migration.command(args)

    def test_mapping_validation(self):
        with self.assertRaises(IAMError): migration.parse_pairs(["Maeve=relative"])
        with self.assertRaises(IAMError): migration.parse_pairs([f"Maeve={self.project}"] * 2)

    def test_resource_excludes_databases_builds_and_credentials(self):
        resource = self.base / "tool"
        resource.mkdir()
        for name in ("package.json", "data.sqlite3", "credentials.json", ".env.local", "private.key"):
            (resource / name).write_text("fixture")
        (resource / "dist").mkdir()
        (resource / "dist" / "main.js").write_text("build")
        self.export(resources={"tool": resource})
        with zipfile.ZipFile(self.bundle) as archive:
            members = [n for n in archive.namelist() if n.startswith("resources/")]
        self.assertEqual(members, ["resources/tool/package.json"])

    def test_missing_binding_is_explicit(self):
        (self.box / ".codex-bridge-state.json").unlink()
        with self.assertRaisesRegex(IAMError, "Missing pinned-session state"): self.export()

    def test_staged_tampering_detected(self):
        self.export()
        stage = self.base / "staging"
        migration.stage_bundle(self.bundle, stage, {"Maeve": str(self.project)})
        (stage / "payload" / "mailboxes" / "Maeve" / "profile.json").write_text("{}")
        with self.assertRaisesRegex(IAMError, "payload changed"): migration.check_stage(stage)

    def test_export_refuses_its_own_source_directory(self):
        self.bundle = self.project / "export.zip"
        with self.assertRaises(IAMError): self.export(workspaces=True)
        self.assertFalse(self.bundle.exists())

    def test_relocation_rejects_live_destination_without_connecting(self):
        with patch.object(migration.iam, "CONFIG", self.root / "config.json"), patch.object(migration.iam, "mailbox", return_value=self.box), \
             patch("iam_orchestrator.pid_alive", return_value=True), patch("iam_orchestrator.appserver_client") as client:
            with self.assertRaisesRegex(IAMError, "must be stopped"):
                asyncio.run(migration.relocate("Maeve", THREAD, self.base / "old", self.project))
            client.assert_not_called()

    def test_relocation_rejects_wrong_pin(self):
        with patch.object(migration.iam, "CONFIG", self.root / "config.json"), patch.object(migration.iam, "mailbox", return_value=self.box), \
             patch("iam_orchestrator.appserver_client") as client:
            with self.assertRaisesRegex(IAMError, "Pinned thread"):
                asyncio.run(migration.relocate("Maeve", "wrong", self.base / "old", self.project))
            client.assert_not_called()

    def test_relocation_busy_and_exact_thread(self):
        client = AsyncMock()
        client.request.return_value = {"thread": {"id": THREAD, "cwd": str(self.base / "old"), "status": {"type": "active"}}}
        with patch.object(migration.iam, "CONFIG", self.root / "config.json"), patch.object(migration.iam, "mailbox", return_value=self.box), \
             patch("iam_orchestrator.pid_alive", return_value=False), patch("iam_orchestrator.configured_endpoint", return_value="managed://"), \
             patch("iam_orchestrator.session_guard", return_value=nullcontext(True)), patch("iam_orchestrator.appserver_client", return_value=client), \
             patch("iam_orchestrator.switch_session_workspace", new_callable=AsyncMock) as switch:
            with self.assertRaises(IAMError): asyncio.run(migration.relocate("Maeve", THREAD, self.base / "old", self.project))
            switch.assert_not_awaited()
            client.close.assert_awaited_once()

    def test_relocation_success_verifies_readback(self):
        client = AsyncMock()
        client.request.side_effect = [
            {"thread": {"id": THREAD, "cwd": str(self.base / "old"), "status": {"type": "idle"}}},
            {"thread": {"id": THREAD, "cwd": str(self.project), "status": {"type": "idle"}}}]
        with patch.object(migration.iam, "CONFIG", self.root / "config.json"), patch.object(migration.iam, "mailbox", return_value=self.box), \
             patch("iam_orchestrator.pid_alive", return_value=False), patch("iam_orchestrator.configured_endpoint", return_value="managed://"), \
             patch("iam_orchestrator.session_guard", return_value=nullcontext(True)), patch("iam_orchestrator.appserver_client", return_value=client), \
             patch("iam_orchestrator.switch_session_workspace", new_callable=AsyncMock) as switch:
            result = asyncio.run(migration.relocate("Maeve", THREAD, self.base / "old", self.project))
            self.assertEqual(result["state"], "workspace-verified")
            switch.assert_awaited_once()
            self.assertTrue(all(c.args[0] == "thread/read" for c in client.request.await_args_list))


if __name__ == "__main__":
    unittest.main()
