"""Unit tests for ``scripts/lint_packs.py`` — the Layer-A pack-quality linter.

Fast, direct, deterministic: each test builds fixture question dicts and calls a
check function (or ``lint_pack`` on a tmp file) and asserts the findings. No
subprocess, no network. Mirrors the style of ``tests/test_factcheck_pack.py``.

Covers the rules added/refined in TASKS.md Tasks 14-21:
  L14 (meta-distractor), L15 (matching near-dup), L16 (answer-position),
  L17 (true_false tells + balance), L20 (acronym-expansion leak), plus the
  Task-18 word-boundary precision pass (L1/L2/L10) and the Task-19 threshold
  tuning (L3 warning tier, L9 min-token guard, L10 contrast-cue tightening).
Every rule has a positive (fires) and a negative (does not fire) fixture.

Run from the project root::

    python3 -m unittest tests.test_lint_packs -v
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


# ── fixture builders ─────────────────────────────────────────────────────────

def mc(**over) -> dict:
    """A valid 4-option multiple_choice question; override any field."""
    base = {
        "id": "q1", "type": "multiple_choice", "topic": "t", "difficulty": "easy",
        "prompt": "Which control repairs damage after an incident?",
        "options": ["Preventive", "Detective", "Corrective", "Compensating"],
        "answer": 2, "explanation": "A corrective control repairs damage.",
    }
    base.update(over)
    return base


def matching(**over) -> dict:
    base = {
        "id": "m1", "type": "matching", "topic": "t", "difficulty": "easy",
        "prompt": "Match each item to its category.",
        "leftItems": ["Kelvin", "Pascal"],
        "rightItems": ["Temperature", "Pressure"],
        "correctPairs": [1, 0],  # non-identity so the L1 identity warning stays out
        "explanation": "Kelvin measures temperature; Pascal measures pressure.",
    }
    base.update(over)
    return base


def tf(**over) -> dict:
    base = {
        "id": "t1", "type": "true_false", "topic": "t", "difficulty": "easy",
        "prompt": "The sky is blue.", "answer": True,
        "explanation": "Rayleigh scattering.",
    }
    base.update(over)
    return base


def rules(findings, rule=None, severity=None):
    out = findings
    if rule is not None:
        out = [f for f in out if f["rule"] == rule]
    if severity is not None:
        out = [f for f in out if f["severity"] == severity]
    return out


# ── L1 — token leak, word-boundary + acronym exception (Task 18) ──────────────
class L1Tests(unittest.TestCase):
    def test_whole_word_token_leak_is_critical(self):
        q = matching(
            leftItems=["Firewall appliance", "Router box"],
            rightItems=["Forwards traffic between networks", "A firewall filters packets"],
            correctPairs=[0, 1],  # left[1] 'Router box' ~ right[1]? we only need a leak
        )
        # left[1] "Router box" -> right[1] "A firewall filters packets": no leak.
        # left[0] "Firewall appliance" -> right[0] "Forwards traffic...": no leak.
        # Re-pair so "firewall" leaks: left[0] -> right[1].
        q["correctPairs"] = [1, 0]
        crit = rules(lp.check_l1_matching_leak(q), "L1", "critical")
        self.assertTrue(any("firewall" in f["detail"] for f in crit))

    def test_short_allcaps_acronym_keeps_substring_match(self):
        # DNS -> DNSSEC must still flag (substring kept for short all-caps tokens).
        q = matching(
            leftItems=["DNS", "ARP"],
            rightItems=["Address resolution chatter", "DNSSEC signed records"],
            correctPairs=[1, 0],
        )
        crit = rules(lp.check_l1_matching_leak(q), "L1", "critical")
        self.assertTrue(any("dns" in f["detail"].lower() for f in crit))

    def test_coincidental_substring_does_not_flag(self):
        # REGRESSION: "port" must not match "Reporting"; "host" not "Ghostwriting".
        q = matching(
            leftItems=["Port forwarding", "Host file"],
            rightItems=["Ghostwriting tips", "Reporting dashboards"],
            correctPairs=[1, 0],
        )
        self.assertEqual(lp.check_l1_matching_leak(q), [])

    def test_lowercase_token_not_treated_as_acronym(self):
        # "Port" (only first letter upper) is NOT an acronym, so word-boundary
        # applies and "port" does not leak into "important".
        q = matching(
            leftItems=["Port scanning", "Banner grabbing"],
            rightItems=["A separate technique", "The single most important step"],
            correctPairs=[1, 0],  # left[0] 'Port scanning' -> right[1] '...important...'
        )
        self.assertEqual(rules(lp.check_l1_matching_leak(q), "L1", "critical"), [])


# ── L2 — stem echo, word-boundary + min_len 5 (Task 18) ──────────────────────
class L2Tests(unittest.TestCase):
    def test_distinctive_noun_only_in_correct_is_critical(self):
        q = mc(
            prompt="Which process performs photosynthesis output for the plant cell?",
            options=["Respiration", "Photosynthesis pathway", "Diffusion", "Osmosis"],
            answer=1,
        )
        self.assertTrue(rules(lp.check_l2_stem_echo(q), "L2", "critical"))

    def test_four_char_noun_no_longer_distinctive(self):
        # "host" (4 chars) appears only in the correct option but is below the
        # bumped MC min_len of 5, so L2 no longer fires.
        q = mc(
            prompt="Which tool scans a host network quickly?",
            options=["Host mapper", "Editor", "Compiler", "Player"],
            answer=0,
        )
        self.assertEqual(rules(lp.check_l2_stem_echo(q), "L2"), [])

    def test_shared_across_options_does_not_fire(self):
        q = mc(
            prompt="Which firewall stance blocks unknown traffic by default?",
            options=["Default-deny firewall", "Default-allow firewall", "Open", "Flat"],
            answer=0,
        )
        # "firewall" appears in two options → not exclusive → no fire.
        self.assertEqual(rules(lp.check_l2_stem_echo(q), "L2"), [])

    def test_vocabulary_stem_exempt(self):
        q = mc(
            prompt="What does HTTP stand for?",
            options=["Hypertext Transfer Protocol", "A", "B", "C"],
            answer=0,
        )
        self.assertEqual(rules(lp.check_l2_stem_echo(q), "L2"), [])


# ── L3 — length tell + warning tier (Task 19) ────────────────────────────────
class L3Tests(unittest.TestCase):
    def test_warning_when_single_longest_and_over_mean(self):
        # c6q6-shape: longest distractor keeps the critical from firing, but the
        # correct option exceeds the MEAN by >=25% with a >=12-char gap.
        q = mc(
            options=[
                "Negligent or careless device administrators",   # correct, 43
                "Limited compute power",                          # 21
                "Constrained battery and power budgets",          # 37
                "The inability to install patches",               # 32
            ],
            answer=0,
        )
        warn = rules(lp.check_l3_length_tell(q), "L3", "warning")
        self.assertEqual(len(warn), 1)
        self.assertEqual(rules(lp.check_l3_length_tell(q), "L3", "critical"), [])

    def test_no_warning_when_gap_below_floor(self):
        # c1q1-shape: correct is longest and >1.25x mean by ratio, but the
        # absolute gap is only ~3 chars → below the floor → no finding.
        q = mc(
            options=["Confidentiality", "Integrity", "Availability", "Authentication"],
            answer=0,
        )
        self.assertEqual(rules(lp.check_l3_length_tell(q), "L3"), [])

    def test_extreme_length_is_still_critical(self):
        q = mc(
            options=[
                "A very long and conspicuously detailed correct answer that dwarfs every distractor here",
                "Short", "Brief", "Tiny",
            ],
            answer=0,
        )
        self.assertTrue(rules(lp.check_l3_length_tell(q), "L3", "critical"))


# ── L9 — near-duplicate stems + min-token guard (Task 19) ────────────────────
class L9Tests(unittest.TestCase):
    def test_short_stems_capped_at_warning(self):
        qs = [
            mc(id="a", prompt="Active reconnaissance scanning"),
            mc(id="b", prompt="Active reconnaissance scanning method"),
        ]
        out = rules(lp.check_l9_near_duplicate_stems(qs), "L9")
        self.assertTrue(out)
        self.assertTrue(all(f["severity"] == "warning" for f in out))

    def test_long_stems_can_reach_critical(self):
        stem = "Which distinctive multi-token cryptographic hashing algorithm produces a fixed digest"
        qs = [mc(id="a", prompt=stem), mc(id="b", prompt=stem)]
        out = rules(lp.check_l9_near_duplicate_stems(qs), "L9", "critical")
        self.assertTrue(out)


# ── L10 — distractor coverage, word-boundary + cue tightening (Tasks 18/19) ──
class L10Tests(unittest.TestCase):
    def test_coincidental_substring_now_surfaces_as_warning(self):
        # "attack" inside "attacker" no longer counts "Replay attack" as covered.
        q = mc(
            prompt="Which attack overwrites a saved return address?",
            options=["Directory traversal", "Race condition", "Buffer overflow", "Replay attack"],
            answer=2,
            explanation=("A buffer overflow points to the attacker's code. A directory "
                         "traversal walks the file system and a race condition exploits timing."),
        )
        warn = rules(lp.check_l10_distractor_coverage(q), "L10", "warning")
        self.assertEqual(len(warn), 1)
        self.assertIn("Replay", warn[0]["detail"])

    def test_dropped_cue_no_longer_rescues_uncovered_explanation(self):
        # "the other" / "instead" were dropped from the cue list → a 0-coverage
        # explanation that leans on them is now CRITICAL.
        q = mc(
            explanation="A corrective control repairs damage; the other types act instead at another time.",
        )
        crit = rules(lp.check_l10_distractor_coverage(q), "L10", "critical")
        self.assertEqual(len(crit), 1)

    def test_kept_cue_still_rescues(self):
        q = mc(
            explanation="A corrective control repairs damage, unlike the preventive and detective controls.",
        )
        # "unlike" is a kept cue → 0-coverage explanation is rescued (no critical).
        self.assertEqual(rules(lp.check_l10_distractor_coverage(q), "L10", "critical"), [])

    def test_other_threat_phrase_still_rescues(self):
        # The calibrated "other threat" phrase (c5q4) survives the tightening.
        q = mc(
            prompt="Which defense most directly blocks SQL injection?",
            options=["Use HTTPS", "Close unused ports", "Require complex passwords", "Validate and filter input"],
            answer=3,
            explanation="Validating input is the direct fix; encryption, port hardening, and password rules address other threats.",
        )
        self.assertEqual(rules(lp.check_l10_distractor_coverage(q), "L10", "critical"), [])


# ── L14 — meta-distractor detection (Task 14) ────────────────────────────────
class L14Tests(unittest.TestCase):
    def test_all_of_the_above_is_warning(self):
        q = mc(options=["A", "B", "C", "All of the above"], answer=3)
        warn = rules(lp.check_l14_meta_distractor(q), "L14", "warning")
        self.assertEqual(len(warn), 1)

    def test_none_of_the_following_is_warning(self):
        q = mc(options=["A", "B", "C", "None of the following"], answer=0)
        self.assertTrue(rules(lp.check_l14_meta_distractor(q), "L14", "warning"))

    def test_position_reference_both_a_and_b_is_critical(self):
        q = mc(options=["A", "B", "Both A and B", "Neither"], answer=2)
        self.assertTrue(rules(lp.check_l14_meta_distractor(q), "L14", "critical"))

    def test_position_reference_a_and_c_is_critical(self):
        q = mc(options=["A and C", "B", "C", "D"], answer=0)
        self.assertTrue(rules(lp.check_l14_meta_distractor(q), "L14", "critical"))

    def test_position_reference_options_1_and_3_is_critical(self):
        q = mc(options=["Options 1 and 3", "B", "C", "D"], answer=0)
        self.assertTrue(rules(lp.check_l14_meta_distractor(q), "L14", "critical"))

    def test_ordinary_options_do_not_fire(self):
        self.assertEqual(lp.check_l14_meta_distractor(mc()), [])

    def test_multidigit_numeric_option_does_not_false_fire(self):
        # "16 and 32" is a plausible real answer, not a position reference.
        q = mc(options=["16 and 32", "8", "64", "128"], answer=0)
        self.assertEqual(lp.check_l14_meta_distractor(q), [])

    def test_non_mc_type_out_of_scope(self):
        self.assertEqual(lp.check_l14_meta_distractor(matching()), [])


# ── L15 — matching near-duplicate options (Task 15) ──────────────────────────
class L15Tests(unittest.TestCase):
    def test_high_overlap_right_items_critical(self):
        q = matching(
            leftItems=["First", "Second"],
            rightItems=[
                "Encrypt the message digest with a private key",
                "Encrypt the message digest with the private key",
            ],
            correctPairs=[0, 1],
        )
        self.assertTrue(rules(lp.check_l15_matching_near_dup(q), "L15", "critical"))

    def test_moderate_overlap_is_warning(self):
        # Jaccard ~0.67 (4 shared of 6 union) → warning, below the 0.8 critical.
        q = matching(
            leftItems=["First", "Second"],
            rightItems=[
                "Encrypted remote terminal login access",
                "Encrypted remote terminal login session",
            ],
            correctPairs=[0, 1],
        )
        out = rules(lp.check_l15_matching_near_dup(q), "L15")
        self.assertEqual([f["severity"] for f in out], ["warning"])

    def test_distinct_options_do_not_fire(self):
        self.assertEqual(lp.check_l15_matching_near_dup(matching()), [])

    def test_short_items_skipped_by_min_token_guard(self):
        # Two identical 2-token options — below the min-token guard → skipped by
        # L15 (an exact dup is L7's job, not L15's).
        q = matching(
            leftItems=["First", "Second"],
            rightItems=["Cross site", "Cross site"],
            correctPairs=[0, 1],
        )
        self.assertEqual(lp.check_l15_matching_near_dup(q), [])


# ── L16 — answer-position distribution (Task 16) ─────────────────────────────
class L16Tests(unittest.TestCase):
    def test_skewed_group_is_warning(self):
        qs = [mc(id=f"q{i}", answer=0) for i in range(5)]
        warn = rules(lp.check_l16_answer_position(qs), "L16", "warning")
        self.assertEqual(len(warn), 1)
        self.assertIsNone(warn[0]["qid"])

    def test_never_critical(self):
        qs = [mc(id=f"q{i}", answer=0) for i in range(8)]
        self.assertEqual(rules(lp.check_l16_answer_position(qs), "L16", "critical"), [])

    def test_small_group_not_flagged(self):
        qs = [mc(id=f"q{i}", answer=0) for i in range(4)]  # below L16_MIN_GROUP
        self.assertEqual(rules(lp.check_l16_answer_position(qs), "L16"), [])

    def test_balanced_distribution_not_flagged(self):
        qs = [mc(id=f"q{i}", answer=i % 4) for i in range(8)]  # 2 per slot
        self.assertEqual(rules(lp.check_l16_answer_position(qs), "L16"), [])


# ── L17 — true_false tells + balance (Task 17) ───────────────────────────────
class L17TellTests(unittest.TestCase):
    def test_absolute_in_false_keyed_statement_is_warning(self):
        q = tf(prompt="Compliance with a standard is always legally mandatory.", answer=False)
        warn = rules(lp.check_l17_true_false_tell(q), "L17", "warning")
        self.assertEqual(len(warn), 1)
        self.assertIn("always", warn[0]["detail"])

    def test_absolute_in_true_keyed_statement_is_fine(self):
        q = tf(prompt="A one-time pad key is never reused.", answer=True)
        self.assertEqual(lp.check_l17_true_false_tell(q), [])

    def test_false_without_absolute_is_fine(self):
        q = tf(prompt="An EOL device stops functioning at end of life.", answer=False)
        self.assertEqual(lp.check_l17_true_false_tell(q), [])

    def test_never_critical(self):
        q = tf(prompt="This is never true and all of it cannot hold.", answer=False)
        self.assertEqual(rules(lp.check_l17_true_false_tell(q), "L17", "critical"), [])


class L17BalanceTests(unittest.TestCase):
    def test_imbalanced_split_is_warning(self):
        qs = [tf(id=f"t{i}", answer=True) for i in range(5)] + [tf(id="t5", answer=False)]
        warn = rules(lp.check_l17_tf_balance(qs), "L17", "warning")
        self.assertEqual(len(warn), 1)
        self.assertIsNone(warn[0]["qid"])

    def test_balanced_split_not_flagged(self):
        qs = [tf(id=f"t{i}", answer=(i % 2 == 0)) for i in range(6)]
        self.assertEqual(rules(lp.check_l17_tf_balance(qs), "L17"), [])

    def test_below_min_count_not_flagged(self):
        qs = [tf(id=f"t{i}", answer=True) for i in range(4)]  # below L17_MIN_TF
        self.assertEqual(rules(lp.check_l17_tf_balance(qs), "L17"), [])


# ── L20 — acronym-expansion leak (Task 20) ───────────────────────────────────
class L20Tests(unittest.TestCase):
    def test_md5_expansion_leak(self):
        q = matching(
            leftItems=["MD5", "AES"],
            rightItems=["Symmetric block standard", "Deprecated message-digest hash"],
            correctPairs=[1, 0],
        )
        leaks = rules(lp.check_l20_acronym_expansion_leak(q), "L20", "warning")
        self.assertTrue(any("MD5" in f["detail"] for f in leaks))

    def test_slash_acronym_normalized(self):
        # S/MIME -> "...electronic mail" must flag (slash stripped to SMIME).
        q = matching(
            leftItems=["S/MIME", "SSH"],
            rightItems=["Encrypted remote terminal login", "Signing and encrypting electronic mail"],
            correctPairs=[1, 0],
        )
        self.assertTrue(rules(lp.check_l20_acronym_expansion_leak(q), "L20"))

    def test_unknown_acronym_not_checked(self):
        q = matching(
            leftItems=["XYZ", "QRS"],
            rightItems=["Some elliptic curve description", "A message digest summary"],
            correctPairs=[0, 1],
        )
        self.assertEqual(lp.check_l20_acronym_expansion_leak(q), [])

    def test_no_expansion_keyword_present(self):
        q = matching(
            leftItems=["AES", "RSA"],
            rightItems=["Asymmetric public-key cipher", "Symmetric block cipher offering 256-bit keys"],
            correctPairs=[1, 0],
        )
        # Neither right item contains a curated expansion keyword for its acronym.
        self.assertEqual(lp.check_l20_acronym_expansion_leak(q), [])

    def test_lowercase_word_not_treated_as_acronym(self):
        q = matching(
            leftItems=["aes", "rsa"],
            rightItems=["Rivest Shamir Adleman cipher", "Advanced standard"],
            correctPairs=[1, 0],
        )
        self.assertEqual(lp.check_l20_acronym_expansion_leak(q), [])


# ── L21 — scenario floor + diagram leak (Task 21) ────────────────────────────
class L21ScenarioTests(unittest.TestCase):
    def test_short_scenario_prompt_is_warning(self):
        q = mc(type="scenario_multiple_choice", prompt="A user clicks a link. What is it?")
        warn = rules(lp.check_l21_low_priority(q), "L21", "warning")
        self.assertEqual(len(warn), 1)

    def test_long_scenario_prompt_is_clean(self):
        q = mc(
            type="scenario_multiple_choice",
            prompt=("A finance employee receives a convincing email that appears to come from "
                    "the chief executive urgently requesting a wire transfer to a new vendor "
                    "account before the end of the business day. Which attack is this?"),
        )
        self.assertEqual(rules(lp.check_l21_low_priority(q), "L21"), [])

    def test_plain_mc_not_subject_to_scenario_floor(self):
        # A short PLAIN multiple_choice prompt is fine — the floor is scenario-only.
        q = mc(prompt="What is a firewall?")
        self.assertEqual(rules(lp.check_l21_low_priority(q), "L21"), [])


class L21DiagramTests(unittest.TestCase):
    def test_diagram_leaking_correct_token_is_critical(self):
        q = mc(
            options=["Star topology", "Bus topology", "Ring topology", "Mesh topology"],
            answer=0,
            diagram="<svg><text>Star</text></svg>",
            diagram_alt="A network laid out as a star.",
        )
        crit = rules(lp.check_l21_low_priority(q), "L21", "critical")
        self.assertEqual(len(crit), 1)
        self.assertIn("star", crit[0]["detail"].lower())

    def test_diagram_without_leak_and_with_alt_is_clean(self):
        q = mc(
            options=["Star topology", "Bus topology", "Ring topology", "Mesh topology"],
            answer=0,
            diagram="<svg><circle/></svg>",
            diagram_alt="An abstract network diagram.",
        )
        self.assertEqual(rules(lp.check_l21_low_priority(q), "L21"), [])

    def test_missing_diagram_alt_is_warning(self):
        q = mc(
            options=["Alpha node", "Beta node", "Gamma node", "Delta node"],
            answer=0,
            diagram="<svg><circle/></svg>",
        )
        warn = rules(lp.check_l21_low_priority(q), "L21", "warning")
        self.assertEqual(len(warn), 1)
        self.assertIn("diagram_alt", warn[0]["detail"])

    def test_object_diagram_markup_is_searched(self):
        q = mc(
            options=["Mesh layout", "Star layout", "Bus layout", "Ring layout"],
            answer=0,
            diagram={"mermaid": "graph TD; A-->Mesh", "text": "topology"},
            diagram_alt="alt text present",
        )
        self.assertTrue(rules(lp.check_l21_low_priority(q), "L21", "critical"))

    def test_no_diagram_is_clean(self):
        self.assertEqual(lp.check_l21_low_priority(mc(diagram=None)), [])


# ── D-15/16: malformed-structure guards in lint_pack ─────────────────────────
class MalformedStructureGuardTests(unittest.TestCase):
    """lint_pack must never raise on bad structure; it emits L7 criticals instead."""

    def _lint(self, payload) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(payload))
            return lp.lint_pack(p)

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
        # May produce L12 (missing explanation), but must not raise.
        for v in res["violations"]:
            self.assertIn(v.get("severity"), ("critical", "warning"))

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
                   "check_l21_low_priority"):
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
        pack = {"pack_id": "x", "questions": [
            mc(id="q1", explanation="A corrective control repairs damage; preventive, "
               "detective, and compensating controls do not repair after the fact."),
        ]}
        res = self._lint(pack)
        self.assertEqual(res["violations"], [])


if __name__ == "__main__":
    unittest.main()
