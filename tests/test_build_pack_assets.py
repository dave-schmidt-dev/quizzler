"""Tests for the build-time pack bundler (`scripts/build_pack_assets.py`).

The suite has two jobs. Most of it exercises discovery, validation, and the
emitted manifest. The last class is the important one: it reads the digest
vectors out of the Swift test file and asserts Python reaches the same values,
which is the only thing standing between the two languages and a silent
canonicalization drift — the failure that kept the CISSP pack out of the app.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_pack_assets as bpa  # noqa: E402


def question(qid: str = "q1") -> dict:
    return {
        "id": qid,
        "type": "multiple_choice",
        "topic": "topic",
        "exam_area": "area",
        "difficulty": "easy",
        "prompt": "Which control limits lateral movement?",
        "explanation": "Segmentation limits what a compromised host can reach.",
        "options": ["Segmentation", "Encryption", "Rotation", "Masking"],
        "answer": 0,
    }


def pack_body(pack_id: str = "demo-pack", subject: str = "Demo", **overrides) -> dict:
    body = {
        "pack_id": pack_id,
        "subject": subject,
        "title": "Core",
        "version": 1,
        "questions": [question()],
    }
    body.update(overrides)
    return body


class BuilderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.packs_root = self.root / "question-packs"
        self.destination = self.root / "Resources"
        self.packs_root.mkdir()
        self.messages: list[str] = []

    def write_pack(self, course: str, name: str, body: dict | str) -> Path:
        course_directory = self.packs_root / course
        course_directory.mkdir(parents=True, exist_ok=True)
        path = course_directory / name
        text = body if isinstance(body, str) else json.dumps(body, indent=2)
        path.write_text(text, encoding="utf-8")
        return path

    def build(self) -> dict:
        return bpa.build(self.packs_root, self.destination, self.messages.append)

    def manifest(self) -> dict:
        return json.loads((self.destination / bpa.MANIFEST_NAME).read_text(encoding="utf-8"))


class DiscoveryTests(BuilderTestCase):
    def test_packs_are_bundled_with_their_course_directory_as_the_course_id(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        result = self.build()

        self.assertEqual(result["rejections"], [])
        manifest = self.manifest()
        self.assertEqual(manifest["contract_version"], bpa.CONTRACT_VERSION)
        self.assertEqual(len(manifest["packs"]), 1)
        entry = manifest["packs"][0]
        self.assertEqual(entry["course_id"], "cissp")
        self.assertEqual(entry["pack_id"], "cissp-core")
        self.assertEqual(entry["path"], "cissp/cissp-core.json")
        self.assertTrue(entry["content_digest"].startswith("sha256:"))

    def test_the_copied_pack_is_byte_identical_to_the_source(self) -> None:
        source = self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        self.build()
        copied = self.destination / bpa.PACKS_SUBDIRECTORY / "cissp" / "cissp-core.json"
        self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_the_digest_matches_the_bundled_bytes(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        self.build()
        entry = self.manifest()["packs"][0]
        copied = self.destination / bpa.PACKS_SUBDIRECTORY / entry["path"]
        self.assertEqual(bpa.content_digest(json.loads(copied.read_text(encoding="utf-8"))), entry["content_digest"])

    def test_underscore_and_dot_directories_are_not_courses(self) -> None:
        self.write_pack("_archive", "old.json", pack_body("old-pack"))
        self.write_pack("_staging", "wip.json", pack_body("wip-pack"))
        self.write_pack(".hidden", "hidden.json", pack_body("hidden-pack"))
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))

        self.build()

        self.assertEqual([entry["pack_id"] for entry in self.manifest()["packs"]], ["cissp-core"])

    def test_course_metadata_and_non_pack_json_are_skipped_without_a_rejection(self) -> None:
        self.write_pack("cissp", "_course.json", {"id": "cissp", "name": "CISSP"})
        self.write_pack("cissp", "BUILD_NOTES.md", "not json at all")
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))

        result = self.build()

        self.assertEqual(result["rejections"], [])
        self.assertEqual(len(self.manifest()["packs"]), 1)

    def test_manifest_ordering_is_deterministic_across_runs(self) -> None:
        self.write_pack("samples", "sample-pack.json", pack_body("samples-demo", "Samples"))
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))

        self.build()
        first = self.manifest()
        self.build()
        self.assertEqual(first, self.manifest())
        self.assertEqual([entry["course_id"] for entry in first["packs"]], ["cissp", "samples"])

    def test_a_pack_removed_from_the_source_tree_does_not_survive_in_the_bundle(self) -> None:
        stale = self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        self.build()
        self.assertTrue((self.destination / bpa.PACKS_SUBDIRECTORY / "cissp" / "cissp-core.json").exists())

        stale.unlink()
        self.write_pack("samples", "sample-pack.json", pack_body("samples-demo", "Samples"))
        self.build()

        self.assertFalse((self.destination / bpa.PACKS_SUBDIRECTORY / "cissp" / "cissp-core.json").exists())
        self.assertEqual([entry["pack_id"] for entry in self.manifest()["packs"]], ["samples-demo"])

    def test_an_empty_packs_root_yields_an_empty_manifest_rather_than_an_error(self) -> None:
        result = self.build()
        self.assertEqual(result["rejections"], [])
        self.assertEqual(self.manifest(), {"contract_version": bpa.CONTRACT_VERSION, "packs": []})

    def test_progress_is_reported_for_every_bundled_pack(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        self.build()
        # INV-1: a build phase that copies content must say what it copied.
        self.assertTrue(any("bundled cissp/cissp-core.json" in message for message in self.messages))


class ValidationTests(BuilderTestCase):
    def assert_rejected(self, fragment: str) -> None:
        result = self.build()
        self.assertEqual(len(result["rejections"]), 1, result["rejections"])
        self.assertIn(fragment, result["rejections"][0])
        self.assertEqual(self.manifest()["packs"], [])

    def test_a_pack_the_native_client_would_refuse_is_not_bundled(self) -> None:
        # The exact defect that hid the CISSP course: an undocumented mode.
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", generation_mode="llm-assisted"))
        self.assert_rejected("generation_mode")

    def test_a_pack_with_an_unparseable_generated_at_is_not_bundled(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", generated_at="2026-08-11"))
        self.assert_rejected("generated_at")

    def test_a_pack_with_a_wrong_contract_version_is_not_bundled(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", version=2))
        self.assert_rejected("version")

    def test_a_pack_with_no_questions_is_not_bundled(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", questions=[]))
        self.assert_rejected("questions")

    def test_malformed_json_is_reported_rather_than_crashing_the_build(self) -> None:
        self.write_pack("cissp", "cissp-core.json", '{"pack_id": "broken", "questions": [')
        self.assert_rejected("unreadable JSON")

    def test_two_courses_claiming_one_pack_id_reject_the_second(self) -> None:
        self.write_pack("alpha", "core.json", pack_body("shared-id", "Alpha"))
        self.write_pack("beta", "core.json", pack_body("shared-id", "Beta"))

        result = self.build()

        self.assertEqual(len(result["rejections"]), 1)
        self.assertIn("already provided by", result["rejections"][0])
        self.assertEqual([entry["course_id"] for entry in self.manifest()["packs"]], ["alpha"])

    def test_a_valid_pack_still_ships_when_a_sibling_is_rejected(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", generation_mode="llm-assisted"))
        self.write_pack("samples", "sample-pack.json", pack_body("samples-demo", "Samples"))

        result = self.build()

        self.assertEqual(len(result["rejections"]), 1)
        self.assertEqual([entry["pack_id"] for entry in self.manifest()["packs"]], ["samples-demo"])


class ExitCodeTests(BuilderTestCase):
    def run_main(self, *extra: str) -> int:
        return bpa.main(
            ["--packs-root", str(self.packs_root), "--destination", str(self.destination), "--quiet", *extra]
        )

    def test_a_clean_build_exits_zero(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", "CISSP"))
        self.assertEqual(self.run_main(), 0)

    def test_a_rejected_pack_fails_the_build(self) -> None:
        self.write_pack("cissp", "cissp-core.json", pack_body("cissp-core", generation_mode="llm-assisted"))
        self.assertEqual(self.run_main(), 1)

    def test_no_packs_is_tolerated_by_default_and_fatal_under_require_pack(self) -> None:
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.run_main("--require-pack"), 1)


class InstalledPackBundleTests(unittest.TestCase):
    """The real tree on this machine must produce a usable bundle.

    A clean checkout has only `samples`; a machine with course material has
    more. Either way the build must emit at least one pack, or the app has
    nothing to teach.
    """

    def test_the_repositorys_own_packs_bundle_without_rejection(self) -> None:
        with TemporaryDirectory() as temporary:
            result = bpa.build(PROJECT_ROOT / "question-packs", Path(temporary), lambda _: None)

        self.assertEqual(result["rejections"], [], "an installed pack no longer satisfies the native contract")
        self.assertGreaterEqual(len(result["assets"]), 1)
        self.assertIn("samples", {asset["course_id"] for asset in result["assets"]})


class DigestVectorParityTests(unittest.TestCase):
    """Python and Swift must hash the same JSON to the same digest.

    The vectors live in the Swift test file and are read from it here. Keeping
    a second copy in Python would let the two drift while both suites passed,
    which is precisely how `generation_mode` drifted in the first place.
    """

    SWIFT = PROJECT_ROOT / "app" / "QuizzlerKit" / "Tests" / "QuizzlerKitTests" / "PackCatalogTests.swift"
    VECTOR_RE = re.compile(r'\("(?P<label>[a-z]+)",\s*#"(?P<json>.*?)"#,\s*"(?P<digest>sha256:[0-9a-f]{64})"\)')

    def vectors(self) -> list[re.Match]:
        return list(self.VECTOR_RE.finditer(self.SWIFT.read_text(encoding="utf-8")))

    def test_the_swift_vector_table_is_present_and_covers_the_known_divergences(self) -> None:
        labels = {match["label"] for match in self.vectors()}
        # `slash` is the case that actually broke: Foundation writes `/` as
        # `\/` unless `.withoutEscapingSlashes` is passed.
        self.assertIn("slash", labels)
        self.assertIn("unicode", labels)
        self.assertGreaterEqual(len(labels), 4)

    def test_python_reaches_the_digest_swift_recorded_for_every_vector(self) -> None:
        matches = self.vectors()
        self.assertTrue(matches, "no digest vectors found in PackCatalogTests.swift")
        for match in matches:
            with self.subTest(vector=match["label"]):
                value = json.loads(match["json"])
                self.assertEqual(bpa.content_digest(value), match["digest"])

    def test_the_slash_vector_would_fail_under_foundations_default_escaping(self) -> None:
        """Guards the guard: prove the slash vector discriminates.

        If this vector were slash-free the parity test would pass no matter
        which escaping either side used, and the check would be decorative.
        """
        match = next(m for m in self.vectors() if m["label"] == "slash")
        value = json.loads(match["json"])
        escaped = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace("/", r"\/")
        self.assertNotEqual(escaped.encode("utf-8"), bpa.canonical_bytes(value))


if __name__ == "__main__":
    unittest.main()
