"""Unit tests for ``scripts/verify_pack.py`` — the pack-readiness gate.

verify_pack runs Layer A (structure) + Layer C (factual LLM critic) as one hard
gate. Layer C's LLM subprocess is NON-deterministic and costs money, so every
test here MOCKS ``factcheck_pack.run_claude`` (and ``shutil.which`` so the gate
believes the ``claude`` CLI is present) to return a canned ``claude
--output-format json`` envelope. NO real LLM or network call happens.

Throw-away temp packs are written per-test (mirroring test_build_manifest.py) so
the real ``question-packs/`` tree is never touched.

Run from the project root::

    python3 -m unittest tests.test_verify_pack -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "verify_pack.py"

_spec = importlib.util.spec_from_file_location("verify_pack", SCRIPT_PATH)
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)
# verify_pack imports factcheck_pack by path during its own load; reach the same
# module object so patches land where run_layer_c looks them up.
fc = vp.factcheck_pack


# A lint-clean MC question: numeric distractors carry no tokens (L10 has nothing
# to assess); explanation/topic/difficulty present satisfy L12.
CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def default_coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def envelope(findings: list[dict]) -> str:
    """Build a canned ``claude --output-format json`` envelope whose `result` is
    the critic's JSON object, exactly what run_claude returns as stdout."""
    inner = json.dumps({"findings": findings, "checked": 99})
    return json.dumps({"type": "result", "result": inner,
                       "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, **payload) -> Path:
        payload.setdefault("pack_id", "verify-test")
        payload.setdefault("questions", [dict(CLEAN_Q)])
        if "coverage_blueprint" not in payload:
            payload["coverage_blueprint"] = default_coverage_blueprint(payload["questions"])
        p = self.tmp_path / "pack.json"
        p.write_text(json.dumps(payload))
        return p

    def write_pack_without_blueprint(self, **payload) -> Path:
        pack = self.write_pack(**payload)
        data = json.loads(pack.read_text())
        data.pop("coverage_blueprint", None)
        pack.write_text(json.dumps(data))
        return pack

    def run_main(self, argv: list[str], findings: list[dict] | None = None):
        """Invoke verify_pack.main with run_claude + which mocked. `findings` is
        the canned Layer-C critic output (None → no findings)."""
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", return_value=envelope(findings or [])), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()


class CleanPackTests(_Base):
    def test_clean_pack_no_findings_is_ready(self):
        pack = self.write_pack()
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        self.assertIn("Layer A (structure): clean", out)
        self.assertIn("Layer C (factual): clean", out)


class LayerATests(_Base):
    def test_layer_a_critical_blocks(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        pack = self.write_pack(questions=[dirty])
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer-A", out)
        self.assertIn("L12", out)


class LayerCTests(_Base):
    FINDING = {"qid": "q1", "severity": "wrong-answer",
               "issue": "two plus two is five, not four",
               "correction": "the answer is four", "confidence": "high"}

    def test_layer_c_finding_blocks(self):
        pack = self.write_pack()
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer C (factual): 1 live finding", out)
        self.assertIn("PACK NOT READY: 0 Layer-A + 1 blocking Layer-C finding(s)", out)

    def test_layer_c_finding_waived_is_ready(self):
        pack = self.write_pack(factcheck_waivers=[
            {"qid": "q1", "reason": "intentional trick distractor; verified by author"},
        ])
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        # The waiver is blanket (qid-only), so FIX G adds a non-blocking hygiene
        # nudge alongside the waive — the pack is still READY.
        self.assertIn("Layer C (factual): clean (1 waived, 1 hygiene)", out)


class NoFactcheckTests(_Base):
    def test_no_factcheck_skips_layer_c_and_prints_note(self):
        pack = self.write_pack()
        # No run_claude mock needed — Layer C must not run at all. If it did, the
        # real `claude` CLI absence would surface; we assert the skip note instead.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        # FIX D: --no-factcheck must NEVER return 0 (a CI `verify_pack
        # --no-factcheck && deploy` would otherwise ship an unfactchecked pack).
        # A clean structure-only run returns the distinct exit code 3.
        self.assertEqual(rc, 3)
        # Structure-only must NOT claim full readiness — it never ran Layer C.
        self.assertIn("NOT certified ready", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())
        self.assertIn("structure-only (Layer C skipped) — this is NOT the full readiness gate.",
                      out.getvalue())

    def test_no_factcheck_still_blocks_on_layer_a(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")
        pack = self.write_pack(questions=[dirty])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())


class LayerAHygieneTests(_Base):
    def test_stale_lint_waiver_is_hygiene_not_blocking(self):
        # FIX E: a stale lint_waiver (matches no finding) is a list-rot nudge,
        # not a content defect. lint_pack folds it into `violations`, but the
        # readiness gate must NOT block an otherwise-clean pack on it — it is
        # surfaced as non-blocking hygiene, exactly like Layer C's own hygiene.
        pack = self.write_pack(lint_waivers=[
            {"rule": "L10", "qid": "ghost", "reason": "no longer needed"},
        ])
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        self.assertIn("Layer A (structure): clean", out)
        # The stale waiver is still surfaced (just not blocking).
        self.assertIn("hygiene", out)
        self.assertIn("WAIVER", out)


class L23CoverageCriticalTests(_Base):
    """L23 absent-`coverage_blueprint` is CRITICAL and blocks readiness."""

    def test_absent_blueprint_critical_blocks_full_gate(self):
        pack = self.write_pack_without_blueprint()
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("L23", out)

    def test_absent_blueprint_critical_blocks_no_factcheck(self):
        pack = self.write_pack_without_blueprint()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())

    def test_blueprint_undercoverage_is_blocking_critical(self):
        pack = self.write_pack(coverage_blueprint=[{"topic": "unseen-topic", "min": 1}])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())
        self.assertIn("L23", out.getvalue())


class EmptyPackTests(_Base):
    def test_empty_questions_not_ready_full_gate(self):
        # FIX B: a pack with zero questions has nothing for the critic to check;
        # the gate must NOT certify it. Exit 2 (NOT READY), not 0.
        pack = self.write_pack(questions=[])
        rc, _out, err = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("no questions", err)

    def test_empty_questions_not_ready_under_no_factcheck(self):
        # FIX B: the guard also fires under --no-factcheck (where Layer C never
        # loads questions) — an empty pack is never ready, even structure-only.
        pack = self.write_pack(questions=[])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("no questions", err.getvalue())


class LayerCCoverageTests(_Base):
    """FIX A: a Layer-C run that did not actually inspect every question must
    NEVER certify PACK READY. run_claude is mocked — NO real LLM/network."""

    @staticmethod
    def _envelope(findings: list[dict], checked) -> str:
        inner = json.dumps({"findings": findings, "checked": checked})
        return json.dumps({"type": "result", "result": inner,
                           "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})

    def test_partial_coverage_is_not_ready(self):
        # Critic self-reports checked=0 of 1 → coverage gap → NOT READY.
        pack = self.write_pack(questions=[dict(CLEAN_Q)])
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude",
                          return_value=self._envelope([], checked=0)), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack)])
        self.assertEqual(rc, 2)
        self.assertIn("coverage incomplete", out.getvalue())
        self.assertIn("1 question(s) unchecked", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())

    def test_one_failed_batch_is_not_ready_not_operational(self):
        # 2 questions @ batch-size 1 → 2 batches. One batch times out, one is
        # clean. A PARTIAL failure must be NOT READY (exit 2), distinct from the
        # all-failed operational error (exit 1). The unchecked batch must not ship.
        q2 = dict(CLEAN_Q)
        q2["id"] = "q2"
        pack = self.write_pack(questions=[dict(CLEAN_Q), q2])
        clean_env = envelope([])  # checked=99 ≥ 1 → no gap on the good batch

        def fake_run_claude(prompt, model, timeout):
            if "q2" in prompt:
                return clean_env
            raise RuntimeError("claude call timed out after 180s")

        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", side_effect=fake_run_claude), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack), "--batch-size", "1"])
        self.assertEqual(rc, 2)
        self.assertIn("NOT checked", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())


