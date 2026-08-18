"""Task 3.4 migration/reconciliation refusal and restart tests."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.export_progress import ExportError, export_source, validate_inventory
from scripts.migrate_progress import MigrationError, build_plan, run_migration
from scripts.reconcile_progress import (
    ReconciliationError,
    build_new_start_baseline,
    canonical_hash,
    reconcile_exports,
    semantic_counts,
    union_documents,
    validate_export_envelope,
)


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


def _document(sessions: int = 1) -> dict:
    return {
        "schema_version": 1,
        "sessions": [
            {
                "operation_id": f"op-{index}",
                "answers": [{"course_id": "c", "pack_id": "p", "question_id": f"q{index}"}],
            }
            for index in range(sessions)
        ],
        "mastery": {},
        "srs": {},
    }


def _envelope(document: dict | None = None, **overrides) -> dict:
    document = document if document is not None else _document()
    envelope = {
        "schema_version": 1,
        "kind": "source_export",
        "migration_epoch": "epoch-verified",
        "source_kind": "browser_export",
        "source_snapshot_hash": "a" * 64,
        "source_export_hash": canonical_hash(document),
        "document": document,
        "counts": semantic_counts(document),
        "scope": {"active_pack_ids": ["p"]},
    }
    envelope.update(overrides)
    return envelope


def _inventory_for(envelopes: list[dict], document: dict) -> dict:
    return {
        "schema_version": 1,
        "path": "one_source" if len(envelopes) == 1 else "multi_source",
        "approval": {
            "approved": True,
            "disposition": "verified export",
            "attestation": {"kind": "local_session_ref", "reference": "b" * 64},
        },
        "counts": {"sources": len(envelopes), "records": semantic_counts(document)["records"]},
        "scope": {"active_pack_ids": ["p"]},
    }


class ExportVerificationTests(unittest.TestCase):
    """INV-9: no import plan may be derived from an unverified export."""

    def test_a_conformant_envelope_is_accepted(self):
        self.assertEqual(validate_export_envelope(_envelope()), _envelope())

    def test_tampered_document_is_caught_by_the_recorded_content_hash(self):
        envelope = _envelope()
        # The document is edited after export; its recorded hash no longer fits.
        envelope["document"]["sessions"][0]["answers"][0]["question_id"] = "tampered"
        with self.assertRaises(ReconciliationError) as caught:
            validate_export_envelope(envelope)
        message = str(caught.exception)
        self.assertIn("export content hash does not describe its document", message)
        self.assertIn(envelope["source_export_hash"], message)
        self.assertIn(canonical_hash(envelope["document"]), message)

    def test_truncated_document_reports_the_exact_count_discrepancy(self):
        document = _document(sessions=3)
        envelope = _envelope(document)
        truncated = _document(sessions=1)
        envelope["document"] = truncated
        envelope["source_export_hash"] = canonical_hash(truncated)
        with self.assertRaises(ReconciliationError) as caught:
            validate_export_envelope(envelope)
        message = str(caught.exception)
        self.assertIn("export counts do not match its document", message)
        self.assertIn("sessions: recorded=3 measured=1", message)
        self.assertIn("records: recorded=3 measured=1", message)

    def test_envelope_shape_version_and_kind_are_enforced(self):
        for overrides in (
            {"kind": "migration_plan"},
            {"schema_version": 2},
            {"source_kind": "carrier_pigeon"},
            {"source_snapshot_hash": "not-a-digest"},
            {"migration_epoch": "   "},
            {"scope": {"active_pack_ids": []}},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ReconciliationError):
                    validate_export_envelope(_envelope(**overrides))
        missing = _envelope()
        del missing["counts"]
        with self.assertRaises(ReconciliationError) as caught:
            validate_export_envelope(missing)
        self.assertIn("missing=['counts']", str(caught.exception))

    def test_reconcile_rejects_duplicate_sources_and_mixed_epochs(self):
        first = _envelope()
        with self.assertRaises(ReconciliationError) as caught:
            reconcile_exports([first, copy.deepcopy(first)])
        self.assertIn("exported more than once", str(caught.exception))

        second = _envelope(_document(sessions=2), source_snapshot_hash="c" * 64, migration_epoch="other")
        with self.assertRaises(ReconciliationError) as caught:
            reconcile_exports([first, second])
        self.assertIn("multiple migration epochs", str(caught.exception))

    def test_inventory_counts_must_match_the_verified_exports(self):
        document = _document(sessions=2)
        envelope = _envelope(document)
        inventory = _inventory_for([envelope], document)
        reconciled = reconcile_exports([envelope], inventory=inventory)
        self.assertEqual(reconciled["counts"], semantic_counts(document))

        wrong_records = copy.deepcopy(inventory)
        wrong_records["counts"]["records"] = 99
        with self.assertRaises(ReconciliationError) as caught:
            reconcile_exports([envelope], inventory=wrong_records)
        self.assertIn("recorded=99", str(caught.exception))
        self.assertIn(f"measured={semantic_counts(document)['records']}", str(caught.exception))

        wrong_sources = copy.deepcopy(inventory)
        wrong_sources["counts"]["sources"] = 2
        with self.assertRaises(ReconciliationError) as caught:
            reconcile_exports([envelope], inventory=wrong_sources)
        self.assertIn("inventory source count does not match", str(caught.exception))

    def test_export_scope_must_match_the_attended_inventory(self):
        document = _document()
        envelope = _envelope(document, scope={"active_pack_ids": ["other-pack"]})
        inventory = _inventory_for([envelope], document)
        with self.assertRaises(ReconciliationError) as caught:
            reconcile_exports([envelope], inventory=inventory)
        self.assertIn("export pack scope does not match", str(caught.exception))

    def test_build_plan_refuses_an_unverified_export(self):
        document = _document(sessions=2)
        envelope = _envelope(document)
        inventory = _inventory_for([envelope], document)
        plan = build_plan(inventory, [envelope], migration_epoch="epoch-verified")
        self.assertTrue(plan["cloudkit_operations"][0]["import_claim"])
        self.assertEqual(plan["verified_exports"][0]["source_export_hash"], envelope["source_export_hash"])

        tampered = copy.deepcopy(envelope)
        tampered["document"]["sessions"].pop()
        # MigrationError subclasses ReconciliationError, so this covers a
        # refusal raised at either the verification or the planning boundary.
        with self.assertRaises(ReconciliationError) as caught:
            build_plan(inventory, [tampered], migration_epoch="epoch-verified")
        self.assertIn("export content hash does not describe its document", str(caught.exception))

    def test_new_start_refuses_to_carry_an_export(self):
        with self.assertRaises(MigrationError) as caught:
            build_plan(LOCAL_INVENTORY, [_envelope()], migration_epoch="epoch")
        self.assertIn("new_start forbids source exports", str(caught.exception))

    def test_plan_adopts_the_export_epoch_and_rejects_a_conflicting_one(self):
        document = _document()
        envelope = _envelope(document)
        inventory = _inventory_for([envelope], document)
        self.assertEqual(build_plan(inventory, [envelope])["migration_epoch"], "epoch-verified")
        with self.assertRaises(MigrationError) as caught:
            build_plan(inventory, [envelope], migration_epoch="a-different-epoch")
        self.assertIn("different migration epoch", str(caught.exception))


class ImportIdentityTests(unittest.TestCase):
    """A raiser and its catcher must agree on the exception class."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_sibling_imports_keep_one_exception_identity_with_scripts_on_syspath(self):
        # Ten scripts insert scripts/ onto sys.path when imported, so by the time
        # a later suite imports scripts.migrate_progress the bare module name
        # already resolves. A try/except import shim would then bind the
        # top-level reconcile_progress and mint a second ReconciliationError.
        probe = (
            "import sys\n"
            f"sys.path.insert(0, {str(self.ROOT / 'scripts')!r})\n"
            f"sys.path.insert(0, {str(self.ROOT)!r})\n"
            "import reconcile_progress\n"
            "from scripts.migrate_progress import MigrationError\n"
            "from scripts.reconcile_progress import ReconciliationError\n"
            "assert issubclass(MigrationError, ReconciliationError), 'MigrationError escaped its base'\n"
            "assert MigrationError.__mro__[1] is ReconciliationError, MigrationError.__mro__\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=self.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_no_script_resolves_siblings_through_a_dual_name_import_shim(self):
        """Forbid `try: import x / except: import pkg.x`, not every guarded import.

        The defect is name-resolution fallback: both branches can succeed, so the
        bound module depends on sys.path. A lazy import guarded to raise a domain
        error is a different, legitimate shape and stays allowed.
        """
        offenders = []
        for directory in (self.ROOT / "scripts", self.ROOT / "app" / "scripts"):
            for path in sorted(directory.glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Try):
                        continue
                    primary = self._imported_modules(node.body)
                    fallback = set()
                    for handler in node.handlers:
                        fallback |= self._imported_modules(handler.body)
                    if any(
                        alternate != name and alternate.endswith("." + name)
                        for name in primary
                        for alternate in fallback
                    ):
                        offenders.append(f"{path.relative_to(self.ROOT)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "resolve siblings with an explicit __package__ guard; a dual-name shim "
            "binds whichever module name happens to resolve first",
        )

    @staticmethod
    def _imported_modules(body) -> set[str]:
        names: set[str] = set()
        for statement in body:
            if isinstance(statement, ast.Import):
                names.update(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom) and statement.module and not statement.level:
                names.add(statement.module)
        return names


if __name__ == "__main__":
    unittest.main()
