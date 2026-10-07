"""Tests for pack-level quarantine and partial-install admission (M3).

Campaign quarantine (M2) shrinks a campaign's frontier in the evidence ledger;
pack quarantine is the pack-file counterpart: it removes the quarantined
questions from the installed pack, records them in a sidecar together with the
free-text reason, and marks the pack partial. The install gate -- wired once,
in ``install_gate.evaluate`` -- refuses a partial pack everywhere (manifest,
release bundler, snapshot) unless the bundler explicitly allows partials, and
even then the retained subset must still pass every quality bar: lint
criticals (including L23 blueprint coverage), the course distribution
aggregates, and a fresh certification.

These tests never invoke a reviewer. Certifications are constructed with the
same deterministic helpers the other gate suites use; pack_quarantine itself
never writes certification metadata (INV-7).

Run from the project root::

    python3 -m unittest tests.test_pack_quarantine -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_pack_assets as bpa  # noqa: E402
import install_gate  # noqa: E402
import pack_cert  # noqa: E402
import pack_quarantine as pq  # noqa: E402


def question(qid: str, number: int, topic: str = "topic",
             area: str = "area") -> dict:
    """Return a lint-clean question (numeric options carry no L10 tokens)."""
    return {
        "id": qid,
        "type": "multiple_choice",
        "topic": topic,
        "exam_area": area,
        "difficulty": "easy",
        "prompt": f"What is {number}+{number}?",
        "explanation": f"{number} plus {number} is {2 * number}.",
        "options": [str(2 * number), "1", "3", "5"],
        "answer": 0,
    }


def certify(body: dict) -> dict:
    """Attach a fresh certification over the body's CURRENT questions."""
    body["certification"] = {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "questions_hash": pack_cert.questions_hash(body),
        "review_method": "external-layer-c-strict",
        "blocking_count": 0,
        "questions_examined": len(body["questions"]),
        "question_stamps": pack_cert.build_question_stamps(body),
    }
    return body


def pack_body(pack_id: str, questions: list[dict],
              blueprint: list[dict] | None = None) -> dict:
    """Return a pack that passes the full install gate (L29, lint, L23, cert)."""
    return certify({
        "pack_id": pack_id,
        "subject": "Demo",
        "title": "Core",
        "version": 1,
        "coverage_blueprint": blueprint
        or [{"topic": "topic", "area": "area", "min": 1}],
        "questions": questions,
    })


