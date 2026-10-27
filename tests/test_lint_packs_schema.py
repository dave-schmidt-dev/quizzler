"""Unit tests for ``scripts/lint_packs.py`` — schema and malformed structure guards."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lint_pack_fixtures import (
    clean_pack_dict,
    lp,
    matching,
    mc,
    ms,
    rules,
    tf,
)


# ── D-15/16: malformed-structure guards in lint_pack ─────────────────────────
class MalformedStructureGuardTests(unittest.TestCase):
    """lint_pack must never raise on bad structure; it emits L7 criticals instead."""

    def _lint(self, payload) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(payload))
            return lp.lint_pack(p)

    def test_preparsed_pack_skips_pack_file_read_with_identical_contract(self):
        payload = {"questions": []}
        missing = Path("/does/not/exist/pack.json")
        original_read_text = Path.read_text

        def reject_pack_reread(path, *args, **kwargs):
            if path == missing:
                raise AssertionError("re-read")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=reject_pack_reread):
            result = lp.lint_pack(missing, parsed_data=payload)
        self.assertEqual(result["pack"], str(missing))
        self.assertFalse(any("could not load pack" in v["detail"]
                             for v in result["violations"]))

    def test_array_root_gives_l7_critical_no_exception(self):
        """A root JSON array (not an object) → single L7 critical, no exception."""
        res = self._lint([])
        crits = rules(res["violations"], "L7", "critical")
        self.assertTrue(crits, "expected at least one L7 critical")
        self.assertTrue(any("JSON object" in f["detail"] for f in crits))

    def test_null_root_gives_l7_critical_no_exception(self):
        """A JSON null root → L7 critical, no exception."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text("null")
            res = lp.lint_pack(p)
        crits = rules(res["violations"], "L7", "critical")
        self.assertTrue(crits, "expected at least one L7 critical")
        self.assertTrue(any("JSON object" in f["detail"] for f in crits))

    def test_non_dict_question_gives_l7_critical_no_exception(self):
        """questions:[123] → L7 critical for the non-dict entry, no exception."""
        res = self._lint({"questions": [123]})
        crits = rules(res["violations"], "L7", "critical")
        self.assertTrue(crits, "expected at least one L7 critical")
        self.assertTrue(any("not an object" in f["detail"] for f in crits))

    def test_int_prompt_question_gives_clean_findings_no_exception(self):
        """A question with prompt:123 (int) must not raise; findings are clean L7."""
        q = {
            "id": "q1", "type": "multiple_choice", "topic": "t",
            "difficulty": "easy", "prompt": 123,
            "options": ["A", "B", "C", "D"], "answer": 0,
        }
        res = self._lint({"questions": [q]})
        # May produce L12 (missing explanation) and L23 (absent blueprint) criticals,
        # but must not raise.
        for v in res["violations"]:
            self.assertIn(v.get("severity"), ("critical", "warning", "advisory"))

    def test_valid_questions_still_linted_after_skipped_non_dict(self):
        """A non-dict entry is skipped but valid siblings are still linted."""
        good_q = {
            "id": "q1", "type": "multiple_choice", "topic": "t",
            "difficulty": "easy", "prompt": "Which item is correct?",
            "options": ["A", "B", "C", "D"], "answer": 0,
            # deliberately missing explanation to trigger L12
        }
        res = self._lint({"questions": [999, good_q]})
        crits = rules(res["violations"], "L7", "critical")
        # The non-dict entry fires one L7 critical.
        self.assertTrue(any("not an object" in f["detail"] for f in crits))
        # The valid question is still linted (L12 critical for missing explanation).
        l12 = rules(res["violations"], "L12", "critical")
        self.assertTrue(l12, "expected L12 critical from the valid sibling question")


