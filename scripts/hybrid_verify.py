#!/usr/bin/env python3
"""Two-pass hybrid Layer-C verify: a cheap DeepSeek(-go) pass reviews first,
Claude runs the certifying pass only if that pass came back clean.

Rationale (measured, not assumed): a same-pack grounded retest comparing a
single Claude Layer-C pass against three DeepSeek-go passes found each side
catches real issues the other misses — DeepSeek alone is not reliable as a
SOLE/certifying critic. verify_pack.py already encodes the matching safety
rule structurally: only its DEFAULT provider (``claude``) certifies on a
single pass; any other single ``--provider`` (including ``opencode``) reviews
and exits 3 (REVIEW PASSED, pack unchanged) but never writes a certification.
HV-1: this script needs ZERO changes to that rule, or to verify_pack.py's
``_write_certification`` / ``APPROVED_REVIEW_METHODS`` internals at all — it
is a pure orchestration layer that calls ``verify_pack.main()`` IN-PROCESS
twice (mirrors scripts/recert_sweep.py's CV-2: a subprocess boundary would
defeat a test's ``patch.object(factcheck_pack, "run_claude")`` mock) and
short-circuits before the second call whenever the first one is not clean:

  1. DS pass  — ``--provider opencode --model <ds-model> --variant <variant>``.
     Cheap review pass. If it finds a blocking issue or errors, STOP — the
     Claude pass never runs, so no Claude quota is spent on a pack that is not
     ready yet.
  2. Claude pass — only reached when the DS pass is clean (exit 3). Runs with
     the default ``claude`` provider, which is the one provider that can
     certify solo. Its result (0 ready / 2 not ready / 1 error) is this
     script's own final result.

The DS pass's own "clean but not certified" outcome (exit 3) is a PROCEED
signal here, never this wrapper's own terminal state — only the Claude pass's
exit code is returned.

Default ``--ds-model`` is opencode's paid "go" tier (``opencode-go/deepseek-
v4-flash``), NOT opencode's free tier. A bare model id such as
``deepseek-v4-flash-free`` gets namespaced to the free tier instead (see
``critic_providers._opencode_model_ref`` / docs/CRITIC_PROVIDERS.md); passing
one already containing ``/`` reaches the go tier explicitly, which is what
this script's default does on every invocation rather than relying on
opencode's own provider default.

Usage:
  python3 scripts/hybrid_verify.py <pack>                    # DS bulk, Opus certifies (default)
  python3 scripts/hybrid_verify.py <pack> --strict
  python3 scripts/hybrid_verify.py <pack> --claude-model sonnet   # cheaper certifying pass
  python3 scripts/hybrid_verify.py <pack> --ds-model opencode/deepseek-v4-flash-free

Exit code — the Claude pass's own meaning when it ran, else the DS pass's:
  0  PACK READY, certified by Claude (DS pass was clean, Claude pass found
     nothing blocking).
  2  NOT READY — either the DS pass found a blocking issue (Claude never ran,
     no Claude quota spent) or the Claude pass did, AFTER a clean DS pass.
     The second case is the disagreement this script's whole design rests on:
     a DS-clean pack can still have a real issue Claude catches — which is
     exactly why Claude stays the certifying pass, not a bug in this wrapper.
  1  Operational error at whichever pass ran last (bad pack path, critic CLI
     missing, malformed provider reply, etc — see the printed report).
Never 3 — that is verify_pack's own "reviewed but not certified" signal for a
single non-default provider; here it only ever means "proceed to the Claude
pass", not a terminal outcome of this script.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# scripts/ isn't a package; import verify_pack by path, the same trick
# verify_pack.py itself uses to reach lint_packs/factcheck_pack/pack_cert.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_pack   # noqa: E402

# NOT a fresh import — the exact factcheck_pack module object verify_pack
# imported, so a test's patch.object(factcheck_pack, "run_claude") reaches
# verify_pack.run_layer_c from here too (mirrors recert_sweep.py's rationale).
factcheck_pack = verify_pack.factcheck_pack

DEFAULT_DS_MODEL = "opencode-go/deepseek-v4-flash"
DEFAULT_DS_VARIANT = "max"
# 2026-08-11: David's standing rule for this pipeline is "DeepSeek for bulk
# work, Opus for logic and validation" — the DS pass above already IS the bulk
# pass, so the certifying pass defaults to Opus rather than Sonnet. Override
# with --claude-model for a specific point release.
DEFAULT_CLAUDE_MODEL = "opus"

# verify_pack's full-gate exit codes for one pass: 0 ready/certified,
# 2 not ready, 3 reviewed-but-not-certified (single non-default provider),
# 1 operational error. Only 3 is a PROCEED signal at the DS stage — every
# other code (including a defensive, currently-impossible 0: --provider
# opencode never certifies solo, see module docstring) stops here rather
# than spend Claude quota on an unexpected outcome.
_DS_PROCEED_CODES = {3}

_DS_STOP_LABELS = {1: "ERROR", 2: "NOT READY"}
_CLAUDE_OUTCOME_LABELS = {0: "READY, certified", 2: "NOT READY", 1: "ERROR"}


def _run_pass(argv: list[str]) -> tuple[int, str]:
    """Call verify_pack.main(argv) IN-PROCESS and capture its printed report
    instead of letting it hit this process's real stdout/stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = verify_pack.main(argv)
    report = out.getvalue()
    if err.getvalue():
        report += ("\n" if report and not report.endswith("\n") else "") + err.getvalue()
    return rc, report