class PackQuarantineCase(unittest.TestCase):
    """A packs tree on disk plus the bundler/CLI helpers to exercise it."""

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

    def write_course(self, course: str, metadata: dict) -> None:
        course_directory = self.packs_root / course
        course_directory.mkdir(parents=True, exist_ok=True)
        (course_directory / "_course.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8")

    def reload(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rewrite(self, path: Path, body: dict) -> None:
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")

    def manifest(self) -> dict:
        return json.loads(
            (self.destination / bpa.MANIFEST_NAME).read_text(encoding="utf-8"))

    def run_main(self, *extra: str) -> int:
        return bpa.main([
            "--packs-root", str(self.packs_root),
            "--destination", str(self.destination),
            "--quiet", *extra,
        ])

    def make_partial(self, questions: list[dict], drop: list[str],
                     blueprint: list[dict] | None = None,
                     course: str = "course-a", name: str = "ch01.json",
                     recertify: bool = True) -> Path:
        """Install a pack, quarantine `drop` from it, and optionally re-stamp."""
        pack = self.write_pack(
            course, name, pack_body(name.removesuffix(".json"), questions,
                                   blueprint))
        pq.quarantine(pack, drop, "quarantined by the campaign verifier")
        if recertify:
            self.rewrite(pack, certify(self.reload(pack)))
        return pack


class QuarantineRecordTests(PackQuarantineCase):
    """Task 7 plan test 4: quarantine writes the sidecar and the marker."""

    def setUp(self) -> None:
        super().setUp()
        self.questions = [question("q1", 1), question("q2", 2), question("q3", 3)]
        self.pack = self.write_pack(
            "course-a", "ch01.json", pack_body("ch01", self.questions))

    def test_quarantine_removes_the_questions_and_writes_sidecar_and_marker(self) -> None:
        marker = pq.quarantine(self.pack, ["q2"], "verifier round 2: wrong answer")

        data = self.reload(self.pack)
        self.assertEqual([q["id"] for q in data["questions"]], ["q1", "q3"])
        self.assertEqual(set(marker), {
            "authored_count", "installed_count", "quarantined_ids",
            "record_digest",
        })
        self.assertEqual(marker["authored_count"], 3)
        self.assertEqual(marker["installed_count"], 2)
        self.assertEqual(marker["quarantined_ids"], ["q2"])

        sidecar = pq.sidecar_path(self.pack)
        self.assertEqual(
            sidecar, self.packs_root / "course-a" / "_quarantine" / "ch01.json")
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(marker["record_digest"], pq.record_digest(record))
        self.assertEqual(record["reason"], "verifier round 2: wrong answer")
        self.assertEqual(
            [entry["question"]["id"] for entry in record["removed_questions"]],
            ["q2"])
        self.assertEqual(record["removed_questions"][0]["index"], 1)
        self.assertEqual(record["removed_questions"][0]["question"],
                         self.questions[1])
        # The free-text reason lives ONLY in the sidecar.
        self.assertNotIn("verifier round 2", self.pack.read_text(encoding="utf-8"))

    def test_quarantine_drops_a_middle_question_and_keeps_pack_order(self) -> None:
        pq.quarantine(self.pack, ["q1", "q3"], "set aside")
        data = self.reload(self.pack)
        self.assertEqual([q["id"] for q in data["questions"]], ["q2"])
        self.assertEqual(data["partial_install"]["quarantined_ids"], ["q1", "q3"])

    def test_quarantine_leaves_the_certification_stale(self) -> None:
        # INV-7: pack quarantine never writes certification metadata, so the
        # reduced pack is uncertified until the campaign re-stamps it.
        self.assertTrue(pack_cert.certification_fresh(self.reload(self.pack)))
        pq.quarantine(self.pack, ["q2"], "wrong answer")
        self.assertFalse(pack_cert.certification_fresh(self.reload(self.pack)))

    def test_quarantine_refuses_an_unknown_question_id(self) -> None:
        with self.assertRaisesRegex(pq.PackQuarantineError, "unknown question"):
            pq.quarantine(self.pack, ["q9"], "typo")
        self.assertNotIn(pq.MARKER_KEY, self.reload(self.pack))

    def test_quarantine_refuses_duplicate_or_empty_ids(self) -> None:
        with self.assertRaisesRegex(pq.PackQuarantineError, "unique"):
            pq.quarantine(self.pack, ["q2", "q2"], "duplicate")
        with self.assertRaisesRegex(pq.PackQuarantineError, "at least one"):
            pq.quarantine(self.pack, [], "none")

    def test_quarantine_refuses_to_quarantine_every_question(self) -> None:
        with self.assertRaisesRegex(pq.PackQuarantineError, "retain at least one"):
            pq.quarantine(self.pack, ["q1", "q2", "q3"], "everything")

    def test_quarantine_refuses_a_blank_reason(self) -> None:
        with self.assertRaisesRegex(pq.PackQuarantineError, "reason"):
            pq.quarantine(self.pack, ["q2"], "   ")

    def test_quarantine_refuses_a_pack_that_is_already_partial(self) -> None:
        pq.quarantine(self.pack, ["q2"], "once")
        with self.assertRaisesRegex(pq.PackQuarantineError, "already partial"):
            pq.quarantine(self.pack, ["q3"], "twice")


class RestoreTests(PackQuarantineCase):
    """Task 7 plan test 6: restore returns the authored pack."""

    def setUp(self) -> None:
        super().setUp()
        self.original = pack_body(
            "ch01", [question("q1", 1), question("q2", 2), question("q3", 3)])
        self.pack = self.write_pack("course-a", "ch01.json", self.original)

    def test_restore_returns_the_authored_pack_and_clears_the_record(self) -> None:
        pq.quarantine(self.pack, ["q2"], "wrong answer")
        result = pq.restore(self.pack)

        self.assertEqual(result["restored_qids"], ["q2"])
        self.assertEqual(result["question_count"], 3)
        self.assertEqual(self.reload(self.pack), self.original)
        self.assertFalse(pq.sidecar_path(self.pack).exists())

    def test_restore_reinserts_questions_at_their_original_positions(self) -> None:
        pq.quarantine(self.pack, ["q1", "q3"], "set aside")
        pq.restore(self.pack)
        self.assertEqual(
            [q["id"] for q in self.reload(self.pack)["questions"]],
            ["q1", "q2", "q3"])

    def test_restore_refuses_a_pack_without_a_marker(self) -> None:
        with self.assertRaisesRegex(pq.PackQuarantineError, "no active"):
            pq.restore(self.pack)

    def test_restore_refuses_a_missing_sidecar(self) -> None:
        pq.quarantine(self.pack, ["q2"], "wrong answer")
        pq.sidecar_path(self.pack).unlink()
        with self.assertRaisesRegex(pq.PackQuarantineError, "sidecar"):
            pq.restore(self.pack)
        # Fail closed: the partial pack is left exactly as it was.
        self.assertIn(pq.MARKER_KEY, self.reload(self.pack))

    def test_restore_refuses_a_tampered_sidecar(self) -> None:
        pq.quarantine(self.pack, ["q2"], "wrong answer")
        sidecar = pq.sidecar_path(self.pack)
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        record["reason"] = "rewritten after the fact"
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(pq.PackQuarantineError, "record_digest"):
            pq.restore(self.pack)


class GateReasonsTests(PackQuarantineCase):
    """Task 7 plan test 7: the gate refuses a partial pack by default."""

    def setUp(self) -> None:
        super().setUp()
        self.pack = self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"])

    def test_gate_reasons_refuse_a_partial_pack_by_default(self) -> None:
        reasons = pq.gate_reasons(self.pack, self.reload(self.pack))
        self.assertEqual(len(reasons), 1)
        self.assertIn("--allow-partial", reasons[0])

    def test_gate_reasons_admit_a_valid_partial_only_when_allowed(self) -> None:
        data = self.reload(self.pack)
        self.assertEqual(
            pq.gate_reasons(self.pack, data, allow_partial=True), [])
        # A whole pack gates exactly as before.
        whole = pack_body("ch01", [question("q1", 1)])
        self.assertEqual(pq.gate_reasons(self.pack, whole), [])

    def test_the_wired_gate_refuses_a_partial_pack_by_default(self) -> None:
        # gate_reasons is wired once, into install_gate.evaluate, so the
        # manifest builder and the bundler cannot diverge on partials.
        gate = install_gate.evaluate(self.packs_root, report=lambda _: None)
        self.assertEqual(gate.admitted, {})
        reasons = gate.rejections[("course-a", "ch01.json")]["reasons"]
        self.assertTrue(any("partial install" in reason for reason in reasons))

    def test_the_wired_gate_admits_a_valid_partial_with_its_marker(self) -> None:
        gate = install_gate.evaluate(
            self.packs_root, report=lambda _: None, allow_partial=True)
        entry = gate.admitted[("course-a", "ch01.json")]
        self.assertEqual([q["id"] for q in entry["data"]["questions"]],
                         ["q1", "q3"])
        self.assertIn(pq.MARKER_KEY, entry["data"])
        bundled = json.loads(entry["raw_bytes"])
        self.assertEqual(bundled[pq.MARKER_KEY]["quarantined_ids"], ["q2"])
        self.assertEqual([q["id"] for q in bundled["questions"]], ["q1", "q3"])


class MalformedMarkerTests(PackQuarantineCase):
    """Task 7 plan test 8: a malformed record is refused even when allowed."""

    def setUp(self) -> None:
        super().setUp()
        self.pack = self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"])

    def assert_refused_even_when_allowed(self, *expected: str) -> None:
        data = self.reload(self.pack)
        reasons = pq.gate_reasons(self.pack, data, allow_partial=True)
        for fragment in expected:
            self.assertTrue(
                any(fragment in reason for reason in reasons),
                f"no reason mentions {fragment!r}: {reasons}")
        gate = install_gate.evaluate(
            self.packs_root, report=lambda _: None, allow_partial=True)
        self.assertEqual(gate.admitted, {})

    def test_an_installed_count_that_disagrees_with_the_questions(self) -> None:
        data = self.reload(self.pack)
        data[pq.MARKER_KEY]["installed_count"] = 5
        self.rewrite(self.pack, data)
        self.assert_refused_even_when_allowed("installed_count")

    def test_an_authored_count_that_does_not_exceed_the_installed_count(self) -> None:
        data = self.reload(self.pack)
        data[pq.MARKER_KEY]["authored_count"] = 2
        self.rewrite(self.pack, data)
        self.assert_refused_even_when_allowed("authored_count")

    def test_duplicate_quarantined_ids(self) -> None:
        data = self.reload(self.pack)
        data[pq.MARKER_KEY]["quarantined_ids"] = ["q2", "q2"]
        self.rewrite(self.pack, data)
        self.assert_refused_even_when_allowed("unique")

    def test_an_extra_marker_field(self) -> None:
        data = self.reload(self.pack)
        data[pq.MARKER_KEY]["reason"] = "the reason must live in the sidecar"
        self.rewrite(self.pack, data)
        self.assert_refused_even_when_allowed("exactly")

    def test_a_missing_sidecar(self) -> None:
        pq.sidecar_path(self.pack).unlink()
        self.assert_refused_even_when_allowed("sidecar missing")

    def test_a_tampered_sidecar_breaks_the_record_digest(self) -> None:
        sidecar = pq.sidecar_path(self.pack)
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        record["reason"] = "rewritten after the fact"
        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.assert_refused_even_when_allowed("record_digest")

    def test_a_partial_whose_questions_were_hand_reinstalled(self) -> None:
        data = self.reload(self.pack)
        data["questions"].insert(1, question("q2", 2))
        self.rewrite(self.pack, data)
        self.assert_refused_even_when_allowed("installed_count")


class BundlerAdmissionTests(PackQuarantineCase):
    """The bundler CLI: release refuses partials; --allow-partial relaxes
    nothing but the partiality itself."""

    def test_release_refuses_a_partial_pack(self) -> None:
        self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"])
        self.assertEqual(self.run_main(), 1)
        result = bpa.build(self.packs_root, self.destination, lambda _: None)
        self.assertTrue(
            any("partial install" in rejection
                for rejection in result["rejections"]),
            result["rejections"])
        self.assertEqual(self.manifest()["packs"], [])

    def test_snapshot_manifest_refuses_a_partial_pack(self) -> None:
        self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"])
        manifest, rejections = bpa.snapshot_manifest(
            self.packs_root, lambda _: None)
        self.assertEqual(manifest["packs"], [])
        self.assertTrue(
            any("partial install" in rejection for rejection in rejections),
            rejections)

    def test_the_debug_flag_bundles_a_valid_partial_with_its_marker(self) -> None:
        self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"])
        self.assertEqual(self.run_main("--allow-partial"), 0)

        entries = self.manifest()["packs"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        bundled = json.loads(
            (self.destination / bpa.PACKS_SUBDIRECTORY / entry["path"])
            .read_text(encoding="utf-8"))
        self.assertEqual(bundled[pq.MARKER_KEY]["installed_count"], 2)
        self.assertEqual([q["id"] for q in bundled["questions"]], ["q1", "q3"])
        self.assertEqual(bpa.content_digest(bundled), entry["content_digest"])

    def test_allow_partial_still_refuses_a_subset_below_a_blueprint_minimum(self) -> None:
        # Authored: three questions on one topic with a blueprint minimum of
        # three. Quarantining one leaves two -- below the blueprint minimum,
        # so the subset is refused even though the partial itself is valid.
        self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q3"],
            blueprint=[{"topic": "topic", "area": "area", "min": 3}])
        self.assertEqual(self.run_main("--allow-partial"), 1)
        result = bpa.build(self.packs_root, self.destination, lambda _: None,
                           allow_partial=True)
        self.assertTrue(
            any("L23" in rejection for rejection in result["rejections"]),
            result["rejections"])
        self.assertEqual(self.manifest()["packs"], [])

    def test_allow_partial_still_refuses_a_subset_that_fails_distribution(self) -> None:
        # A 40-question course split 50/50 across two weighted areas. The
        # authored pack passes the course aggregate; quarantining twelve of
        # the twenty area-b questions skews the surviving distribution far
        # outside the published-weight ranges, so the course is excluded.
        self.write_course("course-a", {
            "id": "course-a",
            "name": "Course A",
            "syllabus": {
                "source": {"kind": "syllabus", "title": "Fixture syllabus"},
                "areas": [
                    {"id": "a", "name": "Area A", "weight": 50},
                    {"id": "b", "name": "Area B", "weight": 50},
                ],
            },
        })
        questions = (
            [question(f"a{i:02d}", i, topic="topic-a", area="a") for i in range(1, 21)]
            + [question(f"b{i:02d}", i, topic="topic-b", area="b") for i in range(21, 41)]
        )
        self.make_partial(
            questions,
            drop=[f"b{i:02d}" for i in range(29, 41)],
            blueprint=[
                {"topic": "topic-a", "area": "a", "min": 1},
                {"topic": "topic-b", "area": "b", "min": 1},
            ])
        self.assertEqual(self.run_main("--allow-partial"), 1)
        result = bpa.build(self.packs_root, self.destination, lambda _: None,
                           allow_partial=True)
        self.assertTrue(
            any("course failed the install gate" in rejection
                for rejection in result["rejections"]),
            result["rejections"])
        self.assertEqual(self.manifest()["packs"], [])

    def test_allow_partial_still_refuses_a_stale_cert(self) -> None:
        # Quarantine without re-certification: the retained subset carries a
        # certification written for the authored question set, which is stale
        # by construction (INV-7: only certify_campaign may re-stamp it).
        self.make_partial(
            [question("q1", 1), question("q2", 2), question("q3", 3)],
            drop=["q2"], recertify=False)
        self.assertEqual(self.run_main("--allow-partial"), 1)
        result = bpa.build(self.packs_root, self.destination, lambda _: None,
                           allow_partial=True)
        self.assertTrue(
            any("INV-8 certification" in rejection
                for rejection in result["rejections"]),
            result["rejections"])
        self.assertEqual(self.manifest()["packs"], [])


if __name__ == "__main__":
    unittest.main()
