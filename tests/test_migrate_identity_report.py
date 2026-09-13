"""Unit coverage for the dry-run projection-identity report."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import migrate_identity_report as report
from scripts import pack_cert


class ProjectionDiffTests(unittest.TestCase):
    def _pack(self, question: dict) -> dict:
        return {
            "subject": "Example certification",
            "questions": [
                {
                    "id": "q1",
                    "type": "multiple_choice",
                    "prompt": "Which answer is correct?",
                    "options": ["A", "B"],
                    "answer": "A",
                    "explanation": "A is correct.",
                    **question,
                }
            ],
        }

    def test_questions_with_null_diagram_key_present_are_reported_changed(self):
        result = report.project_identity_report(self._pack({"diagram": None}))

        self.assertEqual(result["changed_ids"], ["q1"])
        self.assertEqual(result["changed_count"], 1)

    def test_questions_without_any_new_field_are_reported_unchanged(self):
        result = report.project_identity_report(self._pack({}))

        self.assertEqual(result["changed_ids"], [])
        self.assertEqual(result["changed_count"], 0)
        self.assertIn("byte-identical, 0 to re-review", report.render_report(
            Path("fixture.json"), result
        ))

    def test_null_aware_comparison_treats_a_null_valued_new_field_as_unchanged(self):
        result = report.project_identity_report(self._pack({"diagram_alt": None}))

        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(result["changed_count_null_aware"], 0)
        self.assertEqual(result["null_aware_changed_ids"], [])

    def test_relevant_fields_is_restored_after_each_comparison(self):
        original = pack_cert.RELEVANT_FIELDS

        report.project_identity_report(self._pack({"diagram": None}))
        self.assertEqual(pack_cert.RELEVANT_FIELDS, original)
        self.assertIs(pack_cert.RELEVANT_FIELDS, original)

        with patch.object(
            report.pack_cert,
            "build_question_stamps",
            side_effect=[{}, RuntimeError("candidate build failed")],
        ):
            with self.assertRaisesRegex(RuntimeError, "candidate build failed"):
                report.project_identity_report(self._pack({"diagram": None}))
        self.assertEqual(pack_cert.RELEVANT_FIELDS, original)
        self.assertIs(pack_cert.RELEVANT_FIELDS, original)


if __name__ == "__main__":
    unittest.main()
