"""Atomicity tests for ``hybrid_verify.certify_campaign`` (M0c, finding 2).

Campaign finalization reads the pack bytes exactly once: the eligibility
snapshot, Layer A, and the stamp registry are all built from that single
parse, serialized by a per-pack exclusive ``flock`` under ``.logs/locks/``
and guarded by a final pre-replace re-read of the pack bytes and the course
grounding inputs. Each test injects a change at a different point in the run
and asserts the finalizer refuses rather than stamping content the frozen
campaign evidence never covered. No reviewer or network call ever happens:
``run_layer_a`` is patched only where a mid-run edit must be injected, and the
success path exercises the real deterministic Layer A on a lint-clean fixture.

Run from the project root::

    python3 -m unittest tests.test_certify_campaign_atomic -v
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"

_spec = importlib.util.spec_from_file_location("hybrid_verify", SCRIPT_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# The same module objects hybrid_verify uses, so patches land where
# certify_campaign actually looks its collaborators up.
vp = hv.verify_pack
campaign_finalize = sys.modules["campaign_finalize"]
pack_cert = vp.pack_cert

CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}

# See L29: a fixture pack the native decoder would refuse fails Layer A, which
# would mask the atomicity behavior these tests isolate.
NATIVE_METADATA = {"subject": "Math", "title": "Atomic finalize fixture", "version": 1}

CLEAN_LAYER_A = {"live": [], "waived": [], "hygiene": []}


def _pack_payload() -> dict:
    """Return a lint-clean single-question pack payload."""
    payload = {
        **NATIVE_METADATA,
        "pack_id": "ch01",
        "questions": [dict(CLEAN_Q)],
    }
    payload["coverage_blueprint"] = [{"topic": "math", "min": 1}]
    return payload


class _Base(unittest.TestCase):
    """A lint-clean pack plus the frozen ledger whose evidence covers it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.pack = self.tmp_path / "ch01.json"
        self.pack.write_text(json.dumps(_pack_payload()))

    def tearDown(self):
        self._tmp.cleanup()

    def _ledger(self) -> Path:
        """Return a saved ledger whose discovery evidence covers the pack.

        The ledger is frozen against the pack exactly as setUp wrote it, so
        eligibility passes and each test's injected change is the only thing
        that can refuse the stamp.
        """
        snapshot = hv.certification_campaign.build_snapshot(self.pack)
        ledger = hv.certification_campaign.new_ledger(snapshot)

        def pass_report():
            return {
                "ready": False, "outcome": "review_ok", "partial": False,
                "layer_a": {"live": []},
                "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                            "questions_unchecked": 0,
                            "total": len(snapshot["question_ids"])},
            }

        hv.certification_campaign.record_hybrid_discovery(ledger, {
            "schema_version": hv.JSON_SCHEMA_VERSION, "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()}, "exit_code": 3,
        })
        path = self.tmp_path / "campaign.json"
        hv.certification_campaign.save_ledger(path, ledger)
        return path


