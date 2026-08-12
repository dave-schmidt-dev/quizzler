"""Unit tests for ``scripts/recert_sweep.py`` — the out-of-session batch
re-certification sweep.

recert_sweep calls the hybrid orchestrator's ``run_hybrid()`` IN-PROCESS per
pack (CV-2), which in turn drives the Layer-C critics through the internal gate
primitive. Every test here MOCKS that same
``factcheck_pack.run_claude`` (and ``verify_pack.shutil.which``) — exactly the
``patch.object`` pattern tests/test_verify_pack.py uses — so NO real LLM or
network call happens, and no live/paid sweep is ever run.

Run from the project root::

    python3 -m unittest tests.test_recert_sweep -v
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
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "recert_sweep.py"

_spec = importlib.util.spec_from_file_location("recert_sweep", SCRIPT_PATH)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

# recert_sweep imports verify_pack (which imports factcheck_pack) by path; reach
# the SAME module objects here so patches land where verify_pack.run_layer_c
# actually looks them up (mirrors test_verify_pack.py's `fc = vp.factcheck_pack`).
vp = rs.verify_pack
fc = vp.factcheck_pack
pack_cert = rs.pack_cert


class RetiredRouteTests(unittest.TestCase):
    def test_certify_one_fails_closed_without_starting_reviewers(self):
        with patch.object(rs.hybrid_verify, "run_hybrid",
                          return_value=(0, "PACK READY")) as run:
            rc, report = rs.certify_one(
                Path("pack.json"), verifier_profile="claude-opus-high", batch_size=5, timeout=42,
                jobs=2, strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("retired", report)
        self.assertIn("--certify-campaign", report)
        run.assert_not_called()

    def test_panel_route_fails_fast_with_hybrid_guidance(self):
        with patch.object(rs, "discover_packs", return_value=[Path("pack.json")]), \
             patch.object(rs, "certify_one", side_effect=AssertionError("must not run")):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = rs.main(["pack.json", "--panel", "opencode,claude"])
        self.assertEqual(rc, 1)
        self.assertIn("hybrid_verify", err.getvalue())


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def _coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def envelope(findings: list[dict], checked: int = 99) -> str:
    """Canned ``claude --output-format json`` envelope, exactly what
    ``run_claude`` returns as stdout (real call never happens)."""
    inner = json.dumps({"findings": findings, "checked": checked})
    return json.dumps({"type": "result", "result": inner,
                       "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.log_file = self.tmp_path / "sweep.log"

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, name: str, *, fresh: bool = False, **payload) -> Path:
        payload.setdefault("pack_id", name)
        payload.setdefault("questions", [dict(CLEAN_Q)])
        if "coverage_blueprint" not in payload:
            payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        p = self.tmp_path / f"{name}.json"
        p.write_text(json.dumps(payload))
        if fresh:
            data = json.loads(p.read_text())
            data["certification"] = {
                "certified": True,
                "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
                "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
                "verified_at": "2026-01-01T00:00:00+00:00",
                "questions_hash": pack_cert.questions_hash(data),
                "critic_model": "claude-sonnet-5",
                "review_method": "external-layer-c-strict",
                "blocking_count": 0,
                "questions_examined": len(data.get("questions", [])),
                "question_stamps": pack_cert.build_question_stamps(data),
            }
            p.write_text(json.dumps(data))
        return p

    def run_main(self, argv: list[str]):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = rs.main(argv)
        return rc, out.getvalue(), err.getvalue()


class ArgParserTests(unittest.TestCase):
    def test_help_lists_dry_run_and_jobs(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            rs.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        helptext = out.getvalue()
        self.assertIn("--dry-run", helptext)
        self.assertIn("--jobs", helptext)
        self.assertIn("--verifier-profile", helptext)
        self.assertIn("--log-file", helptext)

    def test_no_paths_is_argparse_usage_error(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                rs.main([])
        self.assertNotEqual(cm.exception.code, 0)

    def test_defaults(self):
        args = rs.build_arg_parser().parse_args(["some/pack.json"])
        self.assertEqual(args.jobs, fc.DEFAULT_JOBS)
        self.assertEqual(args.verifier_profile, "codex-terra-high")
        self.assertEqual(args.batch_size, 12)
        self.assertEqual(args.timeout, 180)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.strict)
        self.assertEqual(args.log_file, rs.DEFAULT_LOG_FILE)


class DiscoverPacksTests(_Base):
    def test_directory_expands_excluding_non_pack_files(self):
        self.write_pack("ch01")
        self.write_pack("ch02")
        (self.tmp_path / "_course.json").write_text("{}")
        (self.tmp_path / "manifest.json").write_text("{}")
        found = rs.discover_packs([self.tmp_path])
        names = sorted(p.name for p in found)
        self.assertEqual(names, ["ch01.json", "ch02.json"])

    def test_file_argument_passthrough_even_if_missing(self):
        ghost = self.tmp_path / "does-not-exist.json"
        found = rs.discover_packs([ghost])
        self.assertEqual(found, [ghost])

    def test_dedup_across_inputs(self):
        pack = self.write_pack("ch01")
        found = rs.discover_packs([pack, pack, self.tmp_path])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "ch01.json")

    def test_mixed_dir_and_file_arguments(self):
        self.write_pack("ch01")
        other_dir = Path(tempfile.mkdtemp())
        try:
            standalone = other_dir / "standalone.json"
            standalone.write_text("{}")
            found = rs.discover_packs([self.tmp_path, standalone])
            names = sorted(p.name for p in found)
            self.assertEqual(names, ["ch01.json", "standalone.json"])
        finally:
            standalone.unlink(missing_ok=True)
            other_dir.rmdir()


class DryRunTests(_Base):
    def test_dry_run_lists_planned_packs_and_never_calls_critic(self):
        self.write_pack("ch01")
        self.write_pack("ch02")

        def _must_not_run(*a, **kw):
            raise AssertionError("run_claude must not be called under --dry-run")

        with patch.object(fc, "run_claude", side_effect=_must_not_run):
            rc, out, err = self.run_main(
                [str(self.tmp_path), "--dry-run", "--log-file", str(self.log_file)])

        self.assertEqual(rc, 0)
        self.assertIn("PLAN", out + err)
        self.assertIn("ch01.json", out + err)
        self.assertIn("ch02.json", out + err)
        self.assertIn("2 planned", out)
        # dry-run spends no quota and does no certification, so no log lines.
        self.assertFalse(self.log_file.exists())

    def test_dry_run_still_reports_fresh_packs_as_skipped(self):
        self.write_pack("fresh", fresh=True)
        self.write_pack("stale")

        def _must_not_run(*a, **kw):
            raise AssertionError("run_claude must not be called under --dry-run")

        with patch.object(fc, "run_claude", side_effect=_must_not_run):
            rc, out, err = self.run_main([str(self.tmp_path), "--dry-run"])

        self.assertEqual(rc, 0)
        combined = out + err
        self.assertIn("SKIP", combined)
        self.assertIn("fresh.json", combined)
        self.assertIn("PLAN", combined)
        self.assertIn("stale.json", combined)


class IdempotentResumeTests(_Base):
    """CV-3: fresh packs skip; stale packs are handed to the hybrid route."""

    def test_fresh_pack_skipped_stale_pack_uses_hybrid(self):
        self.write_pack("fresh", fresh=True)
        stale = self.write_pack("stale")
        seen = []

        def fake_certify(pack_path, **kwargs):
            seen.append(pack_path)
            return 0, "PACK READY"

        with patch.object(rs, "certify_one", side_effect=fake_certify):
            rc, out, err = self.run_main([str(self.tmp_path), "--log-file", str(self.log_file)])
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [stale])
        self.assertIn("SKIP", out + err)
        self.assertIn("CERTIFIED", out + err)

    def test_old_critic_contract_is_regraded_not_skipped(self):
        """A version bump must select the stale pack for the hybrid route."""
        pack = self.write_pack("old-contract", fresh=True)
        data = json.loads(pack.read_text())
        data["certification"]["critic_contract_version"] = "2026-07-20"
        pack.write_text(json.dumps(data))
        seen = []

        def fake_certify(pack_path, **kwargs):
            seen.append(pack_path)
            return 0, "PACK READY"

        with patch.object(rs, "certify_one", side_effect=fake_certify):
            rc, out, err = self.run_main(
                [str(self.tmp_path), "--log-file", str(self.log_file)])

        self.assertEqual(rc, 0)
        self.assertEqual(seen, [pack])
        self.assertNotIn("SKIP", out + err)
        self.assertIn("CERTIFIED", out + err)

    def test_panel_option_is_retired_before_any_pack_runs(self):
        self.write_pack("ch01")
        with patch.object(rs, "certify_one", side_effect=AssertionError("must not run")):
            rc, _, err = self.run_main([str(self.tmp_path), "--panel", "opencode,claude"])
        self.assertEqual(rc, 1)
        self.assertIn("hybrid_verify", err)


class NotReadyAndErrorTests(_Base):
    def test_blocking_finding_is_not_ready_and_sweep_exit_is_nonzero(self):
        self.write_pack("bad")

        with patch.object(rs, "certify_one", return_value=(2, "PACK NOT READY")):
            rc, out, err = self.run_main(
                [str(self.tmp_path), "--log-file", str(self.log_file)])

        self.assertEqual(rc, 1)  # sweep-level failure signal
        combined = out + err
        self.assertIn("NOT READY", combined)
        self.assertIn("bad.json", combined)
        log_text = self.log_file.read_text()
        self.assertIn("PACK NOT READY", log_text)

    def test_missing_pack_file_argument_is_error_outcome(self):
        ghost = self.tmp_path / "does-not-exist.json"
        rc, out, err = self.run_main([str(ghost), "--log-file", str(self.log_file)])
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", out + err)

    def test_empty_directory_is_operational_error(self):
        empty_dir = self.tmp_path / "empty-course"
        empty_dir.mkdir()
        rc, _out, err = self.run_main([str(empty_dir)])
        self.assertEqual(rc, 1)
        self.assertIn("no packs found", err)


class CertifyOneTests(_Base):
    def test_live_sweep_cannot_delegate_to_hybrid(self):
        pack = self.write_pack("ch01")
        with patch.object(rs.hybrid_verify, "run_hybrid",
                          return_value=(0, "PACK READY")) as run:
            rc, report = rs.certify_one(
                pack, verifier_profile="claude-opus-high", batch_size=5, timeout=42, jobs=3, strict=True)

        self.assertEqual(rc, 1)
        self.assertIn("retired", report)
        run.assert_not_called()


class SummaryFormatTests(unittest.TestCase):
    def test_format_summary_renders_tags_and_tally(self):
        results = [
            {"pack": "a.json", "outcome": "certified", "exit_code": 0},
            {"pack": "b.json", "outcome": "skipped", "exit_code": None},
            {"pack": "c.json", "outcome": "not_ready", "exit_code": 2},
            {"pack": "d.json", "outcome": "error", "exit_code": 1},
        ]
        out = rs.format_summary(results)
        self.assertIn("4 pack(s)", out)
        self.assertIn("[CERTIFIED]", out)
        self.assertIn("a.json", out)
        self.assertIn("[SKIPPED  ]", out)
        self.assertIn("b.json", out)
        self.assertIn("[NOT READY]", out)
        self.assertIn("(exit 2)", out)
        self.assertIn("[ERROR    ]", out)
        # order preserved: a before b before c before d
        self.assertLess(out.index("a.json"), out.index("b.json"))
        self.assertLess(out.index("b.json"), out.index("c.json"))
        self.assertLess(out.index("c.json"), out.index("d.json"))
        # tally line
        self.assertIn("1 certified", out)
        self.assertIn("1 error", out)
        self.assertIn("1 not_ready", out)
        self.assertIn("1 skipped", out)

    def test_format_summary_empty(self):
        out = rs.format_summary([])
        self.assertIn("0 pack(s)", out)
        self.assertIn("nothing to report", out)


class IsFreshTests(_Base):
    def test_fresh_pack_is_fresh(self):
        pack = self.write_pack("ch01", fresh=True)
        self.assertTrue(rs.is_fresh(pack))

    def test_uncertified_pack_is_not_fresh(self):
        pack = self.write_pack("ch01")
        self.assertFalse(rs.is_fresh(pack))

    def test_unreadable_pack_is_not_fresh(self):
        ghost = self.tmp_path / "does-not-exist.json"
        self.assertFalse(rs.is_fresh(ghost))

    def test_stale_hash_after_edit_is_not_fresh(self):
        pack = self.write_pack("ch01", fresh=True)
        data = json.loads(pack.read_text())
        data["questions"][0]["prompt"] = "What is 2+3?"  # content changed post-cert
        pack.write_text(json.dumps(data))
        self.assertFalse(rs.is_fresh(pack))

    def test_pack_certified_under_old_critic_contract_is_not_fresh(self):
        # 2026-08-11: CRITIC_CONTRACT_VERSION bumped so the sweep re-grades any
        # pack certified before the critic stopped hardcoding a Security+
        # persona (see pack_cert.py's version comment). A cert stamped with
        # the OLD contract version must not read as fresh, even though every
        # other field (hash, stamps, review_method) is otherwise valid.
        pack = self.write_pack("ch01", fresh=True)
        data = json.loads(pack.read_text())
        data["certification"]["critic_contract_version"] = "2026-07-20"
        self.assertNotEqual(data["certification"]["critic_contract_version"],
                            pack_cert.CRITIC_CONTRACT_VERSION)
        pack.write_text(json.dumps(data))
        self.assertFalse(rs.is_fresh(pack))


if __name__ == "__main__":
    unittest.main()
