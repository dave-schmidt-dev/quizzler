"""Tests for the bundler's full install gate (certification hardening M0d).

``scripts/build_pack_assets.py`` must run the SAME install gate as the
manifest builder — ``scripts/install_gate.evaluate`` — on the exact bytes it
bundles. Before M0d the bundler checked only the native contract (L29) and
certification freshness, so a pack failing lint or the coverage gate could
still ship, and it re-read each pack after deciding, so the bytes it copied
were never provably the bytes it gated.

Every test drives the bundler CLI with ``--require-pack`` (the release-build
mode) against a throw-away packs tree, so the assertions cover the exit
contract, not just an in-memory result. Fixtures are gate-clean by
construction: the control test proves a clean pack still bundles, so the
rejection tests below it fail for the reason they name, not incidentally.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_pack_assets as bpa  # noqa: E402
import pack_cert  # noqa: E402


def question(qid: str = "q1", **overrides) -> dict:
    """Return a lint-clean multiple-choice question (numeric options carry no
    L10 tokens; explanation/topic/difficulty satisfy L12)."""
    base = {
        "id": qid,
        "type": "multiple_choice",
        "topic": "math",
        "difficulty": "easy",
        "prompt": "What is 2+2?",
        "options": ["4", "5", "6", "7"],
        "answer": 0,
        "explanation": "Two plus two is four.",
    }
    base.update(overrides)
    return base


def blueprint_for(questions: list[dict]) -> list[dict]:
    """Derive an L23-satisfying blueprint from the questions' topic/area pairs."""
    pairs = sorted({
        (q.get("topic"), q.get("exam_area"))
        for q in questions
        if isinstance(q, dict) and q.get("topic")
    })
    return [
        {"topic": topic, **({"area": area} if area else {}), "min": 1}
        for topic, area in pairs
    ]


def fresh_certification(pack: dict) -> dict:
    """Return a certification block that passes ``pack_cert.certification_fresh``."""
    questions = pack.get("questions", [])
    return {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": "2026-07-20T00:00:00+00:00",
        "questions_hash": pack_cert.questions_hash(pack),
        "critic_model": "test",
        "review_method": "external-layer-c-strict",
        "blocking_count": 0,
        "questions_examined": len(questions) if isinstance(questions, list) else 0,
        "question_stamps": pack_cert.build_question_stamps(pack),
    }


def gate_clean_pack(questions: list[dict], *, pack_id: str = "bundler-gate-fixture",
                    blueprint: list[dict] | None = None, **overrides) -> dict:
    """Return a pack that passes the full install gate (L29 + lint + cert)."""
    body = {
        "pack_id": pack_id,
        "subject": "Fixture Course",
        "title": "Bundler Gate Fixture",
        "version": 1,
        "coverage_blueprint": blueprint_for(questions) if blueprint is None else blueprint,
        "questions": questions,
    }
    body.update(overrides)
    body["certification"] = fresh_certification(body)
    return body


def area_questions(count: int, *, split: bool = True) -> list[dict]:
    """Return `count` lint-clean questions split between syllabus areas a1/a2.

    With ``split=False`` all but the last question name a1, producing a course
    whose area distribution is skewed past the published-weight band.
    """
    questions = []
    for index in range(count):
        area = "a1" if (index < count // 2 if split else index < count - 1) else "a2"
        questions.append(question(
            f"q{index}",
            prompt=f"Which value is correct for fixture item {index}?",
            exam_area=area,
        ))
    return questions


def blueprint_skewed_course() -> tuple[list[dict], list[dict]]:
    """Return questions and a skewed blueprint for the L27-BLUEPRINT-DISTRIBUTION case.

    Actual area counts stay balanced (19 vs 19) so the area-distribution check
    does not fire; only the blueprint's declared minimums are skewed (19 vs 1).
    """
    clean = {
        "type": "multiple_choice", "difficulty": "easy",
        "prompt": "What is 2+2?", "options": ["4", "5", "6", "7"], "answer": 0,
        "explanation": "Two plus two is four.",
    }
    questions: list[dict] = []
    blueprint: list[dict] = []
    for index in range(19):
        topic = f"a1-t{index}"
        questions.append({**clean, "id": f"q-a1-{index}", "topic": topic,
                          "exam_area": "a1",
                          "prompt": f"Which value is correct for fixture item {index}?"})
        blueprint.append({"topic": topic, "area": "a1", "min": 1})
    questions.append({**clean, "id": "q-a2-0", "topic": "a2-t0", "exam_area": "a2",
                      "prompt": "Which value is correct for fixture item 19?"})
    blueprint.append({"topic": "a2-t0", "area": "a2", "min": 1})
    for index in range(18):
        questions.append({
            **clean, "id": f"q-a2-extra-{index}", "topic": f"a2-extra-{index}",
            "exam_area": "a2",
            "prompt": f"Which value is correct for fixture item {20 + index}?",
        })
    return questions, blueprint


WEIGHTED_SYLLABUS = {
    "source": {"kind": "syllabus", "title": "Fixture syllabus"},
    "areas": [
        {"id": "a1", "name": "Area One", "weight": 50},
        {"id": "a2", "name": "Area Two", "weight": 50},
    ],
}

SINGLE_AREA_SYLLABUS = {
    "source": {"kind": "syllabus", "title": "Fixture syllabus"},
    "areas": [{"id": "a1", "name": "Area One"}],
}


class BundlerGateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.packs_root = self.root / "question-packs"
        self.destination = self.root / "Resources"
        self.packs_root.mkdir()

    def write_pack(self, course: str, name: str, body: dict) -> Path:
        course_directory = self.packs_root / course
        course_directory.mkdir(parents=True, exist_ok=True)
        path = course_directory / name
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        return path

    def write_course(self, course: str, syllabus: dict) -> None:
        course_directory = self.packs_root / course
        course_directory.mkdir(parents=True, exist_ok=True)
        (course_directory / "_course.json").write_text(
            json.dumps({"id": course, "syllabus": syllabus}), encoding="utf-8")

    def run_cli(self) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bpa.main([
                "--packs-root", str(self.packs_root),
                "--destination", str(self.destination),
                "--require-pack",
                "--quiet",
            ])
        return rc, err.getvalue()

    def manifest(self) -> dict:
        return json.loads(
            (self.destination / bpa.MANIFEST_NAME).read_text(encoding="utf-8"))


class CleanPackBundlesTests(BundlerGateTestCase):
    """Control: a gate-clean pack still bundles, so the refusal tests isolate."""

    def test_a_gate_clean_pack_bundles(self) -> None:
        self.write_pack("fixture", "mod1.json", gate_clean_pack([question()]))
        rc, err = self.run_cli()
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            [entry["pack_id"] for entry in self.manifest()["packs"]],
            ["bundler-gate-fixture"],
        )


