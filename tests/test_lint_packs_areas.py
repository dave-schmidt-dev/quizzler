"""Unit tests for ``scripts/lint_packs.py`` — exam-area alignment (L27, L30)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.lint_pack_fixtures import (
    PROJECT_ROOT,
    _fenced_json_after,
    clean_pack_dict,
    lp,
    mc,
    rules,
)


# ── L27 — exam-area alignment ────────────────────────────────────────────────
class L27ExamAreaTests(unittest.TestCase):
    """The taxonomy is published and cited; every question names a declared area.

    L27 needs a real course DIRECTORY (the taxonomy lives in the sibling
    `_course.json`), so unlike the other rules these fixtures write a two-file
    tree rather than a bare pack.
    """

    AREAS = [
        {"id": "1.0", "name": "General Security Concepts", "weight": 60},
        {"id": "2.0", "name": "Threats and Vulnerabilities", "weight": 40},
    ]

    def _lint_in_course(self, pack: dict, course: dict | None) -> list:
        """Lint `pack` inside a tmp course dir; return its L27 findings."""
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            if course is not None:
                (course_dir / "_course.json").write_text(json.dumps(course))
            p = course_dir / "pack.json"
            p.write_text(json.dumps(pack))
            return rules(lp.lint_pack(p)["violations"], "L27")

    def _course(self, **over) -> dict:
        base = {
            "id": "c", "name": "C",
            "syllabus": {
                "source": {
                    "kind": "exam_objectives",
                    "title": "Vendor Exam Objectives v1",
                    "url": "https://example.test/objectives.pdf",
                    "syllabus_verified_by": {
                        "reviewer": "independent-objective-reviewer",
                        "date": "2026-08-07",
                    },
                },
                "areas": [dict(a) for a in self.AREAS],
            },
        }
        base.update(over)
        return base

    def _pack(self, *areas: str) -> dict:
        qs = [mc(id=f"q{i}", exam_area=a) for i, a in enumerate(areas, 1)]
        return clean_pack_dict(questions=qs)

    def test_declared_syllabus_area_without_blueprint_entry_is_critical(self):
        """Mutation test: deleting declared-area reachability must fail here."""
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = [{"topic": "t", "area": "1.0", "min": 1}]
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("'2.0'", found[0]["detail"])
        self.assertIn("not named", found[0]["detail"])

    def test_blueprint_area_not_declared_in_syllabus_is_critical(self):
        """Mutation test: deleting blueprint referential integrity must fail."""
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = [
            {"topic": "t", "area": "1.0", "min": 1},
            {"topic": "t", "area": "2.0", "min": 1},
            {"topic": "t", "area": "9.9", "min": 1},
        ]
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("'9.9'", found[0]["detail"])
        self.assertIn("undeclared", found[0]["detail"])

    def test_blueprint_and_syllabus_areas_match_bidirectionally(self):
        pack = self._pack("1.0", "2.0")
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        self.assertEqual(found, [])

    def test_topic_only_blueprint_does_not_reach_published_areas(self):
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = [{"topic": "legacy-topic", "min": 1}]
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        self.assertEqual(len(found), 2)
        self.assertTrue(all("not named" in finding["detail"] for finding in found))

    def test_pack_template_questions_pass_l27_in_a_declared_course(self):
        template = json.loads(
            (PROJECT_ROOT / "question-packs" / "pack-template.json").read_text()
        )
        course = {
            "id": "template-course",
            "name": "Template Course",
            "syllabus": {
                "source": {
                    "kind": "syllabus",
                    "title": "Template syllabus",
                },
                "areas": [{"id": "area-id", "name": "Example area", "weight": 100}],
            },
        }
        self.assertEqual(self._lint_in_course(template, course), [])

    # -- the skip condition ---------------------------------------------------
    def test_a_pack_outside_a_course_is_not_gated(self):
        """A bare file has no course to declare a syllabus; gating it would
        gate on the caller's directory layout, not on the pack."""
        self.assertEqual(self._lint_in_course(self._pack("1.0"), course=None), [])

    def test_unparseable_course_metadata_is_an_l27_critical(self):
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            (course_dir / "_course.json").write_text("{ not json")
            pack_path = course_dir / "pack.json"
            pack_path.write_text(json.dumps(self._pack("1.0")))
            found = rules(lp.lint_pack(pack_path)["violations"], "L27", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("unparseable", found[0]["detail"])

    def test_non_dict_course_metadata_is_an_l27_critical(self):
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            (course_dir / "_course.json").write_text(json.dumps([1, 2, 3]))
            pack_path = course_dir / "pack.json"
            pack_path.write_text(json.dumps(self._pack("1.0")))
            found = rules(lp.lint_pack(pack_path)["violations"], "L27", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("non-dict", found[0]["detail"])

    # -- referential integrity: the reason this rule exists --------------------
    def test_an_undeclared_exam_area_is_critical(self):
        found = rules(
            self._lint_in_course(self._pack("1.0", "9.9"), self._course()),
            "L27", "critical",
        )
        question_findings = [f for f in found if f["qid"] == "q2"]
        self.assertEqual(len(question_findings), 1)
        self.assertIn("'9.9'", question_findings[0]["detail"])

    def test_a_missing_exam_area_is_critical(self):
        pack = clean_pack_dict(questions=[mc(id="q1")])  # no exam_area at all
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        question_findings = [f for f in found if f["qid"] == "q1"]
        self.assertEqual(len(question_findings), 1)

    def test_a_fully_aligned_pack_is_clean(self):
        found = self._lint_in_course(self._pack("1.0", "2.0"), self._course())
        self.assertEqual(rules(found, "L27", "critical"), [])
        self.assertEqual(rules(found, "L27", "warning"), [])

    # -- the taxonomy itself --------------------------------------------------
    def test_a_course_declaring_no_areas_is_critical(self):
        found = rules(
            self._lint_in_course(self._pack("1.0"), self._course(syllabus={})),
            "L27", "critical",
        )
        self.assertEqual(len(found), 1)
        self.assertIn("syllabus.areas", found[0]["detail"])

    def test_an_uncited_taxonomy_is_critical(self):
        course = self._course()
        course["syllabus"]["source"] = {}
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("kind" in f["detail"] for f in found))

    def test_kind_none_is_a_legal_explicit_declaration(self):
        """'no published authority' must be sayable, or authors omit the field."""
        course = self._course()
        course["syllabus"]["source"] = {"kind": "none"}
        found = self._lint_in_course(self._pack("1.0", "2.0"), course)
        self.assertEqual(rules(found, "L27", "critical"), [])

    def test_kind_none_has_no_url_or_attestation_requirement(self):
        course = self._course()
        course["syllabus"]["source"] = {"kind": "none"}
        found = self._lint_in_course(self._pack("1.0", "2.0"), course)
        self.assertFalse(any("url" in f["detail"] or "syllabus_verified_by" in f["detail"]
                             for f in rules(found, "L27")))

    def test_kind_syllabus_has_no_url_or_attestation_requirement(self):
        course = self._course()
        course["syllabus"]["source"] = {"kind": "syllabus", "title": "Class syllabus"}
        found = self._lint_in_course(self._pack("1.0", "2.0"), course)
        self.assertFalse(any("url" in f["detail"] or "syllabus_verified_by" in f["detail"]
                             for f in rules(found, "L27")))

    def test_exam_objectives_without_a_url_is_critical(self):
        course = self._course()
        course["syllabus"]["source"].pop("url")
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("url" in f["detail"] for f in found))

    def test_exam_objectives_require_an_absolute_https_url(self):
        for url in ("http://example.test/objectives", "/objectives.pdf", "https://"):
            with self.subTest(url=url):
                course = self._course()
                course["syllabus"]["source"]["url"] = url
                found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                              "L27", "critical")
                self.assertTrue(any("parseable absolute https URL" in f["detail"]
                                    for f in found))

    def test_exam_objectives_with_absolute_https_url_have_no_url_finding(self):
        found = self._lint_in_course(self._pack("1.0", "2.0"), self._course())
        self.assertFalse(any("url" in f["detail"] for f in rules(found, "L27")))

    def test_exam_objectives_require_independent_reviewer_and_date(self):
        course = self._course()
        course["syllabus"]["source"].pop("syllabus_verified_by")
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("syllabus_verified_by" in f["detail"] for f in found))

    def test_exam_objectives_with_reviewer_and_date_have_no_attestation_finding(self):
        found = self._lint_in_course(self._pack("1.0", "2.0"), self._course())
        self.assertFalse(any("syllabus_verified_by" in f["detail"]
                             for f in rules(found, "L27")))

    def test_duplicate_area_ids_are_critical(self):
        course = self._course()
        course["syllabus"]["areas"][1]["id"] = "1.0"
        found = rules(self._lint_in_course(self._pack("1.0"), course),
                      "L27", "critical")
        self.assertTrue(any("declared 2 times" in f["detail"] for f in found))

    # -- weights --------------------------------------------------------------
    def test_weights_that_do_not_total_100_are_critical(self):
        course = self._course()
        course["syllabus"]["areas"][0]["weight"] = 55  # 55 + 40 = 95
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("sum to 95" in f["detail"] for f in found))

    def test_nan_area_weight_is_critical(self):
        course = self._course()
        course["syllabus"]["areas"][0]["weight"] = float("nan")
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("must be finite" in f["detail"] for f in found))

    def test_infinity_area_weight_is_critical(self):
        course = self._course()
        course["syllabus"]["areas"][0]["weight"] = float("inf")
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("must be finite" in f["detail"] for f in found))

    def test_negative_area_weight_is_critical_even_when_total_is_100(self):
        course = self._course()
        course["syllabus"]["areas"][0]["weight"] = -50
        course["syllabus"]["areas"][1]["weight"] = 150
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("must be non-negative" in f["detail"] for f in found))

    def test_finite_non_negative_weights_totaling_100_have_no_weight_finding(self):
        found = self._lint_in_course(self._pack("1.0", "2.0"), self._course())
        weight_findings = [f for f in found if "weight" in f["detail"]]
        self.assertEqual(weight_findings, [])

    def test_area_weight_count_range_has_inclusive_expected_bounds(self):
        self.assertEqual(lp.area_weight_count_range(60, 20), (12, 11, 13))
        self.assertEqual(lp.area_weight_count_range(40, 20), (8, 7, 9))

    def test_area_weight_count_range_skips_small_packs(self):
        self.assertIsNone(lp.area_weight_count_range(50, 19))

    def test_skewed_weighted_pack_has_a_critical_distribution_finding(self):
        # Keep both blueprint areas reachable so this isolates distribution.
        pack = self._pack(*(["1.0"] * 19 + ["2.0"]))
        found = self._lint_in_course(pack, self._course())
        distribution = [f for f in found if "L27-DISTRIBUTION" in f["detail"]]
        self.assertEqual(len(distribution), 2)
        self.assertTrue(all(f["severity"] == "critical" for f in distribution))

    def test_balanced_weighted_pack_has_no_distribution_finding(self):
        pack = self._pack(*(["1.0"] * 12 + ["2.0"] * 8))
        found = self._lint_in_course(pack, self._course())
        self.assertFalse(any("L27-DISTRIBUTION" in f["detail"] for f in found))

    def test_below_floor_weighted_pack_is_exempt_from_distribution(self):
        pack = self._pack(*(["1.0"] * 19))
        found = self._lint_in_course(pack, self._course())
        self.assertFalse(any("L27-DISTRIBUTION" in f["detail"] for f in found))

    def test_no_weight_course_is_exempt_from_distribution(self):
        course = self._course()
        for area in course["syllabus"]["areas"]:
            area.pop("weight")
        pack = self._pack(*(["1.0"] * 19 + ["2.0"]))
        found = self._lint_in_course(pack, course)
        self.assertFalse(any("L27-DISTRIBUTION" in f["detail"] for f in found))

    # -- blueprint-minimum distribution (Q.9 Layer C) --------------------------
    def _blueprint(self, *area_counts):
        """One coverage_blueprint entry per unit — `_blueprint("1.0", "1.0")`
        declares two min:1 entries under area '1.0'."""
        return [
            {"topic": f"t{i}", "area": area, "min": 1}
            for i, area in enumerate(area_counts)
        ]

    def test_skewed_blueprint_has_a_critical_distribution_finding(self):
        pack = self._pack("1.0", "2.0")  # keep reachability + finding-11 clean
        pack["coverage_blueprint"] = self._blueprint(*(["1.0"] * 19 + ["2.0"]))
        found = self._lint_in_course(pack, self._course())
        distribution = [f for f in found if "L27-BLUEPRINT-DISTRIBUTION" in f["detail"]]
        self.assertEqual(len(distribution), 2)
        self.assertTrue(all(f["severity"] == "critical" for f in distribution))

    def test_balanced_blueprint_has_no_distribution_finding(self):
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = self._blueprint(*(["1.0"] * 12 + ["2.0"] * 8))
        found = self._lint_in_course(pack, self._course())
        self.assertFalse(any("L27-BLUEPRINT-DISTRIBUTION" in f["detail"] for f in found))

    def test_below_floor_blueprint_is_exempt_from_distribution(self):
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = self._blueprint(*(["1.0"] * 18 + ["2.0"]))
        found = self._lint_in_course(pack, self._course())
        self.assertFalse(any("L27-BLUEPRINT-DISTRIBUTION" in f["detail"] for f in found))

    def test_topic_only_blueprint_entries_are_exempt_from_distribution(self):
        # Entries without `area` are legacy L23 wildcards, not distribution units.
        pack = self._pack("1.0", "2.0")
        pack["coverage_blueprint"] = [{"topic": f"t{i}", "min": 1} for i in range(25)]
        found = self._lint_in_course(pack, self._course())
        self.assertFalse(any("L27-BLUEPRINT-DISTRIBUTION" in f["detail"] for f in found))

    def test_a_partial_weight_set_is_critical(self):
        course = self._course()
        course["syllabus"]["areas"][1].pop("weight")
        found = rules(self._lint_in_course(self._pack("1.0", "2.0"), course),
                      "L27", "critical")
        self.assertTrue(any("1 of 2 areas" in f["detail"] for f in found))

    def test_a_published_source_without_weights_is_advisory_not_blocking(self):
        course = self._course()
        for a in course["syllabus"]["areas"]:
            a.pop("weight")
        found = self._lint_in_course(self._pack("1.0", "2.0"), course)
        self.assertEqual(rules(found, "L27", "critical"), [])
        self.assertEqual(len(rules(found, "L27", "advisory")), 1)

    def test_kind_none_gets_no_weights_nudge(self):
        """A demo/self-authored outline has no published percentages to copy."""
        course = self._course()
        course["syllabus"]["source"] = {"kind": "none"}
        for a in course["syllabus"]["areas"]:
            a.pop("weight")
        found = self._lint_in_course(self._pack("1.0", "2.0"), course)
        self.assertEqual(rules(found, "L27", "advisory"), [])

    # -- unused areas are a module-level fact, not a failure ------------------
    def test_an_area_with_no_questions_is_advisory_only(self):
        found = self._lint_in_course(self._pack("1.0"), self._course())
        critical = rules(found, "L27", "critical")
        self.assertTrue(any("'2.0'" in f["detail"] and "not named" in f["detail"]
                            for f in critical))
        advisory = rules(found, "L27", "advisory")
        self.assertEqual(len(advisory), 1)
        self.assertIn("2.0", advisory[0]["detail"])
        self.assertEqual(lp.severity_to_exit(advisory), 0)

    # -- non-waivability ------------------------------------------------------
    def test_a_waiver_does_not_silence_l27(self):
        pack = clean_pack_dict(
            questions=[mc(id="q1", exam_area="9.9")],
            lint_waivers=[{"rule": "L27", "reason": "pack-wide: reviewed"}],
        )
        found = rules(self._lint_in_course(pack, self._course()), "L27", "critical")
        self.assertTrue(any(f["qid"] == "q1" for f in found))


