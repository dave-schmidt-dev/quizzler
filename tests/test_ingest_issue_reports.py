"""Tests for scripts/ingest_issue_reports.py issue report ingestion."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "ingest_issue_reports.py"
FIXTURE_PATH = ROOT / "protocol-fixtures" / "issue-inbox-v1.json"

spec = importlib.util.spec_from_file_location("ingest_issue_reports", SCRIPT_PATH)
ingest_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ingest_mod)


class IngestIssueReportsBaseTest(unittest.TestCase):
    """Base setup providing isolated temporary directories."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.courses_root = self.base_path / "question-packs"
        self.feedback_root = self.base_path / ".logs" / "feedback"
        self.ledger_path = self.feedback_root / ".ingested-issues.json"
        self.log_dir = self.base_path / ".logs"

        self.courses_root.mkdir(parents=True, exist_ok=True)
        self.feedback_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_course(self, course_id: str) -> Path:
        """Create a mock course directory with _course.json."""
        course_dir = self.courses_root / course_id
        course_dir.mkdir(parents=True, exist_ok=True)
        course_file = course_dir / "_course.json"
        course_file.write_text(json.dumps({"course_id": course_id, "title": course_id}), encoding="utf-8")
        return course_dir

    def run_ingest(
        self,
        sources: list[Path],
        dry_run: bool = False,
        summary: bool = False,
        debug: bool = False,
    ) -> int:
        """Invoke ingest_mod.ingest with test directories."""
        return ingest_mod.ingest(
            sources=sources,
            courses_root=self.courses_root,
            feedback_root=self.feedback_root,
            ledger_path=self.ledger_path,
            log_dir=self.log_dir,
            dry_run=dry_run,
            summary=summary,
            debug=debug,
        )

    def run_cli_subprocess(self, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
        """Invoke scripts/ingest_issue_reports.py via subprocess."""
        cmd = [
            sys.executable,
            str(SCRIPT_PATH),
            "--courses-root",
            str(self.courses_root),
            "--feedback-root",
            str(self.feedback_root),
            "--ledger",
            str(self.ledger_path),
            "--log-dir",
            str(self.log_dir),
            *extra_args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )


class TestIngestIssueReports(IngestIssueReportsBaseTest):
    """Test suite covering ingestion logic, formatting, and invariants."""

    def test_golden_fixture_ingestion(self) -> None:
        """Golden fixture files both issues under the right courses with expected headings."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        code = self.run_ingest([FIXTURE_PATH])
        self.assertEqual(code, 0)

        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        it540_target = self.feedback_root / "it540" / "pending.md"

        self.assertTrue(cysa_target.is_file())
        self.assertTrue(it540_target.is_file())

        cysa_content = cysa_target.read_text(encoding="utf-8")
        it540_content = it540_target.read_text(encoding="utf-8")

        heading_pattern = re.compile(
            r"^### \d{4}-\d{2}-\d{2} — `([^`]+)` — source: in-app report, pack `([^`]+)`, issue `([^`]+)`"
        )

        cysa_headings = [line for line in cysa_content.splitlines() if line.startswith("### ")]
        self.assertEqual(len(cysa_headings), 1)
        m1 = heading_pattern.match(cysa_headings[0])
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1), "so005")
        self.assertEqual(m1.group(2), "cysa-plus-core")
        self.assertEqual(m1.group(3), "issue-00000000-0000-4000-8000-000000000001")

        self.assertIn("- Selected response:\n  ```text\n  Option B\n  ```", cysa_content)
        self.assertIn("- Report:\n  ```text\n  Fixture report: the keyed answer looks wrong.\n  ```", cysa_content)

        it540_headings = [line for line in it540_content.splitlines() if line.startswith("### ")]
        self.assertEqual(len(it540_headings), 1)
        m2 = heading_pattern.match(it540_headings[0])
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "s13")
        self.assertEqual(m2.group(2), "it540-midterm-review-mod1-7")
        self.assertEqual(m2.group(3), "issue-00000000-0000-4000-8000-000000000002")

        self.assertIn("- Selected response: (none)", it540_content)
        self.assertIn("- Report:\n  ```text\n  Fixture report with no selected response.\n  ```", it540_content)

        self.assertTrue(self.ledger_path.is_file())
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger.get("version"), 1)
        self.assertIn("issue-00000000-0000-4000-8000-000000000001", ledger["filed"])
        self.assertIn("issue-00000000-0000-4000-8000-000000000002", ledger["filed"])
        self.assertEqual(
            ledger["filed"]["issue-00000000-0000-4000-8000-000000000001"]["target"],
            "cysa-plus/pending.md",
        )
        self.assertEqual(
            ledger["filed"]["issue-00000000-0000-4000-8000-000000000002"]["target"],
            "it540/pending.md",
        )

    def test_appending_to_existing_file_preserves_prefix(self) -> None:
        """Appending to an existing file with prior content and ## Entries keeps prior bytes."""
        self.make_course("cysa-plus")
        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        cysa_target.parent.mkdir(parents=True, exist_ok=True)
        prior_content = (
            "# cysa-plus pending feedback\n\n"
            "Pre-existing intro section.\n\n"
            "## Entries\n\n"
            "### 2026-01-01 — `q001` — source: in-app report, pack `pack1`, issue `issue-prior`\n\n"
            "- Reported: 2026-01-01T00:00:00Z; received on Mac: 2026-01-01T00:01:00Z\n"
            "- App: 1.0.0 (build 1); question type: `multiple_choice`\n"
            "- Selected response: (none)\n"
            "- Report:\n"
            "  ```text\n"
            "  Pre-existing entry.\n"
            "  ```\n"
        )
        prior_bytes = prior_content.encode("utf-8")
        cysa_target.write_bytes(prior_bytes)

        code = self.run_ingest([FIXTURE_PATH])
        self.assertEqual(code, 0)

        new_bytes = cysa_target.read_bytes()
        self.assertTrue(new_bytes.startswith(prior_bytes))
        self.assertIn(b"issue-00000000-0000-4000-8000-000000000001", new_bytes)

    def test_new_course_file_gets_header(self) -> None:
        """A new course file receives the required markdown header."""
        self.make_course("cysa-plus")
        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        self.assertFalse(cysa_target.exists())

        code = self.run_ingest([FIXTURE_PATH])
        self.assertEqual(code, 0)

        content = cysa_target.read_text(encoding="utf-8")
        expected_header = (
            "# cysa-plus pending feedback\n\n"
            "Intake for in-app question reports and study feedback. Append one entry per raised\n"
            "item with its date, question id, and source. Nothing here edits the pack.\n\n"
            "## Entries\n"
        )
        self.assertTrue(content.startswith(expected_header))

    def test_second_run_leaves_files_byte_identical(self) -> None:
        """A second run leaves every file byte-for-byte identical."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        self.assertEqual(self.run_ingest([FIXTURE_PATH]), 0)

        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        it540_target = self.feedback_root / "it540" / "pending.md"
        cysa_before = cysa_target.read_bytes()
        it540_before = it540_target.read_bytes()
        ledger_before = self.ledger_path.read_bytes()

        self.assertEqual(self.run_ingest([FIXTURE_PATH]), 0)

        self.assertEqual(cysa_target.read_bytes(), cysa_before)
        self.assertEqual(it540_target.read_bytes(), it540_before)
        self.assertEqual(self.ledger_path.read_bytes(), ledger_before)

    def test_entry_present_ledger_deleted_backfills_ledger(self) -> None:
        """Entry present in target file but ledger deleted backfills ledger without duplication."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        self.assertEqual(self.run_ingest([FIXTURE_PATH]), 0)

        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        it540_target = self.feedback_root / "it540" / "pending.md"
        cysa_before = cysa_target.read_bytes()
        it540_before = it540_target.read_bytes()

        self.ledger_path.unlink()
        self.assertFalse(self.ledger_path.exists())

        self.assertEqual(self.run_ingest([FIXTURE_PATH]), 0)

        self.assertEqual(cysa_target.read_bytes(), cysa_before)
        self.assertEqual(it540_target.read_bytes(), it540_before)

        self.assertTrue(self.ledger_path.exists())
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn("issue-00000000-0000-4000-8000-000000000001", ledger["filed"])
        self.assertIn("issue-00000000-0000-4000-8000-000000000002", ledger["filed"])

    def test_two_sources_overlapping_ids_deduplicates(self) -> None:
        """Two sources with overlapping issue IDs file each issue once."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        source1 = self.base_path / "source1.json"
        source2 = self.base_path / "source2.json"

        data1 = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        data2 = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

        data2["issues"]["issue-00000000-0000-4000-8000-000000000001"]["issue"]["description"] = (
            "Different payload for issue 1"
        )
        data2["issues"]["issue-00000000-0000-4000-8000-000000000003"] = {
            "reported_at_ms": 1800000240000,
            "received_at_ms": 1800000300000,
            "issue": {
                "schema_version": 1,
                "issue_id": "issue-00000000-0000-4000-8000-000000000003",
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so006",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "description": "Third issue report.",
            },
        }

        source1.write_text(json.dumps(data1), encoding="utf-8")
        source2.write_text(json.dumps(data2), encoding="utf-8")

        stderr_buf: list[str] = []
        old_stderr = sys.stderr
        try:
            from io import StringIO
            fake_stderr = StringIO()
            sys.stderr = fake_stderr
            code = self.run_ingest([source1, source2])
            stderr_buf.append(fake_stderr.getvalue())
        finally:
            sys.stderr = old_stderr

        self.assertEqual(code, 0)
        self.assertIn("Warning: issue 'issue-00000000-0000-4000-8000-000000000001' seen with different payload", stderr_buf[0])

        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        cysa_content = cysa_target.read_text(encoding="utf-8")

        self.assertEqual(cysa_content.count("issue `issue-00000000-0000-4000-8000-000000000001`"), 1)
        self.assertEqual(cysa_content.count("issue `issue-00000000-0000-4000-8000-000000000003`"), 1)
        self.assertIn("Fixture report: the keyed answer looks wrong.", cysa_content)
        self.assertNotIn("Different payload for issue 1", cysa_content)

    def test_hostile_description_sanitization(self) -> None:
        """Hostile description sanitizes cleanly without breaking heading or entry fences."""
        self.make_course("cysa-plus")
        hostile_file = self.base_path / "hostile.json"
        hostile_desc = (
            "Line 1\r\n"
            "`````\n"
            "### fake heading\n"
            "## Entries\n"
            "Control: \x1b[31mRed\x1b[0m \x00NUL\r\n"
            "End line."
        )

        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                "issue-hostile-0001": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-hostile-0001",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "qhostile",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": hostile_desc,
                    },
                }
            },
        }
        hostile_file.write_text(json.dumps(data), encoding="utf-8")

        code = self.run_ingest([hostile_file])
        self.assertEqual(code, 0)

        target = self.feedback_root / "cysa-plus" / "pending.md"
        content = target.read_text(encoding="utf-8")

        h3_matches = [line for line in content.splitlines() if re.match(r"^### ", line)]
        self.assertEqual(len(h3_matches), 1)

        h2_matches = [line for line in content.splitlines() if re.match(r"^## ", line)]
        self.assertEqual(len(h2_matches), 1)
        self.assertEqual(h2_matches[0], "## Entries")

        self.assertNotIn("\x1b", content)
        self.assertNotIn("\x00", content)
        self.assertNotIn("\r", content)

        self.assertIn("``````text", content)
        self.assertIn("  ### fake heading", content)
        self.assertIn("  ## Entries", content)

    def test_unrouted_course_routing_and_target_safety(self) -> None:
        """Unknown course and path-traversal IDs go to _unrouted inside feedback root."""
        test_file = self.base_path / "unrouted.json"
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                "issue-unrouted-01": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unrouted-01",
                        "course_id": "nonexistent-course",
                        "pack_id": "pack1",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report for non-existent course.",
                    },
                },
                "issue-unrouted-02": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unrouted-02",
                        "course_id": "../traversal",
                        "pack_id": "pack1",
                        "question_id": "q2",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report with path traversal.",
                    },
                },
                "issue-unrouted-03": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unrouted-03",
                        "course_id": "UPPERCASE",
                        "pack_id": "pack1",
                        "question_id": "q3",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report with uppercase course.",
                    },
                },
                "issue-unrouted-04": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unrouted-04",
                        "course_id": "x/y",
                        "pack_id": "pack1",
                        "question_id": "q4",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report with slash in course.",
                    },
                },
            },
        }
        test_file.write_text(json.dumps(data), encoding="utf-8")

        code = self.run_ingest([test_file])
        self.assertEqual(code, 0)

        unrouted_target = self.feedback_root / "_unrouted" / "pending.md"
        self.assertTrue(unrouted_target.is_file())
        content = unrouted_target.read_text(encoding="utf-8")
        self.assertIn("# _unrouted pending feedback", content)
        self.assertIn("issue `issue-unrouted-01`", content)
        self.assertIn("issue `issue-unrouted-02`", content)
        self.assertIn("issue `issue-unrouted-03`", content)
        self.assertIn("issue `issue-unrouted-04`", content)

        for root, _, files in os.walk(self.base_path):
            rel = Path(root).resolve().relative_to(self.base_path.resolve())
            for f in files:
                full_path = Path(root) / f
                if full_path.suffix == ".md":
                    self.assertTrue(str(full_path).startswith(str(self.feedback_root)))

    def test_validation_rules_and_rejections(self) -> None:
        """Top-level and per-issue validation errors are rejected or skipped as specified."""
        self.make_course("cysa-plus")

        bad_proto_file = self.base_path / "bad_proto.json"
        bad_proto_file.write_text(
            json.dumps({"protocol": "wrong-proto", "version": 1, "issues": {}}),
            encoding="utf-8",
        )
        valid_file = self.base_path / "valid.json"
        valid_file.write_text(
            json.dumps({
                "protocol": "quizzler-issue-inbox",
                "version": 1,
                "issues": {},
            }),
            encoding="utf-8",
        )

        from io import StringIO
        old_stderr = sys.stderr
        fake_stderr = StringIO()
        try:
            sys.stderr = fake_stderr
            code = self.run_ingest([bad_proto_file, valid_file])
        finally:
            sys.stderr = old_stderr

        self.assertEqual(code, 0)
        self.assertIn("protocol must equal 'quizzler-issue-inbox'", fake_stderr.getvalue())

        bad_issues_file = self.base_path / "bad_issues.json"
        bad_data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "issues": {
                "issue-unknown-key": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unknown-key",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                        "extra_unknown": "not_allowed",
                    },
                },
                "issue-bad-schema-ver": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 99,
                        "issue_id": "issue-bad-schema-ver",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                    },
                },
                "issue-bool-timestamp": {
                    "reported_at_ms": True,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-bool-timestamp",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                    },
                },
                "issue-key-mismatch": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "different-issue-id",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                    },
                },
                "issue-unsafe-qid": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-unsafe-qid",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "unsafe qid with spaces",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                    },
                },
                "bad key with spaces!": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "bad key with spaces!",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "q1",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "desc",
                    },
                },
            },
        }
        bad_issues_file.write_text(json.dumps(bad_data), encoding="utf-8")

        fake_stderr = StringIO()
        try:
            sys.stderr = fake_stderr
            code = self.run_ingest([bad_issues_file])
        finally:
            sys.stderr = old_stderr

        self.assertEqual(code, 0)
        err = fake_stderr.getvalue()
        self.assertIn("Warning: skipping invalid issue 'issue-unknown-key'", err)
        self.assertIn("Warning: skipping invalid issue 'issue-bad-schema-ver'", err)
        self.assertIn("Warning: skipping invalid issue 'issue-bool-timestamp'", err)
        self.assertIn("Warning: skipping invalid issue 'issue-key-mismatch'", err)
        self.assertIn("Warning: skipping invalid issue 'issue-unsafe-qid'", err)
        self.assertIn("Warning: skipping invalid issue at position 5", err)
        self.assertNotIn("bad key with spaces!", err)

    def test_dry_run_writes_nothing(self) -> None:
        """--dry-run reports what would be filed on stderr and writes no files."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        from io import StringIO
        old_stderr = sys.stderr
        fake_stderr = StringIO()
        try:
            sys.stderr = fake_stderr
            code = self.run_ingest([FIXTURE_PATH], dry_run=True)
        finally:
            sys.stderr = old_stderr

        self.assertEqual(code, 0)
        err = fake_stderr.getvalue()
        self.assertIn("Would file issue-00000000-0000-4000-8000-000000000001 -> cysa-plus/pending.md", err)
        self.assertIn("Would file issue-00000000-0000-4000-8000-000000000002 -> it540/pending.md", err)

        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        it540_target = self.feedback_root / "it540" / "pending.md"
        self.assertFalse(cysa_target.exists())
        self.assertFalse(it540_target.exists())
        self.assertFalse(self.ledger_path.exists())

    def test_holding_lock_subprocess_exits_3(self) -> None:
        """Subprocess exits with code 3 when another ingest holds the concurrency lock."""
        lock_file = open(self.feedback_root / ".ingest.lock", "a")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            proc = self.run_cli_subprocess(["--source", str(FIXTURE_PATH)])
            self.assertEqual(proc.returncode, 3)
            self.assertIn("another ingest is running", proc.stderr)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def test_description_and_selected_response_never_logged_or_printed(self) -> None:
        """Description and selected response text never appear in stdout, stderr, or logs."""
        self.make_course("cysa-plus")
        canary_desc = "SECRET_CANARY_DESCRIPTION_ALPHA_999"
        canary_resp = "SECRET_CANARY_RESPONSE_BETA_888"

        test_file = self.base_path / "canary.json"
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "issues": {
                "issue-canary-01": {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": "issue-canary-01",
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "selected_response": canary_resp,
                        "description": canary_desc,
                    },
                }
            },
        }
        test_file.write_text(json.dumps(data), encoding="utf-8")

        proc = self.run_cli_subprocess([
            "--source",
            str(test_file),
            "--debug",
            "--summary",
        ])
        self.assertEqual(proc.returncode, 0)

        self.assertNotIn(canary_desc, proc.stdout)
        self.assertNotIn(canary_resp, proc.stdout)
        self.assertNotIn(canary_desc, proc.stderr)
        self.assertNotIn(canary_resp, proc.stderr)

        log_file = self.log_dir / "quizzler.log"
        self.assertTrue(log_file.is_file())
        log_content = log_file.read_text(encoding="utf-8")
        self.assertNotIn(canary_desc, log_content)
        self.assertNotIn(canary_resp, log_content)

    def test_no_source_readable_exits_1(self) -> None:
        """When no source can be read from disk, exits with code 1."""
        proc = self.run_cli_subprocess([
            "--source",
            str(self.base_path / "nonexistent-1.json"),
            "--source",
            str(self.base_path / "nonexistent-2.json"),
        ])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Source not found", proc.stderr)
        self.assertIn("Error: no source could be read", proc.stderr)

    def test_summary_groups_by_course_pack_question(self) -> None:
        """--summary output groups by course, pack, question, and contains IDs only."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        proc = self.run_cli_subprocess([
            "--source",
            str(FIXTURE_PATH),
            "--summary",
        ])
        self.assertEqual(proc.returncode, 0)

        out = proc.stdout
        self.assertIn("cysa-plus", out)
        self.assertIn("cysa-plus-core", out)
        self.assertIn("so005 (1): issue-00000000-0000-4000-8000-000000000001", out)

        self.assertIn("it540", out)
        self.assertIn("it540-midterm-review-mod1-7", out)
        self.assertIn("s13 (1): issue-00000000-0000-4000-8000-000000000002", out)

        self.assertNotIn("Option B", out)
        self.assertNotIn("Fixture report", out)

    def test_existing_file_without_entries_appends_entries_heading(self) -> None:
        """Existing file without a line exactly ## Entries gets \n## Entries\n appended before entry."""
        self.make_course("cysa-plus")
        cysa_target = self.feedback_root / "cysa-plus" / "pending.md"
        cysa_target.parent.mkdir(parents=True, exist_ok=True)
        initial_content = "# cysa-plus pending feedback\n\nIntro without entries heading.\n"
        cysa_target.write_text(initial_content, encoding="utf-8")

        code = self.run_ingest([FIXTURE_PATH])
        self.assertEqual(code, 0)

        content = cysa_target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(initial_content + "\n## Entries\n\n### "))

    def test_dry_run_with_summary(self) -> None:
        """--dry-run with --summary outputs grouped ledger view to stdout while writing no files."""
        self.make_course("cysa-plus")
        self.make_course("it540")

        proc = self.run_cli_subprocess([
            "--source",
            str(FIXTURE_PATH),
            "--dry-run",
            "--summary",
        ])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("cysa-plus", proc.stdout)
        self.assertIn("cysa-plus-core", proc.stdout)
        self.assertIn("so005 (1): issue-00000000-0000-4000-8000-000000000001", proc.stdout)

        self.assertIn("Would file issue-00000000-0000-4000-8000-000000000001 -> cysa-plus/pending.md", proc.stderr)
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse((self.feedback_root / "cysa-plus" / "pending.md").exists())

    def test_refused_target_outside_feedback_root(self) -> None:
        """Target path resolving outside feedback root refuses and exits 1."""
        unrouted_symlink = self.feedback_root / "_unrouted"
        outside_dir = self.base_path / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(outside_dir, unrouted_symlink)

        with self.assertRaises(SystemExit) as ctx:
            ingest_mod.resolve_target("invalid/course", self.courses_root, self.feedback_root)
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