class GateRejectionTests(BundlerGateTestCase):
    """Each gate failure must refuse the pack and fail the release build."""

    def assert_refused(self, fragment: str) -> None:
        rc, err = self.run_cli()
        self.assertEqual(rc, 1, err)
        self.assertIn("REFUSED", err)
        self.assertIn(fragment, err)
        self.assertEqual(self.manifest()["packs"], [])

    def test_a_pack_without_a_coverage_blueprint_is_refused(self) -> None:
        body = gate_clean_pack([question()])
        body.pop("coverage_blueprint")
        self.write_pack("fixture", "mod1.json", body)
        self.assert_refused("missing coverage_blueprint")

    def test_coverage_below_a_blueprint_minimum_is_refused(self) -> None:
        questions = [
            question("q1"),
            question("q2", topic="algebra", prompt="What is 3+3?"),
        ]
        self.write_pack("fixture", "mod1.json", gate_clean_pack(
            questions,
            blueprint=[{"topic": "math", "min": 2}, {"topic": "algebra", "min": 1}],
        ))
        self.assert_refused("coverage_blueprint requires >=2 question(s) on topic 'math'")

    def test_a_course_area_distribution_failure_is_refused(self) -> None:
        self.write_course("skewed", WEIGHTED_SYLLABUS)
        self.write_pack(
            "skewed", "mod1.json", gate_clean_pack(area_questions(20, split=False)))
        self.assert_refused("L27-DISTRIBUTION")

    def test_a_course_blueprint_distribution_failure_is_refused(self) -> None:
        self.write_course("skewed", WEIGHTED_SYLLABUS)
        questions, blueprint = blueprint_skewed_course()
        self.write_pack("skewed", "mod1.json", gate_clean_pack(
            questions, blueprint=blueprint, pack_id="skewed-blueprint"))
        self.assert_refused("L27-BLUEPRINT-DISTRIBUTION")

    def test_a_phantom_exam_area_is_refused(self) -> None:
        self.write_course("taxonomy", SINGLE_AREA_SYLLABUS)
        self.write_pack("taxonomy", "mod1.json", gate_clean_pack([
            question("q1", exam_area="a1"),
            question("q2", prompt="What is 3+3?", exam_area="ghost-area"),
        ]))
        self.assert_refused("ghost-area")

    def test_a_lint_critical_is_refused(self) -> None:
        dirty = question()
        dirty.pop("explanation")  # L12 critical
        self.write_pack("fixture", "mod1.json", gate_clean_pack([dirty]))
        self.assert_refused("L12")


class GatedBytesTests(BundlerGateTestCase):
    """The bundle carries the bytes the gate admitted, not a later re-read."""

    def test_a_source_mutated_after_gating_still_bundles_the_gated_bytes(self) -> None:
        source = self.write_pack("fixture", "mod1.json", gate_clean_pack([question()]))
        assets, rejections = bpa.collect_packs(self.packs_root, lambda _message: None)
        self.assertEqual(rejections, [])
        self.assertEqual(len(assets), 1)
        gated_bytes = assets[0]["_raw_bytes"]
        gated_data = json.loads(gated_bytes.decode("utf-8"))

        # Mutate the source AFTER the gate admitted its bytes.
        source.write_text(
            json.dumps({"pack_id": "tampered", "questions": []}), encoding="utf-8")

        bpa.write_bundle(assets, self.destination, lambda _message: None)

        bundled = self.destination / bpa.PACKS_SUBDIRECTORY / assets[0]["path"]
        self.assertEqual(bundled.read_bytes(), gated_bytes)
        self.assertNotEqual(bundled.read_bytes(), source.read_bytes())
        entry = self.manifest()["packs"][0]
        self.assertEqual(entry["content_digest"], bpa.content_digest(gated_data))
        self.assertEqual(
            bpa.content_digest(json.loads(bundled.read_text(encoding="utf-8"))),
            entry["content_digest"],
        )


if __name__ == "__main__":
    unittest.main()
