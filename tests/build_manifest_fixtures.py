"""Unit tests for ``scripts/build_manifest.py``.

Exercises the manifest builder against throw-away fixture trees so the real
``question-packs/`` directory is never touched. The script is imported by path
(``scripts/`` isn't a package), then ``PACKS_DIR`` is patched.

Run from the project root::

    python3 -m unittest tests.test_build_manifest -v

"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_manifest.py"


_spec = importlib.util.spec_from_file_location("build_manifest", SCRIPT_PATH)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

_pack_cert_spec = importlib.util.spec_from_file_location(
    "pack_cert", PROJECT_ROOT / "scripts" / "pack_cert.py"
)
pack_cert = importlib.util.module_from_spec(_pack_cert_spec)
_pack_cert_spec.loader.exec_module(pack_cert)


def fresh_certification(pack_dict: dict) -> dict:
    """Return a certification block that passes ``certification_fresh``."""
    questions = pack_dict.get("questions", [])
    return {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": "2026-07-20T00:00:00+00:00",
        "questions_hash": pack_cert.questions_hash(pack_dict),
        "critic_model": "test",
        "review_method": "external-layer-c-strict",
        "blocking_count": 0,
        "questions_examined": len(questions) if isinstance(questions, list) else 0,
        "question_stamps": pack_cert.build_question_stamps(pack_dict),
    }


def write_pack(course_dir: Path, name: str, *, certify: bool = False, **payload) -> Path:
    # L29: a pack the iOS client cannot decode is not a fixture of a pack, so
    # the default payload carries the native metadata it requires.
    payload.setdefault("pack_id", name.replace(".json", ""))
    payload.setdefault("subject", "Manifest Fixture")
    payload.setdefault("title", name.replace(".json", ""))
    payload.setdefault("version", 1)
    payload.setdefault("questions", [{"q": "x"}])
    questions = payload["questions"]
    if "coverage_blueprint" not in payload and isinstance(questions, list):
        pairs = sorted({
            (q.get("topic"), q.get("exam_area"))
            for q in questions
            if isinstance(q, dict) and q.get("topic")
        })
        if pairs:
            payload["coverage_blueprint"] = [
                {
                    "topic": topic,
                    **({"area": area} if area else {}),
                    "min": 1,
                }
                for topic, area in pairs
            ]
    if certify and "certification" not in payload:
        payload["certification"] = fresh_certification(payload)
    p = course_dir / name
    p.write_text(json.dumps(payload))
    return p


def without_generated_at(manifest: dict) -> dict:
    """Return a manifest comparison view without its build timestamp."""
    comparable = dict(manifest)
    comparable.pop("generated_at", None)
    return comparable


def build_repository_copy(packs_dir: Path, *, strict: bool = True) -> tuple[int, dict]:
    """Build a copied repository tree without mutating the checkout."""
    lint_log = packs_dir.parent / "quizzler-lint.log"
    out, err = io.StringIO(), io.StringIO()
    with patch.object(bm, "PACKS_DIR", packs_dir), \
            patch.object(bm, "LINT_LOG", lint_log), \
            redirect_stdout(out), redirect_stderr(err):
        rc, manifest = bm.build(strict=strict)
    return rc, manifest


class _Base(unittest.TestCase):
    """Provides a temp dir with PACKS_DIR patched on the module."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.packs_dir = self.tmp_path / "question-packs"
        self.packs_dir.mkdir()
        self._patches = [
            patch.object(bm, "PACKS_DIR", self.packs_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def run_build(self):
        # These tests exercise manifest STRUCTURE (course discovery, sorting,
        # notes/coupling warnings), not pack quality — lint=False keeps the
        # incidental Layer-A output from polluting the captured streams. The
        # lint pass has its own coverage in LintGateTests below.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(lint=False)
        return rc, manifest, out.getvalue(), err.getvalue()