def run_hybrid(pack: Path, *, ds_model: str, variant: str, claude_model: str,
               batch_size: int, timeout: int, jobs: int, strict: bool,
               progress=lambda msg: None) -> tuple[int, str]:
    """Run the DS pass, then the Claude certifying pass only if DS is clean.

    Returns ``(exit_code, combined_report_text)`` using verify_pack's own
    0/1/2 meanings for whichever pass produced the final result (see module
    docstring). `progress(msg)` fires at each pass boundary — INV-1: two
    LLM-backed passes can each take minutes, so the caller must see which
    pass is running rather than wait on a silent block.
    """
    common = ["--batch-size", str(batch_size), "--timeout", str(timeout),
              "--jobs", str(jobs)]
    if strict:
        common.append("--strict")

    progress(f"[1/2] DS pass ({ds_model}, variant={variant}) — reviewing; "
             "a single non-default provider never certifies alone...")
    ds_argv = ([str(pack), "--provider", "opencode", "--model", ds_model,
               "--variant", variant] + common)
    ds_rc, ds_report = _run_pass(ds_argv)

    if ds_rc not in _DS_PROCEED_CODES:
        label = _DS_STOP_LABELS.get(ds_rc, f"UNEXPECTED exit {ds_rc}")
        progress(f"[1/2] DS pass: {label} — stopping here; the Claude pass "
                 "did NOT run, so no Claude quota was spent.")
        return ds_rc, ds_report

    progress("[1/2] DS pass: clean, not certified (expected).")
    progress(f"[2/2] Claude pass ({claude_model}) — the certifying pass...")
    claude_argv = [str(pack), "--model", claude_model] + common
    claude_rc, claude_report = _run_pass(claude_argv)

    combined = ds_report
    if claude_report:
        combined += ("\n" if combined and not combined.endswith("\n") else "") + claude_report
    if claude_rc == 2:
        combined += (
            "\n\nnote: the DS pass came back clean but Claude's certifying "
            "pass found a blocking issue below — the cheap pass missed "
            "something the certifying pass caught. This is expected "
            "occasionally and is exactly why Claude remains the certifying "
            "model; fix the finding(s) above and re-run.")

    progress(f"[2/2] Claude pass: "
             f"{_CLAUDE_OUTCOME_LABELS.get(claude_rc, f'exit {claude_rc}')}")
    return claude_rc, combined


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Two-pass hybrid Layer-C verify: a cheap DeepSeek(-go) "
        "pass reviews first (never certifies alone); Claude runs the "
        "certifying pass ONLY if that pass is clean, so Claude quota is "
        "spent only on packs that already look ready. Touches none of "
        "verify_pack.py's certification internals — pure orchestration on "
        "top of its existing --provider opencode (reviews) vs default "
        "--provider claude (certifies) contract. See this script's own "
        "module docstring for the full exit-code contract.")
    ap.add_argument("pack", type=Path, help="Question pack JSON to verify.")
    ap.add_argument("--ds-model", default=DEFAULT_DS_MODEL,
                    help=f"opencode model for the DS pass (default "
                    f"{DEFAULT_DS_MODEL!r} — opencode's paid 'go' tier, NOT "
                    "the free tier; a bare id like 'deepseek-v4-flash-free' "
                    "namespaces to the free tier instead — see "
                    "docs/CRITIC_PROVIDERS.md).")
    ap.add_argument("--variant", default=DEFAULT_DS_VARIANT,
                    help="opencode reasoning-effort selector for the DS pass "
                    f"(default {DEFAULT_DS_VARIANT!r}).")
    ap.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL,
                    help="Model for the Claude certifying pass (default "
                    f"{DEFAULT_CLAUDE_MODEL!r} — DeepSeek does the bulk "
                    "review above, Opus certifies; pass 'sonnet' to "
                    "downgrade for a cheaper certifying pass).")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12), "
                    "forwarded to both passes.")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout in seconds (default "
                    "180), forwarded to both passes.")
    ap.add_argument("--jobs", type=int, default=factcheck_pack.DEFAULT_JOBS,
                    help="Concurrent Layer-C batches (default "
                    f"{factcheck_pack.DEFAULT_JOBS}), forwarded to both "
                    "passes.")
    ap.add_argument("--strict", action="store_true",
                    help="Gate both passes on EVERY live Layer-C finding, "
                    "not just blocking ones. Forwarded to both passes.")
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    rc, report = run_hybrid(
        args.pack, ds_model=args.ds_model, variant=args.variant,
        claude_model=args.claude_model, batch_size=args.batch_size,
        timeout=args.timeout, jobs=args.jobs, strict=args.strict,
        progress=progress)

    print(report)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
