"""Protocol-v1 conformance fixtures against the SQLite shared-progress store."""

from __future__ import annotations

import datetime
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "protocol-fixtures" / "progress-v1.json"
STORE_PATH = ROOT / "scripts" / "progress_store.py"
spec = importlib.util.spec_from_file_location("progress_store_protocol", STORE_PATH)
ps = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ps)


def assert_semantic_fields(test: unittest.TestCase, expected, actual, path: str = "$") -> None:
    """Compare every semantic field before accepting a canonical digest."""
    test.assertIs(type(actual), type(expected), path)
    if isinstance(expected, dict):
        test.assertEqual(set(actual), set(expected), path)
        for key in expected:
            assert_semantic_fields(test, expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        test.assertEqual(len(actual), len(expected), path)
        for index, (want, got) in enumerate(zip(expected, actual)):
            assert_semantic_fields(test, want, got, f"{path}[{index}]")
    else:
        test.assertEqual(actual, expected, path)


class ProgressProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "progress.sqlite3")
        ps.init_db(self.db_path)
        self.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fixture_is_versioned_and_uses_canonical_representations(self) -> None:
        self.assertEqual(self.fixture["protocol"], "quizzler-progress")
        self.assertEqual(self.fixture["version"], 1)
        self.assertIn("epoch milliseconds", self.fixture["representations"]["time"])
        self.assertIn("Decimal scores", self.fixture["representations"]["fixed_point"])
        self.assertIn("conditional snapshot/change-tag", self.fixture["representations"]["order"])
        expected = self.fixture["parity_case"]["expected"]
        self.assertEqual(ps.canonical_semantic_hash(expected), self.fixture["parity_case"]["canonical_sha256"])
        with self.assertRaisesRegex(ValueError, "float"):
            ps.canonical_semantic_json({"time_ms": 1.5})
        self.assertEqual(
            ps.canonical_semantic_hash({"label": "e\u0301"}),
            ps.canonical_semantic_hash({"label": "é"}),
        )

    def test_sqlite_store_matches_fixture_semantics_before_hash(self) -> None:
        case = self.fixture["parity_case"]
        operations = {operation["operation_id"]: operation for operation in case["operations"]}
        cas = self.fixture["cas_case"]
        accepted: list[dict] = []
        current_tag = cas["initial_snapshot"]["change_tag"]
        for attempt in cas["attempts"]:
            outcome = attempt["outcome"]
            operation_ids = attempt["operation_ids"]
            if outcome["status"] == "rebase_required":
                with self.assertRaises(ps.RevisionConflictError) as conflict:
                    stale = operations[operation_ids[0]]
                    source = stale["session"]
                    ps.quiz_completed(
                        self.db_path,
                        {"session_id": source["session_id"], "course": source["answers"][0]["course_id"], "pack": source["answers"][0]["pack_id"], "answers": source["answers"]},
                        {}, source["answers"][0]["course_id"], source["answers"][0]["pack_id"], stale["operation_id"],
                        expected_revision=attempt["expected_revision"],
                    )
                self.assertEqual(conflict.exception.current_revision, outcome["full_fetch"]["revision"])
                revision, _ = ps.get_progress(self.db_path)  # Full local snapshot fetch before rebase.
                self.assertEqual(revision, outcome["full_fetch"]["revision"])
                current_tag = outcome["full_fetch"]["change_tag"]
                continue
            self.assertEqual(attempt["expected_change_tag"], current_tag)
            for operation_id in operation_ids:
                operation = operations[operation_id]
                source = operation["session"]
                session = {
                    "session_id": source["session_id"],
                    "course": source["answers"][0]["course_id"],
                    "pack": source["answers"][0]["pack_id"],
                    "timestamp_ms": source["completed_at_ms"],
                    "answers": source["answers"],
                }
                revision = ps.quiz_completed(
                    self.db_path, session, {}, session["course"], session["pack"], operation_id,
                    expected_revision=attempt["expected_revision"],
                )["revision"]
                self.assertEqual(revision, outcome["assigned_revisions"][operation_id])
                accepted.append(operation)
            current_tag = outcome["change_tag"]

        batch = cas["batch"]
        self.assertEqual(batch["expected_revision"], 2)
        self.assertEqual(batch["expected_change_tag"], current_tag)
        self.assertLessEqual(batch["snapshot_records"] + len(batch["operation_ids"]), batch["maximum_atomic_records"])
        self.assertEqual(
            list(batch["outcome"]["assigned_revisions"]),
            sorted(batch["operation_ids"]),
        )

        revision, document = ps.get_progress(self.db_path)
        sessions = sorted(document["sessions"], key=lambda item: item["session_id"])
        actual = {
            "schema_version": document["schema_version"],
            "revision": revision,
            "sessions_total": len(sessions),
            "answered": sum(len(item["answers"]) for item in sessions),
            "correct": sum(answer["correct"] for item in sessions for answer in item["answers"]),
            "score_fixed": "0.666667",
            "operation_order": ["op-a", "op-z"],
            "session_ids": [item["session_id"] for item in sessions],
            "answers": [
                [answer["course_id"], answer["pack_id"], answer["question_id"], answer["correct"]]
                for item in sessions for answer in item["answers"]
            ],
            "times_ms": [item["created_at_ms"] for item in accepted],
        }
        expected = case["expected"]
        assert_semantic_fields(self, expected, actual)
        self.assertEqual(ps.canonical_semantic_hash(actual), case["canonical_sha256"])

        # A complete empty-zone fetch creates a new CloudKit-local revision
        # space. SQLite represents that authoritative empty baseline as a
        # fresh store, then replays retained operations using their original
        # IDs; it must not inherit the deleted zone's revision.
        empty_zone = cas["empty_zone_rebase"]
        self.assertEqual(empty_zone["full_fetch"], {"is_full_snapshot": True, "snapshot": None})
        reset_db = str(Path(self.temp.name) / "empty-zone.sqlite3")
        ps.init_db(reset_db)
        reset_operations = {item["operation_id"]: item for item in accepted}
        for revision, operation_id in enumerate(empty_zone["retained_operation_ids"], start=1):
            operation = reset_operations[operation_id]
            source = operation["session"]
            ps.quiz_completed(
                reset_db,
                {"session_id": source["session_id"], "course": source["answers"][0]["course_id"], "pack": source["answers"][0]["pack_id"], "answers": source["answers"]},
                {}, source["answers"][0]["course_id"], source["answers"][0]["pack_id"], operation_id,
                expected_revision=revision - 1,
            )
        reset_revision, _ = ps.get_progress(reset_db)
        create = empty_zone["conditional_create"]
        self.assertEqual(reset_revision, create["outcome"]["revision"])
        self.assertEqual(create["expected_revision"], 0)
        self.assertIsNone(create["expected_change_tag"])
        self.assertEqual(create["outcome"]["assigned_revisions"], {"op-a": 1, "op-z": 2})
        self.assertEqual(empty_zone["issue_replay"], {"status": "applied", "issue_ids": ["issue-retained"]})

    def test_global_revision_rebase_retention_and_size_boundaries(self) -> None:
        session = {"session_id": "first", "course": "c", "pack": "p", "answers": []}
        ps.quiz_completed(self.db_path, session, {}, "c", "p", "first", expected_revision=0)
        with self.assertRaises(ps.RevisionConflictError):
            ps.quiz_completed(self.db_path, {"session_id": "stale", "course": "c", "pack": "p", "answers": []}, {}, "c", "p", "stale", expected_revision=0)

        for index in range(1, 201):
            ps.quiz_completed(
                self.db_path,
                {"session_id": f"s-{index}", "course": "c", "pack": "p", "answers": []},
                {}, "c", "p", f"op-{index}", expected_revision=index,
            )
        revision, document = ps.get_progress(self.db_path)
        self.assertEqual(revision, 201)
        self.assertEqual(len(document["sessions"]), self.fixture["boundaries"]["session_details"])
        self.assertNotIn("first", [item["session_id"] for item in document["sessions"]])
        ps.validate_mutation_payload(b"x" * ps.MAX_MUTATION_PAYLOAD, "quiz_completed")
        # The exact cap is accepted; one extra byte must fail without mutation.
        with self.assertRaises(ps.PayloadTooLargeError):
            ps.validate_mutation_payload(b"x" * (ps.MAX_MUTATION_PAYLOAD + 1), "quiz_completed")

    def test_operation_record_compaction_and_version_refusal(self) -> None:
        conn = ps._open_db(self.db_path)
        try:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)).isoformat()
            rows = [("test", f"op-{index}", "hash", "{}", now) for index in range(ps.MAX_OPERATION_RECORDS + 1)]
            rows.append(("test", "expired", "hash", "{}", old))
            conn.executemany(
                "INSERT INTO operation_records (endpoint, operation_id, request_hash, response_json, created_at) VALUES (?, ?, ?, ?, ?)", rows,
            )
            ps._prune_operation_records(conn)
            count = conn.execute("SELECT COUNT(*) FROM operation_records").fetchone()[0]
            expired = conn.execute("SELECT COUNT(*) FROM operation_records WHERE operation_id = 'expired'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, self.fixture["boundaries"]["operation_records"])
        self.assertEqual(expired, 0)
        invalid = ps._copy_empty_doc()
        invalid["schema_version"] = 2
        self.assertFalse(ps.validate_normalized_doc(invalid)[0])


if __name__ == "__main__":
    unittest.main()