# ── integration: full lint_pack on a tmp file ────────────────────────────────
class LintPackIntegrationTests(unittest.TestCase):
    def _lint(self, pack: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return lp.lint_pack(p)

    def test_new_per_question_rules_are_registered(self):
        names = {fn.__name__ for fn in lp.PER_QUESTION_CHECKS}
        for fn in ("check_l14_meta_distractor", "check_l15_matching_near_dup",
                   "check_l17_true_false_tell", "check_l20_acronym_expansion_leak",
                   "check_l21_low_priority", "check_l24_unexpanded_acronym"):
            self.assertIn(fn, names)

    def test_pack_level_l14_critical_blocks(self):
        pack = {"pack_id": "x", "questions": [
            mc(id="q1", options=["A", "B", "Both A and B", "Neither"], answer=2),
        ]}
        res = self._lint(pack)
        crit = rules(res["violations"], "L14", "critical")
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0]["qid"], "q1")

    def test_clean_pack_stays_clean(self):
        qs = [
            mc(id="q1", explanation="A corrective control repairs damage; preventive, "
               "detective, and compensating controls do not repair after the fact."),
        ]
        res = self._lint(clean_pack_dict(questions=qs))
        self.assertEqual(res["violations"], [])


# ── 6.1: bool-as-int answer / correctPairs (E-18) ───────────────────────────
class L7BoolAnswerTests(unittest.TestCase):
    def test_bool_true_mc_answer_is_l7_critical(self):
        # answer=True is a bool; bool ⊂ int but must be rejected as an index.
        q = mc(answer=True)
        crits = rules(lp.check_l7_schema(q), "L7", "critical")
        self.assertTrue(crits, "expected L7 critical for bool answer in MC")
        self.assertTrue(any("answer" in f["detail"] for f in crits))

    def test_int_zero_mc_answer_passes(self):
        # Regression: a plain int 0 must still pass.
        self.assertEqual(rules(lp.check_l7_schema(mc(answer=0)), "L7", "critical"), [])

    def test_bool_in_correct_pairs_is_l7_critical(self):
        # correctPairs=[True, 0] — True is bool, not a valid index.
        q = matching(correctPairs=[True, 0])
        crits = rules(lp.check_l7_schema(q), "L7", "critical")
        self.assertTrue(crits, "expected L7 critical for bool in correctPairs")
        self.assertTrue(any("correctPairs" in f["detail"] for f in crits))

    def test_true_false_bool_answer_still_passes(self):
        # The true_false isinstance(answer, bool) check must remain untouched.
        self.assertEqual(rules(lp.check_l7_schema(tf(answer=True)), "L7", "critical"), [])
        self.assertEqual(rules(lp.check_l7_schema(tf(answer=False)), "L7", "critical"), [])


# ── 6.3: L10 distractor fallback (E-21) ─────────────────────────────────────
class L10DistractorOverlapTests(unittest.TestCase):
    def test_fully_overlapping_distractor_is_skipped_not_covered(self):
        # correct "Asymmetric encryption" → tokens {"asymmetric", "encryption"}
        # distractor "Encryption" → distinctive tokens = {} after set-difference
        # Old code fell back to its own tokens → "encryption" in explanation → falsely covered.
        # New code skips it; remaining distractors (Hashing, Compression) are uncovered.
        q = mc(
            options=["Asymmetric encryption", "Encryption", "Hashing", "Compression"],
            answer=0,
            explanation="Asymmetric encryption uses public/private key pairs.",
        )
        crit = rules(lp.check_l10_distractor_coverage(q), "L10", "critical")
        self.assertTrue(crit, "expected L10 critical when overlapping distractor is not falsely covered")


