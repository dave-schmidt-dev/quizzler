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

# verify_pack pulls in factcheck_pack + pack_cert by path during its own load; the
# INV-7 B.1 tests below patch factcheck_pack.run_claude (NO live/paid LLM call) and
# build/read certs, so reach the SAME module objects verify_pack uses internally.
_vp_spec = importlib.util.spec_from_file_location(
    "verify_pack", PROJECT_ROOT / "scripts" / "verify_pack.py"
)
vp = importlib.util.module_from_spec(_vp_spec)
_vp_spec.loader.exec_module(vp)
fc = vp.factcheck_pack
pc = vp.pack_cert


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
        "review_method": "external-layer-c-strict",
        "blocking_count": 0,
        "questions_examined": len(questions) if isinstance(questions, list) else 0,
        "question_stamps": pack_cert.build_question_stamps(pack_dict),
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


# ── INV-7 B.1: per-question re-cert stamps + context_only mode ───────────────────
#
# These tests exercise the per-qid certification stamp registry (`question_stamps`),
# the PM-3 coverage rule in `certification_fresh`, the `context_only` critic mode,
# and verify_pack's `--only` per-qid re-cert path. Every LLM call is MOCKED via
# factcheck_pack.run_claude — NO live/paid sweep runs here.

# Two lint-clean MC questions, distinct enough to stay Layer-A clean together (no
# duplicate-stem/answer tells). Mirrors test_verify_pack's CLEAN_Q / CLEAN_Q2.
_B1_Q1 = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}
_B1_Q2 = {
    "id": "q2", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 3 times 3?",
    "options": ["9", "6", "12", "3"], "answer": 0,
    "explanation": "Three times three is nine.",
}


