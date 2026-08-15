"""Task 3.4 migration/reconciliation refusal and restart tests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.export_progress import ExportError, export_source, validate_inventory
from scripts.migrate_progress import MigrationError, build_plan, run_migration
from scripts.reconcile_progress import ReconciliationError, build_new_start_baseline, canonical_hash, union_documents


ROOT = Path(__file__).resolve().parent.parent
LOCAL_INVENTORY = json.loads((ROOT / ".state/progress-source-inventory.json").read_text(encoding="utf-8"))


class CloudKitMigrationTests(unittest.TestCase):
    def test_new_start_hash_matches_shared_fixture(self):
        # The fixture's source hash is deliberately independent of the local
        # attended inventory, so exercise the shared payload directly.
        from scripts.reconcile_progress import canonical_hash

        payload = {
            "schema_version": 1,
            "kind": "new_start_baseline",
            "migration_epoch": "epoch-fixture",
            "source_snapshot_hash": "a" * 64,
            "active_pack_ids": ["cissp"],
            "document": {"schema_version": 1, "sessions": [], "mastery": {}, "srs": {}},
            "document_revision": 0,
            "operation_id": "baseline",
            "import_claim": False,
        }
        fixture = json.loads((ROOT / "protocol-fixtures/migration-new-start-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical_hash(payload), fixture["baseline_hash"])

    def test_new_start_builds_empty_hash_bound_baseline_without_import_claim(self):
        baseline = build_new_start_baseline(LOCAL_INVENTORY, "epoch-new-start")
        self.assertEqual(baseline["counts"], {"sources": 0, "records": 0})
        self.assertEqual(baseline["document"], {"schema_version": 1, "sessions": [], "mastery": {}, "srs": {}})
        self.assertFalse(baseline["cloudkit_baseline"]["import_claim"])
        self.assertEqual(
            baseline["cloudkit_baseline"]["semantic_hash"],
            canonical_hash(baseline["baseline_hash_payload"]),
        )
        self.assertEqual(
            set(baseline["cloudkit_baseline"]),
            {"document_revision", "operation_id", "semantic_hash", "import_claim"},
        )

    def test_new_start_refuses_source_and_nonzero_inventory(self):
        with self.assertRaises(ExportError):
            export_source(ROOT / "README.md", LOCAL_INVENTORY, migration_epoch="epoch")
        bad = copy.deepcopy(LOCAL_INVENTORY)
        bad["counts"]["records"] = 1
        with self.assertRaises(ExportError):
            validate_inventory(bad)

    def test_example_placeholder_is_not_approved_inventory(self):
        example = json.loads((ROOT / ".state/progress-source-inventory.example.json").read_text(encoding="utf-8"))
        with self.assertRaises(ExportError):
            validate_inventory(example)

    def test_empty_source_and_legacy_answer_without_pack_are_refused(self):
        inventory = copy.deepcopy(LOCAL_INVENTORY)
        inventory["path"] = "one_source"
        inventory["counts"] = {"sources": 1, "records": 1}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps({"schema_version": 1, "sessions": [], "mastery": {}, "srs": {}}), encoding="utf-8")
            with self.assertRaises(ExportError):
                export_source(source, inventory, migration_epoch="epoch")
            source.write_text(json.dumps({
                "schema_version": 1,
                "sessions": [{"session_id": "s", "answers": [{"course": "c", "question_id": "q"}]}],
                "mastery": {},
                "srs": {},
            }), encoding="utf-8")
            with self.assertRaises(ReconciliationError):
                export_source(source, inventory, migration_epoch="epoch")

    def test_source_export_detects_write_during_export(self):
        inventory = copy.deepcopy(LOCAL_INVENTORY)
        inventory["path"] = "one_source"
        inventory["counts"] = {"sources": 1, "records": 1}
        document = {"schema_version": 1, "sessions": [{"session_id": "s"}], "mastery": {}, "srs": {}}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text(json.dumps(document), encoding="utf-8")
            with mock.patch("scripts.export_progress.source_fingerprint", side_effect=["before", "after"]):
                with self.assertRaises(ExportError):
                    export_source(source, inventory, migration_epoch="epoch")

    def test_union_deduplicates_identical_operation_and_rejects_conflict(self):
        document = {"schema_version": 1, "sessions": [{"operation_id": "op", "answers": []}], "mastery": {}, "srs": {}}
        self.assertEqual(len(union_documents([document, copy.deepcopy(document)])["sessions"]), 1)
        changed = copy.deepcopy(document)
        changed["sessions"][0]["answers"] = [{"course_id": "c", "pack_id": "p", "question_id": "q"}]
        with self.assertRaises(ReconciliationError):
            union_documents([document, changed])

    def test_plan_resume_and_rollback_are_idempotent_and_local_only(self):
        plan = build_plan(LOCAL_INVENTORY, migration_epoch="epoch-resume")
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            first = run_migration(plan, state_path, dry_run=True)
            resumed = run_migration(plan, state_path, dry_run=True, resume=True)
            self.assertEqual(first, resumed)
            rolled_back = run_migration(plan, state_path, rollback=True)
            self.assertEqual(rolled_back["phase"], "rolled_back")
            with self.assertRaises(MigrationError):
                run_migration(build_plan(LOCAL_INVENTORY, migration_epoch="different"), state_path, resume=True)

    def test_non_new_start_plan_requires_verified_export(self):
        inventory = copy.deepcopy(LOCAL_INVENTORY)
        inventory["path"] = "one_source"
        with self.assertRaises(MigrationError):
            build_plan(inventory, migration_epoch="epoch")


if __name__ == "__main__":
    unittest.main()
