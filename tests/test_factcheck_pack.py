"""Unit tests for ``scripts/factcheck_pack.py`` — the Layer-C LLM fact critic.

Covers the deterministic, pure helpers (batching, envelope unwrap, fenced-JSON
extraction, report formatting, field slimming, waivers). The LLM subprocess call
itself (``run_claude``) is not unit-tested — it is non-deterministic and costs
money; instead ``--dry-run`` keeps the prompt-building path exercisable offline.

Run from the project root::

    python3 -m unittest tests.test_factcheck_pack -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.factcheck_pack_fixtures import fc

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

    def test_unknown_severity_fails_safe_to_wrong_answer(self):
        # Fail-SAFE (2026-07 hardening): the readiness gate trusts the critic's
        # self-labeled severity/confidence, so an unrecognized label must coerce to
        # the MOST severe (blocking), never the least. Unknown severity ->
        # wrong-answer; unknown/missing confidence -> high. (Was: coerced to "nit"
        # + confidence passthrough, which let a mislabeled real error or a
        # confidence:"High" slip through the gate as advisory.)
        text = '{"findings": [{"qid": "q1", "severity": "bogus", "issue": "i", "confidence": "???"}]}'
        f = fc.extract_findings(text)["findings"][0]
        self.assertEqual(f["severity"], "wrong-answer")
        self.assertEqual(f["confidence"], "high")
        self.assertTrue(fc.is_blocking(f))

    def test_structured_ambiguity_evidence_survives_parsing(self):
        text = json.dumps({"findings": [{
            "qid": "q1", "category": "ambiguous", "severity": "ambiguous",
            "issue": "both options fit", "confidence": "low",
            "ambiguity_evidence": {
                "multiple_defensible_answers": True, "option_indices": [0, 2]
            },
        }]})
        finding = fc.extract_findings(text)["findings"][0]
        self.assertEqual(finding["category"], "ambiguous")
        self.assertEqual(finding["ambiguity_evidence"]["option_indices"], [0, 2])
        self.assertTrue(fc.is_blocking(finding))

    def test_ambiguous_without_structured_evidence_is_quality_advisory(self):
        text = json.dumps({"findings": [{
            "qid": "q1", "category": "ambiguous", "severity": "ambiguous",
            "issue": "the wording feels ambiguous", "confidence": "high",
        }]})
        finding = fc.extract_findings(text)["findings"][0]
        self.assertEqual(finding["category"], "option-quality")
        self.assertIsNone(finding["ambiguity_evidence"])
        self.assertFalse(fc.is_blocking(finding))

    def test_malformed_ambiguity_indices_are_advisory(self):
        finding = {
            "category": "ambiguous", "severity": "ambiguous", "confidence": "high",
            "ambiguity_evidence": {
                "multiple_defensible_answers": True, "option_indices": [1, 1]
            },
        }
        self.assertEqual(fc.finding_category(finding), "option-quality")
        self.assertFalse(fc.is_blocking(finding))

    def test_qidless_finding_with_issue_survives_as_live(self):
        # FIX C: a finding that lacks a qid but carries an `issue` must NOT be
        # silently dropped — in a mandatory gate a dropped finding is a false
        # pass. It survives under the sentinel qid "(no-qid)", which no real
        # waiver can accidentally match, so it stays LIVE and blocks.
        text = '{"findings": [{"severity": "nit", "issue": "no qid"}, {"qid": "q2", "issue": "ok"}]}'
        out = fc.extract_findings(text)
        self.assertEqual([f["qid"] for f in out["findings"]], ["(no-qid)", "q2"])
        self.assertEqual(out["findings"][0]["issue"], "no qid")

    def test_entirely_empty_finding_is_skipped(self):
        # No qid AND no issue → nothing actionable → safely skipped (only a real
        # finding-with-issue is preserved by the FIX C sentinel path).
        text = '{"findings": [{}, {"confidence": "low"}, {"qid": "q3", "issue": "real"}]}'
        out = fc.extract_findings(text)
        self.assertEqual([f["qid"] for f in out["findings"]], ["q3"])

    def test_non_dict_finding_is_skipped(self):
        text = '{"findings": ["a bare string", {"qid": "q4", "issue": "real"}]}'
        out = fc.extract_findings(text)
        self.assertEqual([f["qid"] for f in out["findings"]], ["q4"])

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            fc.extract_findings("the model refused to answer")

    def test_non_dict_json_raises_value_error(self):
        # E-19: a JSON array (or any non-object) at the top level must raise
        # ValueError, never silently yield 0 findings and checked=None.
        with self.assertRaises(ValueError):
            fc.extract_findings("[]")
        with self.assertRaises(ValueError):
            fc.extract_findings('"just a string"')
        with self.assertRaises(ValueError):
            fc.extract_findings("42")

    def test_arbitrary_prose_with_invalid_brackets_raises_value_error(self):
        # Malformed replies with non-JSON curly braces must raise ValueError,
        # never infer findings from arbitrary prose.
        with self.assertRaises(ValueError):
            fc.extract_findings("Here is some text with {no valid: json} inside.")


class ExtractModelTests(unittest.TestCase):
    def test_reads_model_from_modelusage(self):
        env = json.dumps({"result": "x", "modelUsage": {"claude-opus-4-8[1m]": {"inputTokens": 1}}})
        self.assertEqual(fc.extract_model(env), "claude-opus-4-8[1m]")

    def test_falls_back_to_model_field(self):
        self.assertEqual(fc.extract_model('{"result": "x", "model": "sonnet"}'), "sonnet")

    def test_none_when_unknown(self):
        self.assertIsNone(fc.extract_model("not json"))
        self.assertIsNone(fc.extract_model('{"result": "x"}'))


class SemanticBlockerTests(unittest.TestCase):
    """Terra variance must not turn answerability noise into certification blockers."""

    def test_quality_categories_never_block_at_high_confidence(self):
        for category in ("nit", "duplicate", "option-quality", "off-axis", "cue"):
            with self.subTest(category=category):
                self.assertFalse(fc.is_blocking({
                    "category": category, "severity": "ambiguous" if category == "off-axis" else "nit",
                    "confidence": "high", "issue": category,
                }))

    def test_wrong_answer_blocks_at_any_confidence(self):
        for confidence in ("low", "medium", "high"):
            with self.subTest(confidence=confidence):
                self.assertTrue(fc.is_blocking({
                    "category": "wrong-answer", "severity": "wrong-answer",
                    "confidence": confidence,
                }))

    def test_misleading_explanation_only_blocks_when_high(self):
        for confidence, expected in (("low", False), ("medium", False), ("high", True)):
            with self.subTest(confidence=confidence):
                self.assertEqual(fc.is_blocking({
                    "category": "misleading-explanation",
                    "severity": "misleading-explanation", "confidence": confidence,
                }), expected)

    def test_ambiguous_requires_two_defensible_option_indices(self):
        base = {"category": "ambiguous", "severity": "ambiguous", "confidence": "high"}
        self.assertFalse(fc.is_blocking(base))
        self.assertFalse(fc.is_blocking({**base, "ambiguity_evidence": {
            "multiple_defensible_answers": False, "option_indices": [0, 1]}}))
        self.assertFalse(fc.is_blocking({**base, "ambiguity_evidence": {
            "multiple_defensible_answers": True, "option_indices": [0]}}))
        self.assertTrue(fc.is_blocking({**base, "ambiguity_evidence": {
            "multiple_defensible_answers": True, "option_indices": [0, 1]}}))

    def test_strict_still_treats_every_live_finding_as_blocking(self):
        advisory = {"category": "off-axis", "severity": "ambiguous", "confidence": "high"}
        self.assertEqual(fc.blocking_findings([advisory]), [])
        self.assertEqual(fc.blocking_findings([advisory], strict=True), [advisory])

    def test_every_category_pinned_and_unknown_kind_fails_closed(self):
        # Pin is_blocking for EVERY entry of FINDING_CATEGORIES (imported, not
        # hand-copied) so a newly added category cannot ship unpinned, plus the
        # fail-closed contract: difficulty-miscalibration never blocks however
        # spelled, while any other unknown explicit kind coerces to wrong-answer.
        expected = {
            "wrong-answer": True,
            "misleading-explanation": True,   # at high confidence
            "ambiguous": True,                 # with valid ambiguity_evidence
            "nit": False,
            "duplicate": False,
            "option-quality": False,
            "off-axis": False,
            "cue": False,
            "difficulty-miscalibration": False,
        }
        self.assertEqual(set(expected), set(fc.FINDING_CATEGORIES))
        for category in fc.FINDING_CATEGORIES:
            with self.subTest(category=category):
                finding = {"category": category, "confidence": "high"}
                if category == "ambiguous":
                    finding["ambiguity_evidence"] = {
                        "multiple_defensible_answers": True, "option_indices": [0, 1]
                    }
                self.assertIs(fc.is_blocking(finding), expected[category])
        # misleading-explanation blocks only at high confidence
        self.assertFalse(fc.is_blocking({"category": "misleading-explanation"}))
        # ambiguous without valid structured evidence is advisory
        self.assertFalse(fc.is_blocking({"category": "ambiguous", "confidence": "high"}))
        # an unknown explicit kind fails closed to blocking
        self.assertTrue(fc.is_blocking({"kind": "totally-bogus", "confidence": "high"}))
        # difficulty-miscalibration never blocks, however spelled
        self.assertFalse(fc.is_blocking({
            "kind": "difficulty-miscalibration", "confidence": "high"}))
        self.assertFalse(fc.is_blocking({
            "kind": "difficulty_miscalibration", "confidence": "high"}))


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

    def test_finding_detail_format_is_byte_stable(self):
        finding = {
            "qid": "q1", "severity": "wrong-answer", "issue": "bad",
            "correction": "fix", "confidence": "high",
        }
        self.assertEqual(
            fc.format_live_finding_lines([finding], [finding]),
            [
                "  [BLOCKING] [wrong-answer          ] q1 (confidence: high)",
                "      issue:      bad",
                "      correction: fix",
            ],
        )

    def test_batch_errors_surfaced(self):
        out = fc.format_report([], 5, ["batch 1/1 [q1, q2]: claude exited 1"])
        self.assertIn("NOT checked", out)
        self.assertIn("batch 1/1", out)

    def test_coverage_gaps_surfaced_as_nonblocking_note(self):
        # FIX A: main surfaces coverage gaps as a clearly-labeled note (it does
        # not change main's exit-code contract; it blocks only in verify_pack).
        out = fc.format_report(
            [], 5, [], coverage_gaps=["batch 1/1: critic reported checked=3 of 5 questions"])
        self.assertIn("Coverage note", out)
        self.assertIn("checked=3 of 5", out)


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

    def test_explicit_null_severity_still_matches_by_qid(self):
        # FIX F: `"severity": null` means "no severity filter", not an active
        # filter comparing against None (which would never match a real finding).
        self.assertTrue(fc._waiver_matches({"qid": "c1q1", "severity": None}, self.F1))

    def test_explicit_null_issue_contains_still_matches_by_qid(self):
        # FIX F: `"issue_contains": null` is likewise treated as no filter.
        self.assertTrue(fc._waiver_matches({"qid": "c1q1", "issue_contains": None}, self.F1))

    def test_both_null_filters_still_match_by_qid(self):
        self.assertTrue(fc._waiver_matches(
            {"qid": "c1q1", "severity": None, "issue_contains": None}, self.F1))

    # ── _apply_waivers ───────────────────────────────────────────────────────
    def test_partitions_live_and_waived(self):
        live, waived, hygiene = fc._apply_waivers(
            [self.F1, self.F2],
            [{"qid": "c1q1", "reason": "textbook simplification, verified"}],
        )
        self.assertEqual([f["qid"] for f in live], ["c1q2"])
        self.assertEqual([f["qid"] for f in waived], ["c1q1"])
        self.assertEqual(waived[0]["waived_reason"], "textbook simplification, verified")
        # FIX G: a blanket qid-only waiver (no severity/issue_contains) still
        # suppresses, but earns a NON-blocking nudge to narrow it.
        self.assertEqual(len(hygiene), 1)
        self.assertIn("narrow", hygiene[0]["issue"])

    def test_blanket_qid_waiver_gets_narrowing_nudge(self):
        # FIX G: a justified blanket waiver suppresses the finding but earns a
        # hygiene nudge (a blanket waiver can mask a future genuine error). The
        # nudge is non-blocking — `live` is still empty.
        live, waived, hygiene = fc._apply_waivers(
            [self.F1], [{"qid": "c1q1", "reason": "verified false positive"}])
        self.assertEqual(live, [])
        self.assertEqual(len(waived), 1)
        self.assertEqual(len(hygiene), 1)
        self.assertIn("issue_contains", hygiene[0]["issue"])

    def test_severity_scoped_waiver_gets_no_nudge(self):
        # A waiver narrowed by severity is NOT blanket → no nudge.
        _live, waived, hygiene = fc._apply_waivers(
            [self.F1],
            [{"qid": "c1q1", "severity": "wrong-answer", "reason": "ok"}])
        self.assertEqual(len(waived), 1)
        self.assertEqual(hygiene, [])

    def test_issue_scoped_waiver_gets_no_nudge(self):
        # A waiver narrowed by issue_contains is NOT blanket → no nudge.
        _live, waived, hygiene = fc._apply_waivers(
            [self.F1],
            [{"qid": "c1q1", "issue_contains": "symmetric", "reason": "ok"}])
        self.assertEqual(len(waived), 1)
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

    def test_all_matching_waivers_credited_not_just_first(self):
        # E-24: when a pack-wide waiver and a qid-scoped waiver both match the
        # same finding, BOTH must be marked used so neither shows as stale.
        f = {"qid": "c1q1", "severity": "wrong-answer", "issue": "bad claim",
             "correction": "fix", "confidence": "high"}
        wide = {"qid": "c1q1", "reason": "reviewed by SME"}
        narrow = {"qid": "c1q1", "issue_contains": "bad claim",
                  "reason": "targeted review"}
        live, waived, hygiene = fc._apply_waivers([f], [wide, narrow])
        # finding is suppressed
        self.assertEqual(live, [])
        self.assertEqual(len(waived), 1)
        # neither waiver should appear stale
        stale = [h for h in hygiene if "stale" in h.get("issue", "")]
        self.assertEqual(stale, [], f"unexpected stale reports: {stale}")

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


if __name__ == "__main__":
    unittest.main()
