#!/usr/bin/env python3
"""Internal pack-readiness gate primitive (Layer A + C).

The supported operator-facing certification route is
``scripts/hybrid_verify.py``.  This module remains importable because the
hybrid orchestrator runs its two provider-specific passes in-process; its
legacy shell entrypoint is intentionally fail-fast (see :func:`cli_main`).

Quizzler's QA pipeline has two automated layers (Layer A + Layer C); the checks
once envisioned as Layer B are folded into the Layer-C critic prompt
(`factcheck_pack.py:80-97`). Both run as one hard gate here:

  • Layer A — scripts/lint_packs.py: deterministic structure linter (schema,
    answer-leak tells, distractor coverage, duplicate stems). Fast, free,
    reproducible — already enforced at commit time by .githooks/pre-commit
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

This is an internal library primitive. Operators must record discovery through
``hybrid_verify.py --no-certify`` and finalize only with
``hybrid_verify.py <pack> --certify-campaign <ledger>``; the old direct shell
route is retired.

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
  0 — PACK READY. Only a full gate (no ``--only``, no ``--no-factcheck``) writes
      a fresh certification: Layer A has zero live findings AND Layer C ran with
      zero BLOCKING findings (advisory may remain), zero batch errors, and FULL
      coverage. It writes the ``certification`` block (aggregate hash + a
      per-question ``question_stamps`` registry, INV-7 B.1) and reformats the JSON
      via ``json.dumps(indent=2)`` (CV-8).
  2 — PACK NOT READY: a live Layer-A finding or a BLOCKING Layer-C finding, OR
      Layer C coverage was incomplete (a batch errored/timed out, or the critic
      inspected fewer questions than were sent), OR the pack has no questions. A
      timed-out or partial-coverage run NEVER certifies ready.
  3 — NOT certified, but nothing blocking was found. Two cases:
      • --no-factcheck: Layer A clean, Layer C never ran; or
      • --only <subset>: the examined questions are clean, but targeted
        confirmation never certifies and leaves the pack unchanged. Run the full
        gate (no --only, no --no-factcheck) for the canonical 0 that means
        "pack ready".
  1 — operational error (pack unreadable, or `claude` CLI missing when a
      factcheck was requested).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# scripts/ isn't a package; import the two layer modules by path, the same trick
# build_manifest.py uses to reach lint_packs.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import critic_panel
import critic_providers
import factcheck_pack
import lint_packs
import pack_cert
import verifier_profiles

# The ONLY two review methods this module writes. A single pass by the project's
# designated external critic, or a panel of >=2 independent providers. Named
# constants rather than inline literals so the equality with
# pack_cert.APPROVED_REVIEW_METHODS is testable: a method the gate ACCEPTS but
# nothing WRITES is a cert shape only a hand-edit could produce.
SINGLE_REVIEW_METHOD = "external-layer-c-strict"
PANEL_REVIEW_METHOD = "external-layer-c-panel"
CERTIFYING_REVIEW_METHODS = frozenset({SINGLE_REVIEW_METHOD})

# A targeted recheck must remain materially cheaper than a full pass.  The
# target qids are always included; this bounds only the ride-along comparison
# questions used to catch likely duplicate regressions.  It deliberately does
# NOT make a claim about whole-pack duplicate coverage: that is the final full
# certification gate's job.
TARGETED_CONTEXT_LIMIT = 24
_NEIGHBOR_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def run_layer_a(pack_path: Path) -> dict:
    """Layer A: lint_packs.lint_pack returns LIVE findings in `violations` plus the
    suppressed set in `waived`. Block on ANY real live finding — the SAME standard
    the staged-pack pre-commit gate enforces at commit time (criticals AND warnings alike),
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
                on_event=None,
                variant: str | None = None,
                retry_incomplete: bool = True) -> dict:
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
                                  on_event=on_event, variant=variant)

    # Keep the single-critic path on the same INV-1 progress contract as the
    # panel path.  In particular, ``collect_findings`` owns the batch loop, so
    # its completion callback must be adapted rather than silently discarded.
    label = provider
    if on_event:
        on_event("pass_start", label=label, index=0, total=1)

    unavailable = critic_providers.preflight(provider, model)
    if unavailable:
        if on_event:
            on_event("pass_done", label=label, findings=0, errors=1,
                     model=None)
        raise RuntimeError(
            f"provider {provider!r} unavailable: {unavailable}")

    questions, context_qids, effective_batch, total, source_directive, source_text, subject = (
        _layer_c_inputs(pack_path, only, strict, batch_size))

    def _batch_progress(i: int, n: int) -> None:
        if on_event:
            on_event("batch", label=label, i=i, n=n)

    try:
        result = factcheck_pack.collect_findings(
            questions, model, effective_batch, timeout,
            on_batch=_batch_progress, source_directive=source_directive,
            jobs=jobs, context_qids=context_qids, provider=provider,
            variant=variant, source_text=source_text, subject=subject,
            retry_incomplete=retry_incomplete)
    except (RuntimeError, ValueError):
        if on_event:
            on_event("pass_done", label=label, findings=0, errors=1,
                     model=None)
        raise
    all_findings = result["findings"]
    errors = result["errors"]

    if on_event:
        on_event("pass_done", label=label, findings=len(all_findings),
                 errors=len(errors), model=result["model"])

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
        "source_text_active": source_text is not None,
        "subject": subject or factcheck_pack.DEFAULT_SUBJECT,
        "provider": provider,
        "panel": None,          # single-critic run — see _run_layer_c_panel
        "panel_notes": [],
        "solo_qids": [],
    }


