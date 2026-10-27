"""Unit tests for ``scripts/lint_packs.py`` — coverage and blueprint rules."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.lint_pack_fixtures import (
    PROJECT_ROOT,
    clean_pack_dict,
    lp,
    matching,
    mc,
    ms,
    rules,
    tf,
)


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

    def test_blueprint_parser_keys_entries_on_topic_and_area(self):
        self.assertEqual(
            lp._parse_blueprint([
                {"topic": " Crypto ", "area": " Domain 3 ", "min": 2},
            ]),
            [("crypto", "domain 3", 2)],
        )

    def test_objective_blueprint_requires_objective_bearing_questions(self):
        data = {"coverage_blueprint": [{"objective": "1.1", "area": "domain-1", "min": 1}]}
        missing = self._l23(data, [mc(id="q1", exam_area="domain-1")], "critical")
        self.assertEqual(len(missing), 1)
        self.assertIn("objective '1.1'", missing[0]["detail"])
        covered = self._l23(
            data,
            [mc(id="q1", exam_area="domain-1", exam_objective="1.1")],
            "critical",
        )
        self.assertEqual(covered, [])

    def test_same_topic_in_two_areas_satisfies_both_blueprint_entries(self):
        data = {"coverage_blueprint": [
            {"topic": "crypto", "area": "domain-3", "min": 1},
            {"topic": "crypto", "area": "domain-4", "min": 1},
        ]}
        qs = [
            mc(id="q1", topic="crypto", exam_area="domain-3"),
            mc(id="q2", topic="crypto", exam_area="domain-4"),
        ]
        self.assertEqual(self._l23(data, qs, "critical"), [])

    def test_same_topic_in_wrong_area_is_not_accepted(self):
        data = {"coverage_blueprint": [
            {"topic": "crypto", "area": "domain-4", "min": 1},
        ]}
        qs = [mc(id="q1", topic="crypto", exam_area="domain-3")]
        crit = self._l23(data, qs, "critical")
        self.assertEqual(len(crit), 1)
        self.assertIn("area 'domain-4'", crit[0]["detail"])
        self.assertIn("found 0", crit[0]["detail"])

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


class L23AmbiguousAreaTests(unittest.TestCase):
    """(topic, area) keying — Q.9 Layer A: same topic, inconsistent exam_area."""

    def _ambiguous(self, questions):
        out = rules(lp.check_l23_coverage_completeness({}, questions), "L23", "warning")
        return [f for f in out if "is used with" in f["detail"]]

    def test_same_topic_two_areas_is_warning_not_critical(self):
        qs = [mc(id="q1", topic="risk-assessment", exam_area="domain-1"),
              mc(id="q2", topic="risk-assessment", exam_area="domain-6")]
        found = self._ambiguous(qs)
        self.assertEqual(len(found), 1)
        self.assertIn("risk-assessment", found[0]["detail"])
        self.assertIsNone(found[0]["qid"])
        crit = rules(lp.check_l23_coverage_completeness({}, qs), "L23", "critical")
        self.assertFalse(any("risk-assessment" in f["detail"] for f in crit))

    def test_same_topic_same_area_is_not_ambiguous(self):
        qs = [mc(id="q1", topic="crypto", exam_area="domain-3"),
              mc(id="q2", topic="crypto", exam_area="domain-3")]
        self.assertEqual(self._ambiguous(qs), [])

    def test_distinct_topics_different_areas_is_not_ambiguous(self):
        qs = [mc(id="q1", topic="crypto", exam_area="domain-3"),
              mc(id="q2", topic="risk-assessment", exam_area="domain-1")]
        self.assertEqual(self._ambiguous(qs), [])

    def test_question_missing_exam_area_does_not_false_fire(self):
        qs = [mc(id="q1", topic="crypto", exam_area="domain-3"),
              mc(id="q2", topic="crypto")]
        self.assertEqual(self._ambiguous(qs), [])


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
        for pack_path in lp.pack_discovery.iter_installable_packs(self.PACKS_DIR):
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

    These use synthetic fixtures rather than private staged packs, so the rule
    stays tested on any clean checkout.
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

    def test_the_non_waivable_set_includes_objective_integrity(self):
        """A waiver cannot make an invalid native or objective contract valid."""
        self.assertEqual(lp.NON_WAIVABLE_RULES, frozenset({"L25", "L26", "L27", "L29", "L30"}))

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
