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

This script is the deliberate, ON-DEMAND review gate: it runs BOTH layers and
reports whether anything blocks the pack. It never certifies: a clean review
exits 3, and only ``hybrid_verify.py --certify-campaign`` writes a certification
(INV-7). Layer C is the reason this lives on demand rather than in the hook or
the build — an LLM pass is too slow/costly/non-deterministic to run on every
edit or every launch, but it must run before a pack ships.

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

Exit codes (``main`` never returns 0 and never writes the pack):
  2 — PACK NOT READY: a live Layer-A finding or a BLOCKING Layer-C finding, OR
      Layer C coverage was incomplete (a batch errored/timed out, or the critic
      inspected fewer questions than were sent), OR the pack has no questions. A
      timed-out or partial-coverage run NEVER certifies ready.
  3 — NOT certified, but nothing blocking was found. Two cases:
      • --no-factcheck: Layer A clean, Layer C never ran; or
      • Layer A and Layer C (full or ``--only`` subset) are clean: REVIEW PASSED.
        The pack is left unchanged; finish with a frozen hybrid campaign.
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
import critic_providers
import factcheck_pack
import lint_packs
import pack_cert

# The review methods ``_write_certification`` knows about: the single-pass method
# it writes, and the retired panel method it refuses. Named constants rather
# than inline literals so the equality with pack_cert.APPROVED_REVIEW_METHODS is
# testable: a method the gate ACCEPTS but nothing WRITES is a cert shape only a
# hand-edit could produce.
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


def run_layer_c(pack_path: Path, model: str | None, batch_size: int,
                timeout: int, only: set[str] | None = None,
                strict: bool = False,
                jobs: int = factcheck_pack.DEFAULT_JOBS,
                provider: str = factcheck_pack.DEFAULT_PROVIDER,
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

    ``provider`` selects the single critic backend."""
    # INV-1 progress contract: ``collect_findings`` owns the batch loop, so its
    # completion callback must be adapted rather than silently discarded.
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

    if errors and not all_findings and len(errors) == result["batch_count"]:
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
        # Retired panel fields, kept so the JSON verdict shape does not change.
        "panel": None,
        "panel_notes": [],
        "solo_qids": [],
    }


def _layer_c_inputs(pack_path: Path, only: set[str] | None, strict: bool,
                    batch_size: int) -> tuple:
    """Layer-C setup: the questions sent, their batching, and the
    source_directive/source_text/subject policy.

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
      • "structure_ok" — --no-factcheck, Layer A clean, Layer C never ran
      • "review_ok"    — every gate passed (including a clean --only recheck),
                         but a single review pass is not entitled to certify
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
            n_block = len(block)
            lines.append(f"Layer C (factual): {len(c_live)} live finding(s) — "
                         f"{n_block} BLOCKING, {len(c_live) - n_block} advisory{suffix}")
            lines.extend(factcheck_pack.format_live_finding_lines(c_live, block))
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
        # Mirrors pack_cert._frozen_campaign_provenance_fresh: the chained
        # round number is optional so pre-chain stamps stay valid.
        if set(provenance) - {"remediation_round"} != required:
            raise ValueError("frozen-campaign provenance fields are malformed")
        if "remediation_round" in provenance and (
                type(provenance["remediation_round"]) is not int
                or provenance["remediation_round"] < 1):
            raise ValueError("certification provenance remediation_round is malformed")
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
    if args.variant and args.provider not in {"opencode", "codex"}:
        print(f"error: --variant is not supported by provider {args.provider} "
              "(opencode and codex only)",
              file=sys.stderr)
        return 1
    # A review pass never certifies. `external-layer-c-strict` denotes review by
    # a registered verifier profile, and certification is written only by
    # hybrid_verify.py --certify-campaign from a completed, snapshot-bound
    # ledger. Any provider RUNS the review (useful, cheap, fast); none stamps.
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
        # INV-1: a Layer-C pass is a long network wait. Stream per-pass and
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
                                  on_event=_on_event,
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
        clean = a_clean and not blocking and factcheck_pack.coverage_ok(layer_c)
        if not clean:
            outcome, exit_code = "not_ready", 2
        else:
            # Clean, but a review pass is never a certification. Report the good
            # news and withhold the stamp. Exit 3 joins structure_ok: "we
            # checked, it looks fine, this is NOT certification." A clean --only
            # recheck lands here too: a bounded neighborhood cannot prove
            # whole-pack duplicate coverage.
            outcome, exit_code = "review_ok", 3

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
