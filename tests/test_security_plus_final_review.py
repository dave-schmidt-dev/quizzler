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

_lint_spec = importlib.util.spec_from_file_location(
    "lint_packs", ROOT / "scripts" / "lint_packs.py"
)
lint_packs = importlib.util.module_from_spec(_lint_spec)
assert _lint_spec.loader is not None
_lint_spec.loader.exec_module(lint_packs)


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

    def test_candidate_is_caught_by_the_l25_l26_quality_bar(self):
        """The pack that shipped clean is exactly what L25/L26 must now catch.

        This pack passed the install gate on 2026-08-04 with zero live Layer-A
        findings, then proved to be ~59% unusable: 54 prompts attribute the
        answer to a chapter the learner does not have, and 61 questions use
        formats (true_false, matching) that do not exist on SY0-701. The old
        rule set had no opinion about either. This asserts the new one does, so
        the regression cannot come back silently.
        """
        result = lint_packs.lint_pack(self.candidate_path)
        violations = result.get("violations", [])
        l25 = [f for f in violations if f.get("rule") == "L25"]
        l26 = [f for f in violations if f.get("rule") == "L26"]

        self.assertEqual(len(l25), 54, "L25 source-dependent prompt count drifted")
        self.assertEqual(len(l26), 61, "L26 exam-invalid type count drifted")
        for finding in l25 + l26:
            self.assertEqual(finding.get("severity"), "critical")

    def test_l25_and_l26_cannot_be_waived(self):
        """`lint_waivers` must not reopen the two rules that gate usability."""
        self.assertEqual(lint_packs.NON_WAIVABLE_RULES, frozenset({"L25", "L26"}))
        findings = [
            {"qid": "q1", "rule": "L25", "severity": "critical", "detail": "x"},
            {"qid": "q1", "rule": "L26", "severity": "critical", "detail": "y"},
        ]
        waivers = [
            {"rule": "L25", "qid": "q1", "reason": "author says it is fine"},
            {"rule": "L26", "reason": "pack-wide"},
        ]
        live, waived, hygiene = lint_packs._apply_waivers(findings, waivers)

        self.assertEqual(len(live), 2, "non-waivable findings must stay live")
        self.assertEqual(waived, [], "nothing may be suppressed")
        self.assertTrue(
            all("non-waivable" in h["detail"] for h in hygiene),
            "each ignored waiver must say why it did not apply",
        )
        # Reported once per waiver entry, not once per finding it would match.
        self.assertEqual(len(hygiene), 2)

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

        # On-disk pack topology is the cutover contract and holds either way.
        if self.paths.get("status") == "complete":
            self.assertEqual(len(security_packs), 1)
            self.assertEqual(len(itn_packs), 0)
            self.assertEqual(security_packs[0].name, "sy0-701-final-review.json")
            self.assertNotIn("itn260", courses)
        else:
            self.assertEqual(len(security_packs), 28)
            self.assertEqual(len(itn_packs), 1)

        # Manifest contents are now CONDITIONAL on the install gate. This test
        # used to assert sy0-701 was installed with 160 questions unconditionally
        # — which is exactly the assumption the gate exists to break. A pack that
        # fails certification must be absent from the manifest, so assert the
        # gate's verdict rather than a fixed topology.
        gate_ok = all(
            pack_cert.certification_fresh(_load(path)) for path in security_packs
        )
        if gate_ok:
            expected = 1 if self.paths.get("status") == "complete" else 28
            self.assertEqual(len(courses["sy0-701"]["modules"]), expected)
        elif manifest.get("strict_gate"):
            self.assertNotIn(
                "sy0-701", courses,
                "sy0-701 fails the install gate, so a STRICT build must not "
                "install it — a stale manifest keeps serving revoked packs",
            )
        else:
            # Built with --no-strict (the Playwright webServer does this on
            # purpose to get fixtures). An uncertified pack is expected here;
            # what must hold is that the artifact admits which mode made it.
            self.assertIn("strict_gate", manifest)

    def test_self_attested_local_review_is_no_longer_certifiable(self):
        """A pack reviewed only by its own author must fail the install gate.

        The pack carries `review_method: "codex-local-semantic-review"` and a
        `human_spotcheck: "waived-by-David-explicit-cutover-request"` string that
        was a hardcoded constant in the minting script — it recorded that a CLI
        flag was passed, not that a human consented. That method is no longer in
        APPROVED_REVIEW_METHODS, so the cert no longer validates.
        """
        if self.paths.get("status") != "complete":
            self.skipTest("certification topology is asserted after cutover")
        self.assertFalse(
            pack_cert.certification_fresh(self.candidate),
            "a codex-local self-review must not satisfy the install gate",
        )
        self.assertNotIn(
            self.candidate["certification"].get("review_method"),
            pack_cert.APPROVED_REVIEW_METHODS,
        )

    def test_certification_bypass_script_is_gone(self):
        """The script that minted the self-attested cert must not exist.

        Leaving a disabled bypass in the tree invites the next session to
        re-enable it. The constant it depended on is deleted too, so the branch
        cannot be reconstructed from a flag.
        """
        self.assertFalse(
            (ROOT / "scripts" / "certify_codex_review.py").exists(),
            "certify_codex_review.py must stay deleted",
        )
        self.assertFalse(hasattr(pack_cert, "CODEX_REVIEW_METHOD"))
        self.assertFalse(hasattr(pack_cert, "CODEX_HUMAN_SPOTCHECK_STATES"))

    def test_certification_requires_named_method_and_per_question_stamps(self):
        """Neither an unnamed review nor a stamp-less cert may pass.

        Previously an absent `question_stamps` registry fell back to
        aggregate-hash-only validation, so a cert that simply omitted the
        registry skipped per-question coverage entirely.
        """
        base = {
            "questions": [{"id": "q1", "type": "multiple_choice",
                           "prompt": "p", "options": ["a", "b"], "answer": 0}],
        }
        cert = {
            "certified": True,
            "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
            "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
            "blocking_count": 0,
            "questions_examined": 1,
            "review_method": "external-layer-c-strict",
        }
        pack = {**base, "certification": {**cert}}
        pack["certification"]["questions_hash"] = pack_cert.questions_hash(pack)

        # Named method + stamps -> fresh.
        pack["certification"]["question_stamps"] = pack_cert.build_question_stamps(pack)
        self.assertTrue(pack_cert.certification_fresh(pack))

        # Stamps removed -> no longer a legacy pass.
        stamped = pack["certification"].pop("question_stamps")
        self.assertFalse(pack_cert.certification_fresh(pack))
        pack["certification"]["question_stamps"] = stamped

        # Method absent -> fail.
        pack["certification"].pop("review_method")
        self.assertFalse(pack_cert.certification_fresh(pack))

        # Method present but not approved -> fail.
        pack["certification"]["review_method"] = "codex-local-semantic-review"
        self.assertFalse(pack_cert.certification_fresh(pack))


if __name__ == "__main__":
    unittest.main()
