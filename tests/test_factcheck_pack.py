"""Unit tests for ``scripts/factcheck_pack.py`` — the Layer-C LLM fact critic.

Covers the deterministic, pure helpers (batching, envelope unwrap, fenced-JSON
extraction, report formatting, field slimming). The LLM subprocess call itself
(``run_claude``) is not unit-tested — it is non-deterministic and costs money;
instead ``--dry-run`` keeps the prompt-building path exercisable offline.

Run from the project root::

    python3 -m unittest tests.test_factcheck_pack -v
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "factcheck_pack.py"

_spec = importlib.util.spec_from_file_location("factcheck_pack", SCRIPT_PATH)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


class BatchingTests(unittest.TestCase):
    def test_batches_split_evenly_and_remainder(self):
        self.assertEqual(fc.batched([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_size_zero_or_negative_is_one_chunk(self):
        self.assertEqual(fc.batched([1, 2, 3], 0), [[1, 2, 3]])
        self.assertEqual(fc.batched([1, 2, 3], -4), [[1, 2, 3]])

    def test_empty(self):
        self.assertEqual(fc.batched([], 5), [])


class EnvelopeTests(unittest.TestCase):
    def test_extracts_result_field(self):
        env = json.dumps({"type": "result", "result": "HELLO", "is_error": False})
        self.assertEqual(fc.parse_envelope(env), "HELLO")

    def test_non_envelope_text_passes_through(self):
        self.assertEqual(fc.parse_envelope("just text"), "just text")

    def test_empty(self):
        self.assertEqual(fc.parse_envelope("   "), "")


class ExtractFindingsTests(unittest.TestCase):
    def test_plain_json_object(self):
        text = '{"findings": [{"qid": "c1q1", "severity": "wrong-answer", "issue": "x", "correction": "y", "confidence": "high"}], "checked": 3}'
        out = fc.extract_findings(text)
        self.assertEqual(out["checked"], 3)
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["qid"], "c1q1")

    def test_strips_json_code_fence(self):
        text = '```json\n{"findings": [], "checked": 5}\n```'
        out = fc.extract_findings(text)
        self.assertEqual(out["checked"], 5)
        self.assertEqual(out["findings"], [])

    def test_finds_object_amid_prose(self):
        text = 'Here are the results:\n{"findings": [], "checked": 0}\nDone.'
        self.assertEqual(fc.extract_findings(text)["checked"], 0)

    def test_unknown_severity_coerced_to_nit(self):
        text = '{"findings": [{"qid": "q1", "severity": "bogus", "issue": "i"}]}'
        self.assertEqual(fc.extract_findings(text)["findings"][0]["severity"], "nit")

    def test_entries_without_qid_are_dropped(self):
        text = '{"findings": [{"severity": "nit", "issue": "no qid"}, {"qid": "q2", "issue": "ok"}]}'
        out = fc.extract_findings(text)
        self.assertEqual([f["qid"] for f in out["findings"]], ["q2"])

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            fc.extract_findings("the model refused to answer")


class ExtractModelTests(unittest.TestCase):
    def test_reads_model_from_modelusage(self):
        env = json.dumps({"result": "x", "modelUsage": {"claude-opus-4-8[1m]": {"inputTokens": 1}}})
        self.assertEqual(fc.extract_model(env), "claude-opus-4-8[1m]")

    def test_falls_back_to_model_field(self):
        self.assertEqual(fc.extract_model('{"result": "x", "model": "sonnet"}'), "sonnet")

    def test_none_when_unknown(self):
        self.assertIsNone(fc.extract_model("not json"))
        self.assertIsNone(fc.extract_model('{"result": "x"}'))


class FormatReportTests(unittest.TestCase):
    def test_clean_report(self):
        out = fc.format_report([], 20, [])
        self.assertIn("no suspect findings across 20", out)

    def test_model_surfaced_when_provided(self):
        out = fc.format_report([], 5, [], model="claude-opus-4-8[1m]")
        self.assertIn("via claude-opus-4-8[1m]", out)

    def test_findings_sorted_by_severity(self):
        findings = [
            {"qid": "q9", "severity": "nit", "issue": "minor", "correction": "", "confidence": "low"},
            {"qid": "q1", "severity": "wrong-answer", "issue": "bad", "correction": "fix", "confidence": "high"},
        ]
        out = fc.format_report(findings, 10, [])
        # wrong-answer must appear before nit
        self.assertLess(out.index("wrong-answer"), out.index("nit"))
        self.assertIn("q1", out)
        self.assertIn("correction: fix", out)

    def test_batch_errors_surfaced(self):
        out = fc.format_report([], 5, ["batch 1/1 [q1, q2]: claude exited 1"])
        self.assertIn("NOT checked", out)
        self.assertIn("batch 1/1", out)


class WaiverTests(unittest.TestCase):
    """factcheck_waivers — Layer-C's reviewed false-positive escape valve,
    mirroring lint_packs' lint_waivers (qid match, severity/issue_contains
    filters, live/waived/hygiene partitioning, stale + malformed + missing-reason
    hygiene). No LLM is involved — these exercise pure helpers only."""

    F1 = {"qid": "c1q1", "severity": "wrong-answer", "issue": "RSA is symmetric",
          "correction": "RSA is asymmetric", "confidence": "high"}
    F2 = {"qid": "c1q2", "severity": "nit", "issue": "minor wording",
          "correction": "", "confidence": "low"}

    # ── _waiver_matches ──────────────────────────────────────────────────────
    def test_matches_on_qid_only(self):
        self.assertTrue(fc._waiver_matches({"qid": "c1q1"}, self.F1))

    def test_no_match_on_different_qid(self):
        self.assertFalse(fc._waiver_matches({"qid": "other"}, self.F1))

    def test_severity_filter_narrows(self):
        self.assertTrue(fc._waiver_matches({"qid": "c1q1", "severity": "wrong-answer"}, self.F1))
        self.assertFalse(fc._waiver_matches({"qid": "c1q1", "severity": "nit"}, self.F1))

    def test_issue_contains_substring_case_insensitive(self):
        self.assertTrue(fc._waiver_matches({"qid": "c1q1", "issue_contains": "SYMMETRIC"}, self.F1))
        self.assertFalse(fc._waiver_matches({"qid": "c1q1", "issue_contains": "elliptic curve"}, self.F1))

    def test_non_dict_waiver_never_matches(self):
        self.assertFalse(fc._waiver_matches("c1q1", self.F1))

    # ── _apply_waivers ───────────────────────────────────────────────────────
    def test_partitions_live_and_waived(self):
        live, waived, hygiene = fc._apply_waivers(
            [self.F1, self.F2],
            [{"qid": "c1q1", "reason": "textbook simplification, verified"}],
        )
        self.assertEqual([f["qid"] for f in live], ["c1q2"])
        self.assertEqual([f["qid"] for f in waived], ["c1q1"])
        self.assertEqual(waived[0]["waived_reason"], "textbook simplification, verified")
        self.assertEqual(hygiene, [])

    def test_issue_contains_waives_one_finding_not_all_on_qid(self):
        other = {"qid": "c1q1", "severity": "nit", "issue": "spelling",
                 "correction": "", "confidence": "low"}
        live, waived, _ = fc._apply_waivers(
            [self.F1, other],
            [{"qid": "c1q1", "issue_contains": "symmetric", "reason": "verified ok"}],
        )
        self.assertEqual([f["issue"] for f in live], ["spelling"])
        self.assertEqual([f["issue"] for f in waived], ["RSA is symmetric"])

    def test_stale_waiver_reported_as_hygiene(self):
        live, waived, hygiene = fc._apply_waivers(
            [self.F1], [{"qid": "ghost", "reason": "not present"}])
        self.assertEqual(len(live), 1)
        self.assertEqual(waived, [])
        self.assertEqual(len(hygiene), 1)
        self.assertIn("stale", hygiene[0]["issue"])

    def test_missing_reason_reported_as_hygiene(self):
        live, waived, hygiene = fc._apply_waivers([self.F1], [{"qid": "c1q1"}])
        self.assertEqual(live, [])             # still suppressed
        self.assertEqual(len(waived), 1)
        self.assertEqual(len(hygiene), 1)
        self.assertIn("reason", hygiene[0]["issue"])

    def test_malformed_non_dict_entry_flagged_and_suppresses_nothing(self):
        live, waived, hygiene = fc._apply_waivers([self.F1], ["c1q1"])
        self.assertEqual(len(live), 1)         # the bare string suppresses nothing
        self.assertEqual(waived, [])
        self.assertEqual(len(hygiene), 1)
        self.assertIn("not an object", hygiene[0]["issue"])

    # ── load_waivers ─────────────────────────────────────────────────────────
    def _load(self, pack: dict) -> list:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return fc.load_waivers(p)

    def test_load_waivers_returns_list(self):
        out = self._load({"factcheck_waivers": [{"qid": "c1q1", "reason": "x"}], "questions": []})
        self.assertEqual(out, [{"qid": "c1q1", "reason": "x"}])

    def test_load_waivers_missing_key_is_empty(self):
        self.assertEqual(self._load({"questions": []}), [])

    def test_load_waivers_non_list_is_empty(self):
        self.assertEqual(self._load({"factcheck_waivers": "nope", "questions": []}), [])


class LoadAndPromptTests(unittest.TestCase):
    def test_load_slims_to_relevant_fields(self):
        pack = {
            "pack_id": "t",
            "questions": [{
                "id": "q1", "type": "multiple_choice", "topic": "x",
                "difficulty": "easy", "prompt": "p", "options": ["a", "b"],
                "answer": 0, "explanation": "e",
                "diagram": "<svg/>", "tags": ["drop", "me"],
            }],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            qs = fc.load_questions(p)
        self.assertEqual(len(qs), 1)
        self.assertNotIn("diagram", qs[0])
        self.assertNotIn("tags", qs[0])
        self.assertNotIn("difficulty", qs[0])  # not in RELEVANT_FIELDS
        self.assertEqual(qs[0]["explanation"], "e")

    def test_build_prompt_includes_schema_and_questions(self):
        prompt = fc.build_prompt([{"id": "q1", "prompt": "What is 2+2?"}])
        self.assertIn("SY0-701", prompt)
        self.assertIn('"findings"', prompt)  # output-schema instruction present
        self.assertIn("q1", prompt)


if __name__ == "__main__":
    unittest.main()