class OperationalErrorTests(_Base):
    def test_missing_pack_is_operational_error(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = vp.main([str(self.tmp_path / "does-not-exist.json")])
        self.assertEqual(rc, 1)
        self.assertIn("pack not found", err.getvalue())

    def test_missing_claude_cli_is_operational_error(self):
        pack = self.write_pack()
        err = io.StringIO()
        with patch.object(vp.shutil, "which", return_value=None):
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = vp.main([str(pack)])
        self.assertEqual(rc, 1)
        self.assertIn("claude", err.getvalue())


# A second lint-clean question, distinct from CLEAN_Q so a two-question pack stays
# Layer-A clean (no duplicate-stem/answer tells) for the subset tests below.
CLEAN_Q2 = {
    "id": "q2", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 3 times 3?",
    "options": ["9", "6", "12", "3"], "answer": 0,
    "explanation": "Three times three is nine.",
}


class SeverityGateTests(_Base):
    """The severity gate (FIX #1 of the 2026-07 hardening): only a wrong-answer OR
    any high-confidence finding BLOCKS; the probabilistic nit/ambiguous tail is
    advisory (exit 0). This is the behavior that ended the 7-run non-convergence."""

    def test_advisory_only_finding_is_ready(self):
        adv = {"qid": "q1", "severity": "nit", "issue": "off-axis distractor",
               "correction": "swap it", "confidence": "medium"}
        rc, out, _ = self.run_main([str(self.write_pack())], findings=[adv])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        self.assertIn("advisory", out)

    def test_high_confidence_nit_blocks(self):
        # ANY high-confidence finding blocks, even severity "nit".
        f = {"qid": "q1", "severity": "nit", "issue": "the NAV acronym is wrong",
             "correction": "Network Allocation Vector", "confidence": "high"}
        rc, out, _ = self.run_main([str(self.write_pack())], findings=[f])
        self.assertEqual(rc, 2)
        self.assertIn("1 blocking", out)

    def test_strict_makes_advisory_block(self):
        adv = {"qid": "q1", "severity": "ambiguous", "issue": "two defensible answers",
               "correction": "tighten stem", "confidence": "low"}
        rc, out, _ = self.run_main([str(self.write_pack()), "--strict"], findings=[adv])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)

    def test_mislabeled_finding_fails_safe(self):
        # A garbled severity/confidence must BLOCK (fail-safe), never slip to
        # advisory — the gate trusts these labels, so unknown = most severe.
        f = {"qid": "q1", "severity": "totally-bogus", "issue": "x",
             "correction": "y", "confidence": "who-knows"}
        rc, _out, _ = self.run_main([str(self.write_pack())], findings=[f])
        self.assertEqual(rc, 2)


