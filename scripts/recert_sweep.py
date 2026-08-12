#!/usr/bin/env python3
"""Retired live re-certification sweep for question packs.

A full-course re-certification (every pack in question-packs/<course>/ run
through the Layer-A + Layer-C readiness gate) can take hours at the safe
``--jobs 1`` setting. This script is the batch runner for that job: it walks a
pack list or a whole course directory and runs the supported hybrid route for
each pack in turn, not one pack at a time by hand.

Run this out of any interactive verifier session, as a plain background shell
process (e.g. ``nohup python3 scripts/recert_sweep.py ... &`` or a tmux pane).
The old sweep used the same DeepSeek bulk pass and configured high-capability
verifier as ``hybrid_verify.py``. That route is retired: a live reviewer pass
is campaign evidence, never a certification writer. Complete frozen campaign
ledgers and invoke ``hybrid_verify.py --certify-campaign`` deterministically
per pack instead.

CV-2 — in-process, not a subprocess: this module imports the hybrid
orchestrator and called its ``run_hybrid()`` directly for each pack. That was
a second live-stamping route and is intentionally fail-closed now.

CV-3 — idempotent resume: before certifying a pack, this script checks
``pack_cert.certification_fresh`` and SKIPS it if already fresh (reported as
SKIPPED, not re-run). A re-invocation after a partial sweep — quota
exhaustion, a killed process, a bad pack in the middle — only re-spends quota
on packs that are not yet certified (or whose content hash moved), never on
ones that already passed.

Cross-pack concurrency is intentionally NOT offered: the hybrid orchestrator's
report capture uses a GLOBAL swap of ``sys.stdout``/``sys.stderr``, not a
thread-local one. Running packs concurrently would let one captured report
bleed into another (or into the terminal), so packs run sequentially; ``--jobs``
already gives per-pack Layer-C concurrency.

Usage:
  python3 scripts/recert_sweep.py question-packs/sy0-701
  python3 scripts/recert_sweep.py question-packs/sy0-701/ch01-obj1.1-security-controls.json ...
  python3 scripts/recert_sweep.py question-packs/sy0-701 --dry-run
  python3 scripts/recert_sweep.py question-packs/sy0-701 --jobs 6 --verifier-profile codex-terra-high

Exit code: 1. This command does not run reviewers or write certification.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# scripts/ isn't a package; import verify_pack by path, the same trick
# verify_pack.py itself uses to reach lint_packs/factcheck_pack/pack_cert.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hybrid_verify
import pack_cert
import verify_pack

# NOT a fresh import — this is the exact factcheck_pack module object
# verify_pack imported and calls internally, so a test's
# patch.object(factcheck_pack, "run_claude") reaches verify_pack.run_layer_c
# (CV-2). Re-importing "factcheck_pack" here directly would resolve to the
# same sys.modules-cached object anyway, but going through verify_pack's own
# attribute makes that identity obvious rather than incidental.
factcheck_pack = verify_pack.factcheck_pack

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
    mirrors the internal gate primitive's own pack-label rendering."""
    try:
        return str(pack_path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(pack_path)


def is_fresh(pack_path: Path) -> bool:
    """CV-3: True if `pack_path` already carries a current, matching
    certification block (pack_cert.certification_fresh) — this pack should be
    SKIPPED, not re-certified. False on any read/parse failure too (an
    unreadable pack is not "fresh"; it will surface its own error when
    certify_one actually tries to run the hybrid route against it)."""
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return pack_cert.certification_fresh(data)


def certify_one(pack_path: Path, *, verifier_profile: str, batch_size: int, timeout: int,
                jobs: int, strict: bool,
                variant: str | None = None) -> tuple[int, str]:
    """Fail closed: batch live-review certification is retired.

    Kept as a callable compatibility boundary so in-process callers cannot
    accidentally regain the old stamping behavior. No reviewer is invoked.
    """
    return 1, (
        "recert_sweep live certification is retired; run frozen campaign "
        "discovery and then hybrid_verify.py --certify-campaign <ledger>"
    )


_OUTCOME_LABELS = {
    "certified": "CERTIFIED", "not_ready": "NOT READY", "error": "ERROR",
    "skipped": "SKIPPED", "planned": "PLANNED",
}

# Hybrid exit codes: 0 READY, 2 NOT READY, 1 operational error. Anything else
# is labeled "unknown" defensively so a future contract change is visible.
_EXIT_CODE_OUTCOMES = {0: "certified", 2: "not_ready", 1: "error"}


def _append_log(log_path: Path, text: str) -> None:
    """Append `text` to `log_path`, creating parent directories as needed."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text)


def run_sweep(pack_paths: list[Path], *, verifier_profile: str, batch_size: int, timeout: int,
             jobs: int, strict: bool, dry_run: bool, log_path: Path,
             force: bool = False,
             variant: str | None = None,
             progress=lambda msg: None) -> list[dict]:
    """Certify every pack in `pack_paths` sequentially, skipping fresh ones
    (CV-3) and dry-running when `dry_run` (no critic call, no quota spent).

    `force` re-grades even already-fresh packs.

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

        if is_fresh(pack_path) and not force:
            progress(f"[{i}/{total}] SKIP   {label} (already certified, fresh)")
            results.append({"pack": label, "outcome": "skipped", "exit_code": None})
            continue

        if dry_run:
            progress(f"[{i}/{total}] PLAN   {label} (would certify)")
            results.append({"pack": label, "outcome": "planned", "exit_code": None})
            continue

        progress(f"[{i}/{total}] START  {label}")
        rc, report = certify_one(pack_path, verifier_profile=verifier_profile,
                                 batch_size=batch_size,
                                 timeout=timeout, jobs=jobs, strict=strict,
                                 variant=variant)
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
        "hybrid readiness gate (Layer A + Layer C) over a pack list or "
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
                    "to the hybrid passes. Packs themselves always run "
                    "sequentially, one at a time.")
    ap.add_argument("--verifier-profile", default=hybrid_verify.DEFAULT_VERIFIER_PROFILE,
                    choices=tuple(hybrid_verify.verifier_profiles.PROFILES),
                    help="Registered high-capability verifier route (default: "
                    f"{hybrid_verify.DEFAULT_VERIFIER_PROFILE}).")
    # Keep the retired spelling parseable so operators get actionable guidance
    # instead of an opaque argparse error.
    ap.add_argument("--panel", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12).")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout in seconds (default 180).")
    ap.add_argument("--strict", action="store_true",
                    help="Pass --strict through to both hybrid passes: gate on EVERY "
                    "live Layer-C finding, not just errors.")
    ap.add_argument("--variant", default=None,
                    help="opencode reasoning-effort selector (e.g. 'max'), passed "
                    "straight through to the DeepSeek hybrid pass.")
    ap.add_argument("--force", action="store_true",
                    help="Re-grade every pack, including ones whose certification "
                    "is already fresh. Freshness is a CONTENT check, so `--force` "
                    "re-runs the canonical hybrid review despite a fresh stamp. "
                    "Costs a full sweep's quota; idempotent resume is the default.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List which packs WOULD be certified and which WOULD "
                    "be skipped (already fresh) — spends no quota; never "
                    "calls the Layer-C critic.")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE,
                    help=f"Append each certified pack's full hybrid "
                    f"report here (default {DEFAULT_LOG_FILE}).")
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.panel:
        print("error: --panel certification is retired; run "
              "python3 scripts/recert_sweep.py without --panel. The sweep "
              "uses hybrid_verify: DeepSeek Flash Go (max), then the configured "
              "high-capability verifier.",
              file=sys.stderr)
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
        pack_paths, verifier_profile=args.verifier_profile, batch_size=args.batch_size,
        timeout=args.timeout, jobs=args.jobs, strict=args.strict,
        dry_run=args.dry_run, log_path=args.log_file,
        force=args.force, variant=args.variant, progress=progress)

    print(format_summary(results))
    if not args.dry_run:
        progress(f"full per-pack reports appended to {args.log_file}")

    failed = any(r["outcome"] in ("not_ready", "error", "unknown") for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