def _run_layer_c_panel(pack_path: Path, panel: list, batch_size: int, timeout: int,
                       *, only: set[str] | None, strict: bool, jobs: int,
                       on_event=None, variant: str | None = None) -> dict:
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
    questions, context_qids, effective_batch, total, source_directive, source_text, subject = (
        _layer_c_inputs(pack_path, only, strict, batch_size))

    result = critic_panel.run_panel(
        questions, panel, effective_batch, timeout, jobs=jobs,
        source_directive=source_directive, context_qids=context_qids,
        on_event=on_event, variant=variant, source_text=source_text, subject=subject)

    if not any(p.get("ok") for p in result["passes"]):
        raise RuntimeError("every Layer-C panel pass failed; see: "
                           + "; ".join(result["errors"]))

    live, waived, hygiene = factcheck_pack._apply_waivers(
        result["findings"], factcheck_pack.load_waivers(pack_path))
    out = {"live": live, "waived": waived, "hygiene": hygiene,
           "source_directive_active": source_directive is not None,
           "source_text_active": source_text is not None,
           "subject": subject or factcheck_pack.DEFAULT_SUBJECT,
           "provider": "panel"}
    out.update(_adapt_panel(result, total))
    return out


def _layer_c_inputs(pack_path: Path, only: set[str] | None, strict: bool,
                    batch_size: int) -> tuple:
    """Shared Layer-C setup for the single-critic and panel paths.

    Extracted so both paths send the SAME questions with the SAME batching and
    the same source_directive/source_text/subject policy. If they diverged,
    panel findings would not be comparable to single-critic findings and a
    re-cert could change verdict for reasons unrelated to the pack.

    Returns ``(questions, context_qids, effective_batch, total, source_directive,
    source_text, subject)``.
    """
    # --strict re-grades against the pack's subject generically: drop the
    # pack's source_directive so a paranoid pass can't be talked out of a
    # finding by author-written text. source_text (real course content, not an
    # author assertion) is kept even under --strict — see
    # factcheck_pack.build_prompt's docstring. subject (e.g. "CISSP") is basic
    # pack identity, not a framing assertion, so --strict never drops it — a
    # paranoid pass should still know WHAT it's grading, just not trust the
    # author's claims about how to grade it.
    source_directive = None if strict else factcheck_pack.load_source_directive(pack_path)
    source_text = factcheck_pack.load_source_text(pack_path)
    subject = factcheck_pack.load_subject(pack_path)
    questions = factcheck_pack.load_questions(pack_path)

    if only is not None:
        # Targeted rechecks grade the requested ids and compare them to a small,
        # deterministic neighborhood.  Earlier code carried the whole pack in a
        # single prompt, which made ``--only`` as expensive as a full pass.  The
        # bounded comparison is a remediation aid, not a substitute for the final
        # whole-pack duplicate review.
        questions, context_qids = _targeted_questions_with_context(questions, only)
        return (questions, context_qids, max(1, len(questions)), len(only),
                source_directive, source_text, subject)
    # Full pass: report the full questions_sent count.
    return questions, None, batch_size, None, source_directive, source_text, subject


