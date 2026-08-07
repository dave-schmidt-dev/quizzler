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


def ms(**over) -> dict:
    """A valid 4-option multiple_select with 2 correct answers; override any field.

    Deliberately clean for L22: 2-of-4 correct (not a lone distractor), balanced
    option lengths, no distinctive prompt term echoed only in the correct set, no
    count word in the prompt, no meta/position options.
    """
    base = {
        "id": "s1", "type": "multiple_select", "topic": "t", "difficulty": "medium",
        "prompt": "Which of the listed protocols operate at the transport layer?",
        "options": ["The TCP protocol", "The UDP protocol", "The ARP protocol", "The ICMP protocol"],
        "answers": [0, 1],
        "explanation": "TCP and UDP are transport-layer; ARP and ICMP are not.",
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


def coverage_blueprint_for(questions: list[dict]) -> list[dict]:
    """Default coverage_blueprint covering every topic in ``questions``."""
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def clean_pack_dict(*, pack_id: str = "x", questions: list[dict], **extra) -> dict:
    """Build a lint-clean pack payload with a matching coverage_blueprint."""
    pack = {"pack_id": pack_id, "questions": questions, **extra}
    pack.setdefault("coverage_blueprint", coverage_blueprint_for(questions))
    return pack


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


# ── L23 — coverage completeness (full-topic-coverage standard) ────────────────
class L23BlueprintTests(unittest.TestCase):
    """Direct check_l23 tests for the coverage_blueprint contract."""

    def _l23(self, data, questions, severity=None):
        return rules(lp.check_l23_coverage_completeness(data, questions), "L23", severity)

    def test_declared_topic_with_zero_questions_is_critical(self):
        # Blueprint requires 2 on 'rds-multi-az'; no question carries that topic.
        data = {"coverage_blueprint": [{"topic": "rds-multi-az", "min": 2}]}
        qs = [mc(id="q1", topic="ec2-pricing"), mc(id="q2", topic="vpc-cidr")]
        crit = self._l23(data, qs, "critical")
        self.assertEqual(len(crit), 1)
        self.assertIn("rds-multi-az", crit[0]["detail"])
        self.assertIn("found 0", crit[0]["detail"])
        self.assertIsNone(crit[0]["qid"])  # pack-level, attributed like L16

    def test_declared_topic_meeting_min_has_no_critical(self):
        # Two questions on 'iam-roles'; blueprint min is 2 → satisfied.
        data = {"coverage_blueprint": [{"topic": "iam-roles", "min": 2}]}
        qs = [mc(id="q1", topic="iam-roles"), mc(id="q2", topic="iam-roles")]
        self.assertEqual(self._l23(data, qs, "critical"), [])

    def test_bare_string_blueprint_entry_defaults_min_one(self):
        # Shorthand "sqs-vs-sns" == {"topic": "sqs-vs-sns", "min": 1}.
        data = {"coverage_blueprint": ["sqs-vs-sns"]}
        qs = [mc(id="q1", topic="ec2-pricing")]
        crit = self._l23(data, qs, "critical")
        self.assertEqual(len(crit), 1)
        self.assertIn("sqs-vs-sns", crit[0]["detail"])
        # And when covered once, no critical.
        qs2 = [mc(id="q1", topic="sqs-vs-sns")]
        self.assertEqual(self._l23(data, qs2, "critical"), [])

    def test_topic_match_is_case_insensitive_strip(self):
        # Blueprint 'RDS-Multi-AZ' matches a question topic ' rds-multi-az '.
        data = {"coverage_blueprint": [{"topic": "RDS-Multi-AZ", "min": 1}]}
        qs = [mc(id="q1", topic=" rds-multi-az ")]
        self.assertEqual(self._l23(data, qs, "critical"), [])

    def test_no_blueprint_present_is_critical_blocking(self):
        # The absent-blueprint case emits exactly one CRITICAL finding.
        data = {}
        qs = [mc(id="q1", topic="iam-roles")]
        out = self._l23(data, qs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "critical")
        self.assertIn("coverage_blueprint", out[0]["detail"])
        self.assertEqual(self._l23(data, qs, "critical"), out)

    def test_blueprint_present_suppresses_absent_blueprint_critical(self):
        data = {"coverage_blueprint": [{"topic": "iam-roles", "min": 1}]}
        qs = [mc(id="q1", topic="iam-roles")]
        self.assertEqual(self._l23(data, qs, "critical"), [])


class L23ConcentrationTests(unittest.TestCase):
    def _l23(self, data, questions, severity=None):
        return rules(lp.check_l23_coverage_completeness(data, questions), "L23", severity)

    def test_over_concentration_is_warning(self):
        # 10 questions, 3 share topic 'aws-kms' (30% > 15%) → WARNING.
        qs = ([mc(id=f"k{i}", topic="aws-kms") for i in range(3)]
              + [mc(id=f"q{i}", topic=f"topic-{i}") for i in range(7)])
        warn = self._l23({}, qs, "warning")
        conc = [f for f in warn if "over-concentrated" in f["detail"]]
        self.assertEqual(len(conc), 1)
        self.assertIn("aws-kms", conc[0]["detail"])

    def test_tiny_pack_does_not_false_fire_concentration(self):
        # 3 questions, one topic 2/3 (67%) — below the min-pack guard → no fire.
        qs = [mc(id="q1", topic="aws-kms"), mc(id="q2", topic="aws-kms"),
              mc(id="q3", topic="vpc")]
        conc = [f for f in self._l23({}, qs, "warning") if "over-concentrated" in f["detail"]]
        self.assertEqual(conc, [])

    def test_even_spread_large_pack_no_concentration(self):
        qs = [mc(id=f"q{i}", topic=f"topic-{i}") for i in range(12)]
        conc = [f for f in self._l23({}, qs, "warning") if "over-concentrated" in f["detail"]]
        self.assertEqual(conc, [])


class L23SlugTests(unittest.TestCase):
    def _dup(self, questions):
        out = rules(lp.check_l23_coverage_completeness({}, questions), "L23", "warning")
        return [f for f in out if "near-duplicate" in f["detail"]]

    def test_near_duplicate_slugs_prefix_extension_is_warning(self):
        # shared-responsibility vs shared-responsibility-model — the itn254 bug.
        qs = [mc(id="q1", topic="shared-responsibility"),
              mc(id="q2", topic="shared-responsibility-model")]
        dup = self._dup(qs)
        self.assertEqual(len(dup), 1)
        self.assertIn("shared-responsibility", dup[0]["detail"])

    def test_single_token_slug_skipped_by_min_token_guard(self):
        # 'soar' (1 token) must NOT fire against 'siem-vs-soar' (a substring but
        # a distinct concept) — the min-token guard skips 1-token slugs.
        qs = [mc(id="q1", topic="soar"), mc(id="q2", topic="siem-vs-soar")]
        self.assertEqual(self._dup(qs), [])

    def test_distinct_multitoken_slugs_do_not_fire(self):
        qs = [mc(id="q1", topic="ec2-pricing-models"), mc(id="q2", topic="vpc-cidr-sizing")]
        self.assertEqual(self._dup(qs), [])


class L23IntegrationTests(unittest.TestCase):
    """lint_pack-level behavior: absent-blueprint critical + L23 waiverability."""

    def _lint(self, pack: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return lp.lint_pack(p)

    def test_absent_blueprint_critical_blocks(self):
        pack = {"pack_id": "x", "questions": [
            mc(id="q1", explanation="A corrective control repairs damage; preventive, "
               "detective, and compensating controls do not repair after the fact."),
        ]}
        res = self._lint(pack)
        l23 = rules(res["violations"], "L23", "critical")
        self.assertEqual(len(l23), 1)
        self.assertIn("coverage_blueprint", l23[0]["detail"])
        self.assertEqual(lp.severity_to_exit(res["violations"]), 1)

    def test_l23_pack_level_critical_blocks(self):
        pack = {"pack_id": "x",
                "coverage_blueprint": [{"topic": "rds-multi-az", "min": 2}],
                "questions": [mc(id="q1", topic="ec2-pricing",
                                 explanation="Preventive, detective, and compensating "
                                 "controls differ from the corrective one described here.")]}
        res = self._lint(pack)
        crit = rules(res["violations"], "L23", "critical")
        self.assertEqual(len(crit), 1)
        self.assertIsNone(crit[0]["qid"])

    def test_l23_pack_level_waiver_suppresses_finding(self):
        # A pack-wide {"rule": "L23"} waiver (qid omitted) moves the pack-level
        # L23 critical from violations to waived — confirming _apply_waivers
        # handles no-qid L23 findings.
        pack = {
            "pack_id": "x",
            "coverage_blueprint": [{"topic": "rds-multi-az", "min": 2}],
            "lint_waivers": [
                {"rule": "L23", "reason": "blueprint topic covered in a sibling pack"},
            ],
            "questions": [mc(id="q1", topic="ec2-pricing",
                             explanation="Preventive, detective, and compensating "
                             "controls differ from the corrective one described here.")],
        }
        res = self._lint(pack)
        # No live L23 finding remains.
        self.assertEqual(rules(res["violations"], "L23"), [])
        # It moved to `waived` with the justification.
        waived_l23 = [w for w in res["waived"] if w["rule"] == "L23"]
        self.assertEqual(len(waived_l23), 1)
        self.assertIn("sibling pack", waived_l23[0]["waived_reason"])
        # And the waiver is not reported stale.
        stale = [v for v in res["violations"]
                 if v.get("rule") == "WAIVER" and "stale" in v.get("detail", "")]
        self.assertEqual(stale, [])


class L23MigrationTests(unittest.TestCase):
    """PM-8: every non-hidden course pack under question-packs/ must declare a blueprint."""

    PACKS_DIR = PROJECT_ROOT / "question-packs"

    def test_shipped_packs_declare_coverage_blueprint(self):
        missing: list[str] = []
        for course_dir in sorted(self.PACKS_DIR.iterdir()):
            if not course_dir.is_dir():
                continue
            if course_dir.name.startswith((".", "_")):
                continue
            if course_dir.name.startswith("zz-hooktest-"):
                continue
            for pack_path in sorted(course_dir.glob("*.json")):
                if pack_path.name.startswith("_"):
                    continue
                data = json.loads(pack_path.read_text())
                raw = data.get("coverage_blueprint")
                if not isinstance(raw, list) or not raw:
                    missing.append(str(pack_path.relative_to(PROJECT_ROOT)))
        self.assertEqual(
            missing, [],
            "non-_ course packs must declare a non-empty coverage_blueprint: "
            + ", ".join(missing),
        )

# ── format_human advisory rendering ─────────────────────────────────────────
class FormatHumanAdvisoryTests(unittest.TestCase):
    """format_human() must render advisory-tier findings in their own separated
    block, never affecting the ✓/✗ verdict, and with a distinct [advisory] tag."""

    def _make_result(self, *, pack="test-pack", violations=None, waived=None):
        return {"pack": pack, "violations": violations or [], "waived": waived or []}

    def test_advisory_only_shows_clean_verdict_with_advisory_block(self):
        """Advisory findings keep the pack ✓ clean; they appear in a separated block."""
        res = self._make_result(violations=[
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'PCI' is not spelled out anywhere in this explanation"},
        ])
        out = lp.format_human([res])
        self.assertIn("  ✓  test-pack: clean", out)
        self.assertNotIn("  ✗", out)
        self.assertIn("[advisory]", out)
        self.assertIn("(1 advisory", out)
        self.assertNotIn("[critical]", out)
        self.assertNotIn("[warning]", out)

    def test_advisory_plus_critical_shows_critical_verdict_and_advisory_block(self):
        """Critical findings still drive ✗; advisory block appears below them."""
        res = self._make_result(violations=[
            {"qid": "(pack)", "rule": "L23", "severity": "critical",
             "detail": "missing coverage_blueprint"},
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'PCI' is not spelled out"},
        ])
        out = lp.format_human([res])
        self.assertIn("  ✗  test-pack: 1 critical, 0 warning", out)
        self.assertIn("[critical]", out)
        self.assertIn("[advisory]", out)

    def test_advisory_plus_warning_shows_warning_verdict_and_advisory_block(self):
        """Warning-only findings drive ✗ (exit code 2); advisory block below."""
        res = self._make_result(violations=[
            {"qid": "q1", "rule": "L14", "severity": "warning",
             "detail": "position reference 'Both A and B' found"},
            {"qid": "q2", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'CVE' is not spelled out"},
        ])
        out = lp.format_human([res])
        self.assertIn("  ✗  test-pack: 0 critical, 1 warning", out)
        self.assertIn("[warning ]", out)
        self.assertIn("[advisory]", out)

    def test_advisory_block_surrounded_by_blank_lines(self):
        """Advisory block must be blank-line separated from surrounding content."""
        res = self._make_result(violations=[
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'PCI' is not spelled out"},
        ])
        out = lp.format_human([res])
        self.assertIn("\n\n       (1 advisory —", out)
        self.assertIn("\n\nTotal:", out)

    def test_waived_block_before_advisory_block(self):
        """Waived findings render before the advisory block."""
        res = self._make_result(
            violations=[
                {"qid": "q1", "rule": "L24", "severity": "advisory",
                 "detail": "acronym 'PCI' is not spelled out"},
            ],
            waived=[
                {"qid": "(pack)", "rule": "L23", "waived_reason": "covered in sibling pack"},
            ],
        )
        out = lp.format_human([res])
        waived_pos = out.index("[waived")
        advisory_pos = out.index("[advisory]")
        self.assertLess(waived_pos, advisory_pos,
                        "waived block must appear before advisory block")

    def test_no_advisory_findings_no_advisory_text(self):
        """When there are no advisory findings, output must not mention advisory."""
        res = self._make_result(violations=[
            {"qid": "(pack)", "rule": "L23", "severity": "critical",
             "detail": "missing coverage_blueprint"},
        ])
        out = lp.format_human([res])
        self.assertNotIn("[advisory]", out)
        self.assertNotIn("advisory —", out)
        self.assertNotIn("advisory, non-blocking", out)

    def test_advisory_count_in_summary(self):
        """Summary line tracks total advisory count with '(N advisory, non-blocking)'."""
        res = self._make_result(violations=[
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'PCI' is not spelled out"},
            {"qid": "q2", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'RTO' is not spelled out"},
        ])
        out = lp.format_human([res])
        self.assertIn("(2 advisory, non-blocking)", out)

    def test_multiple_packs_aggregate_advisory_counts(self):
        """Advisory counts sum across all packs in results."""
        r1 = self._make_result(pack="pack-a", violations=[
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'PCI' is not spelled out"},
        ])
        r2 = self._make_result(pack="pack-b", violations=[
            {"qid": "q1", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'RTO' is not spelled out"},
            {"qid": "q2", "rule": "L24", "severity": "advisory",
             "detail": "acronym 'SLE' is not spelled out"},
        ])
        out = lp.format_human([r1, r2])
        self.assertIn("(3 advisory, non-blocking)", out)
        self.assertIn("  ✓  pack-a: clean", out)
        self.assertIn("  ✓  pack-b: clean", out)

    def test_advisory_and_waived_both_in_summary(self):
        """Summary includes both waived count and advisory count when both present."""
        res = self._make_result(
            violations=[
                {"qid": "q1", "rule": "L24", "severity": "advisory",
                 "detail": "acronym 'PCI' is not spelled out"},
            ],
            waived=[
                {"qid": "(pack)", "rule": "L23", "waived_reason": "intentional"},
            ],
        )
        out = lp.format_human([res])
        self.assertIn("(1 waived)", out)
        self.assertIn("(1 advisory, non-blocking)", out)


class SeverityToExitMixedTests(unittest.TestCase):
    """severity_to_exit with mixtures of advisory and other severity tiers."""

    def test_advisory_only_returns_zero(self):
        violations = [
            {"rule": "L24", "severity": "advisory", "detail": "acronym 'PCI'"},
        ]
        self.assertEqual(lp.severity_to_exit(violations), 0)

    def test_advisory_plus_warning_returns_two(self):
        """WAIVER hygiene findings carry severity 'warning' and do affect exit code."""
        violations = [
            {"rule": "L24", "severity": "advisory", "detail": "acronym 'PCI'"},
            {"rule": "WAIVER", "severity": "warning",
             "detail": "stale lint_waiver for 'L10' matched no finding"},
        ]
        self.assertEqual(lp.severity_to_exit(violations), 2)

    def test_advisory_plus_critical_returns_one(self):
        violations = [
            {"rule": "L24", "severity": "advisory", "detail": "acronym 'PCI'"},
            {"rule": "L23", "severity": "critical", "detail": "missing coverage_blueprint"},
        ]
        self.assertEqual(lp.severity_to_exit(violations), 1)

    def test_advisory_plus_critical_plus_warning_returns_one(self):
        """Critical takes priority over warning (exit 1, not 2)."""
        violations = [
            {"rule": "L24", "severity": "advisory", "detail": "acronym 'PCI'"},
            {"rule": "L23", "severity": "critical", "detail": "missing coverage_blueprint"},
            {"rule": "WAIVER", "severity": "warning", "detail": "stale waiver"},
        ]
        self.assertEqual(lp.severity_to_exit(violations), 1)


# ── L25: prompts must be self-contained (no source dependency) ───────────────
class L25SourceDependentPromptTests(unittest.TestCase):
    """L25 fires on prompts that only answerable with the source material in hand.

    These live here, not in test_security_plus_final_review.py, because that
    module's setUpClass raises SkipTest when the private staging artifacts are
    absent — which would silently un-test the rule on any clean checkout.
    """

    def _l25(self, prompt: str) -> list:
        return rules(lp.check_l25_source_dependent_prompt(mc(prompt=prompt)), "L25")

    def test_unambiguous_source_noun_fires_bare(self):
        # "chapter"/"textbook"/"Exam Cram" are never legitimate security content,
        # so they fire without needing an attribution verb.
        for prompt in (
            "According to the chapter, which control is preventive?",
            "Which port does the textbook list for LDAPS?",
            "Per Exam Cram, what is the first incident-response phase?",
            "Which term does the chapter's port table define as 636?",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(len(self._l25(prompt)), 1, f"L25 should fire on: {prompt}")

    def test_ambiguous_noun_fires_only_inside_an_attribution_frame(self):
        # "the author says" is source-dependent; "the author of the CSR" is content.
        self.assertEqual(len(self._l25("What does the author say about salting?")), 1)
        self.assertEqual(len(self._l25("How does the book describe microservices?")), 1)
        self.assertEqual(len(self._l25("Which risks does the module identify?")), 1)

    def test_legitimate_security_prompts_stay_clean(self):
        # Adversarial false-positive set: each contains an ambiguous source noun
        # used as ordinary security vocabulary.
        for prompt in (
            "Which field identifies the author of the signing request?",
            "Which input validation applies to the text field?",
            "Which symbols does the module export at load time?",
            "Which cipher uses a book as its shared key?",
            "Which section of the packet header carries the TTL?",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(self._l25(prompt), [], f"L25 false positive on: {prompt}")

    def test_finding_is_critical(self):
        found = self._l25("According to the chapter, which control is preventive?")
        self.assertEqual(found[0]["severity"], "critical")


# ── L26: exam-invalid question formats ───────────────────────────────────────
class L26ExamInvalidTypeTests(unittest.TestCase):
    def _l26(self, q: dict) -> list:
        return rules(lp.check_l26_exam_invalid_type(q), "L26")

    def test_true_false_and_matching_are_rejected(self):
        for q in (tf(), matching()):
            with self.subTest(type=q["type"]):
                found = self._l26(q)
                self.assertEqual(len(found), 1, f"L26 should fire on {q['type']}")
                self.assertEqual(found[0]["severity"], "critical")

    def test_exam_valid_types_pass(self):
        for q in (mc(), ms()):
            with self.subTest(type=q["type"]):
                self.assertEqual(self._l26(q), [])


# ── Non-waivability (the enforcement property, not just the rules) ───────────
class NonWaivableRuleTests(unittest.TestCase):
    """L25/L26 are quality-bar rules; a waiver must not silence them.

    This is the property the whole gate rests on — if a pack can waive its way
    past L25/L26, the gate is decorative.
    """

    def _lint(self, pack: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            return lp.lint_pack(p)

    def test_l25_and_l26_are_declared_non_waivable(self):
        self.assertEqual(lp.NON_WAIVABLE_RULES, frozenset({"L25", "L26"}))

    def test_waiver_does_not_silence_l25_or_l26(self):
        pack = clean_pack_dict(
            questions=[
                mc(id="q1", prompt="According to the chapter, which control is preventive?"),
                tf(id="q2"),
            ],
            lint_waivers=[
                {"rule": "L25", "reason": "pack-wide: reviewed"},
                {"rule": "L26", "qid": "q2", "reason": "intentional"},
            ],
        )
        res = self._lint(pack)
        for rule in ("L25", "L26"):
            live = rules(res["violations"], rule, "critical")
            self.assertEqual(len(live), 1, f"{rule} must stay live despite a waiver")

    def test_ignored_waiver_is_reported_as_hygiene_warning(self):
        pack = clean_pack_dict(
            questions=[mc(id="q1", prompt="According to the chapter, which control is preventive?")],
            lint_waivers=[{"rule": "L25", "reason": "pack-wide: reviewed"}],
        )
        res = self._lint(pack)
        waiver_notes = [
            v for v in res["violations"]
            if v.get("rule") == "WAIVER" and "L25" in v.get("detail", "")
        ]
        self.assertEqual(len(waiver_notes), 1,
                         "an ignored non-waivable waiver should be flagged exactly once")

    def test_non_waivable_criticals_drive_a_failing_exit_code(self):
        pack = clean_pack_dict(
            questions=[tf(id="q2")],
            lint_waivers=[{"rule": "L26", "qid": "q2", "reason": "intentional"}],
        )
        res = self._lint(pack)
        self.assertEqual(lp.severity_to_exit(res["violations"]), 1)


if __name__ == "__main__":
    unittest.main()