class L27ShippedSamplesTests(unittest.TestCase):
    """The committed sample course is the public reference shape for L27."""

    def test_the_sample_course_declares_a_cited_syllabus(self):
        meta = json.loads(
            (PROJECT_ROOT / "question-packs" / "samples" / "_course.json").read_text())
        syllabus = meta.get("syllabus")
        self.assertIsInstance(syllabus, dict, "samples/_course.json must declare `syllabus`")
        self.assertIn(syllabus.get("source", {}).get("kind"), lp.L27_SOURCE_KINDS)
        self.assertTrue(syllabus.get("areas"))

    def test_every_sample_question_names_a_declared_area(self):
        pack = PROJECT_ROOT / "question-packs" / "samples" / "sample-pack.json"
        found = rules(lp.lint_pack(pack)["violations"], "L27")
        blocking = [f for f in found if f["severity"] in ("critical", "warning")]
        self.assertEqual(blocking, [], f"shipped sample pack fails L27: {blocking}")


class L30ExamObjectiveTests(unittest.TestCase):
    """Objective-level coverage is enabled only by an explicit course taxonomy."""

    def _lint(self, pack: dict, objectives: list[dict]) -> list:
        course = {
            "id": "c", "name": "C",
            "syllabus": {
                "source": {"kind": "none"},
                "areas": [{"id": "domain-1", "name": "Domain 1"}],
                "objectives": objectives,
            },
        }
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            (course_dir / "_course.json").write_text(json.dumps(course))
            path = course_dir / "pack.json"
            path.write_text(json.dumps(pack))
            return rules(lp.lint_pack(path)["violations"], "L30", "critical")

    def test_missing_question_objective_is_critical(self):
        pack = clean_pack_dict(
            coverage_blueprint=[{"objective": "1.1", "area": "domain-1", "min": 1}],
            questions=[mc(id="q1", exam_area="domain-1")],
        )
        found = self._lint(pack, [{"id": "1.1", "area": "domain-1", "name": "Ethics"}])
        self.assertTrue(any(f["qid"] == "q1" and "exam_objective" in f["detail"] for f in found))

    def test_undeclared_or_unplanned_objective_is_critical(self):
        pack = clean_pack_dict(
            coverage_blueprint=[{"objective": "1.1", "area": "domain-1", "min": 1}],
            questions=[mc(id="q1", exam_area="domain-1", exam_objective="1.2")],
        )
        found = self._lint(pack, [{"id": "1.1", "area": "domain-1", "name": "Ethics"}])
        self.assertTrue(any(f["qid"] == "q1" and "not declared" in f["detail"] for f in found))

    def test_every_declared_objective_requires_a_blueprint_entry(self):
        pack = clean_pack_dict(
            coverage_blueprint=[{"objective": "1.1", "area": "domain-1", "min": 1}],
            questions=[mc(id="q1", exam_area="domain-1", exam_objective="1.1")],
        )
        found = self._lint(pack, [
            {"id": "1.1", "area": "domain-1", "name": "Ethics"},
            {"id": "1.2", "area": "domain-1", "name": "Concepts"},
        ])
        self.assertTrue(any("'1.2'" in f["detail"] for f in found))

    def test_objective_blueprint_rejects_non_positive_minimum(self):
        pack = clean_pack_dict(
            coverage_blueprint=[{"objective": "1.1", "area": "domain-1", "min": 0}],
            questions=[mc(id="q1", exam_area="domain-1", exam_objective="1.1")],
        )
        found = self._lint(pack, [{"id": "1.1", "area": "domain-1", "name": "Ethics"}])
        self.assertTrue(any("invalid `min`" in f["detail"] for f in found))