def _b1_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def _critic_envelope(findings: list[dict], checked=99) -> str:
    """A canned ``claude --output-format json`` envelope (what run_claude returns)."""
    inner = json.dumps({"findings": findings, "checked": checked})
    return json.dumps({"type": "result", "result": inner,
                       "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})


def _new_format_cert(pack_dict: dict, *, model: str = "claude-sonnet-5") -> dict:
    """A full NEW-format cert: aggregate hash + a per-qid ``question_stamps`` registry."""
    questions = pack_dict.get("questions", [])
    return {
        "certified": True,
        "hash_schema_version": pc.HASH_SCHEMA_VERSION,
        "critic_contract_version": pc.CRITIC_CONTRACT_VERSION,
        "verified_at": "2026-07-20T00:00:00+00:00",
        "questions_hash": pc.questions_hash(pack_dict),
        "critic_model": model,
        "review_method": "external-layer-c-strict",
        "blocking_count": 0,
        "questions_examined": len(questions) if isinstance(questions, list) else 0,
        "question_stamps": pc.build_question_stamps(pack_dict),
    }


class _RecertBase(unittest.TestCase):
    """Throw-away temp-pack harness + mocked Layer-C critic (no live LLM call)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, questions: list[dict], *, certification: dict | None = None,
                   **extra) -> Path:
        payload = {
            "title": "b1-recert-test", "notes": "",
            "coverage_blueprint": _b1_blueprint(questions),
            "questions": questions,
        }
        payload.update(extra)
        if certification is not None:
            payload["certification"] = certification
        p = self.tmp_path / "pack.json"
        p.write_text(json.dumps(payload))
        return p

    def reload(self, pack_path: Path) -> dict:
        return json.loads(pack_path.read_text())

    def run_main(self, argv: list[str], findings: list[dict] | None = None,
                 checked=99):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude",
                          return_value=_critic_envelope(findings or [], checked)), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()


class PerQidCoverageFreshnessTests(_RecertBase):
    """PM-3: a NEW-format aggregate is fresh only when EVERY qid has a matching fresh
    per-qid stamp; a LEGACY cert (no stamps) stays valid by aggregate hash alone.
    Exercises ``pack_cert.certification_fresh`` DIRECTLY."""

    def _pack_dict(self) -> dict:
        return {
            "questions": [dict(_B1_Q1), dict(_B1_Q2)],
            "source_directive": "Grade against the course text.",
        }

    def test_new_format_fresh_requires_every_qid_stamp(self):
        pack = self._pack_dict()
        pack["certification"] = _new_format_cert(pack)
        # Baseline: a complete new-format cert is fresh.
        self.assertTrue(pc.certification_fresh(pack))

        # Drop q2's stamp → NOT fresh, even though the aggregate questions_hash
        # still matches. This is the PM-3 coverage rule: no fresh aggregate while a
        # qid is unaccounted for.
        missing = copy.deepcopy(pack)
        del missing["certification"]["question_stamps"]["q2"]
        self.assertTrue(
            missing["certification"]["questions_hash"] == pc.questions_hash(missing),
            "aggregate hash must still match — proving the failure is coverage, not hash",
        )
        self.assertFalse(pc.certification_fresh(missing))

        # A present-but-STALE stamp for q2 → also not fresh.
        wrong = copy.deepcopy(pack)
        wrong["certification"]["question_stamps"]["q2"] = "sha256:" + "0" * 64
        self.assertFalse(pc.certification_fresh(wrong))

    def test_editing_a_covered_qid_makes_new_format_stale(self):
        pack = self._pack_dict()
        pack["certification"] = _new_format_cert(pack)
        # A content edit to q1 (explanation is in RELEVANT_FIELDS) breaks BOTH the
        # aggregate hash and q1's per-qid stamp.
        pack["questions"][0]["explanation"] = "Edited: two plus two still equals four."
        self.assertFalse(pc.certification_fresh(pack))

    def test_legacy_cert_without_stamps_is_no_longer_valid(self):
        # REVERSED 2026-08-07. The legacy aggregate-only path used to keep a
        # stamp-less cert valid so an upgrade would not invalidate the world.
        # That also meant a cert could skip per-question coverage entirely just
        # by omitting the registry — "not graded question-by-question" was
        # indistinguishable from "graded and clean". Per-qid stamps are now
        # mandatory; a stamp-less cert must be re-certified, not grandfathered.
        pack = self._pack_dict()
        cert = _new_format_cert(pack)
        del cert["question_stamps"]  # legacy shape
        pack["certification"] = cert
        self.assertFalse(pc.certification_fresh(pack))

        # Restoring the registry re-validates it (the aggregate is unchanged).
        pack["certification"]["question_stamps"] = pc.build_question_stamps(pack)
        self.assertTrue(pc.certification_fresh(pack))

        # A cert whose aggregate hash is stale is still rejected.
        stale = copy.deepcopy(pack)
        stale["certification"]["questions_hash"] = "sha256:" + "0" * 64
        self.assertFalse(pc.certification_fresh(stale))

    def test_malformed_stamps_registry_fails_closed(self):
        # A `question_stamps` present but not a dict must NOT certify (fail closed).
        pack = self._pack_dict()
        cert = _new_format_cert(pack)
        cert["question_stamps"] = ["q1", "q2"]  # wrong type
        pack["certification"] = cert
        self.assertFalse(pc.certification_fresh(pack))


class PerQidRecertPathTests(_RecertBase):
    """verify_pack ``--only`` RE-CERTIFIES the whole-pack aggregate when every qid is
    covered by a fresh per-qid stamp (the refresh path). run_claude is MOCKED."""

    def test_only_recert_refreshes_edited_qid_and_restamps_aggregate(self):
        # Fully-certified new-format pack, then edit q1's content (explanation only,
        # so Layer A stays clean — no stem/option tells introduced).
        certified = {"questions": [dict(_B1_Q1), dict(_B1_Q2)]}
        pack = self.write_pack([dict(_B1_Q1), dict(_B1_Q2)],
                               certification=_new_format_cert(certified))
        data = self.reload(pack)
        data["questions"][0]["explanation"] = "Edited: 2+2 = 4, a basic sum."
        pack.write_text(json.dumps(data))

        # The edit made the pack STALE (q1's stamp + aggregate no longer match).
        self.assertFalse(pc.certification_fresh(self.reload(pack)))

        # Re-cert ONLY q1 → all qids now covered by fresh stamps → exit 0.
        rc, out, err = self.run_main([str(pack), "--only", "q1"], findings=[])
        self.assertEqual(rc, 0, f"expected recert exit 0; err={err!r} out={out!r}")
        self.assertIn("RE-CERTIFIED", out)
        self.assertNotIn("PACK NOT READY", out)

        reloaded = self.reload(pack)
        self.assertTrue(pc.certification_fresh(reloaded),
                        "aggregate must be fresh after per-qid re-cert")
        self.assertEqual(set(reloaded["certification"]["question_stamps"]),
                         {"q1", "q2"}, "every qid must carry a fresh stamp")
        self.assertEqual(reloaded["certification"]["questions_examined"], 2,
                         "examined must be the FULL pack count, not the subset")

    def test_only_recert_refused_when_other_qid_edited_but_unaudited(self):
        # The non-bypass property: q1 AND q2 are both edited, but only q1 is
        # re-graded via --only q1. q2's carried stamp no longer matches its content,
        # so the aggregate must NOT be re-stamped — exit 3, pack left UNCHANGED.
        certified = {"questions": [dict(_B1_Q1), dict(_B1_Q2)]}
        pack = self.write_pack([dict(_B1_Q1), dict(_B1_Q2)],
                               certification=_new_format_cert(certified))
        data = self.reload(pack)
        data["questions"][0]["explanation"] = "Edited q1 explanation."
        data["questions"][1]["explanation"] = "Edited q2 explanation — NOT re-audited."
        pack.write_text(json.dumps(data))
        before = pack.read_text()

        rc, out, _ = self.run_main([str(pack), "--only", "q1"], findings=[])
        self.assertEqual(rc, 3)
        self.assertIn("SUBSET RECHECK PASSED", out)
        self.assertNotIn("RE-CERTIFIED", out)
        # Pack byte-unchanged: no forged aggregate over the unaudited q2 edit.
        self.assertEqual(pack.read_text(), before)
        self.assertFalse(pc.certification_fresh(self.reload(pack)))


class ContextOnlyDedupBlockTests(_RecertBase):
    """A context_only re-cert of a newly-DUPLICATED edited qid STILL BLOCKS the
    aggregate — dedup safety is preserved. The dup is SEMANTIC (Layer A / L9 sees
    only stem tokens and stays silent); the block comes solely from the mocked
    context_only Layer-C critic finding, which is why editing only the explanation
    keeps Layer A provably clean."""

    def test_semantic_dup_on_edited_qid_blocks_and_does_not_certify(self):
        certified = {"questions": [dict(_B1_Q1), dict(_B1_Q2)]}
        pack = self.write_pack([dict(_B1_Q1), dict(_B1_Q2)],
                               certification=_new_format_cert(certified))
        # Edit q1's explanation so it now re-tests q2's keyed fact (a semantic dup
        # L9 cannot see — the stems "What is 2+2?" / "What is 3 times 3?" don't
        # overlap). Structure unchanged → Layer A clean.
        data = self.reload(pack)
        data["questions"][0]["explanation"] = (
            "Two plus two is four; note three times three is nine.")
        pack.write_text(json.dumps(data))

        # The context_only critic flags a high-confidence cross-question duplication
        # ON THE GRADED qid (q1) → blocking.
        dup = {"qid": "q1", "severity": "ambiguous",
               "issue": "q1 now re-tests the same keyed fact as q2 (3x3=9)",
               "correction": "merge or diversify q1", "confidence": "high"}
        rc, out, _ = self.run_main([str(pack), "--only", "q1"], findings=[dup])

        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertNotIn("RE-CERTIFIED", out)
        # The blocked run must NOT forge a fresh aggregate.
        self.assertFalse(pc.certification_fresh(self.reload(pack)))

    def test_context_only_prompt_requests_cross_question_dedup(self):
        # Guards the dedup MECHANISM (not just "a finding blocks"): build_prompt must
        # inject the CONTEXT-ONLY instruction naming the graded vs context ids so the
        # critic is actually asked for whole-pack cross-question duplication. The
        # default (no context_qids) path must stay byte-clean of that instruction.
        with_ctx = fc.build_prompt([dict(_B1_Q1), dict(_B1_Q2)], context_qids={"q2"})
        self.assertIn("CONTEXT-ONLY", with_ctx)
        self.assertIn("CROSS-QUESTION DUPLICATION", with_ctx)
        self.assertIn("q1", with_ctx)  # graded id named
        self.assertIn("q2", with_ctx)  # context id named
        self.assertNotIn("CONTEXT-ONLY", fc.build_prompt([dict(_B1_Q1)]))


class ContextOnlyCostTests(unittest.TestCase):
    """Cost win (F2): the context_only path submits FEWER qids for grading than a
    full pass. Asserts on ``collect_findings(...)['questions_graded']`` with the
    critic mocked — no live LLM call, no verify_pack round-trip needed."""

    def test_context_only_grades_fewer_qids_than_full_pass(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        env = _critic_envelope([], checked=99)
        with patch.object(fc, "run_claude", return_value=env):
            full = fc.collect_findings(qs, None, 12, 5)
            ctx = fc.collect_findings(qs, None, 12, 5, context_qids={"q2", "q3"})
        # Full pass grades all three; context_only grades only the one non-context qid.
        self.assertEqual(full["questions_graded"], 3)
        self.assertEqual(ctx["questions_graded"], 1)
        self.assertLess(ctx["questions_graded"], full["questions_graded"])
        # The context ride-along must not be mistaken for a coverage gap (checked ≥
        # graded), so a clean context_only pass stays fully covered.
        self.assertTrue(fc.coverage_ok(ctx))


if __name__ == "__main__":
    unittest.main()