class SubsetTests(_Base):
    """--only re-checks a subset for shrinking confirmation runs but NEVER certifies
    the whole pack (FIX #2): a clean subset exits 3, not 0."""

    def _pack(self):
        return self.write_pack(questions=[dict(CLEAN_Q), dict(CLEAN_Q2)])

    def test_clean_subset_is_not_certification(self):
        rc, out, _ = self.run_main([str(self._pack()), "--only", "q1"], findings=[])
        self.assertEqual(rc, 3)  # exit 3, NOT 0 — mirrors --no-factcheck
        self.assertIn("SUBSET RECHECK PASSED", out)
        self.assertNotIn("PACK READY", out)

    def test_blocking_in_subset_still_blocks(self):
        f = {"qid": "q1", "severity": "wrong-answer", "issue": "x",
             "correction": "y", "confidence": "high"}
        rc, out, _ = self.run_main([str(self._pack()), "--only", "q1"], findings=[f])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)

    def test_unmatched_only_is_error(self):
        rc, _out, err = self.run_main([str(self._pack()), "--only", "nonesuch"], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("none of the --only ids matched", err)


CLEAN_LAYER_A = {"live": [], "waived": [], "hygiene": []}


def _clean_layer_c(**overrides) -> dict:
    base = {
        "live": [], "waived": [], "hygiene": [],
        "errors": [], "coverage_gaps": [],
        "questions_unchecked": 0,
        "model": "claude-sonnet-5",
        "total": 1,
        "source_directive_active": False,
    }
    base.update(overrides)
    return base


def _stale_certification(*, model: str = "claude-sonnet-5", examined: int = 1) -> dict:
    """Pre-existing cert block (stale hash) for write-guard preservation tests."""
    return {
        "certified": True,
        "hash_schema_version": vp.pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": vp.pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": "2020-01-01T00:00:00+00:00",
        "questions_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "critic_model": model,
        "blocking_count": 0,
        "questions_examined": examined,
    }


class WriteGuardTests(_Base):
    """CV-6: certification is stamped only on full-gate READY (exit 0, no --only)."""

    def test_write_certification_stamps_matching_hash_and_versions(self):
        pack = self.write_pack()
        data = json.loads(pack.read_text())
        expected_hash = vp.pack_cert.questions_hash(data)

        vp._write_certification(pack, model="claude-sonnet-5", questions_examined=1)

        cert = json.loads(pack.read_text())["certification"]
        self.assertTrue(cert["certified"])
        self.assertEqual(cert["questions_hash"], expected_hash)
        self.assertEqual(cert["blocking_count"], 0)
        self.assertEqual(cert["hash_schema_version"], vp.pack_cert.HASH_SCHEMA_VERSION)
        self.assertEqual(cert["critic_contract_version"], vp.pack_cert.CRITIC_CONTRACT_VERSION)
        self.assertEqual(cert["critic_model"], "claude-sonnet-5")
        self.assertEqual(cert["questions_examined"], 1)
        self.assertIsInstance(cert["verified_at"], str)

    def test_only_subset_does_not_write_certification(self):
        pack = self.write_pack(questions=[dict(CLEAN_Q), dict(CLEAN_Q2)])
        original_text = pack.read_text()
        layer_c = _clean_layer_c(total=1)

        out, err = io.StringIO(), io.StringIO()
        with patch.object(vp, "run_layer_a", return_value=CLEAN_LAYER_A), \
             patch.object(vp, "run_layer_c", return_value=layer_c), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack), "--only", "q1"])

        self.assertEqual(rc, 3)
        self.assertEqual(pack.read_text(), original_text)
        self.assertNotIn("certification", json.loads(pack.read_text()))

    def test_only_subset_preserves_existing_certification(self):
        existing = _stale_certification(examined=2)
        pack = self.write_pack(
            questions=[dict(CLEAN_Q), dict(CLEAN_Q2)],
            certification=dict(existing),
        )
        layer_c = _clean_layer_c(total=1)

        with patch.object(vp, "run_layer_a", return_value=CLEAN_LAYER_A), \
             patch.object(vp, "run_layer_c", return_value=layer_c), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            rc = vp.main([str(pack), "--only", "q1"])

        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(pack.read_text())["certification"], existing)

    def test_no_factcheck_does_not_write_certification(self):
        pack = self.write_pack()
        original_text = pack.read_text()

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])

        self.assertEqual(rc, 3)
        self.assertEqual(pack.read_text(), original_text)
        self.assertNotIn("certification", json.loads(pack.read_text()))

    def test_no_factcheck_preserves_existing_certification(self):
        existing = _stale_certification()
        pack = self.write_pack(certification=dict(existing))

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])

        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(pack.read_text())["certification"], existing)

    def test_not_ready_layer_a_preserves_existing_certification(self):
        existing = _stale_certification()
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")
        pack = self.write_pack(questions=[dirty], certification=dict(existing))

        rc, _, _ = self.run_main([str(pack)], findings=[])

        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(pack.read_text())["certification"], existing)

    def test_not_ready_layer_c_preserves_existing_certification(self):
        existing = _stale_certification()
        pack = self.write_pack(certification=dict(existing))
        finding = {"qid": "q1", "severity": "wrong-answer",
                   "issue": "bad", "correction": "fix", "confidence": "high"}

        rc, _, _ = self.run_main([str(pack)], findings=[finding])

        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(pack.read_text())["certification"], existing)


