"""CLI, concurrency, and argument parsing tests for scripts/ingest_issue_reports.py."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.ingest_issue_report_fixtures import (
    FIXTURE_PATH,
    IngestIssueReportsBaseTest,
    ingest_mod,
)


class TestIngestIssueReports(IngestIssueReportsBaseTest):
    """Test suite covering CLI flags, process isolation, and argument handling."""

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

    def test_timestamp_out_of_range_skipped(self) -> None:
        """Issues with reported_at_ms of -1 or 10**17 are skipped as invalid while valid issue is filed."""
        self.make_course("cysa-plus")
        source_file = self.base_path / "timestamps.json"
        valid_id = "issue-valid-timestamp"
        neg_id = "issue-negative-timestamp"
        huge_id = "issue-huge-timestamp"

        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                neg_id: {
                    "reported_at_ms": -1,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": neg_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report with negative timestamp.",
                    },
                },
                huge_id: {
                    "reported_at_ms": 10**17,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": huge_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Report with huge timestamp.",
                    },
                },
                valid_id: {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": valid_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Valid issue report.",
                    },
                },
            },
        }
        source_file.write_text(json.dumps(data), encoding="utf-8")

        from io import StringIO
        old_stderr = sys.stderr
        fake_stderr = StringIO()
        try:
            sys.stderr = fake_stderr
            code = self.run_ingest([source_file])
        finally:
            sys.stderr = old_stderr

        self.assertEqual(code, 0)
        err = fake_stderr.getvalue()
        self.assertIn(f"Warning: skipping invalid issue '{neg_id}': timestamp out of range", err)
        self.assertIn(f"Warning: skipping invalid issue '{huge_id}': timestamp out of range", err)

        target = self.feedback_root / "cysa-plus" / "pending.md"
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")

        self.assertIn(f"issue `{valid_id}`", content)
        self.assertNotIn(neg_id, content)
        self.assertNotIn(huge_id, content)

        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(valid_id, ledger["filed"])
        self.assertNotIn(neg_id, ledger["filed"])
        self.assertNotIn(huge_id, ledger["filed"])


if __name__ == "__main__":
    unittest.main()
