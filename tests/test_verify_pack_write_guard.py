"""Certification write-guard and helper tests for ``verify_pack``."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.verify_pack_fixtures import (
    CLEAN_LAYER_A,
    CLEAN_Q,
    CLEAN_Q2,
    _Base,
    _clean_layer_c,
    _stale_certification,
    default_coverage_blueprint,
    fc,
    vp,
)

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
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
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
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
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
        self.assertFalse(fc.is_blocking({"severity": "nit", "confidence": "high"}))
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

    def test_subject_injected(self):
        p_plain = fc.build_prompt([{"id": "x"}])
        p_cissp = fc.build_prompt([{"id": "x"}], subject="CISSP")
        self.assertIn(fc.DEFAULT_SUBJECT, p_plain)
        self.assertNotIn("Security+", p_plain)
        self.assertIn("CISSP", p_cissp)
        self.assertNotIn("Security+", p_cissp)

    def test_normalizer_fails_safe(self):
        env = ('{"findings":[{"qid":"q","severity":"critical","confidence":"HIGH",'
               '"issue":"x"}],"checked":1}')
        f = fc.extract_findings(env)["findings"][0]
        self.assertEqual(f["severity"], "wrong-answer")  # unknown -> most severe
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(fc.is_blocking(f))

    def test_combined_report_finding_detail_is_byte_stable(self):
        finding = {
            "qid": "q1", "severity": "wrong-answer", "issue": "bad",
            "correction": "fix", "confidence": "high",
        }
        layer_c = _clean_layer_c(
            live=[finding], blocking=[finding], subject="Fixture")
        report = vp.format_report(
            "fixture.json", CLEAN_LAYER_A, layer_c, "not_ready")
        self.assertIn(
            "  [BLOCKING] [wrong-answer          ] q1 (confidence: high)\n"
            "      issue:      bad\n"
            "      correction: fix",
            report,
        )


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
            for method in ("external-layer-c-strict",):
                with self.subTest(method=method):
                    vp._write_certification(p, model="m", questions_examined=1,
                                            review_method=method)
                    cert = json.loads(p.read_text())["certification"]
                    self.assertEqual(cert["review_method"], method)
                    self.assertTrue(
                        vp.pack_cert.certification_fresh(json.loads(p.read_text())))
            with self.assertRaises(ValueError):
                vp._write_certification(p, model="m", questions_examined=1,
                                        review_method="external-layer-c-panel")

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
