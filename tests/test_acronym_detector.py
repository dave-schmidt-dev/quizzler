"""Unit tests for ``detect_unexpanded_acronyms`` in ``scripts/lint_packs.py``.

Covers the rule-4a acronym-expansion DETECTOR added as an importable helper
(docs/AUTHORING_GUIDE.md:13, question-packs/AUTHORING.md:139). This is not yet
a firing lint rule -- it is exercised directly against the function, not via
``lint_pack`` / ``run_lint`` / ``--all``. Fast, direct, deterministic: each
test calls the function with a fixture explanation string and asserts the
findings. No subprocess, no network. Mirrors the import style of
``tests/test_lint_packs.py``.

Run from the project root::

    python3 -m unittest tests.test_acronym_detector -v
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "lint_packs.py"

_spec = importlib.util.spec_from_file_location("lint_packs", SCRIPT_PATH)
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)


def _flagged(explanation: str) -> set[str]:
    """Upper-cased set of every acronym the detector flagged for *explanation*."""
    return {f["acronym"].upper() for f in lp.detect_unexpanded_acronyms(explanation)}


class TestDetectUnexpandedAcronymsTruePositives(unittest.TestCase):
    """Unexpanded first-use acronyms must be flagged."""

    def test_pci_dss_unexpanded(self):
        expl = "The PCI DSS framework governs cardholder data handling across the industry."
        self.assertTrue({"PCI", "DSS"} & _flagged(expl))

    def test_fips_140_2_unexpanded(self):
        expl = "The module complies with FIPS 140-2 for cryptographic validation."
        self.assertIn("FIPS", _flagged(expl))

    def test_oauth_unexpanded(self):
        expl = "The service uses OAuth to delegate authorization without sharing passwords."
        self.assertIn("OAUTH", _flagged(expl))

    def test_sle_unexpanded(self):
        expl = "Calculate SLE for the affected asset before estimating the annual loss."
        self.assertIn("SLE", _flagged(expl))

    def test_ale_unexpanded(self):
        expl = "ALE combines single loss and rate of occurrence into one number."
        self.assertIn("ALE", _flagged(expl))

    def test_slash_form_unexpanded(self):
        """Mixed slash-joined form (S/MIME) per the task spec, not just plain acronyms."""
        expl = "Configure S/MIME on the mail client to sign outgoing messages."
        self.assertIn("S/MIME", _flagged(expl))

    def test_dedupes_repeated_acronym_to_one_finding(self):
        expl = "SLE matters. SLE is computed first, then used to derive ALE."
        findings = lp.detect_unexpanded_acronyms(expl)
        sle_hits = [f for f in findings if f["acronym"] == "SLE"]
        self.assertEqual(len(sle_hits), 1)

    def test_index_points_at_first_occurrence(self):
        expl = "Before anything else, SLE must be computed."
        findings = lp.detect_unexpanded_acronyms(expl)
        sle = next(f for f in findings if f["acronym"] == "SLE")
        self.assertEqual(expl[sle["index"]:sle["index"] + 3], "SLE")


class TestDetectUnexpandedAcronymsTrueNegatives(unittest.TestCase):
    """Allowlisted terms and already-expanded first uses must NOT be flagged."""

    def test_allowlisted_it_not_flagged(self):
        expl = "The IT team handles the rollout; no other shorthand appears here."
        self.assertNotIn("IT", _flagged(expl))

    def test_allowlisted_id_not_flagged(self):
        expl = "Every record needs a unique ID before it is added to the pack."
        self.assertNotIn("ID", _flagged(expl))

    def test_expansion_after_acronym_form(self):
        """"ACRONYM (Full expansion)" form."""
        expl = "FIPS 140-2 (Federal Information Processing Standard) governs cryptographic modules."
        self.assertNotIn("FIPS", _flagged(expl))

    def test_expansion_before_acronym_form(self):
        """"Full expansion (ACRONYM)" form."""
        expl = "Payment Card Industry Data Security Standard (PCI DSS) applies to card processors."
        self.assertFalse({"PCI", "DSS"} & _flagged(expl))

    def test_oauth_expanded(self):
        expl = "OAuth (Open Authorization) removes the need to share a password with third parties."
        self.assertNotIn("OAUTH", _flagged(expl))

    def test_slash_form_expanded(self):
        expl = "S/MIME (Secure/Multipurpose Internet Mail Extensions) encrypts and signs email."
        self.assertNotIn("S/MIME", _flagged(expl))

    def test_empty_explanation_returns_no_findings(self):
        self.assertEqual(lp.detect_unexpanded_acronyms(""), [])

    def test_none_explanation_returns_no_findings(self):
        self.assertEqual(lp.detect_unexpanded_acronyms(None), [])

    def test_prose_with_no_acronyms_returns_no_findings(self):
        expl = "This option is wrong because it describes a different, unrelated control entirely."
        self.assertEqual(lp.detect_unexpanded_acronyms(expl), [])


class TestL24FiringRule(unittest.TestCase):
    """L24 firing-rule tests through ``lint_pack`` on complete pack fixtures.

    Each test builds a pack dict, writes it to a temp file, calls
    ``lp.lint_pack``, and inspects the violations.
    """

    def _lint(self, pack: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return lp.lint_pack(p)

    def test_unexpanded_acronym_produces_advisory(self):
        """Unexpanded acronym in explanation → advisory (rule L24, severity advisory)."""
        pack = {
            # L29 requires the metadata the iOS client needs to decode a pack.
            "pack_id": "l24-test", "subject": "L24 Fixture",
            "title": "L24 Fixture Pack", "version": 1,
            "questions": [
                {
                    "id": "q1", "type": "multiple_choice", "topic": "crypto",
                    "difficulty": "easy",
                    "prompt": "What does PCI DSS govern?",
                    "options": ["A", "B", "C", "D"], "answer": 0,
                    "explanation": "The PCI DSS framework governs cardholder data handling across the industry.",
                }
            ],
            "coverage_blueprint": [{"topic": "crypto", "min": 1}],
        }
        res = self._lint(pack)
        l24 = [v for v in res["violations"] if v["rule"] == "L24"]
        self.assertTrue(len(l24) >= 1, "expected at least one L24 advisory finding")
        self.assertTrue(all(v["severity"] == "advisory" for v in l24))
        self.assertTrue(all(v["rule"] == "L24" for v in l24))
        self.assertTrue(all(v["qid"] == "q1" for v in l24))
        self.assertIn("PCI", l24[0]["detail"])
        other = [v for v in res["violations"] if v["rule"] != "L24"]
        self.assertEqual(other, [], "fixture must produce no non-L24 violations")

    def test_expanded_acronym_produces_no_finding(self):
        """Already-expanded acronym in explanation → no L24 finding."""
        pack = {
            # L29 requires the metadata the iOS client needs to decode a pack.
            "pack_id": "l24-test", "subject": "L24 Fixture",
            "title": "L24 Fixture Pack", "version": 1,
            "questions": [
                {
                    "id": "q1", "type": "multiple_choice", "topic": "crypto",
                    "difficulty": "easy",
                    "prompt": "What is FIPS 140-2?",
                    "options": ["A", "B", "C", "D"], "answer": 0,
                    "explanation": "FIPS 140-2 (Federal Information Processing Standard) governs cryptographic modules.",
                }
            ],
            "coverage_blueprint": [{"topic": "crypto", "min": 1}],
        }
        res = self._lint(pack)
        l24 = [v for v in res["violations"] if v["rule"] == "L24"]
        self.assertEqual(l24, [], "expanded acronyms must not produce L24 findings")

    def test_no_explanation_produces_no_finding(self):
        """Question with no explanation → no L24 finding."""
        pack = {
            # L29 requires the metadata the iOS client needs to decode a pack.
            "pack_id": "l24-test", "subject": "L24 Fixture",
            "title": "L24 Fixture Pack", "version": 1,
            "questions": [
                {
                    "id": "q1", "type": "multiple_choice", "topic": "crypto",
                    "difficulty": "easy",
                    "prompt": "What is PCI DSS?",
                    "options": ["A", "B", "C", "D"], "answer": 0,
                }
            ],
            "coverage_blueprint": [{"topic": "crypto", "min": 1}],
        }
        res = self._lint(pack)
        l24 = [v for v in res["violations"] if v["rule"] == "L24"]
        self.assertEqual(l24, [], "no explanation must not produce L24 findings")

    def test_advisory_findings_do_not_block_verify_pack(self):
        """severity_to_exit on advisory-only findings returns exit code 0."""
        pack = {
            # L29 requires the metadata the iOS client needs to decode a pack.
            "pack_id": "l24-test", "subject": "L24 Fixture",
            "title": "L24 Fixture Pack", "version": 1,
            "questions": [
                {
                    "id": "q1", "type": "multiple_choice", "topic": "crypto",
                    "difficulty": "easy",
                    "prompt": "What is SLE?",
                    "options": ["A", "B", "C", "D"], "answer": 0,
                    "explanation": "Calculate SLE for the affected asset before estimating the annual loss.",
                }
            ],
            "coverage_blueprint": [{"topic": "crypto", "min": 1}],
        }
        res = self._lint(pack)
        violations = res["violations"]
        self.assertTrue(violations, "expected at least one advisory finding")
        non_advisory = [v for v in violations if v["severity"] != "advisory"]
        self.assertEqual(non_advisory, [], "fixture must produce ONLY advisory findings")
        self.assertEqual(lp.severity_to_exit(violations), 0)


if __name__ == "__main__":
    unittest.main()
