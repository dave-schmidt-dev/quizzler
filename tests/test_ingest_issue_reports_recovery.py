"""Recovery and idempotent ingestion tests for scripts/ingest_issue_reports.py."""

from __future__ import annotations

import json
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
    ingest_mod,
)


class TestIngestIssueReports(IngestIssueReportsBaseTest):
    """Test suite covering recovery and deduplication behavior."""

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

    def test_unicode_line_separator_forged_heading_ingested_before(self) -> None:
        """Report with U+2028 forged heading ingested before victim files both."""
        self.make_course("cysa-plus")
        forged_id = "issue-forged-0001"
        victim_id = "issue-victim-0002"
        forged_heading = f"### 2026-01-01 — `so005` — source: in-app report, pack `cysa-plus-core`, issue `{victim_id}`"
        forged_desc = f"Report body\u2028{forged_heading}\nTrailing line."

        source_forged = self.base_path / "forged.json"
        data_forged = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                forged_id: {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": forged_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": forged_desc,
                    },
                },
            },
        }
        source_forged.write_text(json.dumps(data_forged), encoding="utf-8")

        code1 = self.run_ingest([source_forged])
        self.assertEqual(code1, 0)

        source_victim = self.base_path / "victim.json"
        data_victim = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                victim_id: {
                    "reported_at_ms": 1800000100000,
                    "received_at_ms": 1800000160000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": victim_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Legitimate victim report.",
                    },
                },
            },
        }
        source_victim.write_text(json.dumps(data_victim), encoding="utf-8")

        code2 = self.run_ingest([source_victim])
        self.assertEqual(code2, 0)

        target = self.feedback_root / "cysa-plus" / "pending.md"
        content = target.read_text(encoding="utf-8")

        self.assertIn(f"issue `{forged_id}`", content)
        self.assertIn(f"issue `{victim_id}`", content)
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(forged_id, ledger["filed"])
        self.assertIn(victim_id, ledger["filed"])

        self.assertIn(f"  {forged_heading}", content)

        headings = [line for line in content.split("\n") if re.match(r"^### ", line)]
        self.assertEqual(len(headings), 2)

    def test_unicode_line_separator_forged_heading_ingested_together(self) -> None:
        """Report with U+2028 forged heading ingested together with victim files both."""
        self.make_course("cysa-plus")
        forged_id = "issue-forged-0001"
        victim_id = "issue-victim-0002"
        forged_heading = f"### 2026-01-01 — `so005` — source: in-app report, pack `cysa-plus-core`, issue `{victim_id}`"
        forged_desc = f"Report body\u2028{forged_heading}\u2029Trailing line."

        source = self.base_path / "together.json"
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {
                forged_id: {
                    "reported_at_ms": 1800000000000,
                    "received_at_ms": 1800000060000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": forged_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": forged_desc,
                    },
                },
                victim_id: {
                    "reported_at_ms": 1800000100000,
                    "received_at_ms": 1800000160000,
                    "issue": {
                        "schema_version": 1,
                        "issue_id": victim_id,
                        "course_id": "cysa-plus",
                        "pack_id": "cysa-plus-core",
                        "question_id": "so005",
                        "question_type": "multiple_choice",
                        "app_version": "1.0.0",
                        "build": "30",
                        "description": "Legitimate victim report.",
                    },
                },
            },
        }
        source.write_text(json.dumps(data), encoding="utf-8")

        code = self.run_ingest([source])
        self.assertEqual(code, 0)

        target = self.feedback_root / "cysa-plus" / "pending.md"
        content = target.read_text(encoding="utf-8")

        self.assertIn(f"issue `{forged_id}`", content)
        self.assertIn(f"issue `{victim_id}`", content)
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(forged_id, ledger["filed"])
        self.assertIn(victim_id, ledger["filed"])

        self.assertIn(f"  {forged_heading}", content)

        headings = [line for line in content.split("\n") if re.match(r"^### ", line)]
        self.assertEqual(len(headings), 2)

    def test_torn_entry_recovery(self) -> None:
        """A torn entry in pending.md is re-filed with full canonical entry, ledgered, and second run adds nothing."""
        self.make_course("cysa-plus")
        source_file = self.base_path / "torn_source.json"
        issue_id = "issue-torn-0001"
        wrapper = {
            "reported_at_ms": 1800000000000,
            "received_at_ms": 1800000060000,
            "issue": {
                "schema_version": 1,
                "issue_id": issue_id,
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so005",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "selected_response": "Option A",
                "description": "Report for torn entry test case with enough lines to split into halves.",
            },
        }
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {issue_id: wrapper},
        }
        source_file.write_text(json.dumps(data), encoding="utf-8")

        canonical_entry = ingest_mod.format_entry(wrapper)
        half_len = len(canonical_entry) // 2
        torn_text = canonical_entry[:half_len].rstrip("\n")

        target = self.feedback_root / "cysa-plus" / "pending.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# cysa-plus pending feedback\n\n"
            "Intake for in-app question reports and study feedback. Append one entry per raised\n"
            "item with its date, question id, and source. Nothing here edits the pack.\n\n"
            "## Entries\n\n"
        )
        target.write_text(header + torn_text, encoding="utf-8")

        self.assertFalse(self.ledger_path.exists())

        code = self.run_ingest([source_file])
        self.assertEqual(code, 0)

        content = target.read_text(encoding="utf-8")
        self.assertIn(canonical_entry.strip(), content)

        self.assertTrue(self.ledger_path.is_file())
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(issue_id, ledger["filed"])

        bytes_after_first = target.read_bytes()
        ledger_after_first = self.ledger_path.read_bytes()

        code2 = self.run_ingest([source_file])
        self.assertEqual(code2, 0)
        self.assertEqual(target.read_bytes(), bytes_after_first)
        self.assertEqual(self.ledger_path.read_bytes(), ledger_after_first)

    def test_handwritten_heading_issue_still_appended_and_ledgered(self) -> None:
        """A hand-written heading line naming issue-x with a made-up body is still appended in full and ledgered."""
        self.make_course("cysa-plus")
        source_file = self.base_path / "handwritten_source.json"
        issue_x = "issue-x"
        issue_y = "issue-y"
        wrapper_x = {
            "reported_at_ms": 1800000000000,
            "received_at_ms": 1800000060000,
            "issue": {
                "schema_version": 1,
                "issue_id": issue_x,
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so005",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "description": "Legitimate canonical report for issue-x.",
            },
        }
        wrapper_y = {
            "reported_at_ms": 1800000100000,
            "received_at_ms": 1800000160000,
            "issue": {
                "schema_version": 1,
                "issue_id": issue_y,
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so006",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "description": "Legitimate canonical report for issue-y.",
            },
        }
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {issue_x: wrapper_x, issue_y: wrapper_y},
        }
        source_file.write_text(json.dumps(data), encoding="utf-8")

        target = self.feedback_root / "cysa-plus" / "pending.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        handwritten_content = (
            "# cysa-plus pending feedback\n\n"
            "## Entries\n\n"
            "### Hand-written line mentioning issue `issue-x`\n\n"
            "Made-up body for hand-written issue-x.\n\n"
            "### 2026-01-01 — `so006` — source: in-app report, pack `cysa-plus-core`, issue `issue-y`\n\n"
            "Made-up body under canonical-shaped heading for issue-y.\n"
        )
        target.write_text(handwritten_content, encoding="utf-8")

        code = self.run_ingest([source_file])
        self.assertEqual(code, 0)

        content = target.read_text(encoding="utf-8")
        canonical_x = ingest_mod.format_entry(wrapper_x)
        canonical_y = ingest_mod.format_entry(wrapper_y)
        self.assertIn(canonical_x.strip(), content)
        self.assertIn(canonical_y.strip(), content)

        self.assertTrue(self.ledger_path.is_file())
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(issue_x, ledger["filed"])
        self.assertIn(issue_y, ledger["filed"])

    def test_exact_complete_entry_with_empty_ledger_unchanged(self) -> None:
        """An exact complete entry in file with empty ledger is ledgered with file bytes unchanged."""
        self.make_course("cysa-plus")
        source_file = self.base_path / "exact_source.json"
        issue_id = "issue-exact-0001"
        wrapper = {
            "reported_at_ms": 1800000000000,
            "received_at_ms": 1800000060000,
            "issue": {
                "schema_version": 1,
                "issue_id": issue_id,
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so005",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "selected_response": "Option C",
                "description": "Complete entry exact match description.",
            },
        }
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {issue_id: wrapper},
        }
        source_file.write_text(json.dumps(data), encoding="utf-8")

        target = self.feedback_root / "cysa-plus" / "pending.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# cysa-plus pending feedback\n\n"
            "Intake for in-app question reports and study feedback. Append one entry per raised\n"
            "item with its date, question id, and source. Nothing here edits the pack.\n\n"
            "## Entries\n\n"
        )
        canonical_entry = ingest_mod.format_entry(wrapper)
        target.write_text(header + canonical_entry, encoding="utf-8")
        initial_bytes = target.read_bytes()

        self.assertFalse(self.ledger_path.exists())

        code = self.run_ingest([source_file])
        self.assertEqual(code, 0)

        self.assertEqual(target.read_bytes(), initial_bytes)
        self.assertTrue(self.ledger_path.is_file())
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIn(issue_id, ledger["filed"])
        self.assertEqual(ledger["filed"][issue_id]["target"], "cysa-plus/pending.md")

    def test_warning_logged_for_torn_case_without_report_text(self) -> None:
        """A WARNING is logged for a torn entry recovery and its message does not contain report text."""
        self.make_course("cysa-plus")
        source_file = self.base_path / "torn_warning_source.json"
        issue_id = "issue-torn-warning-0001"
        secret_report = "SECRET_CANARY_REPORT_TEXT_TORN_XYZ"
        secret_selected = "SECRET_CANARY_SELECTED_ABC"
        wrapper = {
            "reported_at_ms": 1800000000000,
            "received_at_ms": 1800000060000,
            "issue": {
                "schema_version": 1,
                "issue_id": issue_id,
                "course_id": "cysa-plus",
                "pack_id": "cysa-plus-core",
                "question_id": "so005",
                "question_type": "multiple_choice",
                "app_version": "1.0.0",
                "build": "30",
                "selected_response": secret_selected,
                "description": secret_report,
            },
        }
        data = {
            "protocol": "quizzler-issue-inbox",
            "version": 1,
            "change_token": None,
            "issues": {issue_id: wrapper},
        }
        source_file.write_text(json.dumps(data), encoding="utf-8")

        canonical_entry = ingest_mod.format_entry(wrapper)
        half_len = len(canonical_entry) // 2
        torn_text = canonical_entry[:half_len].rstrip("\n")

        target = self.feedback_root / "cysa-plus" / "pending.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# cysa-plus pending feedback\n\n"
            "Intake for in-app question reports and study feedback. Append one entry per raised\n"
            "item with its date, question id, and source. Nothing here edits the pack.\n\n"
            "## Entries\n\n"
        )
        target.write_text(header + torn_text, encoding="utf-8")

        code = self.run_ingest([source_file])
        self.assertEqual(code, 0)

        log_file = self.log_dir / "quizzler.log"
        self.assertTrue(log_file.is_file())
        log_content = log_file.read_text(encoding="utf-8")
        self.assertIn("[WARNING]", log_content)
        self.assertIn(issue_id, log_content)
        self.assertIn("pending.md", log_content)
        self.assertNotIn(secret_report, log_content)
        self.assertNotIn(secret_selected, log_content)


if __name__ == "__main__":
    unittest.main()