class FactcheckHelperTests(unittest.TestCase):
    """Unit coverage for the new factcheck_pack primitives the gate leans on."""

    def test_is_blocking(self):
        self.assertTrue(fc.is_blocking({"severity": "wrong-answer", "confidence": "low"}))
        self.assertTrue(fc.is_blocking({"severity": "nit", "confidence": "high"}))
        self.assertFalse(fc.is_blocking({"severity": "nit", "confidence": "medium"}))
        self.assertFalse(fc.is_blocking({"severity": "ambiguous", "confidence": "low"}))

    def test_blocking_findings_strict(self):
        live = [{"severity": "nit", "confidence": "medium"},
                {"severity": "wrong-answer", "confidence": "low"}]
        self.assertEqual(len(fc.blocking_findings(live)), 1)
        self.assertEqual(len(fc.blocking_findings(live, strict=True)), 2)

    def test_source_directive_injected(self):
        p_plain = fc.build_prompt([{"id": "x"}])
        p_src = fc.build_prompt([{"id": "x"}], source_directive="Ciampa 8e is authoritative.")
        self.assertNotIn("COURSE SOURCE", p_plain)
        self.assertIn("COURSE SOURCE", p_src)
        self.assertIn("Ciampa 8e is authoritative.", p_src)
        self.assertIn("<question_data>", p_plain)  # header stays well-formed

    def test_normalizer_fails_safe(self):
        env = ('{"findings":[{"qid":"q","severity":"critical","confidence":"HIGH",'
               '"issue":"x"}],"checked":1}')
        f = fc.extract_findings(env)["findings"][0]
        self.assertEqual(f["severity"], "wrong-answer")  # unknown -> most severe
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(fc.is_blocking(f))