# ── 6.4: acronym-match precision (E-22/23) ───────────────────────────────────
class L1AcronymPrecisionTests(unittest.TestCase):
    def test_arp_does_not_fire_on_sharp(self):
        # "ARP" in left should NOT fire when right contains "sharp"
        # (\\barp does not match inside "sharp").
        q = matching(
            leftItems=["ARP", "TCP"],
            rightItems=["Transmission control sessions", "A sharp musical interval"],
            correctPairs=[1, 0],  # ARP -> right[1] "A sharp musical interval"
        )
        crit = rules(lp.check_l1_matching_leak(q), "L1", "critical")
        self.assertFalse(
            any("arp" in f["detail"].lower() for f in crit),
            "ARP should not fire on 'sharp'",
        )

    def test_dns_still_fires_on_dnssec(self):
        # DNS -> DNSSEC must still flag (prefix match: \\bdns matches dnssec).
        q = matching(
            leftItems=["DNS", "ARP"],
            rightItems=["Address resolution protocol", "DNSSEC signed zone records"],
            correctPairs=[1, 0],  # DNS -> right[1] "DNSSEC signed zone records"
        )
        crit = rules(lp.check_l1_matching_leak(q), "L1", "critical")
        self.assertTrue(
            any("dns" in f["detail"].lower() for f in crit),
            "DNS must still fire on DNSSEC",
        )


class L20CapitalizedWordFilterTests(unittest.TestCase):
    def test_ordinary_capitalized_word_does_not_fire(self):
        # "Des" in "Des Moines …" is Capitalized, not all-caps → raw.isupper() is False
        # → filtered out, so "DES"-expansion keyword "data" in the right item does NOT fire.
        q = matching(
            leftItems=["Des Moines metropolitan area", "Ames"],
            rightItems=["University town with data research", "Iowa capital region"],
            correctPairs=[0, 1],
        )
        self.assertEqual(
            lp.check_l20_acronym_expansion_leak(q), [],
            "Capitalized (non-acronym) word should not trigger L20",
        )


