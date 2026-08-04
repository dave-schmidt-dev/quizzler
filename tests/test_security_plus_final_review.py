"""Regression contract for the staged Security+ final-review consolidation.

The private question-pack tree is intentionally local, so this suite validates
the current machine's staged artifact and its pre/post-cutover topology.  It
does not invoke a reviewer, build the manifest, move packs, or read study-source
prose outside the Quizzler repository.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "question-packs" / "_staging" / "security-plus-final-review-2026-08-04"
STAGED_CANDIDATE = STAGING / "sy0-701-final-review.json"
CUTOVER_CANDIDATE = (
    ROOT
    / "question-packs"
    / "_staging"
    / "security-plus-cutover-2026-08-04"
    / "sy0-701"
    / "sy0-701-final-review.json"
)
ACTIVE_CANDIDATE = ROOT / "question-packs" / "sy0-701" / "sy0-701-final-review.json"
LEDGER = STAGING / "selection-ledger.json"
INVENTORY = STAGING / "source-inventory.json"
PATHS = STAGING / "cutover-paths.json"
MANIFEST = ROOT / "question-packs" / "manifest.json"

_script_spec = importlib.util.spec_from_file_location(
    "security_plus_consolidation", ROOT / "scripts" / "security_plus_consolidation.py"
)
consolidation = importlib.util.module_from_spec(_script_spec)
assert _script_spec.loader is not None
_script_spec.loader.exec_module(consolidation)

_cert_spec = importlib.util.spec_from_file_location(
    "pack_cert", ROOT / "scripts" / "pack_cert.py"
)
pack_cert = importlib.util.module_from_spec(_cert_spec)
assert _cert_spec.loader is not None
_cert_spec.loader.exec_module(pack_cert)

_codex_cert_spec = importlib.util.spec_from_file_location(
    "certify_codex_review", ROOT / "scripts" / "certify_codex_review.py"
)
certify_codex_review = importlib.util.module_from_spec(_codex_cert_spec)
assert _codex_cert_spec.loader is not None
_codex_cert_spec.loader.exec_module(certify_codex_review)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object at {path}")
    return value


class SecurityPlusFinalReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = (LEDGER, INVENTORY, PATHS)
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise unittest.SkipTest(
                "private Security+ staging artifacts are absent; local-only "
                "topology assertions are skipped: " + ", ".join(missing)
            )
        cls.ledger = _load(LEDGER)
        cls.inventory = _load(INVENTORY)
        cls.paths = _load(PATHS)
        if cls.paths.get("status") == "complete":
            cls.candidate_path = ACTIVE_CANDIDATE
        elif STAGED_CANDIDATE.exists():
            cls.candidate_path = STAGED_CANDIDATE
        else:
            cls.candidate_path = CUTOVER_CANDIDATE
        if not cls.candidate_path.exists():
            raise AssertionError(f"candidate is missing: {cls.candidate_path}")
        cls.candidate = _load(cls.candidate_path)

    def test_staged_candidate_is_discovery_excluded(self):
        if self.paths["status"] == "complete":
            self.assertEqual(self.candidate_path, ACTIVE_CANDIDATE)
        else:
            self.assertIn("_staging", self.candidate_path.parts)
            self.assertNotEqual(
                self.candidate_path.parent, ROOT / "question-packs" / "sy0-701"
            )
            self.assertEqual(self.paths["status"], "planned-not-executed")

    def test_candidate_and_ledger_reconcile_exactly(self):
        questions = self.candidate.get("questions")
        entries = self.ledger.get("entries")
        self.assertIsInstance(questions, list)
        self.assertIsInstance(entries, list)
        self.assertEqual(len(questions), 160)
        self.assertEqual(len(entries), 478)
        self.assertEqual(len({q.get("id") for q in questions}), 160)

        selected = [entry for entry in entries if entry.get("status") == "selected"]
        legacy = [entry for entry in selected if entry.get("origin") == "legacy"]
        remediation = [entry for entry in selected if entry.get("origin") == "remediation"]
        self.assertEqual(len(legacy), 136)
        self.assertEqual(len(remediation), 24)
        self.assertEqual(
            {entry.get("final_id") for entry in selected},
            {question.get("id") for question in questions},
        )

        dropped = [entry for entry in entries if entry.get("status") == "not_selected"]
        self.assertEqual(len(dropped), 454 - 136)
        self.assertTrue(all(entry.get("final_id") is None for entry in dropped))
        self.assertTrue(all(len(entry.get("source_sha256", "")) == 64 for entry in legacy))
        self.assertTrue(all(entry.get("review_disposition") == "pending-content-qa" for entry in selected))

    def test_every_official_objective_meets_four_question_floor(self):
        roster = self.ledger.get("objective_roster")
        self.assertEqual(len(roster), 28)
        objectives = {row.get("objective") for row in roster}
        self.assertEqual(len(objectives), 28)
        selected = [entry for entry in self.ledger["entries"] if entry.get("status") == "selected"]
        counts = Counter(entry.get("objective") for entry in selected)
        self.assertEqual(set(counts), objectives)
        for objective in sorted(objectives):
            with self.subTest(objective=objective):
                self.assertGreaterEqual(counts[objective], 4)

    def test_source_inventory_and_snapshot_validate(self):
        consolidation.validate_artifacts(
            self.candidate,
            self.ledger,
            self.inventory,
            check_snapshot=self.paths.get("status") != "complete",
        )
        if self.paths.get("status") == "complete":
            consolidation._validate_snapshot(self.inventory)
        self.assertEqual(self.inventory["course_counts"]["sy0-701"]["pack_count"], 28)
        self.assertEqual(self.inventory["course_counts"]["sy0-701"]["question_count"], 454)
        self.assertEqual(self.inventory["course_counts"]["itn260"]["pack_count"], 1)
        self.assertEqual(self.inventory["course_counts"]["itn260"]["question_count"], 113)

    def test_candidate_layer_a_has_no_live_findings(self):
        # L24 advisory findings are intentionally allowed by the linter's
        # contract; criticals and warnings are the staged gate.
        lint = consolidation.PROJECT_ROOT / "scripts" / "lint_packs.py"
        spec = importlib.util.spec_from_file_location("lint_packs", lint)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        result = module.lint_pack(self.candidate_path)
        live = [
            finding for finding in result.get("violations", [])
            if finding.get("severity") in {"critical", "warning"}
        ]
        self.assertEqual(live, [])

    def test_active_topology_matches_pre_or_post_cutover_contract(self):
        security_dir = ROOT / "question-packs" / "sy0-701"
        itn_dir = ROOT / "question-packs" / "itn260"
        security_packs = sorted(
            path for path in security_dir.glob("*.json") if path.name != "_course.json"
        )
        itn_packs = sorted(
            path for path in itn_dir.glob("*.json") if path.name != "_course.json"
        )
        manifest = _load(MANIFEST)
        courses = {course.get("id"): course for course in manifest.get("courses", [])}

        if self.paths.get("status") == "complete":
            self.assertEqual(len(security_packs), 1)
            self.assertEqual(len(itn_packs), 0)
            self.assertEqual(security_packs[0].name, "sy0-701-final-review.json")
            self.assertNotIn("itn260", courses)
            self.assertEqual(len(courses["sy0-701"]["modules"]), 1)
            self.assertEqual(courses["sy0-701"]["modules"][0]["questionCount"], 160)
        else:
            self.assertEqual(len(security_packs), 28)
            self.assertEqual(len(itn_packs), 1)
            self.assertEqual(len(courses["sy0-701"]["modules"]), 28)
            self.assertEqual(len(courses["itn260"]["modules"]), 1)

    def test_codex_local_certification_is_fresh_and_explicit(self):
        if self.paths.get("status") != "complete":
            self.skipTest("the Codex certification is exercised after cutover")
        self.assertTrue(pack_cert.certification_fresh(self.candidate))
        certification = self.candidate.get("certification")
        review = self.candidate.get("codex_review")
        self.assertEqual(
            certification.get("review_method"),
            pack_cert.CODEX_REVIEW_METHOD,
        )
        self.assertEqual(review.get("reviewer"), "codex")
        self.assertEqual(review.get("questions_examined"), 160)
        self.assertEqual(
            review.get("human_spotcheck"),
            "waived-by-David-explicit-cutover-request",
        )
        self.assertEqual(review.get("external_review"), {
            "claude_sonnet_5": "not-certified-incomplete",
            "agy_claude_sonnet_4_6": "not-run",
        })

    def test_codex_fallback_requires_explicit_human_waiver(self):
        with self.assertRaisesRegex(ValueError, "human-spotcheck-waived-by-david"):
            certify_codex_review.certify(
                self.candidate_path,
                human_spotcheck_waived=False,
            )


if __name__ == "__main__":
    unittest.main()
