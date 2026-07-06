#!/usr/bin/env python3
"""Pack-readiness gate — the single "this pack is done" command (Layer A + C).

Quizzler's QA pipeline has three layers. Two of them run as one hard gate here:

  • Layer A — scripts/lint_packs.py: deterministic structure linter (schema,
    answer-leak tells, distractor coverage, duplicate stems). Fast, free,
    reproducible — already enforced at authoring time by scripts/lint_hook.py
    and at build time by scripts/build_manifest.py.
  • Layer C — scripts/factcheck_pack.py: LLM factual critic (is the keyed answer
    actually TRUE?). Slow (~seconds/batch), costs money (~$0.10+/call), and
    PROBABILISTIC — so it is NOT in the per-edit hook or the per-launch build.

This script is the deliberate, ON-DEMAND readiness gate: it runs BOTH layers and
is the only thing that may declare a pack ready. A pack is "done" only when it
exits 0 here. Layer C is the reason this lives on demand rather than in the hook
or the build — an LLM pass is too slow/costly/non-deterministic to run on every
edit or every launch, but it must run once before a pack ships.

Both layers honor their pack-level waiver escape valves: Layer A reads
`lint_waivers`, Layer C reads `factcheck_waivers`. A reviewed false-positive is
dismissed by adding a waiver entry to the pack JSON, not by editing a real
question (see docs/VALIDATION_RULES.md).

Usage:
  python3 scripts/verify_pack.py question-packs/<course>/<pack>.json
  python3 scripts/verify_pack.py <pack> --no-factcheck    # structure-only (NOT the full gate)
  python3 scripts/verify_pack.py <pack> --model opus --batch-size 12
  python3 scripts/verify_pack.py <pack> --json            # machine-readable verdict

Readiness gate (why the bar is "errors", not "zero findings"):
  Layer C is a PROBABILISTIC LLM critic — it surfaces a different ~N findings each
  run, and its low/medium-confidence tail (nits, "ambiguous" hedges, off-axis
  distractor gripes) shifts question-to-question. Gating exit-0 on "zero live
  findings" therefore never converges: fix ten, the next run finds ten new ones
  elsewhere (this pipeline once re-ran a pack 7x doing exactly that). So the gate
  blocks only on BLOCKING findings — a `wrong-answer` (any confidence) or ANY
  high-confidence finding (see factcheck_pack.is_blocking) — and reports the rest
  as advisory. Two levers keep the loop terminating: `source_directive` (pack-level
  note that tells the critic to grade against the course text, killing the biggest
  false-positive class at the source) and `--only` (re-verify just the questions
  you changed, so confirmation runs shrink). `--strict` restores the old
  zero-any-finding bar for a final belt-and-suspenders pass.

Exit codes:
  0 — PACK READY: Layer A has zero live findings AND Layer C ran with zero BLOCKING
      findings (advisory findings may remain), zero batch errors, and FULL coverage
      of the questions sent (all of them, or all of `--only`).
  2 — PACK NOT READY: a live Layer-A finding or a BLOCKING Layer-C finding, OR
      Layer C coverage was incomplete (a batch errored/timed out, or the critic
      inspected fewer questions than were sent), OR the pack has no questions. A
      timed-out or partial-coverage run NEVER certifies ready.
  3 — NOT certified, but nothing blocking was found. Two cases:
      • --no-factcheck: Layer A clean, Layer C never ran; or
      • --only <subset>: the examined questions are clean, but the rest of the
        pack was NOT checked — a subset run never certifies the whole pack.
      Neither --no-factcheck nor --only ever returns 0. Run the full gate (no
      --only, no --no-factcheck) for the single 0 that means "pack ready".
  1 — operational error (pack unreadable, or `claude` CLI missing when a
      factcheck was requested).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# scripts/ isn't a package; import the two layer modules by path, the same trick
# build_manifest.py uses to reach lint_packs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_packs       # noqa: E402
import factcheck_pack   # noqa: E402


def run_layer_a(pack_path: Path) -> dict:
    """Layer A: lint_packs.lint_pack returns LIVE findings in `violations` plus the
    suppressed set in `waived`. Block on ANY real live finding — the SAME standard
    scripts/lint_hook.py enforces at authoring time (criticals AND warnings alike),
    so the readiness gate and the per-edit gate agree on what "clean" means.

    BUT lint_pack folds two kinds of non-blocking nudge into `violations`:
    WAIVER-rule hygiene warnings (a stale/malformed/unjustified `lint_waivers`
    entry) and the L23 absent-`coverage_blueprint` nudge (severity "advisory").
    Both are list-rot / coverage nudges, not content defects, so the gate treats
    them like Layer C treats ITS hygiene: surfaced as a non-blocking advisory, NOT
    a reason to fail an otherwise-clean pack. Partition them out here — rule ==
    "WAIVER" (the marker lint_packs._apply_waivers stamps on hygiene) OR severity
    == "advisory" (the non-blocking tier L23 uses for the absent-blueprint case) —
    so `live` carries only real blocking findings. This is why every pre-existing
    pack, none of which declares a coverage_blueprint, does NOT newly fail this
    gate on the absent-blueprint advisory."""
    result = lint_packs.lint_pack(pack_path)
    violations = result.get("violations", [])

    def _non_blocking(v: dict) -> bool:
        return v.get("rule") == "WAIVER" or v.get("severity") == "advisory"

    live = [v for v in violations if not _non_blocking(v)]
    hygiene = [v for v in violations if _non_blocking(v)]
    return {
        "live": live,
        "waived": result.get("waived", []),
        "hygiene": hygiene,
    }


def run_layer_c(pack_path: Path, model: str | None, batch_size: int,
                timeout: int, only: set[str] | None = None,
                strict: bool = False) -> dict:
    """Layer C: run the SHARED canonical batch loop
    (factcheck_pack.collect_findings) over the pack's questions, then apply the
    pack's `factcheck_waivers`. Returns the live/waived/hygiene partition PLUS the
    batch `errors` and `coverage_gaps` that the readiness verdict MUST consult — a
    timed-out batch or a critic that inspected fewer questions than were sent makes
    the pack NOT ready, never "clean". Raises RuntimeError if the `claude` CLI is
    unavailable, or if EVERY batch failed (a hard operational failure, distinct
    from partial incompleteness which is reported back as not-ready)."""
    if not shutil.which("claude"):
        raise RuntimeError("`claude` CLI not on PATH; cannot run the Layer-C critic")

    questions = factcheck_pack.load_questions(pack_path, only=only)
    # --strict re-grades against generic Security+: drop the pack's source_directive
    # so a paranoid pass can't be talked out of a finding by author-written text.
    source_directive = None if strict else factcheck_pack.load_source_directive(pack_path)
    result = factcheck_pack.collect_findings(
        questions, model, batch_size, timeout, source_directive=source_directive)
    all_findings = result["findings"]
    errors = result["errors"]

    n_batches = len(factcheck_pack.batched(questions, batch_size))
    if errors and not all_findings and len(errors) == n_batches:
        raise RuntimeError("every Layer-C batch failed; see: " + "; ".join(errors))

    live, waived, hygiene = factcheck_pack._apply_waivers(
        all_findings, factcheck_pack.load_waivers(pack_path))
    return {
        "live": live, "waived": waived, "hygiene": hygiene,
        "errors": errors, "coverage_gaps": result["coverage_gaps"],
        "questions_unchecked": result["questions_unchecked"],
        "model": result["model"], "total": result["questions_sent"],
        "source_directive_active": source_directive is not None,
    }


def format_report(pack_label: str, layer_a: dict, layer_c: dict | None,
                  outcome: str) -> str:
    """Combined human verdict: a Layer-A section, a Layer-C section (or a skip
    note), then the final verdict line. `outcome` is one of:
      • "ready"        — full gate passed (may carry advisory findings)
      • "subset_ok"    — a clean --only run: examined questions clear, but NOT
                         full-pack certification (the rest were never checked)
      • "structure_ok" — --no-factcheck, Layer A clean, Layer C never ran
      • "not_ready"    — a Layer-A live finding, a BLOCKING Layer-C finding, or
                         incomplete Layer-C coverage."""
    lines = [f"Pack-readiness gate for {pack_label}", ""]

    a_live = layer_a["live"]
    a_waived = layer_a["waived"]
    a_hygiene = layer_a.get("hygiene", [])
    a_parts = []
    if a_waived:
        a_parts.append(f"{len(a_waived)} waived")
    if a_hygiene:
        a_parts.append(f"{len(a_hygiene)} hygiene")
    a_note = f" ({', '.join(a_parts)})" if a_parts else ""
    if a_live:
        lines.append(f"Layer A (structure): {len(a_live)} live finding(s){a_note}")
        for v in a_live:
            qid = v.get("qid") or "(pack)"
            lines.append(f"  [{v.get('severity', '?'):8s}] {v.get('rule', '?')} @ {qid}: {v.get('detail', '')}")
    else:
        lines.append(f"Layer A (structure): clean{a_note}")
    # WAIVER-rule hygiene (stale/malformed lint_waivers) is a non-blocking
    # list-rot nudge — surfaced, but it does NOT gate readiness (FIX E).
    for h in a_hygiene:
        qid = h.get("qid") or "(pack)"
        lines.append(f"  [hygiene] {h.get('rule', '?')} @ {qid}: {h.get('detail', '')}")

    if layer_c is None:
        lines.append("")
        lines.append("NOTE: structure-only (Layer C skipped) — this is NOT the full readiness gate.")
    else:
        c_live = layer_c["live"]
        c_waived = layer_c["waived"]
        c_hygiene = layer_c["hygiene"]
        parts = []
        if c_waived:
            parts.append(f"{len(c_waived)} waived")
        if c_hygiene:
            parts.append(f"{len(c_hygiene)} hygiene")
        # Transparency (both reviews' ask): surface what may have SUPPRESSED
        # findings — the pack's source_directive and any waivers — so a reader
        # sees what the critic was told to accept, not just the residue.
        if layer_c.get("source_directive_active"):
            parts.append("source_directive active")
        suffix = f" ({', '.join(parts)})" if parts else ""
        if layer_c["errors"]:
            lines.append("")
            lines.append("Layer C batch errors (these questions were NOT checked):")
            lines.extend(f"  ! {e}" for e in layer_c["errors"])
        if layer_c.get("coverage_gaps"):
            lines.append("")
            lines.append("Layer C coverage gaps (critic inspected fewer questions than sent):")
            lines.extend(f"  ! {g}" for g in layer_c["coverage_gaps"])
        lines.append("")
        if c_live:
            block = layer_c.get("blocking")
            if block is None:
                block = factcheck_pack.blocking_findings(c_live)
            block_ids = {id(f) for f in block}
            n_block = len(block)
            lines.append(f"Layer C (factual): {len(c_live)} live finding(s) — "
                         f"{n_block} BLOCKING, {len(c_live) - n_block} advisory{suffix}")
            for f in c_live:
                tag = "BLOCKING" if id(f) in block_ids else "advisory"
                lines.append(f"  [{tag}] [{f.get('severity', '?'):22s}] {f.get('qid', '?')} (confidence: {f.get('confidence', '?')})")
                lines.append(f"      issue:      {f.get('issue', '')}")
                if f.get("correction"):
                    lines.append(f"      correction: {f['correction']}")
        else:
            lines.append(f"Layer C (factual): clean{suffix}")
        for f in c_waived:
            reason = f.get("waived_reason") or "(no reason given)"
            lines.append(f"  [waived] {f.get('qid', '?')}: {f.get('issue', '')} — {reason}")
        for h in c_hygiene:
            qid = h.get("qid") or "(pack)"
            lines.append(f"  [hygiene] {qid}: {h.get('issue', '')}")

    lines.append("")
    if outcome == "structure_ok":
        # --no-factcheck, Layer A clean: never print the unqualified "PACK READY"
        # — Layer C never ran, so the pack is NOT certified.
        lines.append("STRUCTURE OK — Layer C not run; pack NOT certified ready "
                     "(re-run without --no-factcheck for the full gate).")
    elif outcome == "ready":
        # Ready may coexist with advisory Layer-C findings — say so, so "READY"
        # isn't misread as "the critic found nothing."
        c_adv = len(layer_c["live"]) if layer_c else 0
        if c_adv:
            lines.append(f"PACK READY (with {c_adv} advisory Layer-C finding(s) — "
                         "non-blocking; skim, don't chase)")
        else:
            lines.append("PACK READY")
    elif outcome == "subset_ok":
        # A clean --only run: the examined questions are clear, but the rest were
        # never checked, so this is explicitly NOT full-pack certification.
        n = layer_c.get("total", 0)
        c_adv = len(layer_c["live"])
        adv_note = f", {c_adv} advisory" if c_adv else ""
        lines.append(f"SUBSET RECHECK PASSED — {n} checked question(s) clean{adv_note}; "
                     "pack NOT certified (run the full gate without --only before shipping).")
    else:  # not_ready
        if layer_c is None:
            lines.append(f"PACK NOT READY: {len(a_live)} Layer-A finding(s).")
        else:
            c_live = layer_c["live"]
            c_block = layer_c.get("blocking")
            if c_block is None:
                c_block = factcheck_pack.blocking_findings(c_live)
            # An incomplete-coverage run (a batch errored/timed out, or the critic
            # inspected fewer questions than sent) with NO blocking findings is the
            # dangerous case: nothing blocking was found ONLY because not everything
            # was checked. Call it out explicitly rather than implying the pack is fine.
            incomplete = bool(layer_c.get("errors") or layer_c.get("coverage_gaps"))
            if not a_live and not c_block and incomplete:
                unchecked = layer_c.get("questions_unchecked", 0)
                lines.append("PACK NOT READY: Layer C coverage incomplete "
                             f"({unchecked} question(s) unchecked)")
            else:
                adv = len(c_live) - len(c_block)
                adv_note = f" (+{adv} advisory)" if adv else ""
                lines.append(f"PACK NOT READY: {len(a_live)} Layer-A + "
                             f"{len(c_block)} blocking Layer-C finding(s){adv_note}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Pack-readiness gate: runs Layer A (structure) + Layer C "
        "(factual) as one hard gate. Exit 0 only when BOTH are clean. This is "
        "THE 'pack is done' command — the FULL gate REQUIRES Layer C, so "
        "--no-factcheck is structure-only and does NOT certify readiness.")
    ap.add_argument("pack", type=Path, help="Question pack JSON to verify.")
    ap.add_argument("--no-factcheck", action="store_true",
                    help="Skip Layer C (structure-only). NOT the full readiness "
                    "gate — the full gate requires the Layer-C factual critic. "
                    "Exits 3 (NOT 0) when structure is clean, so a CI "
                    "`verify_pack --no-factcheck && deploy` can never ship an "
                    "unfactchecked pack.")
    ap.add_argument("--model", default="claude-sonnet-5",
                    help="Model for the Layer-C critic (default: claude-sonnet-5; "
                    "pass --model opus to escalate, or an alias like 'sonnet'/'opus').")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12).")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout (s).")
    ap.add_argument("--only", default=None,
                    help="Comma-separated question ids to re-verify (default: all). "
                    "Powers shrinking confirmation runs: after the initial full "
                    "audit, re-check ONLY the questions you changed. A clean subset "
                    "run exits 3 (SUBSET RECHECK PASSED) — it clears those questions "
                    "but NEVER certifies the whole pack; run the full gate (no --only) "
                    "before shipping. Cross-question checks only see the sent subset.")
    ap.add_argument("--strict", action="store_true",
                    help="Gate on EVERY live Layer-C finding, not just errors. Default "
                    "readiness = 0 Layer-A live + 0 BLOCKING Layer-C findings "
                    "(wrong-answer or high-confidence) + full coverage; the "
                    "probabilistic nit/ambiguous tail is advisory. --strict restores "
                    "the old zero-any-finding bar for a final belt-and-suspenders pass.")
    ap.add_argument("--json", action="store_true",
                    help="Emit the combined verdict as JSON.")
    args = ap.parse_args(argv)
    only = ({q.strip() for q in args.only.split(",") if q.strip()}
            if args.only else None)

    if not args.pack.is_file():
        print(f"error: pack not found: {args.pack}", file=sys.stderr)
        return 1

    # Empty-pack guard (applies to BOTH paths, including --no-factcheck where
    # Layer C never loads questions): a pack with zero/missing `questions` can
    # never be certified — there is nothing for the critic to check, so the gate
    # must not pass it. An empty pack is NOT READY (exit 2); an unreadable/
    # malformed pack is an operational error (exit 1), matching
    # factcheck_pack.main's contract instead of a bare traceback.
    try:
        questions = factcheck_pack.load_questions(args.pack, only=only)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read pack: {e}", file=sys.stderr)
        return 1
    if not questions:
        print("error: " + ("none of the --only ids matched a question" if only
                           else "pack has no questions"), file=sys.stderr)
        return 2

    # Render a repo-relative label when possible; fall back to the raw path.
    try:
        pack_label = str(args.pack.resolve().relative_to(
            Path(__file__).resolve().parent.parent))
    except ValueError:
        pack_label = str(args.pack)

    # ── Layer A ────────────────────────────────────────────────────────────────
    try:
        layer_a = run_layer_a(args.pack)
    except Exception as e:  # noqa: BLE001 — surface any lint failure as op-error
        print(f"error: Layer-A lint failed: {e}", file=sys.stderr)
        return 1

    # ── Layer C (unless skipped) ───────────────────────────────────────────────
    layer_c: dict | None = None
    if not args.no_factcheck:
        try:
            layer_c = run_layer_c(args.pack, args.model, args.batch_size,
                                  args.timeout, only=only, strict=args.strict)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    a_clean = not layer_a["live"]
    if layer_c is None:
        # Structure-only (--no-factcheck): NEVER certify ready, NEVER exit 0.
        #   structure_ok / 3 — Layer A clean but Layer C not run (NOT certified)
        #   not_ready   / 2 — Layer A has live findings
        outcome = "structure_ok" if a_clean else "not_ready"
        exit_code = 3 if a_clean else 2
    else:
        # Full gate: ready ONLY when Layer A is clean AND Layer C has no BLOCKING
        # findings (wrong-answer or high-confidence errors; the probabilistic
        # nit/ambiguous tail is advisory unless --strict) AND full coverage. A
        # timed-out or partial-coverage Layer C run is NOT ready (coverage_ok
        # consults both). Blocking is computed post-waiver, so a reviewed
        # high-confidence false-positive suppressed by a waiver does not block.
        blocking = factcheck_pack.blocking_findings(layer_c["live"], strict=args.strict)
        layer_c["blocking"] = blocking       # surface for the report + JSON verdict
        layer_c["partial"] = bool(only)
        clean = a_clean and not blocking and factcheck_pack.coverage_ok(layer_c)
        if not clean:
            outcome, exit_code = "not_ready", 2
        elif only:
            # A --only subset can CLEAR the questions it examined but must NEVER
            # certify the WHOLE pack — else `verify_pack --only q1 && deploy` ships
            # the unexamined rest (the exact hole --no-factcheck->3 was built to
            # close). Clean subset -> exit 3: "these questions are clean, pack NOT
            # certified"; a blocking/incomplete subset still -> 2.
            outcome, exit_code = "subset_ok", 3
        else:
            outcome, exit_code = "ready", 0

    if args.json:
        out = {
            "pack": pack_label,
            "ready": exit_code == 0,
            "outcome": outcome,
            "exit_code": exit_code,
            "partial": bool(only),
            "layer_a": layer_a,
            "layer_c": layer_c,  # None when --no-factcheck
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(format_report(pack_label, layer_a, layer_c, outcome))

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
