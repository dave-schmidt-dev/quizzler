#!/usr/bin/env python3
"""Pack-readiness gate — the single "this pack is done" command (Layer A + C).

Quizzler's QA pipeline has two automated layers (Layer A + Layer C); the checks
once envisioned as Layer B are folded into the Layer-C critic prompt
(`factcheck_pack.py:80-97`). Both run as one hard gate here:

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
  python3 scripts/verify_pack.py <pack> --jobs 6          # concurrent Layer-C batches
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
  0 — PACK READY / RE-CERTIFIED. Two cases both write a fresh certification:
      • Full gate (no ``--only``, no ``--no-factcheck``): Layer A has zero live
        findings AND Layer C ran with zero BLOCKING findings (advisory may remain),
        zero batch errors, and FULL coverage. Writes the ``certification`` block
        (aggregate hash + a per-question ``question_stamps`` registry, INV-7 B.1)
        and reformats the JSON via ``json.dumps(indent=2)`` (CV-8).
      • ``--only <subset>`` per-qid RE-CERT: the subset re-graded clean AND, after
        refreshing the graded qids' stamps and carrying the rest, EVERY question is
        covered by a fresh per-qid stamp. Only then is the whole-pack aggregate
        re-stamped. If any qid is edited-but-unaudited its carried stamp won't
        match, the re-cert is refused, and the run falls to exit 3 (below) — so a
        subset run can never forge a fresh aggregate over unchecked questions.
  2 — PACK NOT READY: a live Layer-A finding or a BLOCKING Layer-C finding, OR
      Layer C coverage was incomplete (a batch errored/timed out, or the critic
      inspected fewer questions than were sent), OR the pack has no questions. A
      timed-out or partial-coverage run NEVER certifies ready.
  3 — NOT certified, but nothing blocking was found. Two cases:
      • --no-factcheck: Layer A clean, Layer C never ran; or
      • --only <subset>: the examined questions are clean, but the pack is NOT
        fully covered by fresh per-qid stamps (some qid unaudited or edited but not
        re-graded), so the whole pack is not certified — pack left UNCHANGED.
      --no-factcheck never returns 0; --only returns 0 ONLY via a full per-qid
      re-cert (every qid covered). Run the full gate (no --only, no --no-factcheck)
      for the canonical 0 that means "pack ready".
  1 — operational error (pack unreadable, or `claude` CLI missing when a
      factcheck was requested).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# scripts/ isn't a package; import the two layer modules by path, the same trick
# build_manifest.py uses to reach lint_packs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_packs        # noqa: E402
import factcheck_pack    # noqa: E402
import critic_panel      # noqa: E402
import critic_providers  # noqa: E402
import pack_cert         # noqa: E402


def run_layer_a(pack_path: Path) -> dict:
    """Layer A: lint_packs.lint_pack returns LIVE findings in `violations` plus the
    suppressed set in `waived`. Block on ANY real live finding — the SAME standard
    scripts/lint_hook.py enforces at authoring time (criticals AND warnings alike),
    so the readiness gate and the per-edit gate agree on what "clean" means.

    BUT lint_pack folds WAIVER-rule hygiene warnings (a stale/malformed/unjustified
    `lint_waivers` entry) into `violations` alongside real findings. Those are
    list-rot nudges, not content defects, so the gate treats them like Layer C
    treats ITS hygiene: surfaced as non-blocking hygiene, NOT a reason to fail
    an otherwise-clean pack. Partition them out here — rule == "WAIVER" (the
    marker lint_packs._apply_waivers stamps on hygiene) OR severity == "advisory"
    (any remaining non-blocking tier) — so `live` carries only real blocking
    findings. L23 absent-`coverage_blueprint` is CRITICAL and stays in `live`."""
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


def _adapt_panel(panel: dict, only_total: int | None) -> dict:
    """Fold a :func:`critic_panel.run_panel` result into ``run_layer_c``'s shape.

    One decision matters here. The readiness gate treats any ``errors`` or
    ``coverage_gaps`` as incomplete coverage and refuses to certify. Applied
    naively to a panel, that means a flaky third opinion — a local model that
    timed out, a key that expired — would block a pack that a complete pass had
    already reviewed end to end. Authors would respond by dropping the extra
    passes, and the panel would decay back into single-critic review.

    So in panel mode, coverage blocks on :func:`critic_panel.panel_coverage_ok`:
    at least ONE pass must have covered every graded question with no errors.
    When that holds, the failing passes' errors move to ``panel_notes`` — still
    printed, still in the JSON verdict, never silently dropped — instead of
    ``errors``. When it does NOT hold, every error stays in ``errors`` and the
    gate fails exactly as it would for a single critic. The bar is not lowered;
    it is applied to the panel as a whole rather than to each member.
    """
    covered = critic_panel.panel_coverage_ok(panel)
    return {
        "errors": [] if covered else list(panel["errors"]),
        "coverage_gaps": [] if covered else list(panel["coverage_gaps"]),
        # Always present, regardless of `covered` — the record of what went wrong
        # in the passes that failed, so a degraded panel is visible rather than
        # inferred from a pass count.
        "panel_notes": list(panel["errors"]) + list(panel["coverage_gaps"]),
        "questions_unchecked": panel["questions_unchecked"],
        # A panel has no single model. Record the roster instead, so the report
        # and the certification name every critic that actually graded.
        "model": ", ".join(
            p["model_observed"] or f"{p['model_requested'] or p['provider']}(unreported)"
            for p in panel["passes"] if p.get("ok")) or None,
        "panel": critic_panel.panel_summary(panel),
        "solo_qids": panel["solo_qids"],
        "total": only_total if only_total is not None else panel["questions_sent"],
        "questions_graded": panel["questions_graded"],
    }


def run_layer_c(pack_path: Path, model: str | None, batch_size: int,
                timeout: int, only: set[str] | None = None,
                strict: bool = False,
                jobs: int = factcheck_pack.DEFAULT_JOBS,
                provider: str = factcheck_pack.DEFAULT_PROVIDER,
                panel: list | None = None,
                on_event=None) -> dict:
    """Layer C: run the SHARED canonical batch loop
    (factcheck_pack.collect_findings) over the pack's questions, then apply the
    pack's `factcheck_waivers`. Returns the live/waived/hygiene partition PLUS the
    batch `errors` and `coverage_gaps` that the readiness verdict MUST consult — a
    timed-out batch or a critic that inspected fewer questions than were sent makes
    the pack NOT ready, never "clean". Raises RuntimeError if the selected provider
    is unavailable, or if EVERY batch failed (a hard operational failure, distinct
    from partial incompleteness which is reported back as not-ready).

    ``provider`` selects a single critic backend. ``panel`` (a list of
    :class:`critic_panel.PassSpec`) instead runs SEVERAL independent critics over
    the same questions and merges the union of their findings — see
    :mod:`critic_panel` for why the merge is a union and never a majority vote.
    ``panel`` takes precedence over ``provider`` when both are given; the returned
    dict has the same shape either way, plus a ``panel`` provenance block, so the
    readiness verdict below is written once and does not branch on critic count."""
    if panel:
        return _run_layer_c_panel(pack_path, panel, batch_size, timeout,
                                  only=only, strict=strict, jobs=jobs,
                                  on_event=on_event)

    unavailable = critic_providers.preflight(provider, model)
    if unavailable:
        raise RuntimeError(
            f"provider {provider!r} unavailable: {unavailable}")

    questions, context_qids, effective_batch, total, source_directive = (
        _layer_c_inputs(pack_path, only, strict, batch_size))

    result = factcheck_pack.collect_findings(
        questions, model, effective_batch, timeout, source_directive=source_directive,
        jobs=jobs, context_qids=context_qids, provider=provider)
    all_findings = result["findings"]
    errors = result["errors"]

    n_batches = len(factcheck_pack.batched(questions, effective_batch))
    if errors and not all_findings and len(errors) == n_batches:
        raise RuntimeError("every Layer-C batch failed; see: " + "; ".join(errors))

    live, waived, hygiene = factcheck_pack._apply_waivers(
        all_findings, factcheck_pack.load_waivers(pack_path))
    return {
        "live": live, "waived": waived, "hygiene": hygiene,
        "errors": errors, "coverage_gaps": result["coverage_gaps"],
        "questions_unchecked": result["questions_unchecked"],
        "model": result["model"],
        "total": total if total is not None else result["questions_sent"],
        "questions_graded": result["questions_graded"],
        "source_directive_active": source_directive is not None,
        "provider": provider,
        "panel": None,          # single-critic run — see _run_layer_c_panel
        "panel_notes": [],
        "solo_qids": [],
    }


def _run_layer_c_panel(pack_path: Path, panel: list, batch_size: int, timeout: int,
                       *, only: set[str] | None, strict: bool, jobs: int,
                       on_event=None) -> dict:
    """Layer C via a multi-provider panel. Same contract as :func:`run_layer_c`.

    Waivers are applied to the MERGED union, exactly once, not per pass: a waiver
    is a statement about a defect claim, and the same claim reaching the author
    from three critics is still one reviewed false-positive, not three.

    Raises:
        RuntimeError: Only when EVERY pass failed outright — the panel equivalent
            of "every batch failed". A panel where one member died is a degraded
            panel (reported via ``panel_notes``), not an operational failure; if
            it were, adding a cheap third opinion could take down a run that a
            complete pass had already covered.
    """
    questions, context_qids, effective_batch, total, source_directive = (
        _layer_c_inputs(pack_path, only, strict, batch_size))

    result = critic_panel.run_panel(
        questions, panel, effective_batch, timeout, jobs=jobs,
        source_directive=source_directive, context_qids=context_qids,
        on_event=on_event)

    if not any(p.get("ok") for p in result["passes"]):
        raise RuntimeError("every Layer-C panel pass failed; see: "
                           + "; ".join(result["errors"]))

    live, waived, hygiene = factcheck_pack._apply_waivers(
        result["findings"], factcheck_pack.load_waivers(pack_path))
    out = {"live": live, "waived": waived, "hygiene": hygiene,
           "source_directive_active": source_directive is not None,
           "provider": "panel"}
    out.update(_adapt_panel(result, total))
    return out


def _layer_c_inputs(pack_path: Path, only: set[str] | None, strict: bool,
                    batch_size: int) -> tuple:
    """Shared Layer-C setup for the single-critic and panel paths.

    Extracted so both paths send the SAME questions with the SAME batching and
    the same source_directive policy. If they diverged, panel findings would not
    be comparable to single-critic findings and a re-cert could change verdict
    for reasons unrelated to the pack.

    Returns ``(questions, context_qids, effective_batch, total, source_directive)``.
    """
    # --strict re-grades against generic Security+: drop the pack's source_directive
    # so a paranoid pass can't be talked out of a finding by author-written text.
    source_directive = None if strict else factcheck_pack.load_source_directive(pack_path)
    questions = factcheck_pack.load_questions(pack_path)

    if only is not None:
        # context_only re-cert (INV-7 B.1): send the WHOLE pack so cross-question
        # duplication is compared against every question, but GRADE only the
        # --only ids for their own correctness — the rest ride along as dedup
        # context. One batch (batch size = pack size) so the whole pack is a
        # single comparison window: a semantic dup against ANY other question is
        # visible, not just one that lands in the same slice. `total` reflects the
        # graded count (what "N checked" means for a subset).
        graded_ids = {q.get("id") for q in questions if q.get("id") in only}
        context_qids = {q.get("id") for q in questions
                        if q.get("id") and q.get("id") not in graded_ids}
        return (questions, context_qids, max(1, len(questions)),
                len(graded_ids), source_directive)
    # Full pass: report the full questions_sent count.
    return questions, None, batch_size, None, source_directive


def format_report(pack_label: str, layer_a: dict, layer_c: dict | None,
                  outcome: str) -> str:
    """Combined human verdict: a Layer-A section, a Layer-C section (or a skip
    note), then the final verdict line. `outcome` is one of:
      • "ready"        — full gate passed (may carry advisory findings)
      • "recert"       — a clean --only run whose every question is covered by a
                         fresh per-qid stamp: the whole-pack aggregate was
                         re-certified (INV-7 B.1)
      • "subset_ok"    — a clean --only run that did NOT cover every qid: examined
                         questions clear, but NOT full-pack certification (some
                         qid was never checked / edited but not re-graded)
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
        panel = layer_c.get("panel")
        if panel:
            lines.append("")
            lines.append(
                f"Layer C panel: {panel['passes_completed']}/"
                f"{panel['passes_attempted']} pass(es) covered the pack")
            for p in panel["passes"]:
                lines.append(
                    f"  [{'ok' if p['coverage_ok'] else 'INCOMPLETE'}] "
                    f"{p['label']} -> observed model: "
                    f"{p['model_observed'] or 'unreported'}")
            if panel.get("solo_qids"):
                # Not a suppression list — these findings are already live below.
                # This flags where only ONE critic saw anything, i.e. where a
                # stronger second opinion is worth its cost.
                lines.append(
                    f"  uncorroborated qids ({len(panel['solo_qids'])}): "
                    + ", ".join(panel["solo_qids"][:20])
                    + (" ..." if len(panel["solo_qids"]) > 20 else ""))
        if layer_c.get("panel_notes") and not layer_c["errors"]:
            # A degraded panel that still had one complete pass: reported, never
            # silently swallowed, but not a reason to fail an already-covered pack.
            lines.append("")
            lines.append("Layer C panel notes (non-blocking — another pass covered "
                         "the pack in full):")
            lines.extend(f"  ! {n}" for n in layer_c["panel_notes"])
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
    elif outcome == "recert":
        # A clean --only run where EVERY question was covered by a fresh per-qid
        # stamp: the graded qids were re-hashed and the untouched rest still match,
        # so the whole-pack aggregate was legitimately re-certified (INV-7 B.1).
        n = layer_c.get("total", 0) if layer_c else 0
        c_adv = len(layer_c["live"]) if layer_c else 0
        adv_note = (f" (with {c_adv} advisory Layer-C finding(s) — non-blocking)"
                    if c_adv else "")
        lines.append(f"PACK RE-CERTIFIED — {n} question(s) re-graded; all questions "
                     f"covered by fresh per-qid stamps, aggregate re-stamped{adv_note}.")
    elif outcome == "subset_ok":
        # A clean --only run: the examined questions are clear, but the rest were
        # never checked (or an edited qid was not re-graded), so this is explicitly
        # NOT full-pack certification and the pack is left unchanged.
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