class L27DocExampleTests(unittest.TestCase):
    """The `syllabus` examples in the docs must themselves pass L27.

    A copy-paste-ready example that fails the rule it documents is worse than no
    example: the author's first lint run rejects the thing the docs told them to
    write, and the natural reading is that the rule is broken. This caught both
    shipped examples abridging a real vendor's domain list down to two entries,
    whose weights then summed to 34 instead of 100 (L27 finding 5).
    """

    def _assert_syllabus_lints_clean(self, syllabus: dict, where: str) -> None:
        """Lint a pack that uses every declared area, in a tmp course dir."""
        areas = syllabus.get("areas") or []
        self.assertTrue(areas, f"{where}: example declares no areas")
        source = syllabus.get("source")
        if isinstance(source, dict) and source.get("kind") == "exam_objectives":
            # These examples predate Task 1.5's source attestation. Keep this
            # helper focused on the documented area shape; dedicated tests
            # above cover the URL and attestation requirements themselves.
            source["url"] = "https://vendor.example/objectives.pdf"
            source["syllabus_verified_by"] = {
                "reviewer": "independent-objective-reviewer",
                "date": "2026-08-07",
            }
        pack = clean_pack_dict(questions=[
            mc(id=f"q{i}", exam_area=a["id"]) for i, a in enumerate(areas, 1)])
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            (course_dir / "_course.json").write_text(
                json.dumps({"id": "c", "name": "C", "syllabus": syllabus}))
            p = course_dir / "pack.json"
            p.write_text(json.dumps(pack))
            found = rules(lp.lint_pack(p)["violations"], "L27")
        self.assertEqual(found, [], f"{where} example fails the rule it documents: {found}")

    def test_the_authoring_course_json_example_passes_l27(self):
        block = _fenced_json_after(
            PROJECT_ROOT / "question-packs" / "AUTHORING.md",
            "Add a `_course.json` file in that folder:")
        meta = json.loads(block)
        self.assertIn("syllabus", meta, "AUTHORING.md _course.json example dropped `syllabus`")
        self._assert_syllabus_lints_clean(meta["syllabus"], "AUTHORING.md")

    def test_the_validation_rules_syllabus_example_passes_l27(self):
        # A fragment, not a whole object — it is shown in the context of
        # `_course.json`, so wrap it back into one before parsing.
        block = _fenced_json_after(
            PROJECT_ROOT / "docs" / "VALIDATION_RULES.md",
            "The course declares its taxonomy once, in `_course.json`:")
        syllabus = json.loads("{" + block + "}")["syllabus"]
        # The `source` values are elided as "…" in this doc for brevity; restore
        # a citation so the test measures the AREAS, which is what it is about.
        syllabus["source"] = {
            "kind": "exam_objectives", "title": "Example Objectives",
            "url": "https://vendor.example/objectives.pdf",
            "syllabus_verified_by": {
                "reviewer": "independent-objective-reviewer",
                "date": "2026-08-07",
            },
        }
        self._assert_syllabus_lints_clean(syllabus, "docs/VALIDATION_RULES.md")


if __name__ == "__main__":
    unittest.main()
