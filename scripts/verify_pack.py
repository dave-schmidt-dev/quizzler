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

Exit codes:
  0 — PACK READY: Layer A has zero live findings AND (Layer C skipped OR Layer C
      has zero live findings).
  2 — PACK NOT READY: Layer A and/or Layer C reported live findings.
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
    """Layer A: lint_packs.lint_pack already returns LIVE (unwaived) findings in
    `violations` plus the suppressed set in `waived`. Block on ANY live finding —
    the SAME standard scripts/lint_hook.py enforces at authoring time (the hook
    exits 2 whenever `violations` is non-empty, criticals AND warnings alike), so
    the readiness gate and the per-edit gate agree on what "clean" means."""
    result = lint_packs.lint_pack(pack_path)
    return {
        "live": result.get("violations", []),
        "waived": result.get("waived", []),
    }


def run_layer_c(pack_path: Path, model: str | None, batch_size: int,
                timeout: int) -> dict:
    """Layer C: collect critic findings across batches exactly like
    factcheck_pack.main, then apply the pack's `factcheck_waivers`. Block on any
    live finding. Raises RuntimeError if the `claude` CLI is unavailable."""
    if not shutil.which("claude"):
        raise RuntimeError("`claude` CLI not on PATH; cannot run the Layer-C critic")

    questions = factcheck_pack.load_questions(pack_path)
    batches = factcheck_pack.batched(questions, batch_size)

    all_findings: list[dict] = []
    errors: list[str] = []
    model_used: str | None = None
    for i, b in enumerate(batches):
        try:
            stdout = factcheck_pack.run_claude(
                factcheck_pack.build_prompt(b), model, timeout)
            if model_used is None:
                model_used = factcheck_pack.extract_model(stdout)
            parsed = factcheck_pack.extract_findings(
                factcheck_pack.parse_envelope(stdout))
            all_findings.extend(parsed["findings"])
        except (RuntimeError, ValueError) as e:
            qids = ", ".join(q.get("id", "?") for q in b)
            errors.append(f"batch {i + 1}/{len(batches)} [{qids}]: {e}")

    if errors and not all_findings and len(errors) == len(batches):
        raise RuntimeError("every Layer-C batch failed; see: " + "; ".join(errors))

    live, waived, hygiene = factcheck_pack._apply_waivers(
        all_findings, factcheck_pack.load_waivers(pack_path))
    return {
        "live": live, "waived": waived, "hygiene": hygiene,
        "errors": errors, "model": model_used, "total": len(questions),
    }


def format_report(pack_label: str, layer_a: dict, layer_c: dict | None,
                  ready: bool) -> str:
    """Combined human verdict: a Layer-A section, a Layer-C section (or a skip
    note), then the final PACK READY / PACK NOT READY line."""
    lines = [f"Pack-readiness gate for {pack_label}", ""]

    a_live = layer_a["live"]
    a_waived = layer_a["waived"]
    waived_note = f" ({len(a_waived)} waived)" if a_waived else ""
    if a_live:
        lines.append(f"Layer A (structure): {len(a_live)} live finding(s){waived_note}")
        for v in a_live:
            qid = v.get("qid") or "(pack)"
            lines.append(f"  [{v.get('severity', '?'):8s}] {v.get('rule', '?')} @ {qid}: {v.get('detail', '')}")
    else:
        lines.append(f"Layer A (structure): clean{waived_note}")

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
        suffix = f" ({', '.join(parts)})" if parts else ""
        if layer_c["errors"]:
            lines.append("")
            lines.append("Layer C batch errors (these questions were NOT checked):")
            lines.extend(f"  ! {e}" for e in layer_c["errors"])
        lines.append("")
        if c_live:
            lines.append(f"Layer C (factual): {len(c_live)} live finding(s){suffix}")
            for f in c_live:
                lines.append(f"  [{f.get('severity', '?'):22s}] {f.get('qid', '?')} (confidence: {f.get('confidence', '?')})")
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
    if layer_c is None:
        # Structure-only run: never print the unqualified "PACK READY" — Layer C
        # never ran, so the pack is NOT certified. Saying "ready" here would be a
        # false pass, defeating the point of a hard gate.
        if ready:
            lines.append("STRUCTURE OK — Layer C not run; pack NOT certified ready "
                         "(re-run without --no-factcheck for the full gate).")
        else:
            lines.append(f"PACK NOT READY: {len(a_live)} Layer-A finding(s).")
    elif ready:
        lines.append("PACK READY")
    else:
        lines.append(f"PACK NOT READY: {len(a_live)} Layer-A + "
                     f"{len(layer_c['live'])} Layer-C finding(s)")
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
                    "gate — the full gate requires the Layer-C factual critic.")
    ap.add_argument("--model", default="claude-sonnet-5",
                    help="Model for the Layer-C critic (default: claude-sonnet-5; "
                    "pass --model opus to escalate, or an alias like 'sonnet'/'opus').")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12).")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout (s).")
    ap.add_argument("--json", action="store_true",
                    help="Emit the combined verdict as JSON.")
    args = ap.parse_args(argv)

    if not args.pack.is_file():
        print(f"error: pack not found: {args.pack}", file=sys.stderr)
        return 1

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
            layer_c = run_layer_c(args.pack, args.model, args.batch_size, args.timeout)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    a_clean = not layer_a["live"]
    c_clean = layer_c is None or not layer_c["live"]
    ready = a_clean and c_clean

    if args.json:
        out = {
            "pack": pack_label,
            "ready": ready,
            "layer_a": layer_a,
            "layer_c": layer_c,  # None when --no-factcheck
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(format_report(pack_label, layer_a, layer_c, ready))

    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
