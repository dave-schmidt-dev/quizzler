"""Tests for ``scripts/progress_store.py`` — the SQLite-backed progress store.

Run: python3 -m unittest tests.test_progress_store -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "progress_store.py"

_spec = importlib.util.spec_from_file_location("progress_store", SCRIPT_PATH)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


class EmptyDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_db_creates_tables(self):
        ps.init_db(self.db_path)
        self.assertTrue(os.path.exists(self.db_path))

    def test_init_db_enables_wal(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            self.assertEqual(row[0], "wal")
        finally:
            conn.close()

    def test_init_db_passes_integrity_check(self):
        ps.init_db(self.db_path)

    def test_get_progress_returns_empty_on_fresh_db(self):
        ps.init_db(self.db_path)
        revision, doc = ps.get_progress(self.db_path)
        self.assertEqual(revision, 0)
        self.assertEqual(doc["sessions"], [])
        self.assertEqual(doc["mastery"], {})
        self.assertEqual(doc["srs"], {})

    def test_get_revision_returns_zero_on_fresh_db(self):
        ps.init_db(self.db_path)
        self.assertEqual(ps.get_revision(self.db_path), 0)

    def test_get_document_returns_empty_on_fresh_db(self):
        ps.init_db(self.db_path)
        doc = ps.get_document(self.db_path)
        self.assertEqual(doc["sessions"], [])
        self.assertEqual(doc["mastery"], {})
        self.assertEqual(doc["srs"], {})

    def test_init_db_sets_user_version(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, ps.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_init_db_rejects_corrupt_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w") as f:
            f.write("not a sqlite database")
        with self.assertRaises(Exception):
            ps.init_db(self.db_path)

    def test_init_db_rejects_future_schema(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            conn.execute("PRAGMA user_version = 999")
        finally:
            conn.close()
        with self.assertRaises(ps.FutureSchemaError):
            ps.init_db(self.db_path)

    def test_busy_timeout_set(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
            self.assertEqual(row[0], ps.BUSY_TIMEOUT)
        finally:
            conn.close()


class SaveProgressTests(unittest.TestCase):
    def _init(self):
        ps.init_db(self.db_path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        self._init()

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def _make_op(self, endpoint, op_id, request_body, response_body):
        return ps._build_operation_record(endpoint, op_id, request_body, response_body)

    def test_save_progress_writes_first_revision(self):
        doc = self._empty_doc()
        doc["sessions"] = [{"course": "foo", "pack": "bar", "score": 85}]
        _, op_record = self._make_op("quiz_completed", "op1", {}, {"ok": True})
        result = ps.save_progress(0, doc, op_record, self.db_path)
        self.assertEqual(result, {"ok": True})

        revision, stored = ps.get_progress(self.db_path)
        self.assertEqual(revision, 1)
        self.assertEqual(len(stored["sessions"]), 1)

    def test_save_progress_increments_revision(self):
        for i in range(3):
            doc = self._empty_doc()
            doc["sessions"] = [{"n": i}]
            _, op_record = self._make_op("quiz_completed", f"op{i}", {}, {"ok": True})
            ps.save_progress(i, doc, op_record, self.db_path)

        revision, stored = ps.get_progress(self.db_path)
        self.assertEqual(revision, 3)
        self.assertEqual(stored["sessions"][0]["n"], 2)

    def test_save_progress_idempotent_replay_same_body(self):
        doc = self._empty_doc()
        _, op_record = self._make_op("quiz_completed", "op1", {"x": 1}, {"r": 1})
        r1 = ps.save_progress(0, doc, op_record, self.db_path)
        self.assertEqual(r1, {"r": 1})

        r2 = ps.save_progress(0, doc, op_record, self.db_path)
        self.assertEqual(r2, {"r": 1})

        revision, _ = ps.get_progress(self.db_path)
        self.assertEqual(revision, 1)

    def test_save_progress_rejects_different_body_same_operation_id(self):
        doc = self._empty_doc()
        _, op_record1 = self._make_op("quiz_completed", "op1", {"x": 1}, {"r": 1})
        ps.save_progress(0, doc, op_record1, self.db_path)

        _, op_record2 = self._make_op("quiz_completed", "op1", {"x": 2}, {"r": 1})
        with self.assertRaises(ps.OperationConflictError):
            ps.save_progress(0, doc, op_record2, self.db_path)

    def test_save_progress_rejects_stale_revision(self):
        doc = self._empty_doc()
        _, op_record = self._make_op("quiz_completed", "op1", {}, {"ok": True})
        ps.save_progress(0, doc, op_record, self.db_path)

        doc2 = self._empty_doc()
        _, op_record2 = self._make_op("quiz_completed", "op2", {}, {"ok": True})
        with self.assertRaises(ps.RevisionConflictError) as ctx:
            ps.save_progress(0, doc2, op_record2, self.db_path)
        self.assertEqual(ctx.exception.current_revision, 1)

    def test_save_progress_rolls_back_on_invalid_doc(self):
        doc = self._empty_doc()
        doc["sessions"] = "not-a-list"
        _, op_record = self._make_op("quiz_completed", "op1", {}, {"ok": True})
        with self.assertRaises(ValueError):
            ps.save_progress(0, doc, op_record, self.db_path)

        revision, _ = ps.get_progress(self.db_path)
        self.assertEqual(revision, 0)


class QuizCompletedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_quiz_completed_adds_session_and_mastery(self):
        session = {
            "course": "math",
            "pack": "algebra",
            "score": 85,
            "questions": [],
            "timestamp": "2024-01-01T00:00:00Z",
        }
        delta = {
            "seen": {"q1": True, "q2": True},
            "correct": {"q1": True, "q2": False},
            "consecutive": {"q1": 2},
        }
        result = ps.quiz_completed(
            self.db_path, session, delta, "math", "algebra", "op-quiz-1"
        )

        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(len(doc["sessions"]), 1)
        self.assertEqual(doc["sessions"][0]["course"], "math")
        self.assertEqual(doc["sessions"][0]["pack"], "algebra")

        mastery = doc["mastery"]["math"]["algebra"]
        self.assertTrue(mastery["seen"]["q1"])
        self.assertTrue(mastery["seen"]["q2"])
        self.assertTrue(mastery["correct"]["q1"])
        self.assertFalse(mastery["correct"]["q2"])
        self.assertEqual(mastery["consecutive"]["q1"], 2)

    def test_quiz_completed_caps_sessions_at_200(self):
        for i in range(250):
            session = {
                "course": "math",
                "pack": "algebra",
                "score": i,
                "questions": [],
                "timestamp": f"2024-01-{i+1:02d}T00:00:00Z",
            }
            ps.quiz_completed(
                self.db_path, session, {}, "math", "algebra", f"op-{i}"
            )

        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(len(doc["sessions"]), 200)
        self.assertEqual(doc["sessions"][0]["score"], 249)

    def test_quiz_completed_is_idempotent(self):
        session = {"course": "math", "pack": "alg", "score": 90, "questions": [],
                    "timestamp": "2024-01-01T00:00:00Z"}
        r1 = ps.quiz_completed(self.db_path, session, {}, "math", "alg", "op-x")
        r2 = ps.quiz_completed(self.db_path, session, {}, "math", "alg", "op-x")
        self.assertEqual(r1, r2)
        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(len(doc["sessions"]), 1)


class SRSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _prime_srs_entry(self, course_id, key, tier=3):
        conn = ps._open_db(self.db_path)
        try:
            doc = {
                "schema_version": 1,
                "sessions": [],
                "mastery": {},
                "srs": {
                    course_id: {
                        "schema_version": 1,
                        "updated_at": "2024-01-01T00:00:00+00:00",
                        "questions": {
                            key: {
                                "tier": tier,
                                "review_count": 3,
                                "lapse_count": 0,
                                "next_due_at": "2024-01-01T00:00:00+00:00",
                                "last_reviewed_at": "2024-01-01T00:00:00+00:00",
                                "last_result": "good",
                            }
                        },
                    }
                },
            }
            doc_json = json.dumps(doc, sort_keys=True)
            conn.execute(
                "INSERT OR REPLACE INTO progress_state (id, schema_version, revision, document_json, updated_at) "
                "VALUES (1, ?, 1, ?, ?)",
                (1, doc_json, "2024-01-01T00:00:00+00:00"),
            )
        finally:
            conn.close()

    def test_srs_rated_again_drops_two_tiers(self):
        self._prime_srs_entry("math", "math::alg::q1", tier=5)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q1", "again", "op-srs-1")
        self.assertEqual(result["old_tier"], 5)
        self.assertEqual(result["new_tier"], 3)

    def test_srs_rated_again_minimum_tier_1(self):
        self._prime_srs_entry("math", "math::alg::q2", tier=1)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q2", "again", "op-srs-2")
        self.assertEqual(result["new_tier"], 1)

    def test_srs_rated_hard_keeps_tier(self):
        self._prime_srs_entry("math", "math::alg::q3", tier=3)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q3", "hard", "op-srs-3")
        self.assertEqual(result["old_tier"], 3)
        self.assertEqual(result["new_tier"], 3)

    def test_srs_rated_good_advances_one_tier(self):
        self._prime_srs_entry("math", "math::alg::q4", tier=3)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q4", "good", "op-srs-4")
        self.assertEqual(result["old_tier"], 3)
        self.assertEqual(result["new_tier"], 4)

    def test_srs_rated_easy_advances_two_tiers(self):
        self._prime_srs_entry("math", "math::alg::q5", tier=3)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q5", "easy", "op-srs-5")
        self.assertEqual(result["old_tier"], 3)
        self.assertEqual(result["new_tier"], 5)

    def test_srs_rated_good_at_max_tier(self):
        self._prime_srs_entry("math", "math::alg::q6", tier=7)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q6", "good", "op-srs-6")
        self.assertEqual(result["new_tier"], 7)

    def test_srs_rated_easy_at_max_tier(self):
        self._prime_srs_entry("math", "math::alg::q7", tier=7)
        result = ps.srs_rated(self.db_path, "math", "math::alg::q7", "easy", "op-srs-7")
        self.assertEqual(result["new_tier"], 7)

    def test_srs_rated_unassigned_defaults_tier_1(self):
        """A new question with no existing SRS entry starts at tier 1."""
        result = ps.srs_rated(self.db_path, "math", "math::alg::q_new", "good", "op-srs-new")
        self.assertEqual(result["old_tier"], 1)
        self.assertEqual(result["new_tier"], 2)

    def test_srs_rated_sets_next_due_at(self):
        result = ps.srs_rated(self.db_path, "math", "math::alg::q_new", "good", "op-srs-due")
        _, doc = ps.get_progress(self.db_path)
        entry = doc["srs"]["math"]["questions"]["math::alg::q_new"]
        self.assertIn("next_due_at", entry)
        self.assertIn("last_reviewed_at", entry)
        self.assertEqual(entry["review_count"], 1)
        self.assertEqual(entry["last_result"], "good")

    def test_srs_rated_lapse_increments_lapse_count(self):
        result = ps.srs_rated(self.db_path, "math", "math::alg::q_lapse", "again", "op-srs-lapse")
        _, doc = ps.get_progress(self.db_path)
        entry = doc["srs"]["math"]["questions"]["math::alg::q_lapse"]
        self.assertEqual(entry["lapse_count"], 1)

    def test_srs_rated_is_idempotent(self):
        r1 = ps.srs_rated(self.db_path, "math", "math::alg::q_idem", "good", "op-srs-idem")
        r2 = ps.srs_rated(self.db_path, "math", "math::alg::q_idem", "good", "op-srs-idem")
        self.assertEqual(r1, r2)
        _, doc = ps.get_progress(self.db_path)
        entry = doc["srs"]["math"]["questions"]["math::alg::q_idem"]
        self.assertEqual(entry["review_count"], 1)


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def test_backup_creates_file(self):
        doc = self._empty_doc()
        _, op_record = ps._build_operation_record("test", "b1", {}, {"ok": True})
        ps.save_progress(0, doc, op_record, self.db_path)

        backup_path = ps.backup_db(self.db_path)
        self.assertTrue(os.path.exists(backup_path))
        self.assertIn(".backup-", backup_path)

    def test_backup_retains_max_5(self):
        doc = self._empty_doc()
        for i in range(7):
            _, op_record = ps._build_operation_record("test", f"bb{i}", {}, {"ok": True})
            ps.save_progress(i, doc, op_record, self.db_path)
            ps.backup_db(self.db_path)

        parent = os.path.dirname(self.db_path)
        base = os.path.basename(self.db_path)
        backups = [f for f in os.listdir(parent) if f.startswith(f"{base}.backup-")]
        self.assertLessEqual(len(backups), 5)

    def test_restore_reverts_to_backup_state(self):
        doc1 = self._empty_doc()
        doc1["sessions"] = [{"n": 1}]
        _, op_record = ps._build_operation_record("test", "r1", {}, {"ok": True})
        ps.save_progress(0, doc1, op_record, self.db_path)

        backup_path = ps.backup_db(self.db_path)

        doc2 = self._empty_doc()
        doc2["sessions"] = [{"n": 2}]
        _, op_record2 = ps._build_operation_record("test", "r2", {}, {"ok": True})
        ps.save_progress(1, doc2, op_record2, self.db_path)

        ps.restore_db(self.db_path, backup_path)

        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(len(doc["sessions"]), 1)
        self.assertEqual(doc["sessions"][0]["n"], 1)

    def test_restore_auto_picks_latest_backup(self):
        doc1 = self._empty_doc()
        doc1["sessions"] = [{"n": 1}]
        _, op_record = ps._build_operation_record("test", "ra1", {}, {"ok": True})
        ps.save_progress(0, doc1, op_record, self.db_path)
        ps.backup_db(self.db_path)

        doc2 = self._empty_doc()
        doc2["sessions"] = [{"n": 2}]
        _, op_record2 = ps._build_operation_record("test", "ra2", {}, {"ok": True})
        ps.save_progress(1, doc2, op_record2, self.db_path)
        ps.backup_db(self.db_path)

        ps.restore_db(self.db_path)

        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(doc["sessions"][0]["n"], 2)

    def test_restore_nonexistent_backup_raises(self):
        with self.assertRaises(FileNotFoundError):
            ps.restore_db(self.db_path, os.path.join(self.tmp.name, "nonexistent"))

    def test_restore_no_backups_raises(self):
        with self.assertRaises(FileNotFoundError):
            ps.restore_db(self.db_path)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_empty_version_0(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            conn.execute("PRAGMA user_version = 0")
        finally:
            conn.close()
        ps.migrate_db(self.db_path)

        conn = ps._open_db(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, ps.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_migrate_current_version_noop(self):
        ps.init_db(self.db_path)
        ps.migrate_db(self.db_path)

    def test_migrate_future_version_raises(self):
        ps.init_db(self.db_path)
        conn = ps._open_db(self.db_path)
        try:
            conn.execute("PRAGMA user_version = 999")
        finally:
            conn.close()
        with self.assertRaises(ps.FutureSchemaError):
            ps.migrate_db(self.db_path)


class ConcurrentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def test_concurrent_read_during_write_does_not_corrupt(self):
        import threading as th

        results = []

        def writer():
            for i in range(20):
                doc = self._empty_doc()
                doc["sessions"] = [{"n": i}]
                _, op_record = ps._build_operation_record("w", f"w{i}", {}, {"ok": True})
                ps.save_progress(i, doc, op_record, self.db_path)

        def reader():
            for _ in range(20):
                rev, doc = ps.get_progress(self.db_path)
                results.append((rev, doc))
                time.sleep(0.001)

        wt = th.Thread(target=writer)
        rt = th.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join()
        rt.join()

        rev, _ = ps.get_progress(self.db_path)
        self.assertEqual(rev, 20)

    def test_concurrent_writes_serialize_correctly(self):
        import threading as th

        errors = []

        def do_write(start_rev):
            try:
                for i in range(5):
                    doc = self._empty_doc()
                    doc["sessions"] = [{"n": i}]
                    _, op_record = ps._build_operation_record("cw", f"cw-{start_rev}-{i}", {}, {"ok": True})
                    ps.save_progress(start_rev + i, doc, op_record, self.db_path)
                    time.sleep(0.005)
            except ps.RevisionConflictError as e:
                errors.append(e)

        t1 = th.Thread(target=do_write, args=(0,))
        t2 = th.Thread(target=do_write, args=(0,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        rev, _ = ps.get_progress(self.db_path)
        self.assertGreater(rev, 0)


class OperationRecordPruningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def test_operation_records_pruned_above_cap(self):
        doc = self._empty_doc()
        for i in range(ps.MAX_OPERATION_RECORDS + 100):
            _, op_record = ps._build_operation_record("prune", f"op-{i}", {"i": i}, {"ok": True})
            ps.save_progress(i, doc, op_record, self.db_path)

        conn = ps._open_db(self.db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM operation_records").fetchone()[0]
            self.assertLessEqual(count, ps.MAX_OPERATION_RECORDS)
        finally:
            conn.close()


class PayloadLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_progress_rejects_over_2mb(self):
        big_doc = {
            "schema_version": 1,
            "sessions": [],
            "mastery": {},
            "srs": {},
            "padding": "x" * (ps.MAX_IMPORT_PAYLOAD + 1),
        }
        with self.assertRaises(ps.PayloadTooLargeError):
            ps.import_progress(self.db_path, big_doc, "op-big")

    def test_validate_mutation_payload_accepts_small(self):
        ps.validate_mutation_payload(b'{"small": true}', "quiz_completed")

    def test_validate_mutation_payload_rejects_large_import(self):
        with self.assertRaises(ps.PayloadTooLargeError):
            ps.validate_mutation_payload(b"x" * (ps.MAX_IMPORT_PAYLOAD + 1), "import_progress")


class ResetProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reset_clear_history(self):
        session = {"course": "math", "pack": "alg", "score": 90, "questions": [],
                    "timestamp": "2024-01-01T00:00:00Z"}
        delta = {"seen": {"q1": True}, "correct": {"q1": True}, "consecutive": {"q1": 1}}
        ps.quiz_completed(self.db_path, session, delta, "math", "alg", "op-1")
        ps.srs_rated(self.db_path, "math", "math::alg::q1", "good", "op-srs-1")

        ps.reset_progress(self.db_path, "op-reset")

        _, doc = ps.get_progress(self.db_path)
        self.assertEqual(doc["sessions"], [])
        self.assertEqual(doc["mastery"], {})
        self.assertIn("math", doc["srs"])

    def test_reset_srs_for_specific_course(self):
        ps.srs_rated(self.db_path, "math", "math::alg::q1", "good", "op-srs-1")
        ps.srs_rated(self.db_path, "history", "history::ww1::q1", "good", "op-srs-2")

        ps.reset_progress(self.db_path, "op-srs-reset", clear_srs_course_id="math")

        _, doc = ps.get_progress(self.db_path)
        self.assertNotIn("math", doc["srs"])
        self.assertIn("history", doc["srs"])


class CleanupOrphansTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cleanup_removes_inactive_course_mastery(self):
        session = {"course": "math", "pack": "alg", "score": 90, "questions": [],
                    "timestamp": "2024-01-01T00:00:00Z"}
        ps.quiz_completed(self.db_path, session,
                          {"seen": {"q1": True}, "correct": {"q1": True}, "consecutive": {}},
                          "math", "alg", "op-1")

        session2 = {"course": "history", "pack": "ww1", "score": 80, "questions": [],
                     "timestamp": "2024-01-02T00:00:00Z"}
        ps.quiz_completed(self.db_path, session2,
                          {"seen": {"q2": True}, "correct": {"q2": True}, "consecutive": {}},
                          "history", "ww1", "op-2")

        result = ps.cleanup_orphans(self.db_path, ["math"], "op-cleanup")
        self.assertEqual(result["mastery_courses_removed"], 1)
        self.assertEqual(result["sessions_removed"], 1)

        _, doc = ps.get_progress(self.db_path)
        self.assertIn("math", doc["mastery"])
        self.assertNotIn("history", doc["mastery"])
        self.assertEqual(len(doc["sessions"]), 1)
        self.assertEqual(doc["sessions"][0]["course"], "math")


class ImportProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_replaces_entire_document(self):
        doc = {
            "schema_version": 1,
            "sessions": [{"course": "imported", "pack": "p1", "score": 100, "questions": [],
                          "timestamp": "2024-01-01T00:00:00Z"}],
            "mastery": {"c1": {"p1": {"seen": {"q1": True}, "correct": {"q1": True}, "consecutive": {}}}},
            "srs": {},
        }
        ps.import_progress(self.db_path, doc, "op-import")

        _, stored = ps.get_progress(self.db_path)
        self.assertEqual(len(stored["sessions"]), 1)
        self.assertEqual(stored["sessions"][0]["course"], "imported")
        self.assertIn("c1", stored["mastery"])

    def test_import_rejects_invalid_doc(self):
        bad_doc = {"schema_version": 1, "sessions": "not-a-list", "mastery": {}, "srs": {}}
        with self.assertRaises(ValueError):
            ps.import_progress(self.db_path, bad_doc, "op-bad")


class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def test_rollback_on_invalid_doc_leaves_db_unchanged(self):
        doc1 = self._empty_doc()
        doc1["sessions"] = [{"course": "valid", "pack": "p1", "score": 80,
                              "questions": [], "timestamp": "2024-01-01T00:00:00Z"}]
        _, op_record1 = ps._build_operation_record("test", "rb1", {}, {"ok": True})
        ps.save_progress(0, doc1, op_record1, self.db_path)

        doc2 = self._empty_doc()
        doc2["sessions"] = "not-a-list"
        _, op_record2 = ps._build_operation_record("test", "rb2", {}, {"ok": True})
        with self.assertRaises(ValueError):
            ps.save_progress(1, doc2, op_record2, self.db_path)

        rev, doc = ps.get_progress(self.db_path)
        self.assertEqual(rev, 1)
        self.assertEqual(len(doc["sessions"]), 1)


class LockTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        ps.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _empty_doc(self):
        return json.loads(json.dumps(ps.EMPTY_DOCUMENT))

    def test_exhausted_lock_wait_raises_specific_error(self):
        original_timeout = ps.WRITE_LOCK_TIMEOUT
        ps.WRITE_LOCK_TIMEOUT = 0.01
        try:
            acquired = ps._write_lock.acquire()
            self.assertTrue(acquired)
            try:
                doc = self._empty_doc()
                _, op_record = ps._build_operation_record("test", "lock1", {}, {"ok": True})
                with self.assertRaises(ps.LockTimeoutError):
                    ps.save_progress(0, doc, op_record, self.db_path)
            finally:
                ps._write_lock.release()
        finally:
            ps.WRITE_LOCK_TIMEOUT = original_timeout


class DocumentValidationTests(unittest.TestCase):
    def test_validate_accepts_valid_doc(self):
        doc = {
            "schema_version": 1,
            "sessions": [],
            "mastery": {"c1": {"p1": {"seen": {"q1": True}, "correct": {"q1": False}, "consecutive": {}}}},
            "srs": {"c1": {"schema_version": 1, "updated_at": "", "questions": {"k": {}}}},
        }
        valid, reason = ps.validate_normalized_doc(doc)
        self.assertTrue(valid, reason)

    def test_validate_rejects_missing_schema_version(self):
        valid, _ = ps.validate_normalized_doc({"sessions": [], "mastery": {}, "srs": {}})
        self.assertFalse(valid)

    def test_validate_rejects_sessions_not_list(self):
        valid, _ = ps.validate_normalized_doc({"schema_version": 1, "sessions": {}, "mastery": {}, "srs": {}})
        self.assertFalse(valid)

    def test_validate_rejects_mastery_not_object(self):
        valid, _ = ps.validate_normalized_doc({"schema_version": 1, "sessions": [], "mastery": [], "srs": {}})
        self.assertFalse(valid)

    def test_validate_rejects_mastery_nested_not_object(self):
        doc = {"schema_version": 1, "sessions": [], "mastery": {"c": {"p": []}}, "srs": {}}
        valid, _ = ps.validate_normalized_doc(doc)
        self.assertFalse(valid)

    def test_validate_rejects_mastery_missing_seen(self):
        doc = {"schema_version": 1, "sessions": [], "mastery": {"c": {"p": {"correct": {}}}}, "srs": {}}
        valid, _ = ps.validate_normalized_doc(doc)
        self.assertFalse(valid)

    def test_validate_rejects_srs_not_object(self):
        doc = {"schema_version": 1, "sessions": [], "mastery": {}, "srs": []}
        valid, _ = ps.validate_normalized_doc(doc)
        self.assertFalse(valid)

    def test_validate_rejects_srs_missing_schema_version(self):
        doc = {"schema_version": 1, "sessions": [], "mastery": {}, "srs": {"c": {"questions": {}}}}
        valid, _ = ps.validate_normalized_doc(doc)
        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
