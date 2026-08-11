"""Unit tests for ``scripts/recert_sweep.py`` — the out-of-session batch
re-certification sweep.

recert_sweep calls scripts/verify_pack.py's ``main()`` IN-PROCESS per pack
(CV-2), which in turn drives the Layer-C LLM critic through
``factcheck_pack.run_claude``. Every test here MOCKS that same
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
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                rs.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        helptext = out.getvalue()
        self.assertIn("--dry-run", helptext)
        self.assertIn("--jobs", helptext)
        self.assertIn("--model", helptext)
        self.assertIn("--log-file", helptext)

    def test_no_paths_is_argparse_usage_error(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                rs.main([])
        self.assertNotEqual(cm.exception.code, 0)

    def test_defaults(self):
        args = rs.build_arg_parser().parse_args(["some/pack.json"])
        self.assertEqual(args.jobs, fc.DEFAULT_JOBS)
        self.assertEqual(args.model, "claude-sonnet-5")
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
    """CV-3: an already-fresh pack is skipped (no quota spent on it); a re-run
    after a partial sweep only re-certifies packs that are not yet fresh."""

    def test_fresh_pack_skipped_stale_pack_certified(self):
        self.write_pack("fresh", fresh=True)
        self.write_pack("stale")

        call_count = 0

        def _counting_run_claude(prompt, model, timeout):
            nonlocal call_count
            call_count += 1
            return envelope([])

        with patch.object(fc, "run_claude", side_effect=_counting_run_claude), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            rc, out, err = self.run_main(
                [str(self.tmp_path), "--log-file", str(self.log_file)])

        self.assertEqual(rc, 0)
        combined = out + err
        self.assertIn("SKIP", combined)
        self.assertIn("fresh.json", combined)
        self.assertIn("CERTIFIED", combined)
        self.assertIn("stale.json", combined)
        # Only the stale pack's single question/single batch reached the critic.
        self.assertEqual(call_count, 1)
        # The certified pack's full verify_pack report was logged.
        log_text = self.log_file.read_text()
        self.assertIn("stale.json", log_text)
        self.assertIn("PACK READY", log_text)
        # The already-fresh pack never triggered a certification run at all,
        # so it has no log entry of its own.
        self.assertNotIn("fresh.json", log_text)

    def test_pack_certified_under_old_critic_contract_is_regraded_not_skipped(self):
        """2026-08-11: the CRITIC_CONTRACT_VERSION bump must actually force a
        re-grade end-to-end, not just fail an is_fresh() unit check — a pack
        stamped under the old contract has to spend quota and come out
        re-certified under the current one, exactly like any other stale
        pack."""
        pack = self.write_pack("ch01", fresh=True)
        data = json.loads(pack.read_text())
        data["certification"]["critic_contract_version"] = "2026-07-20"
        pack.write_text(json.dumps(data))

        call_count = 0

        def _counting_run_claude(prompt, model, timeout):
            nonlocal call_count
            call_count += 1
            return envelope([])

        with patch.object(fc, "run_claude", side_effect=_counting_run_claude), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            rc, out, err = self.run_main(
                [str(self.tmp_path), "--log-file", str(self.log_file)])

        self.assertEqual(rc, 0)
        self.assertEqual(call_count, 1, "the critic must actually run — not be skipped")
        self.assertIn("CERTIFIED", out + err)
        self.assertNotIn("SKIP", out + err)
        reloaded = json.loads(pack.read_text())
        self.assertEqual(reloaded["certification"]["critic_contract_version"],
                         pack_cert.CRITIC_CONTRACT_VERSION)

    def test_rerun_after_success_skips_everything(self):
        # Simulates re-invoking the sweep after a prior run certified both
        # packs: the second run must skip both and spend zero quota.
        pack_a = self.write_pack("a")
        pack_b = self.write_pack("b")

        with patch.object(fc, "run_claude", return_value=envelope([])), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            rc1, _, _ = self.run_main([str(self.tmp_path)])
        self.assertEqual(rc1, 0)
        # Both packs are now certified on disk (verify_pack stamped them).
        self.assertIn("certification", json.loads(pack_a.read_text()))
        self.assertIn("certification", json.loads(pack_b.read_text()))

        def _must_not_run(*a, **kw):
            raise AssertionError("re-run must not re-spend quota on fresh packs")

        with patch.object(fc, "run_claude", side_effect=_must_not_run):
            rc2, out2, err2 = self.run_main([str(self.tmp_path)])

        self.assertEqual(rc2, 0)
        combined = out2 + err2
        self.assertIn("a.json", combined)
        self.assertIn("b.json", combined)
        self.assertEqual(combined.count("SKIP"), 4)  # 2 progress lines + 2 summary tags
        self.assertIn("2 skipped", out2)


class NotReadyAndErrorTests(_Base):
    def test_blocking_finding_is_not_ready_and_sweep_exit_is_nonzero(self):
        self.write_pack("bad")
        finding = {"qid": "q1", "severity": "wrong-answer", "issue": "wrong",
                   "correction": "fix", "confidence": "high"}

        with patch.object(fc, "run_claude", return_value=envelope([finding])), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
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
        rc, out, err = self.run_main([str(empty_dir)])
        self.assertEqual(rc, 1)
        self.assertIn("no packs found", err)


class CertifyOneTests(_Base):
    def test_passes_jobs_model_batch_size_timeout_through(self):
        pack = self.write_pack("ch01")
        captured_argv = {}

        def fake_main(argv):
            captured_argv["argv"] = argv
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = rs.certify_one(
                pack, model="opus", batch_size=5, timeout=42, jobs=3, strict=True)

        self.assertEqual(rc, 0)
        argv = captured_argv["argv"]
        self.assertEqual(argv[0], str(pack))
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertIn("--batch-size", argv)
        self.assertEqual(argv[argv.index("--batch-size") + 1], "5")
        self.assertIn("--timeout", argv)
        self.assertEqual(argv[argv.index("--timeout") + 1], "42")
        self.assertIn("--jobs", argv)
        self.assertEqual(argv[argv.index("--jobs") + 1], "3")
        self.assertIn("--strict", argv)

    def test_strict_flag_omitted_by_default(self):
        pack = self.write_pack("ch01")
        captured_argv = {}

        def fake_main(argv):
            captured_argv["argv"] = argv
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rs.certify_one(pack, model="claude-sonnet-5", batch_size=12,
                           timeout=180, jobs=6, strict=False)

        self.assertNotIn("--strict", captured_argv["argv"])

    def test_panel_is_forwarded_and_replaces_model(self):
        """The sweep is the bulk path — the panel has to be reachable here too.

        `--model` must NOT ride along: it defaults to a Claude model id, and
        each panel pass already carries its own model in the spec.
        """
        pack = self.write_pack("ch01")
        captured_argv = {}

        def fake_main(argv):
            captured_argv["argv"] = argv
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rs.certify_one(pack, model="claude-sonnet-5", batch_size=12,
                           timeout=180, jobs=6, strict=False,
                           panel="opencode,openai-compatible=gw-model")

        argv = captured_argv["argv"]
        self.assertIn("--panel", argv)
        self.assertEqual(argv[argv.index("--panel") + 1],
                         "opencode,openai-compatible=gw-model")
        self.assertNotIn("--model", argv)

    def test_variant_is_forwarded_when_given(self):
        pack = self.write_pack("ch01")
        captured_argv = {}

        def fake_main(argv):
            captured_argv["argv"] = argv
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rs.certify_one(pack, model="claude-sonnet-5", batch_size=12,
                           timeout=180, jobs=6, strict=False,
                           panel="opencode,claude", variant="max")

        argv = captured_argv["argv"]
        self.assertIn("--variant", argv)
        self.assertEqual(argv[argv.index("--variant") + 1], "max")

    def test_variant_omitted_by_default(self):
        pack = self.write_pack("ch01")
        captured_argv = {}

        def fake_main(argv):
            captured_argv["argv"] = argv
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rs.certify_one(pack, model="claude-sonnet-5", batch_size=12,
                           timeout=180, jobs=6, strict=False)

        self.assertNotIn("--variant", captured_argv["argv"])

    def test_panel_over_a_single_critic_course_reports_the_method_mismatch(self):
        """`--panel` on an existing single-critic fleet must not silently no-op.

        Freshness is a CONTENT check, so every `external-layer-c-strict` pack is
        "fresh" against a panel run too. Without a signal, upgrading a course to
        panel certification would print SKIP for every pack, grade nothing, exit
        0, and read as "the course is already panel-certified".
        """
        self.write_pack("ch01", fresh=True)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(rs, "certify_one",
                          side_effect=AssertionError("nothing should be graded")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = rs.main([str(self.tmp_path), "--panel", "opencode,claude"])
        self.assertEqual(rc, 0)
        self.assertIn("method=external-layer-c-strict", err.getvalue())
        self.assertIn("--force", err.getvalue())

    def test_force_regrades_a_fresh_pack(self):
        """The remedy the mismatch note names has to actually work."""
        self.write_pack("ch01", fresh=True)
        graded = []

        def fake_certify(pack_path, **kw):
            graded.append(kw.get("panel"))
            return 0, "ok"

        with patch.object(rs, "certify_one", side_effect=fake_certify):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rs.main([str(self.tmp_path), "--panel", "opencode,claude",
                              "--force"])
        self.assertEqual(rc, 0)
        self.assertEqual(graded, ["opencode,claude"])

    def test_without_force_a_fresh_pack_of_the_same_method_is_still_skipped(self):
        """Idempotent resume (CV-3) survives: no mismatch note, no re-grading."""
        self.write_pack("ch01", fresh=True)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(rs, "certify_one",
                          side_effect=AssertionError("nothing should be graded")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = rs.main([str(self.tmp_path)])
        self.assertEqual(rc, 0)
        self.assertIn("SKIP", err.getvalue())
        self.assertNotIn("--force", err.getvalue())

    def test_a_bad_panel_spec_aborts_before_any_pack_is_graded(self):
        """A sweep runs for hours; a typo must not cost pack 1's quota first."""
        self.write_pack("ch01")
        with patch.object(rs, "certify_one",
                          side_effect=AssertionError("no pack may be graded")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                rc = rs.main([str(self.tmp_path), "--panel", "opencode"])
        self.assertEqual(rc, 1)
        self.assertIn("at least 2", err.getvalue())

    def test_variant_without_panel_aborts_before_any_pack_is_graded(self):
        """--variant alone certifies via the default claude provider, which
        does not support one — catch it up front, not per-pack."""
        self.write_pack("ch01")
        with patch.object(rs, "certify_one",
                          side_effect=AssertionError("no pack may be graded")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                rc = rs.main([str(self.tmp_path), "--variant", "max"])
        self.assertEqual(rc, 1)
        self.assertIn("--variant", err.getvalue())


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