def _write_certification(pack_path: Path, *, model: str, questions_examined: int,
                         stamps: dict | None = None,
                         review_method: str = "external-layer-c-strict",
                         panel: dict | None = None) -> None:
    """Stamp a full-gate READY certification block onto the pack (CV-2, CV-8).

    Re-reads the pack, computes ``questions_hash`` from question content (ignores
    any prior ``certification`` field), writes atomically via a ``.tmp`` sibling.
    Call only from a true READY branch (full-gate exit 0, or a ``--only`` per-qid
    re-cert that covers every question via :func:`_try_recert_only`).

    Also writes the per-question stamp registry ``question_stamps`` (INV-7 B.1):
    when ``stamps`` is None it is (re)built for the whole pack via
    :func:`pack_cert.build_question_stamps` (the full-gate case); the ``--only``
    re-cert passes a merged registry (freshly-recomputed graded stamps + carried
    stamps for the untouched rest). The registry is what makes the aggregate
    ``certification_fresh`` only when EVERY qid has a fresh stamp, so a subset
    re-cert can never forge a fresh whole-pack aggregate (PM-3).

    Raises:
        OSError, json.JSONDecodeError, TypeError, ValueError: On read/hash/write
        failure. Callers must catch and treat as operational error (exit 1).
    """
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    if stamps is None:
        stamps = pack_cert.build_question_stamps(data)
    data["certification"] = {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "questions_hash": pack_cert.questions_hash(data),
        "critic_model": model,
        # INV-7: the cert must NAME an approved review method. This function is
        # reached only from a true READY branch of the real Layer-C gate, which
        # is what `external-layer-c-strict` denotes. An unnamed method no longer
        # certifies, so a hand-written or self-attested block cannot pass.
        "review_method": review_method,
        "blocking_count": 0,
        "questions_examined": questions_examined,
        "question_stamps": stamps,
    }
    if panel is not None:
        # Provenance for a multi-critic pass: which providers ran, which models
        # they REPORTED using, and which qids no second pass corroborated. Written
        # as an extra field rather than folded into `critic_model` so it is
        # machine-readable, and deliberately NOT part of questions_hash (that
        # hashes question CONTENT), so recording richer provenance can never
        # invalidate an existing certification.
        data["certification"]["critic_panel"] = panel
    if review_method not in pack_cert.APPROVED_REVIEW_METHODS:
        raise ValueError(
            f"refusing to write certification with unapproved review_method "
            f"{review_method!r}; expected one of "
            f"{sorted(pack_cert.APPROVED_REVIEW_METHODS)}"
        )
    tmp = pack_path.with_name(pack_path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, pack_path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _try_recert_only(pack_path: Path, *, graded_ids: set[str], model: str,
                     review_method: str = "external-layer-c-strict",
                     panel: dict | None = None) -> bool:
    """Attempt a per-qid re-certification of a clean ``--only`` subset (INV-7 B.1).

    Refreshes the per-question stamp for each freshly-graded qid, carries over the
    prior stamps for the untouched rest, and re-stamps the WHOLE-pack aggregate
    certification IFF every question is then covered by a fresh stamp
    (:func:`pack_cert.question_stamps_fresh`). Otherwise it writes NOTHING and
    returns False — leaving the pack byte-unchanged — so a subset run whose
    unaudited questions were edited can never forge a fresh aggregate (this is the
    exact ``--only && deploy`` bypass the per-qid coverage rule closes).

    ``questions_examined`` is stamped as the FULL pack count (not the graded
    subset count) so the re-stamped aggregate satisfies
    ``certification_fresh``'s ``questions_examined == len(questions)`` check.

    Args:
        pack_path: The pack to re-certify (already Layer-A + Layer-C clean for the
            graded subset — the caller only reaches here on a clean subset run).
        graded_ids: The ``--only`` ids that were just re-graded this run.
        model: The critic model to record in the certification block.

    Returns:
        True if the aggregate was re-certified (a fresh new-format cert was
        written); False if any qid remained unaudited/stale (pack left unchanged).

    Raises:
        OSError, json.JSONDecodeError, TypeError, ValueError: On read/hash/write
        failure. Callers must catch and treat as operational error (exit 1).
    """
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return False
    cert = data.get("certification")
    prior = cert.get("question_stamps") if isinstance(cert, dict) else None
    merged: dict = dict(prior) if isinstance(prior, dict) else {}
    for q in questions:
        if isinstance(q, dict) and q.get("id") in graded_ids:
            merged[q["id"]] = pack_cert.question_content_hash(q, data)
    # Coverage gate: only re-stamp the aggregate when EVERY current question is
    # covered by a matching fresh stamp. A carried-over stamp for an edited-but-
    # unaudited qid will not match its content → False → no write.
    if not pack_cert.question_stamps_fresh(data, merged):
        return False
    _write_certification(
        pack_path, model=model, questions_examined=len(questions), stamps=merged,
        review_method=review_method, panel=panel,
    )
    return True


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
    ap.add_argument("--provider", default=factcheck_pack.DEFAULT_PROVIDER,
                    choices=critic_providers.provider_names(),
                    help="Single critic backend (default: claude). Ignored when "
                    "--panel is given.")
    ap.add_argument("--panel", default=None,
                    help="Run SEVERAL independent critics and gate on the UNION of "
                    "their findings, e.g. "
                    "'deepseek=deepseek-v4-flash,ollama=qwen3:8b,claude'. Cheap "
                    "providers make repeated independent review affordable, which is "
                    "what distinguishes 'reviewed and clean' from 'nobody looked'. "
                    "Certifies as review_method=external-layer-c-panel. See "
                    "docs/CRITIC_PROVIDERS.md.")
    ap.add_argument("--model", default=None,
                    help="Model for the Layer-C critic. Defaults to claude-sonnet-5 "
                    "for --provider claude (pass --model opus to escalate, or an "
                    "alias like 'sonnet'/'opus'), otherwise the provider's own "
                    "default. Per-pass models are set inside --panel instead.")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12).")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout (s).")
    ap.add_argument("--jobs", type=int, default=factcheck_pack.DEFAULT_JOBS,
                    help="Concurrent Layer-C LLM batches (default 6). Batches are "
                    "independent, so this is a near-linear speedup; lower it if you "
                    "hit API rate limits. Use 1 to force serial.")
    ap.add_argument("--only", default=None,
                    help="Comma-separated question ids to re-verify (default: all). "
                    "Powers shrinking confirmation runs: after the initial full "
                    "audit, re-check ONLY the questions you changed. The changed "
                    "questions are graded; the rest of the pack rides along as dedup "
                    "context (whole pack sent as one batch, so cross-question "
                    "duplication is seen against every question, not just a slice). "
                    "If the subset is clean AND every question is then covered by a "
                    "fresh per-qid stamp, the whole-pack aggregate is RE-CERTIFIED "
                    "(exit 0); if any other qid was edited but not re-graded its "
                    "stamp won't match and the run exits 3 (SUBSET RECHECK PASSED, "
                    "pack unchanged) rather than certify unchecked content.")
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
    # Resolve --model against the chosen provider rather than one global default,
    # so `--provider deepseek` doesn't inherit a Claude model id.
    model = args.model
    if model is None and args.provider == factcheck_pack.DEFAULT_PROVIDER:
        model = "claude-sonnet-5"
    try:
        panel_passes = critic_panel.parse_panel(args.panel) if args.panel else None
    except ValueError as e:
        print(f"error: --panel: {e}", file=sys.stderr)
        return 1
    # A panel certifies under its own review_method so the certification records
    # HOW the pack was reviewed, not just that it was. INV-7's whole premise is
    # that an unstated method is indistinguishable from a self-attested one.
    review_method = ("external-layer-c-panel" if panel_passes
                     else "external-layer-c-strict")

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
        # INV-1: a multi-pass panel is a long network wait. Stream per-pass and
        # per-batch progress to stderr so the run is never a silent block.
        def _on_event(kind: str, **info) -> None:
            if args.json:
                return
            if kind == "pass_start":
                print(f"[Layer C pass {info['index'] + 1}/{info['total']}] "
                      f"{info['label']}...", file=sys.stderr)
            elif kind == "batch":
                print(f"  {info['label']}: checked batch "
                      f"{info['i'] + 1}/{info['n']}", file=sys.stderr)
            elif kind == "pass_done":
                print(f"  {info['label']}: {info['findings']} finding(s), "
                      f"{info['errors']} error(s), "
                      f"model={info['model'] or 'unknown'}", file=sys.stderr)

        try:
            layer_c = run_layer_c(args.pack, model, args.batch_size,
                                  args.timeout, only=only, strict=args.strict,
                                  jobs=args.jobs, provider=args.provider,
                                  panel=panel_passes, on_event=_on_event)
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
            # A clean --only subset re-grades the changed questions. Attempt a
            # per-qid re-certification (INV-7 B.1): refresh the graded qids' stamps,
            # carry the rest, and re-stamp the WHOLE-pack aggregate IFF every qid is
            # then covered by a fresh stamp. That coverage rule is what lets a
            # subset run legitimately certify the whole pack WITHOUT reopening the
            # `--only && deploy` bypass — an edited-but-unaudited qid's carried stamp
            # won't match, so the re-cert is refused and the pack ships uncertified.
            #   recert     / 0 — all qids covered by fresh stamps; aggregate re-stamped
            #   subset_ok  / 3 — some qid unaudited/edited; pack UNCHANGED, not certified
            critic_model = str((layer_c or {}).get("model") or model)
            try:
                recertified = _try_recert_only(
                    args.pack, graded_ids=only, model=critic_model,
                    review_method=review_method,
                    panel=(layer_c or {}).get("panel"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
                print(f"error: per-qid re-cert failed: {e}", file=sys.stderr)
                return 1
            if recertified:
                outcome, exit_code = "recert", 0
            else:
                outcome, exit_code = "subset_ok", 3
        else:
            outcome, exit_code = "ready", 0

    if outcome == "ready" and exit_code == 0:
        # Full-gate READY only. The "recert" outcome (a covered --only re-cert)
        # already wrote its own new-format cert via _try_recert_only, so it must
        # NOT fall through here (it would restamp with the graded-subset count).
        # Prefer Layer-C's resolved model + questions_sent over CLI alias / re-read.
        critic_model = (layer_c or {}).get("model") or model
        examined = (layer_c or {}).get("total")
        if examined is None:
            examined = (layer_c or {}).get("questions_sent")
        if examined is None:
            try:
                examined = len(
                    json.loads(args.pack.read_text(encoding="utf-8")).get("questions")
                    or []
                )
            except (OSError, json.JSONDecodeError):
                examined = 0
        try:
            _write_certification(
                args.pack,
                model=str(critic_model),
                questions_examined=int(examined),
                review_method=review_method,
                panel=(layer_c or {}).get("panel"),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"error: certification stamp failed: {e}", file=sys.stderr)
            return 1

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
