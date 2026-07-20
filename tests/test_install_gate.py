"""Install gate anchor (INV-7 T7): every installed pack must pass the full quality bar.

Walks real packs under ``question-packs/`` (mirrors ``build_manifest`` discovery:
skip ``.``/``_`` course dirs, ``_course.json``, top-level ``pack-template.json``)
and asserts blueprint presence, L23 compliance, fresh certification, no pack-wide
L23 waiver (PM-5), and full Layer-C coverage (PM-6). Also unit-tests
``pack_cert.questions_hash`` and strict ``build_manifest`` refusal of uncertified
packs.

Run from the project root::

    python3 -m unittest tests.test_install_gate -v
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"

_lint_spec = importlib.util.spec_from_file_location(
    "lint_packs", PROJECT_ROOT / "scripts" / "lint_packs.py"
)
lint_packs = importlib.util.module_from_spec(_lint_spec)
_lint_spec.loader.exec_module(lint_packs)

_pack_cert_spec = importlib.util.spec_from_file_location(
    "pack_cert", PROJECT_ROOT / "scripts" / "pack_cert.py"
)
pack_cert = importlib.util.module_from_spec(_pack_cert_spec)
_pack_cert_spec.loader.exec_module(pack_cert)

_bm_spec = importlib.util.spec_from_file_location(
    "build_manifest", PROJECT_ROOT / "scripts" / "build_manifest.py"
)
bm = importlib.util.module_from_spec(_bm_spec)
_bm_spec.loader.exec_module(bm)


def iter_installed_packs(packs_dir: Path = PACKS_DIR):
    """Yield pack JSON paths that ``build_manifest`` would lint/install.

    Skips ``zz-hooktest-*`` course dirs — ephemeral fixtures from
    ``tests.test_lint_hook`` that live under ``question-packs/`` so the
    authoring hook path-filter matches, and can race parallel workers.
    """
    for course_dir in sorted(packs_dir.iterdir(), key=lambda p: p.name):
        if not course_dir.is_dir():
            continue
        if course_dir.name.startswith((".", "_")):
            continue
        if course_dir.name.startswith("zz-hooktest-"):
            continue
        for pack_path in sorted(course_dir.glob("*.json")):
            if pack_path.name == "_course.json":
                continue
            yield pack_path


def has_pack_wide_l23_waiver(data: dict) -> bool:
    """PM-5: installed packs must not carry a pack-wide L23 ``lint_waivers`` entry."""
    waivers = data.get("lint_waivers")
    if not isinstance(waivers, list):
        return False
    for entry in waivers:
        if not isinstance(entry, dict):
            continue
        if entry.get("rule") == "L23" and not entry.get("qid"):
            return True
    return False


def cert_pm6_ok(data: dict) -> tuple[bool, str]:
    """PM-6: ``blocking_count == 0`` and ``questions_examined == len(questions)``."""
    cert = data.get("certification")
    if not isinstance(cert, dict):
        return False, "missing certification block"
    questions = data.get("questions", [])
    if not isinstance(questions, list):
        return False, "questions is not a list"
    if cert.get("blocking_count") != 0:
        return False, f"blocking_count={cert.get('blocking_count')!r}"
    examined = cert.get("questions_examined")
    if examined != len(questions):
        return False, f"questions_examined={examined!r} != {len(questions)}"
    return True, ""


def _minimal_mc_q(**over) -> dict:
    base = {
        "id": "q1",
        "type": "multiple_choice",
        "topic": "science",
        "difficulty": "easy",
        "tags": ["alpha", "beta"],
        "prompt": "What is 2+2?",
        "options": ["3", "4", "5", "6"],
        "answer": 1,
        "explanation": "Two plus two equals four.",
    }
    base.update(over)
    return base


def fresh_certification(pack_dict: dict) -> dict:
    questions = pack_dict.get("questions", [])
    return {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": "2026-07-20T00:00:00+00:00",
        "questions_hash": pack_cert.questions_hash(pack_dict),
        "critic_model": "test",
        "blocking_count": 0,
        "questions_examined": len(questions) if isinstance(questions, list) else 0,
    }


class InstalledPackGateTests(unittest.TestCase):
    """INV-7: every installed pack passes blueprint, L23, cert, PM-5, and PM-6."""

    def test_every_installed_pack_passes_install_gate(self):
        packs = list(iter_installed_packs())
        self.assertGreater(len(packs), 0, "expected at least one installed pack")
        for pack_path in packs:
            rel = pack_path.relative_to(PROJECT_ROOT)
            with self.subTest(pack=str(rel)):
                data = json.loads(pack_path.read_text())
                self.assertIsInstance(data, dict)

                blueprint = data.get("coverage_blueprint")
                self.assertIsInstance(
                    blueprint, list,
                    f"{rel}: missing or non-list coverage_blueprint",
                )
                self.assertGreater(
                    len(blueprint), 0,
                    f"{rel}: coverage_blueprint is empty",
                )

                lint_result = lint_packs.lint_pack(pack_path)
                l23_criticals = [
                    v for v in lint_result["violations"]
                    if v.get("rule") == "L23" and v.get("severity") == "critical"
                ]
                self.assertEqual(
                    l23_criticals, [],
                    f"{rel}: L23 critical(s): "
                    + "; ".join(v.get("detail", "") for v in l23_criticals),
                )

                self.assertTrue(
                    pack_cert.certification_fresh(data),
                    f"{rel}: certification missing or stale",
                )

                self.assertFalse(
                    has_pack_wide_l23_waiver(data),
                    f"{rel}: pack-wide L23 lint_waivers entry forbidden (PM-5)",
                )

                ok, detail = cert_pm6_ok(data)
                self.assertTrue(ok, f"{rel}: {detail}")


class QuestionsHashTests(unittest.TestCase):
    """``pack_cert.questions_hash`` stability and sensitivity."""

    def test_tag_and_difficulty_changes_do_not_affect_hash(self):
        base = {"questions": [_minimal_mc_q()]}
        variant = {
            "questions": [
                _minimal_mc_q(
                    tags=["beta", "alpha", "gamma"],
                    difficulty="hard",
                )
            ]
        }
        self.assertEqual(
            pack_cert.questions_hash(base),
            pack_cert.questions_hash(variant),
        )

    def test_content_edits_change_hash(self):
        base = {"questions": [_minimal_mc_q()]}
        cases = {
            "prompt": _minimal_mc_q(prompt="What is 2+3?"),
            "option": _minimal_mc_q(options=["3", "5", "5", "6"]),
            "answer": _minimal_mc_q(answer=2),
            "source_directive": None,  # handled below
        }
        base_hash = pack_cert.questions_hash(base)
        for label, q in cases.items():
            if label == "source_directive":
                edited = copy.deepcopy(base)
                edited["source_directive"] = "Grade against textbook X."
            else:
                edited = {"questions": [q]}
            with self.subTest(field=label):
                self.assertNotEqual(
                    pack_cert.questions_hash(edited),
                    base_hash,
                    f"{label} edit should change questions_hash",
                )


class BuildGateRefusalTests(unittest.TestCase):
    """Uncertified fixture pack aborts strict ``build_manifest`` (install gate)."""

    CLEAN_Q = {
        "id": "q1", "type": "multiple_choice", "topic": "math",
        "difficulty": "easy", "prompt": "What is 2+2?",
        "options": ["4", "5", "6", "7"], "answer": 0,
        "explanation": "Two plus two is four.",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.packs_dir = self.tmp_path / "question-packs"
        self.packs_dir.mkdir()
        self.manifest_path = self.packs_dir / "manifest.json"
        self.lint_log = self.tmp_path / "lint.log"
        self._patches = [
            patch.object(bm, "PACKS_DIR", self.packs_dir),
            patch.object(bm, "MANIFEST", self.manifest_path),
            patch.object(bm, "LINT_LOG", self.lint_log),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _write_pack(self, course_dir: Path, name: str, *, certify: bool) -> Path:
        questions = [dict(self.CLEAN_Q)]
        payload = {
            "title": name.replace(".json", ""),
            "notes": "",
            "coverage_blueprint": [{"topic": "math", "min": 1}],
            "questions": questions,
        }
        if certify:
            payload["certification"] = fresh_certification(payload)
        p = course_dir / name
        p.write_text(json.dumps(payload))
        return p

    def test_strict_build_aborts_on_uncertified_pack(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        self._write_pack(course, "mod1.json", certify=False)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bm.build(lint=True, strict=True)
        self.assertEqual(rc, 1)
        combined = out.getvalue() + err.getvalue()
        self.assertIn("install gate", combined)
        self.assertIn("certification missing or stale", combined)
        self.assertIn("strict mode", combined)
        self.assertFalse(self.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