# ── 6.5: waiver credit-all (E-24) ────────────────────────────────────────────
class WaiverCreditAllTests(unittest.TestCase):
    def _lint(self, pack: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return lp.lint_pack(p)

    def test_specific_qid_waiver_after_broad_both_credited(self):
        # Pack-wide L14 waiver listed BEFORE a qid-specific L14 waiver for the same rule.
        # Old code: next() credits only the first (pack-wide) → the qid waiver appears stale.
        # New code: all matched waivers are credited → neither is stale.
        pack = {
            "pack_id": "x",
            "questions": [
                mc(id="q1", options=["A", "B", "Both A and B", "Neither"], answer=2),
            ],
            "lint_waivers": [
                {"rule": "L14", "reason": "pack-wide: position refs reviewed"},
                {"rule": "L14", "qid": "q1", "reason": "q1-specific: intentional for teaching"},
            ],
        }
        res = self._lint(pack)
        # The L14 finding must be waived, not live.
        l14_live = rules(res["violations"], "L14")
        self.assertEqual(l14_live, [], "L14 should be waived")
        # Neither waiver should be reported stale.
        stale = [
            v for v in res["violations"]
            if v.get("rule") == "WAIVER" and "stale" in v.get("detail", "")
        ]
        self.assertEqual(stale, [], f"neither waiver should be stale; got: {stale}")


# ── 6.6: diagram list shape (E-25) ───────────────────────────────────────────
class L21DiagramListTests(unittest.TestCase):
    def test_diagram_as_list_triggers_answer_leak_critical(self):
        # diagram is a list (not str/dict) — _diagram_markup previously returned ""
        # so the L21(b) scan silently no-oped. After the fix, list items are searched.
        q = mc(
            options=["Star topology", "Bus topology", "Ring topology", "Mesh topology"],
            answer=0,
            diagram=["<svg><text>Star</text></svg>"],
            diagram_alt="A network diagram.",
        )
        crit = rules(lp.check_l21_low_priority(q), "L21", "critical")
        self.assertEqual(len(crit), 1, "expected L21 critical for list diagram leaking 'star'")
        self.assertIn("star", crit[0]["detail"].lower())

    def test_diagram_as_list_without_leak_is_clean(self):
        q = mc(
            options=["Star topology", "Bus topology", "Ring topology", "Mesh topology"],
            answer=0,
            diagram=["<svg><circle/></svg>"],
            diagram_alt="An abstract network diagram.",
        )
        self.assertEqual(rules(lp.check_l21_low_priority(q), "L21", "critical"), [])


class L7MultiSelectTests(unittest.TestCase):
    def _crit(self, q):
        return rules(lp.check_l7_schema(q), "L7", "critical")

    def test_valid_multiselect_passes(self):
        self.assertEqual(lp.check_l7_schema(ms()), [])

    def test_options_not_a_list_is_critical(self):
        self.assertTrue(self._crit(ms(options="not a list")))

    def test_fewer_than_three_options_is_critical(self):
        self.assertTrue(self._crit(ms(options=["Only one", "Only two"], answers=[0])))

    def test_duplicate_options_is_critical(self):
        q = ms(options=["Same text", "Same text", "Other", "More"], answers=[0, 2])
        self.assertTrue(any("duplicate" in f["detail"] for f in self._crit(q)))

    def test_answers_missing_is_critical(self):
        q = ms()
        del q["answers"]
        self.assertTrue(any("answers" in f["detail"] for f in self._crit(q)))

    def test_answers_not_a_list_is_critical(self):
        self.assertTrue(self._crit(ms(answers=1)))

    def test_answers_empty_is_critical(self):
        self.assertTrue(self._crit(ms(answers=[])))

    def test_out_of_range_index_is_critical(self):
        self.assertTrue(self._crit(ms(answers=[0, 9])))

    def test_boolean_index_is_critical(self):
        # bool is an int subclass; is_int_not_bool must reject True so it can't
        # silently coerce to index 1.
        self.assertTrue(self._crit(ms(answers=[True, 2])))

    def test_duplicate_index_is_critical(self):
        self.assertTrue(any("duplicate" in f["detail"] for f in self._crit(ms(answers=[1, 1]))))

    def test_all_options_correct_is_critical(self):
        q = ms(answers=[0, 1, 2, 3])
        self.assertTrue(any("distractor" in f["detail"] for f in self._crit(q)))


class L12MultiSelectTests(unittest.TestCase):
    def test_missing_explanation_is_critical(self):
        q = ms()
        del q["explanation"]
        self.assertTrue(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"))

    def test_blank_explanation_is_critical(self):
        self.assertTrue(rules(lp.check_l12_explanation_and_meta(ms(explanation="  ")), "L12", "critical"))

    def test_valid_multiselect_has_no_l12_critical(self):
        self.assertEqual(rules(lp.check_l12_explanation_and_meta(ms()), "L12", "critical"), [])

    def test_display_position_reference_is_critical(self):
        q = mc(explanation="Option C is wrong because it does not repair damage.")
        crit = rules(lp.check_l12_explanation_and_meta(q), "L12", "critical")
        self.assertEqual(len(crit), 1)
        self.assertIn("displayed option position", crit[0]["detail"])

    def test_numeric_display_position_reference_is_critical(self):
        q = mc(explanation="Option 3 is wrong because it does not repair damage.")
        crit = rules(lp.check_l12_explanation_and_meta(q), "L12", "critical")
        self.assertEqual(len(crit), 1)

    def test_bare_ordinal_and_named_reference_variants_are_critical(self):
        for explanation in (
            "The third is incorrect because it does not repair damage.",
            "Choice C is wrong because it does not repair damage.",
            "Answer D is incorrect because it does not repair damage.",
            "The second choice is wrong because it does not repair damage.",
            "Options A and B are distractors.",
            "The last two options are wrong.",
            "(A) is incorrect because it does not repair damage.",
            "C) is wrong because it does not repair damage.",
        ):
            with self.subTest(explanation=explanation):
                crit = rules(lp.check_l12_explanation_and_meta(mc(explanation=explanation)),
                             "L12", "critical")
                self.assertEqual(len(crit), 1)

    def test_matching_right_item_position_reference_is_critical(self):
        q = matching(explanation="The second is incorrect because it measures pressure.")
        self.assertTrue(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"))

    def test_historical_d5q06_wording_is_critical(self):
        q = mc(explanation=(
            "The third is incorrect because a copied password can be used by another "
            "person. The fourth is incorrect because a password can be reset."
        ))
        self.assertTrue(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"))

    def test_content_reference_is_not_a_position_reference(self):
        q = mc(explanation="A detective control detects activity but does not repair damage.")
        self.assertEqual(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"), [])

    def test_stem_relative_order_language_is_not_a_position_reference(self):
        q = mc(explanation="The third factor is inherence, which depends on a physical trait.")
        self.assertEqual(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"), [])

    def test_quoted_stem_order_and_acronym_suffix_are_not_position_references(self):
        q = mc(explanation=(
            '"The first is real user monitoring" reverses the approaches; '
            "an NCA) restricts competitive work after employment."
        ))
        self.assertEqual(rules(lp.check_l12_explanation_and_meta(q), "L12", "critical"), [])


class L22Tests(unittest.TestCase):
    def _l22(self, q, severity=None):
        return rules(lp.check_l22_multiselect(q), "L22", severity)

    def test_clean_multiselect_has_no_findings(self):
        self.assertEqual(self._l22(ms()), [])

    def test_non_multiselect_ignored(self):
        self.assertEqual(lp.check_l22_multiselect(mc()), [])

    def test_single_correct_is_warning(self):
        w = self._l22(ms(answers=[0]), "warning")
        self.assertTrue(any("multiple_choice" in f["detail"] for f in w))

    def test_lone_distractor_is_warning(self):
        w = self._l22(ms(answers=[0, 1, 2]), "warning")
        self.assertTrue(any("lone" in f["detail"] for f in w))

    def test_meta_option_is_warning(self):
        q = ms(options=["Alpha", "Beta", "Gamma", "All of the above"], answers=[0, 1])
        self.assertTrue(any("meta-option" in f["detail"] for f in self._l22(q, "warning")))

    def test_position_reference_is_critical(self):
        q = ms(options=["Alpha", "Beta", "Both A and B", "Delta"], answers=[0, 1])
        self.assertTrue(self._l22(q, "critical"))

    def test_length_tell_is_warning(self):
        q = ms(
            prompt="Which statements are accurate?",
            options=[
                "This deliberately verbose correct option runs quite long indeed",
                "Another deliberately verbose correct option also running long here",
                "Short one",
                "Short two",
            ],
            answers=[0, 1],
        )
        self.assertTrue(any("length tell" in f["detail"] for f in self._l22(q, "warning")))

    def test_stem_echo_is_warning(self):
        q = ms(
            prompt="Which methods apply encryption?",
            options=["Uses encryption", "Adds encryption", "Plain scheme A", "Plain scheme B"],
            answers=[0, 1],
        )
        self.assertTrue(any("encryption" in f["detail"] for f in self._l22(q, "warning")))

    def test_count_disclosure_is_warning(self):
        q = ms(
            prompt="Select the two correct entries below.",
            options=["Alpha entry", "Beta entry", "Gamma entry", "Delta entry"],
            answers=[0, 1],
        )
        self.assertTrue(any("discloses the number" in f["detail"] for f in self._l22(q, "warning")))

    def test_out_of_range_answer_defers_to_l7(self):
        # An invalid index is L7's job; L22 must not emit a filtered-key finding
        # (e.g. "only one correct") on top of it.
        self.assertEqual(self._l22(ms(answers=[0, 99])), [])

    def test_duplicate_answer_defers_to_l7(self):
        self.assertEqual(self._l22(ms(answers=[1, 1])), [])


if __name__ == "__main__":
    unittest.main()
