"""Tests for scripts/ingest_issue_reports.py issue report ingestion."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.ingest_issue_report_fixtures import (
    FIXTURE_PATH,
    IngestIssueReportsBaseTest,
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


if __name__ == "__main__":
    unittest.main()