class CertificationReviewMethodTests(unittest.TestCase):
    """Only a named, approved review method can be stamped onto a pack.

    The deleted `certify_codex_review.py` minted certifications carrying a
    self-attested `review_method` of its own invention. Removing that script is
    not enough on its own — `_write_certification` is the remaining write path,
    so it has to refuse any method that `pack_cert` does not recognize.
    """

    def _pack(self, tmp: Path) -> Path:
        payload = {
            "title": "t",
            "coverage_blueprint": default_coverage_blueprint([CLEAN_Q]),
            "questions": [dict(CLEAN_Q)],
        }
        p = tmp / "pack.json"
        p.write_text(json.dumps(payload))
        return p

    def test_approved_method_is_written_through(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._pack(Path(d))
            for method in sorted(vp.pack_cert.APPROVED_REVIEW_METHODS):
                with self.subTest(method=method):
                    vp._write_certification(p, model="m", questions_examined=1,
                                            review_method=method)
                    cert = json.loads(p.read_text())["certification"]
                    self.assertEqual(cert["review_method"], method)
                    self.assertTrue(
                        vp.pack_cert.certification_fresh(json.loads(p.read_text())))

    def test_unapproved_method_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._pack(Path(d))
            for method in ("codex-local-semantic-review", "self-review", "", None):
                with self.subTest(method=method):
                    with self.assertRaises(ValueError):
                        vp._write_certification(p, model="m", questions_examined=1,
                                                review_method=method)
            self.assertNotIn("certification", json.loads(p.read_text()),
                             "a refused method must not leave a partial cert behind")


if __name__ == "__main__":
    unittest.main()
