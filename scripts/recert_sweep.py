#!/usr/bin/env python3
"""Out-of-session batch re-certification sweep for question packs.

A full-course re-certification (every pack in question-packs/<course>/ run
through the Layer-A + Layer-C readiness gate) can take hours at the safe
``--jobs 1`` setting. This script is the batch runner for that job: it walks a
pack list or a whole course directory and certifies each pack in turn via
scripts/verify_pack.py — IN-PROCESS (see CV-2 below), not one pack at a time
by hand.

Run this OUTSIDE an interactive Claude Code session, as a plain background
shell process (e.g. ``nohup python3 scripts/recert_sweep.py ... &`` or a tmux
pane). A nested ``claude -p`` Layer-C critic invoked FROM INSIDE a Claude Code
session is forced down to ``--jobs 1`` (concurrency times out in that nested
context) and a long multi-pack sweep can exhaust session quota at the tail,
producing false ``claude exited 1`` / non-JSON-reply failures on the packs
still queued — see question-packs/sy0-701/BUILD_NOTES.md, "Infra note
(nested-Claude flakiness, not content)". Run this out-of-session and pass
``--jobs 6`` (the default) for real per-pack concurrency without that failure
mode.

CV-2 — in-process, not a subprocess: this module imports scripts/verify_pack.py
and calls its ``main()`` directly for each pack. It deliberately does NOT shell
out to ``python3 scripts/verify_pack.py`` — a subprocess boundary would defeat
a test's ``patch.object(factcheck_pack, "run_claude")`` mock, since the mock
only patches the CURRENT process's module object. Calling ``verify_pack.main``
in-process is also what lets this script reuse verify_pack's exact readiness
decision, exit codes, and certification stamping without re-deriving any of it.

CV-3 — idempotent resume: before certifying a pack, this script checks
``pack_cert.certification_fresh`` and SKIPS it if already fresh (reported as
SKIPPED, not re-run). A re-invocation after a partial sweep — quota
exhaustion, a killed process, a bad pack in the middle — only re-spends quota
on packs that are not yet certified (or whose content hash moved), never on
ones that already passed.

Cross-pack concurrency is intentionally NOT offered: verify_pack.main() writes
its report via plain ``print()`` to the process's real stdout/stderr, and this
script captures that per-pack via ``contextlib.redirect_stdout/stderr`` — a
GLOBAL swap of ``sys.stdout``/``sys.stderr``, not a thread-local one. Running
that concurrently across packs would let one pack's captured report bleed
into another's (or into the terminal). Doing it safely would mean either
reworking verify_pack to return its report instead of printing it, or
per-thread file-descriptor redirection — neither is "trivial", so packs run
strictly sequentially; --jobs already gives per-pack Layer-C concurrency.

Usage:
  python3 scripts/recert_sweep.py question-packs/sy0-701
  python3 scripts/recert_sweep.py question-packs/sy0-701/ch01-obj1.1-security-controls.json ...
  python3 scripts/recert_sweep.py question-packs/sy0-701 --dry-run
  python3 scripts/recert_sweep.py question-packs/sy0-701 --jobs 6 --model opus

Exit code: 0 if every pack certified READY or was already fresh (skipped) or
this was a --dry-run; 1 if any pack came back NOT READY or hit an operational
error (mirroring verify_pack's own "not everything is fine" signal).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

# scripts/ isn't a package; import verify_pack by path, the same trick
# verify_pack.py itself uses to reach lint_packs/factcheck_pack/pack_cert.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_pack   # noqa: E402
import pack_cert     # noqa: E402

# NOT a fresh import — this is the exact factcheck_pack module object
# verify_pack imported and calls internally, so a test's
# patch.object(factcheck_pack, "run_claude") reaches verify_pack.run_layer_c
# (CV-2). Re-importing "factcheck_pack" here directly would resolve to the
# same sys.modules-cached object anyway, but going through verify_pack's own
# attribute makes that identity obvious rather than incidental.
factcheck_pack = verify_pack.factcheck_pack
critic_panel = verify_pack.critic_panel   # same rationale: one module object

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Mirrors build_manifest.py's LINT_LOG convention: a well-known /tmp path so a
# real sweep's output lands somewhere durable without needing a repo-local
# .gitignore entry (this script only touches scripts/recert_sweep.py +
# tests/test_recert_sweep.py; it does not add its own gitignore rule).
DEFAULT_LOG_FILE = Path("/tmp/quizzler-recert-sweep.log")

# Files inside a course directory that are never packs — mirrors
# scripts/lint_hook.py's NON_PACK_NAMES, plus the top-level template (present
# only if someone points this at question-packs/ itself rather than one course
# subfolder).
NON_PACK_NAMES = {"_course.json", "manifest.json", "manifest.example.json",
                  "pack-template.json"}


def discover_packs(paths: list[Path]) -> list[Path]:
    """Expand `paths` (pack JSON files and/or course directories) into a
    deduped, order-preserving list of pack paths to sweep.

    A directory contributes every ``*.json`` file directly inside it except
    the known non-pack files (sorted, so a course directory sweeps in a
    stable, reproducible order). A file argument is passed through as-is —
    existence is NOT checked here; a missing/unreadable pack surfaces through
    the normal certify_one -> verify_pack.main error path instead of a second,
    duplicate check here."""
    out: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        key = p.resolve() if p.exists() else p
        if key not in seen:
            seen.add(key)
            out.append(p)

    for p in paths:
        if p.is_dir():
            for child in sorted(p.glob("*.json")):
                if child.name not in NON_PACK_NAMES:
                    _add(child)
        else:
            _add(p)
    return out


def pack_label(pack_path: Path) -> str:
    """Repo-relative label for a pack path when possible, else the raw path —
    mirrors verify_pack.main's own pack_label rendering."""
    try:
        return str(pack_path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(pack_path)


def is_fresh(pack_path: Path) -> bool:
    """CV-3: True if `pack_path` already carries a current, matching
    certification block (pack_cert.certification_fresh) — this pack should be
    SKIPPED, not re-certified. False on any read/parse failure too (an
    unreadable pack is not "fresh"; it will surface its own error when
    certify_one actually tries to run verify_pack against it)."""
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return pack_cert.certification_fresh(data)


def certify_one(pack_path: Path, *, model: str, batch_size: int, timeout: int,
                jobs: int, strict: bool, panel: str | None = None) -> tuple[int, str]:
    """Run the full verify_pack readiness gate for ONE pack, IN-PROCESS (CV-2).

    Always the FULL gate — no --only, no --no-factcheck — so a 0 here means
    the same thing verify_pack's own 0 means: PACK READY, certification
    stamped. Returns (exit_code, combined stdout+stderr report text) for the
    caller to log; verify_pack.main's own prints are captured rather than left
    to bleed onto this process's real stdout, so the sweep's live per-pack
    progress lines (on the real stderr) stay readable.

    `panel` forwards a ``--panel`` spec verbatim. A sweep is the bulk path for a
    multi-pack course, which is precisely where a single-critic false negative
    did the most damage — so the panel has to be reachable here, not just on the
    one-pack command. When it is set, ``--model`` is deliberately NOT forwarded:
    each pass carries its own model in the spec, and a stray global model id
    (default ``claude-sonnet-5``) would be nonsense to hand a DeepSeek pass."""
    argv = [str(pack_path), "--batch-size", str(batch_size),
            "--timeout", str(timeout), "--jobs", str(jobs)]
    if panel:
        argv += ["--panel", panel]
    else:
        argv += ["--model", model]
    if strict:
        argv.append("--strict")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = verify_pack.main(argv)
    report = out.getvalue()
    if err.getvalue():
        report += ("\n" if report and not report.endswith("\n") else "") + err.getvalue()
    return rc, report


_OUTCOME_LABELS = {
    "certified": "CERTIFIED", "not_ready": "NOT READY", "error": "ERROR",
    "skipped": "SKIPPED", "planned": "PLANNED",
}

# verify_pack's full-gate exit codes: 0 READY, 2 NOT READY, 1 operational error.
# (3 is the --only/--no-factcheck partial-certification code; recert_sweep
# never passes either flag, so it should never see a 3 — mapped to "unknown"
# defensively rather than asserted, so a future verify_pack change surfaces
# here as a labeled oddity instead of a crash.)
_EXIT_CODE_OUTCOMES = {0: "certified", 2: "not_ready", 1: "error"}


def _append_log(log_path: Path, text: str) -> None:
    """Append `text` to `log_path`, creating parent directories as needed."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text)


def run_sweep(pack_paths: list[Path], *, model: str, batch_size: int, timeout: int,
             jobs: int, strict: bool, dry_run: bool, log_path: Path,
             panel: str | None = None,
             progress=lambda msg: None) -> list[dict]:
    """Certify every pack in `pack_paths` sequentially, skipping fresh ones
    (CV-3) and dry-running when `dry_run` (no critic call, no quota spent).

    `progress(msg)` fires once per pack start/finish (and once for a skip or a
    dry-run plan) — INV-1: a full-course sweep can run for hours, so every
    pack boundary must emit a visible line rather than leave the caller
    waiting on a silent multi-hour block.

    Returns a list of ``{"pack", "outcome", "exit_code"}`` dicts in the same
    order as `pack_paths`; `exit_code` is None for "skipped"/"planned"
    entries (no gate ran)."""
    results: list[dict] = []
    total = len(pack_paths)
    for i, pack_path in enumerate(pack_paths, start=1):
        label = pack_label(pack_path)

        if is_fresh(pack_path):
            progress(f"[{i}/{total}] SKIP   {label} (already certified, fresh)")
            results.append({"pack": label, "outcome": "skipped", "exit_code": None})
            continue

        if dry_run:
            progress(f"[{i}/{total}] PLAN   {label} (would certify)")
            results.append({"pack": label, "outcome": "planned", "exit_code": None})
            continue

        progress(f"[{i}/{total}] START  {label}")
        rc, report = certify_one(pack_path, model=model, batch_size=batch_size,
                                 timeout=timeout, jobs=jobs, strict=strict,
                                 panel=panel)
        outcome = _EXIT_CODE_OUTCOMES.get(rc, "unknown")
        stamp = datetime.now(timezone.utc).isoformat()
        _append_log(log_path,
                    f"\n=== {label} — {stamp} (exit {rc}) ===\n{report}\n")
        progress(f"[{i}/{total}] DONE   {label}: "
                 f"{_OUTCOME_LABELS.get(outcome, outcome.upper())} (exit {rc})")
        results.append({"pack": label, "outcome": outcome, "exit_code": rc})
    return results


def format_summary(results: list[dict]) -> str:
    """Render the final per-pack pass/fail summary plus a tally line."""
    lines = [f"Re-cert sweep summary ({len(results)} pack(s)):"]
    tally: dict[str, int] = {}
    for r in results:
        tally[r["outcome"]] = tally.get(r["outcome"], 0) + 1
        tag = _OUTCOME_LABELS.get(r["outcome"], r["outcome"].upper())
        suffix = f" (exit {r['exit_code']})" if r["exit_code"] is not None else ""
        lines.append(f"  [{tag:9s}] {r['pack']}{suffix}")
    lines.append("")
    lines.append(", ".join(f"{v} {k}" for k, v in sorted(tally.items())) or "nothing to report")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Out-of-session batch re-certification sweep: runs the "
        "verify_pack readiness gate (Layer A + Layer C) over a pack list or "
        "whole course directories, one pack at a time, IN-PROCESS. Skips any "
        "pack that is already certified and fresh (idempotent resume). Run "
        "this OUTSIDE an interactive Claude Code session — see the module "
        "docstring / question-packs/sy0-701/BUILD_NOTES.md 'Infra note' for "
        "why a nested `claude -p` critic misbehaves inside one.")
    ap.add_argument("paths", nargs="+", type=Path,
                    help="Pack JSON file(s) and/or course directories (e.g. "
                    "question-packs/sy0-701) to sweep.")
    ap.add_argument("--jobs", type=int, default=factcheck_pack.DEFAULT_JOBS,
                    help="Concurrent Layer-C batches PER PACK (default "
                    f"{factcheck_pack.DEFAULT_JOBS}), passed straight through "
                    "to verify_pack --jobs. Packs themselves always run "
                    "sequentially, one at a time.")
    ap.add_argument("--model", default="claude-sonnet-5",
                    help="Model for the Layer-C critic (default: "
                    "claude-sonnet-5; pass --model opus to escalate).")
    ap.add_argument("--panel", default=None,
                    help="Certify every pack with a multi-provider critic panel "
                    "instead of one critic, e.g. "
                    "'deepseek,ollama=qwen3:8b,claude' (>=2 distinct passes). "
                    "A sweep is the BULK path for a whole course, which is "
                    "exactly where a single-critic false negative does the most "
                    "damage. Overrides --model (each pass carries its own). "
                    "See docs/CRITIC_PROVIDERS.md.")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12).")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout in seconds (default 180).")
    ap.add_argument("--strict", action="store_true",
                    help="Pass --strict through to verify_pack: gate on EVERY "
                    "live Layer-C finding, not just errors.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List which packs WOULD be certified and which WOULD "
                    "be skipped (already fresh) — spends no quota; never "
                    "calls the Layer-C critic.")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE,
                    help=f"Append each certified pack's full verify_pack "
                    f"report here (default {DEFAULT_LOG_FILE}).")
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)

    # Validate the panel spec ONCE, up front. A sweep can run for hours; a typo
    # caught on pack 1 of 12 after the first pack's quota is spent is a worse
    # failure than the same typo caught before anything ran.
    if args.panel:
        try:
            critic_panel.parse_panel(args.panel)
        except ValueError as e:
            print(f"error: --panel: {e}", file=sys.stderr)
            return 1

    pack_paths = discover_packs(args.paths)
    if not pack_paths:
        print("error: no packs found at the given path(s)", file=sys.stderr)
        return 1

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    progress(f"recert_sweep: {len(pack_paths)} pack(s) discovered"
             + (" — DRY RUN, no quota will be spent" if args.dry_run else ""))

    results = run_sweep(
        pack_paths, model=args.model, batch_size=args.batch_size,
        timeout=args.timeout, jobs=args.jobs, strict=args.strict,
        dry_run=args.dry_run, log_path=args.log_file, panel=args.panel,
        progress=progress)

    print(format_summary(results))
    if not args.dry_run:
        progress(f"full per-pack reports appended to {args.log_file}")

    failed = any(r["outcome"] in ("not_ready", "error", "unknown") for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