def _question_tokens(question: dict) -> set[str]:
    """Return deterministic lexical cues used to select duplicate neighbors.

    This is intentionally local and conservative: it narrows the prompt to
    questions that share a topic or meaningful wording with an edited question;
    it does not pretend to provide semantic whole-pack duplicate coverage.
    """
    parts: list[str] = []
    for field in ("topic", "prompt", "explanation"):
        value = question.get(field)
        if isinstance(value, str):
            parts.append(value.lower())
    options = question.get("options")
    if isinstance(options, list):
        parts.extend(value.lower() for value in options if isinstance(value, str))
    return set(_NEIGHBOR_TOKEN_RE.findall(" ".join(parts)))


def _targeted_questions_with_context(questions: list[dict], only: set[str]) -> tuple[list[dict], set[str]]:
    """Select requested qids plus a bounded deterministic dedup neighborhood.

    Invalid requested ids are an input error, never silently dropped.  Candidate
    context is ranked by shared topic first, then lexical overlap with any target,
    with original pack order as the stable tie-breaker.  The selected payload
    itself retains pack order so provider output and prompts stay reproducible.
    """
    ids = {q.get("id") for q in questions if isinstance(q.get("id"), str)}
    unknown = sorted(only - ids)
    if unknown:
        raise ValueError("unknown --only question id(s): " + ", ".join(unknown))

    target_questions = [q for q in questions if q.get("id") in only]
    target_topics = {
        q.get("topic") for q in target_questions
        if isinstance(q.get("topic"), str) and q.get("topic")
    }
    target_token_sets = [_question_tokens(q) for q in target_questions]
    candidates: list[tuple[tuple[int, int, int], int, str]] = []
    for index, question in enumerate(questions):
        qid = question.get("id")
        if not isinstance(qid, str) or qid in only:
            continue
        same_topic = int(question.get("topic") in target_topics)
        tokens = _question_tokens(question)
        overlap = max((len(tokens & target_tokens) for target_tokens in target_token_sets),
                      default=0)
        # Negated values make a normal ascending sort put stronger neighbors
        # first; index gives a deterministic tie-breaker.
        candidates.append(((-same_topic, -overlap, index), index, qid))
    candidates.sort()
    context_qids = {
        qid for _score, _index, qid in candidates[:TARGETED_CONTEXT_LIMIT]
    }
    selected_ids = only | context_qids
    selected = [q for q in questions if q.get("id") in selected_ids]
    return selected, context_qids