class CertifyCampaignAtomicTests(_Base):
    """M0c: a mid-run edit must refuse the stamp, never ride under it."""

    def test_layer_a_edit_mid_run_is_refused_and_pack_left_as_is(self):
        """A Layer-A hook that edits a prompt must not earn a stamp over it.

        The edit keeps the question count unchanged, so only the final
        pre-replace re-read of the pack bytes can catch it.
        """
        ledger = self._ledger()

        def editing_layer_a(pack_path, *, parsed_data=None):
            payload = json.loads(pack_path.read_text())
            payload["questions"][0]["prompt"] = "Edited while Layer A ran"
            pack_path.write_text(json.dumps(payload))
            return dict(CLEAN_LAYER_A)

        with patch.object(vp, "run_layer_a", side_effect=editing_layer_a):
            rc, out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 2)
        self.assertIn("the pack bytes changed during finalization", out)
        payload = json.loads(self.pack.read_text())
        self.assertNotIn("certification", payload)
        # The finalizer must not overwrite the mid-run edit with its own write.
        self.assertEqual(payload["questions"][0]["prompt"],
                         "Edited while Layer A ran")

    def test_mutation_after_stamp_building_is_refused(self):
        """An edit injected after the stamps are built must refuse the replace."""
        ledger = self._ledger()
        real_build_stamps = pack_cert.build_question_stamps

        def mutate_after_stamping(data):
            stamps = real_build_stamps(data)
            payload = json.loads(self.pack.read_text())
            payload["questions"][0]["explanation"] = "Injected after stamping"
            self.pack.write_text(json.dumps(payload))
            return stamps

        with patch.object(pack_cert, "build_question_stamps",
                         side_effect=mutate_after_stamping):
            rc, out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 2)
        self.assertIn("the pack bytes changed during finalization", out)
        payload = json.loads(self.pack.read_text())
        self.assertNotIn("certification", payload)
        self.assertEqual(payload["questions"][0]["explanation"],
                         "Injected after stamping")
        # A refused replace must not leave the .tmp sibling behind.
        self.assertFalse(
            self.pack.with_name(self.pack.name + ".tmp").exists())

    def test_grounding_source_changed_mid_run_is_refused(self):
        """A course source text edited after eligibility must refuse the stamp.

        The pack bytes are untouched, so only the recomputed campaign snapshot
        (whose grounding digest covers the source text) can catch the change.
        """
        text_root = self.tmp_path / "texts"
        text_root.mkdir()
        source = text_root / "ch01.txt"
        source.write_text("Chapter one source text.\n")
        course = self.tmp_path / "gcourse"
        course.mkdir()
        self.pack = course / "ch01.json"
        self.pack.write_text(json.dumps(_pack_payload()))
        (course / "_course.json").write_text(json.dumps({
            "grounding": {
                "text_root": str(text_root),
                "packs": {"ch01.json": "ch01.txt"},
            },
        }))
        ledger = self._ledger()

        def editing_layer_a(pack_path, *, parsed_data=None):
            source.write_text("Chapter one source text, edited mid-run.\n")
            return dict(CLEAN_LAYER_A)

        with patch.object(vp, "run_layer_a", side_effect=editing_layer_a):
            rc, out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 2)
        self.assertIn("the campaign snapshot changed during finalization", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_held_lock_is_refused(self):
        """A concurrent finalizer holding the pack lock must be refused."""
        ledger = self._ledger()
        lock_path = campaign_finalize._finalization_lock_path(self.pack)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc, out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 2)
        self.assertIn("finalization in progress", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_success_path_writes_stamps_for_the_parsed_input(self):
        """A clean run stamps exactly the parsed pack and releases the lock."""
        ledger = self._ledger()
        parsed_input = json.loads(self.pack.read_text())
        rc, _out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 0)
        stamped = json.loads(self.pack.read_text())
        cert = stamped["certification"]
        self.assertEqual(cert["question_stamps"],
                         pack_cert.build_question_stamps(parsed_input))
        self.assertTrue(pack_cert.certification_fresh(stamped))
        # The lock must be released so the next finalization is not refused.
        lock_path = campaign_finalize._finalization_lock_path(self.pack)
        with lock_path.open("a") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_from_data_snapshot_matches_build_snapshot(self):
        """The single-parse snapshot must equal the file-reading builder's.

        Pins ``verify_pack.build_snapshot_from_data`` to
        ``campaign_snapshot.build_snapshot`` so the mirror cannot drift from
        the snapshot every ledger is frozen against.
        """
        data = json.loads(self.pack.read_text())
        for profile in ("codex-terra-high", "claude-opus-high"):
            with self.subTest(profile=profile):
                self.assertEqual(
                    vp.build_snapshot_from_data(
                        self.pack, data, verifier_profile=profile),
                    hv.certification_campaign.build_snapshot(
                        self.pack, verifier_profile=profile),
                )

    def test_finalization_reads_the_pack_bytes_once_for_content(self):
        """The pack file is read once for content plus once for the re-check.

        The decision read happens in ``certify_campaign``; the only other
        permitted read of the pack is the plan-mandated pre-replace re-check.
        Nothing may read the pack via ``read_text`` at all.
        """
        ledger = self._ledger()
        real_read_bytes = Path.read_bytes
        real_read_text = Path.read_text
        byte_reads: list[Path] = []
        text_reads: list[Path] = []

        def spy_read_bytes(self):
            byte_reads.append(self)
            return real_read_bytes(self)

        def spy_read_text(self, *args, **kwargs):
            text_reads.append(self)
            return real_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_bytes", spy_read_bytes), \
                patch.object(Path, "read_text", spy_read_text):
            rc, _out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 0)
        self.assertEqual(
            [p for p in byte_reads if p == self.pack],
            [self.pack, self.pack],
            "the pack must be read once for content and once for the re-check")
        self.assertEqual(
            [p for p in text_reads if p == self.pack],
            [],
            "no consumer may re-read the pack for content")


if __name__ == "__main__":
    unittest.main()
