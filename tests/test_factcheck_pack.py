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
from unittest.mock import patch

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
        live, waived, hygiene = fc._apply_waivers(
            [self.F1],
            [{"qid": "c1q1", "severity": "wrong-answer", "reason": "ok"}])
        self.assertEqual(len(waived), 1)
        self.assertEqual(hygiene, [])

    def test_issue_scoped_waiver_gets_no_nudge(self):
        # A waiver narrowed by issue_contains is NOT blanket → no nudge.
        live, waived, hygiene = fc._apply_waivers(
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


class PromptCheckLanguageTests(unittest.TestCase):
    """Tasks 22/23/24 — the Layer-C critic prompt must EXPLICITLY cue the three
    documented semantic pattern families (off-axis distractor, two-defensible-
    answer ambiguity, cross-question duplication), while keeping the JSON output
    contract and the canonical severity set UNCHANGED. The parser coerces an
    unknown severity to ``nit`` (see ExtractFindingsTests), so a prompt that
    invented a new severity value would silently mask exactly these findings —
    these tests pin both the new cue language and the unchanged contract.

    No LLM is involved: ``build_prompt`` only string-concatenates PROMPT_HEADER
    with the batch JSON, so the prompt is fully inspectable offline."""

    PROMPT = fc.build_prompt([{"id": "q1", "prompt": "What is 2+2?"}])

    # ── Task 22: off-axis / category-outlier distractor ──────────────────────
    def test_prompt_flags_off_axis_category_distractor(self):
        low = self.PROMPT.lower()
        self.assertIn("off-axis", low)
        self.assertIn("category-outlier", low)
        self.assertIn("self-eliminates", low)          # the answerability defect
        self.assertIn("same-axis near-miss", low)      # the documented fix
        # mapped onto an existing severity, not a new one
        self.assertIn("severity `ambiguous`", low)

    # ── Task 23: two-defensible-answer ambiguity sub-patterns ────────────────
    def test_prompt_flags_inversion_subtype_and_terminology_ambiguity(self):
        low = self.PROMPT.lower()
        self.assertIn("logical inversions/antonyms", low)   # (a) antonym/inversion
        self.assertIn("subtype/superset", low)              # (b) subtype/superset
        self.assertIn("most precisely", low)                # the hedge-word cue
        self.assertIn("terminology-overload", low)          # (c) terminology overload
        self.assertIn("per the course text", low)           # the documented fix

    # ── Task 24: cross-question concept / answer-fact duplication ─────────────
    def test_prompt_flags_cross_question_duplication(self):
        low = self.PROMPT.lower()
        self.assertIn("cross-question duplication", low)
        self.assertIn("same", low)                          # "the SAME keyed fact"
        self.assertIn("recycled option pool", low)
        self.assertIn("beyond mere stem-word overlap", low)  # not just L9 token overlap

    # ── the output contract is unchanged ─────────────────────────────────────
    def test_json_schema_instruction_unchanged(self):
        for key in ('"findings"', '"qid"', '"severity"', '"issue"',
                    '"correction"', '"confidence"', '"checked"'):
            self.assertIn(key, self.PROMPT)
        self.assertIn(
            "wrong-answer|misleading-explanation|ambiguous|nit", self.PROMPT)

    def test_severities_constant_is_unchanged(self):
        self.assertEqual(
            fc.SEVERITIES,
            ("wrong-answer", "misleading-explanation", "ambiguous", "nit"))

    def test_new_checks_assign_only_existing_severities(self):
        # Every backtick-quoted severity the prompt ASSIGNS to a check must be in
        # SEVERITIES — inventing one would be coerced to `nit` and mask the
        # finding. (Matches `Severity \\`x\\`` assignments, not the schema's
        # pipe-list or the parenthetical fallbacks.)
        import re
        assigned = set(re.findall(r"[Ss]everity `([a-z-]+)`", self.PROMPT))
        self.assertTrue(assigned, "the new checks should assign severities")
        self.assertTrue(
            assigned <= set(fc.SEVERITIES),
            f"prompt assigns non-canonical severities: "
            f"{assigned - set(fc.SEVERITIES)}")

    def test_existing_instructions_preserved(self):
        # The answer-key semantics, the "rely on established knowledge" anchor,
        # and the textbook-simplification guard must survive the enhancement.
        self.assertIn("0-based index", self.PROMPT)
        self.assertIn("correctPairs[i]", self.PROMPT)
        self.assertIn("true_false uses a boolean", self.PROMPT)
        self.assertIn("established Security+", self.PROMPT)
        self.assertIn(
            "do NOT flag acceptable textbook simplifications", self.PROMPT)
        self.assertIn("Only report PROBLEMS", self.PROMPT)


class CollectFindingsTests(unittest.TestCase):
    """The shared canonical batch loop (FIX A). `run_claude` is mocked — NO real
    LLM or network call happens here."""

    @staticmethod
    def _env(findings: list[dict], checked) -> str:
        inner = json.dumps({"findings": findings, "checked": checked})
        return json.dumps({"type": "result", "result": inner,
                           "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})

    def test_full_coverage_no_errors_is_covered(self):
        qs = [{"id": "q1"}, {"id": "q2"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=2)):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertEqual(res["questions_sent"], 2)
        self.assertTrue(fc.coverage_ok(res))

    def test_partial_checked_is_a_coverage_gap(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["coverage_gaps"]), 1)
        self.assertIn("checked=1 of 3", res["coverage_gaps"][0])
        self.assertEqual(res["questions_unchecked"], 2)
        self.assertFalse(fc.coverage_ok(res))

    def test_batch_error_recorded_and_not_covered(self):
        qs = [{"id": "q1"}]
        with patch.object(fc, "run_claude", side_effect=RuntimeError("timed out")):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_on_batch_callback_invoked_per_batch(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        seen = []
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            fc.collect_findings(qs, model=None, batch_size=1, timeout=5,
                                on_batch=lambda i, n: seen.append((i, n)))
        self.assertEqual(seen, [(0, 3), (1, 3), (2, 3)])

    def test_non_dict_critic_reply_is_batch_error(self):
        # E-19: a non-dict JSON reply (array) raises ValueError in extract_findings,
        # caught as a batch error → unchecked, coverage_ok False.
        qs = [{"id": "q1"}]
        # envelope whose result is a JSON array (not an object)
        env = json.dumps({"type": "result", "result": "[]",
                          "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=env):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_nan_checked_is_coverage_gap(self):
        # E-20: a critic reply with checked=NaN must produce a coverage gap.
        # Without math.isfinite guard, NaN < n is always False so the batch
        # would appear fully covered — a silent integrity hole.
        qs = [{"id": "q1"}, {"id": "q2"}]
        inner = json.dumps({"findings": [], "checked": float("nan")}, allow_nan=True)
        env = json.dumps({"type": "result", "result": inner,
                          "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=env):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["coverage_gaps"]), 1)
        self.assertIn("non-finite", res["coverage_gaps"][0])
        self.assertEqual(res["questions_unchecked"], 2)
        self.assertFalse(fc.coverage_ok(res))


if __name__ == "__main__":
    unittest.main()