def format_report(pack_label: str, layer_a: dict, layer_c: dict | None,
                  outcome: str, no_cert_reason: str | None = None) -> str:
    """Combined human verdict: a Layer-A section, a Layer-C section (or a skip
    note), then the final verdict line. `outcome` is one of:
      • "ready"        — full gate passed (may carry advisory findings)
      • "subset_ok"    — a clean --only run: examined questions clear, but NOT
                         full-pack certification
      • "structure_ok" — --no-factcheck, Layer A clean, Layer C never ran
      • "review_ok"    — every gate passed, but the run was not entitled to
                         certify (single non-designated provider, or a panel
                         whose passes turned out not to be independent)
      • "not_ready"    — a Layer-A live finding, a BLOCKING Layer-C finding, or
                         incomplete Layer-C coverage.

    `no_cert_reason` explains a "review_ok" outcome in the caller's own words.
    An unexplained exit 3 is what makes someone reach for a bypass, so the
    verdict line always says which rule withheld the stamp."""
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
        # Transparency (both reviews' ask): surface what the critic was told —
        # what may have SUPPRESSED findings (source_directive, source_text,
        # waivers) plus what subject-matter anchor it graded against (always
        # shown, even the DEFAULT_SUBJECT fallback, since "generic anchor, no
        # course-specific subject declared" is itself worth surfacing) — so a
        # reader sees the grading context, not just the residue.
        if layer_c.get("source_directive_active"):
            parts.append("source_directive active")
        if layer_c.get("source_text_active"):
            parts.append("source_text grounded")
        if layer_c.get("subject"):
            parts.append(f"graded as: {layer_c['subject']}")
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
    elif outcome == "review_ok":
        # Clean under a single non-designated provider. Say plainly that this is
        # a review, not a certification, and name the one command that closes the
        # gap — an unexplained exit 3 invites someone to reach for a bypass.
        c_adv = len(layer_c["live"]) if layer_c else 0
        adv_note = f" (with {c_adv} advisory Layer-C finding(s))" if c_adv else ""
        reason = no_cert_reason or (
            "a single non-default provider does NOT certify: one cheap pass "
            "cannot tell 'reviewed carefully' from 'did not look'")
        lines.append(
            f"REVIEW PASSED — every gate clear{adv_note}, but {reason}. "
            "Pack UNCHANGED.")
        lines.append("  To certify, complete a frozen hybrid campaign, then run:")
        lines.append("    python3 scripts/hybrid_verify.py <pack> --certify-campaign <ledger>")
    elif outcome == "subset_ok":
        # Targeted confirmation is explicitly NOT full-pack certification and
        # leaves the pack unchanged.
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
                         review_method: str = SINGLE_REVIEW_METHOD,
                         panel: dict | None = None,
                         provider: str | None = None,
                         requested_model: str | None = None,
                         reasoning_effort: str | None = None,
                         provenance: dict | None = None) -> None:
    """Stamp a full-gate READY certification block onto the pack (CV-2, CV-8).

    Re-reads the pack, computes ``questions_hash`` from question content (ignores
    any prior ``certification`` field), writes atomically via a ``.tmp`` sibling.
    Call only from a true full-gate READY branch (exit 0 without ``--only``).

    Also writes the per-question stamp registry ``question_stamps`` (INV-7 B.1):
    The stamp registry is always built for the complete pack via
    :func:`pack_cert.build_question_stamps` so certification represents one full
    gate, not a collection of targeted confirmations.

    Raises:
        OSError, json.JSONDecodeError, TypeError, ValueError: On read/hash/write
        failure. Callers must catch and treat as operational error (exit 1).
    """
    if panel is not None or review_method == PANEL_REVIEW_METHOD:
        raise ValueError("panel certification route is retired")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise ValueError("certification provenance must be an object")
        required = {
            "kind", "evidence_policy", "campaign_snapshot_fingerprint",
            "base_snapshot_fingerprint", "verifier_profile",
            "verifier_provider", "verifier_model", "remediation_qids",
        }
        if set(provenance) != required:
            raise ValueError("frozen-campaign provenance fields are malformed")
        if provenance["kind"] != "frozen-campaign-evidence":
            raise ValueError("certification provenance kind is invalid")
        if provenance["evidence_policy"] != "no-new-llm-call":
            raise ValueError("certification provenance policy is invalid")
        for name in ("campaign_snapshot_fingerprint", "base_snapshot_fingerprint"):
            if (not isinstance(provenance[name], str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", provenance[name])):
                raise ValueError(f"certification provenance {name} is malformed")
        if (not isinstance(provenance["verifier_profile"], str)
                or not provenance["verifier_profile"].strip()
                or not isinstance(provenance["verifier_provider"], str)
                or not isinstance(provenance["verifier_model"], str)
                or not isinstance(provenance["remediation_qids"], list)
                or any(not isinstance(qid, str) or not qid for qid in provenance["remediation_qids"])):
            raise ValueError("certification provenance verifier fields are malformed")
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
        "critic_provider": provider,
        "critic_model_requested": requested_model,
        "critic_reasoning_effort": reasoning_effort,
        # INV-7: the cert must NAME an approved review method. This function is
        # reached only from a true READY branch of the real Layer-C gate, which
        # is what `external-layer-c-strict` denotes. An unnamed method no longer
        # certifies, so a hand-written or self-attested block cannot pass.
        "review_method": review_method,
        "blocking_count": 0,
        "questions_examined": questions_examined,
        "question_stamps": stamps,
    }
    if review_method not in pack_cert.APPROVED_REVIEW_METHODS:
        raise ValueError(
            f"refusing to write certification with unapproved review_method "
            f"{review_method!r}; expected one of "
            f"{sorted(pack_cert.APPROVED_REVIEW_METHODS)}"
        )
    if provenance is not None:
        data["certification"]["provenance"] = dict(provenance)
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


def _observed_or_unknown(layer_c: dict | None, requested: str | None) -> str:
    """Return provider-attested model identity without laundering a request.

    Codex's output-last-message mode does not report the served model. Keep the
    certification provenance honest instead of substituting the requested id.
    """
    observed = (layer_c or {}).get("model")
    if observed:
        return str(observed)
    if (layer_c or {}).get("provider") == "codex":
        return "unknown"
    return str(requested or "unknown")


def main(argv: list[str], *, _hybrid_certifier: str | None = None) -> int:
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
                    help="Single critic backend for review (default: claude). "
                    "Direct calls never certify; only hybrid_verify's registered "
                    "verifier profile can stamp readiness.")
    ap.add_argument("--panel", default=None,
                    help="Retired and rejected. Use hybrid_verify with a registered "
                    "verifier profile.")
    ap.add_argument("--model", default=None,
                    help="Model for the Layer-C critic. Defaults to claude-sonnet-5 "
                    "for --provider claude, otherwise the provider's own "
                    "default. Hybrid supplies the approved profile model.")
    ap.add_argument("--variant", default=None,
                    help="Provider-specific reasoning-effort selector (e.g. "
                    "opencode 'max' or Codex 'high').")
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
                    "questions are graded with up to 24 deterministic duplicate-neighbor "
                    "questions as context; this is not whole-pack duplicate coverage. "
                    "A clean subset exits 3 (SUBSET RECHECK PASSED) and never "
                    "writes certification; only the final full gate can certify.")
    ap.add_argument("--strict", action="store_true",
                    help="Gate on EVERY live Layer-C finding, not just errors. Default "
                    "readiness = 0 Layer-A live + 0 BLOCKING Layer-C findings "
                    "(wrong-answer or high-confidence) + full coverage; the "
                    "probabilistic nit/ambiguous tail is advisory. --strict restores "
                    "the old zero-any-finding bar for a final belt-and-suspenders pass.")
    ap.add_argument("--json", action="store_true",
                    help="Emit the combined verdict as JSON.")
    ap.add_argument("--no-retry-incomplete", action="store_true",
                    help="Record a failed or incomplete Layer-C batch without its "
                    "usual one retry. Intended only for an advisory critic whose "
                    "failure must not delay the designated verifier.")
    args = ap.parse_args(argv)
    only = ({q.strip() for q in args.only.split(",") if q.strip()}
            if args.only else None)
    # Resolve --model against the chosen provider rather than one global default,
    # so `--provider opencode` doesn't inherit a Claude model id.
    model = args.model
    if model is None and args.provider == factcheck_pack.DEFAULT_PROVIDER:
        model = "claude-sonnet-5"
    if args.panel:
        print("error: --panel certification route is retired; use "
              "hybrid_verify.py with a registered verifier profile",
              file=sys.stderr)
        return 1
    panel_passes = None
    if args.variant and not panel_passes and args.provider not in {"opencode", "codex"}:
        print(f"error: --variant is not supported by provider {args.provider} "
              "(opencode and codex only)",
              file=sys.stderr)
        return 1
    review_method = SINGLE_REVIEW_METHOD
    # ...and a review_method only means something if it is not mintable by any
    # backend the caller happens to point at. `external-layer-c-strict` denotes
    # review by the project's designated external critic (the `claude` CLI).
    # Adding --provider made that name reachable from ANY endpoint: a 1B local
    # model — or an HTTP stub that returns `{"findings": []}` — would otherwise
    # stamp the same certification the install gate trusts, which is exactly the
    # self-attestation INV-7 exists to refuse. So a non-default single provider
    # RUNS the review (useful, cheap, fast) but does not certify. To certify with
    profile = (verifier_profiles.PROFILES.get(_hybrid_certifier)
               if _hybrid_certifier else None)
    certifying = bool(
        profile
        and args.provider == profile.provider
        and model == profile.model
        and args.variant == profile.reasoning_effort
    )
    no_cert_reason: str | None = None
    if not certifying:
        no_cert_reason = (
            f"a single non-designated provider ({args.provider}) does NOT certify: "
            "only a completed hybrid evidence campaign may designate a certification")

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
        all_questions = factcheck_pack.load_questions(args.pack)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read pack: {e}", file=sys.stderr)
        return 1
    if only:
        known_ids = {q.get("id") for q in all_questions if isinstance(q.get("id"), str)}
        unknown = sorted(only - known_ids)
        if unknown:
            if len(unknown) == len(only):
                print("error: none of the --only ids matched a question", file=sys.stderr)
            else:
                print("error: unknown --only question id(s): " + ", ".join(unknown),
                      file=sys.stderr)
            return 2
    questions = [q for q in all_questions if only is None or q.get("id") in only]
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
                                  panel=panel_passes, on_event=_on_event,
                                  variant=args.variant,
                                  retry_incomplete=not args.no_retry_incomplete)
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
        # A panel's roster can look independent and not be — two distinct
        # `--panel` labels prove nothing about distinct weights (e.g. two
        # openai-compatible aliases routed by one gateway to the same model).
        # Only the models' own reported ids can settle it, and they are only
        # known now, so this check necessarily lands after the passes have run.
        if certifying and panel_passes:
            repeated = critic_panel.duplicate_observed_models(
                (layer_c or {}).get("panel") or {})
            if repeated:
                certifying = False
                no_cert_reason = (
                    "the panel was not independent — "
                    f"{', '.join(repeated)} served more than one pass, so this "
                    "is correlated repetition wearing the panel's review_method")
        clean = a_clean and not blocking and factcheck_pack.coverage_ok(layer_c)
        if not clean:
            outcome, exit_code = "not_ready", 2
        elif not certifying:
            # Clean, but graded by a single non-designated provider. Report the
            # good news and withhold the stamp — never silently downgrade to a
            # cert nobody asked for. Exit 3 joins structure_ok/subset_ok: "we
            # checked, it looks fine, this is NOT certification."
            outcome, exit_code = "review_ok", 3
        elif only:
            # Targeted confirmation is deliberately non-certifying. A bounded
            # neighborhood cannot prove whole-pack duplicate coverage; one final
            # full gate is the certification authority for this campaign.
            outcome, exit_code = "subset_ok", 3
        else:
            outcome, exit_code = "ready", 0

    if outcome == "ready" and exit_code == 0:
        # Full-gate READY only. Prefer Layer-C's resolved model + questions_sent
        # over CLI alias / re-read.
        critic_model = _observed_or_unknown(layer_c, model)
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
                provider=(layer_c or {}).get("provider") or args.provider,
                requested_model=model,
                reasoning_effort=args.variant,
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
        print(format_report(pack_label, layer_a, layer_c, outcome,
                            no_cert_reason=no_cert_reason))

    return exit_code


def cli_main(argv: list[str] | None = None) -> int:
    """Reject the retired direct certification command with actionable help."""
    print(
        "error: scripts/verify_pack.py is an internal library primitive; "
        "direct certification is retired. Run hybrid discovery with "
        "--no-certify, then finalize only with "
        "hybrid_verify.py <pack> --certify-campaign <ledger>.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))
