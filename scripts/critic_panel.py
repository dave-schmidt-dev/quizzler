#!/usr/bin/env python3
"""Layer-C PANEL — several independent critic passes over one pack, merged.

The problem this solves
-----------------------
A single Layer-C pass cannot distinguish "reviewed carefully, found nothing" from
"did not really look". Both produce an empty findings list, and the certification
records the same thing either way. SY0-701 is the worked example: one pass, clean
report, certification minted, 115 criticals shipped.

Running the SAME model twice does not fix that — correlated failure modes miss the
same questions both times. Running DIFFERENT models does: independent weights make
different mistakes, so a defect that survives every pass is meaningfully harder to
produce by accident than one that survives a single pass. Cheap providers make
that affordable — several opencode/local passes cost a fraction of one frontier
pass — which is the actual mechanism by which this gets faster and cheaper, not
"use a smaller model instead".

UNION, never majority — the one rule to not get wrong
-----------------------------------------------------
It is tempting to take a 2-of-3 vote and treat solo findings as noise. That is
backwards for a GATE. The failure being fixed is a FALSE NEGATIVE: defects that
no critic reported. A majority threshold SUPPRESSES exactly the findings only one
model was sharp enough to catch, which makes false negatives more likely, not
less. So:

* every finding from every pass survives the merge (union);
* ``agreement`` is an ANNOTATION — how many passes independently flagged the same
  (qid, severity) — used to rank attention and to route escalation;
* ``agreement`` NEVER removes a finding.

Same reasoning as ``extract_findings``'s ``(no-qid)`` sentinel and its fail-safe
severity coercion: in a mandatory gate, a dropped finding is a false pass.

Coverage across passes
----------------------
``questions_unchecked`` for the panel is the MINIMUM across passes, not the sum or
the max. If any single pass covered the whole pack with no batch errors, then the
pack WAS fully reviewed at least once — a second pass timing out does not
un-review it. :func:`panel_coverage_ok` says the same thing directly: at least one
pass must be individually complete. Passes that failed still get reported, because
a panel that silently degrades to one working provider is a panel in name only.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# scripts/ isn't a package; keep sibling imports cwd-independent.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import critic_providers  # noqa: E402
import factcheck_pack  # noqa: E402

# Ranked worst-first. Used to pick a representative severity/confidence when two
# passes describe the same defect with different labels: always keep the MORE
# severe one, so merging can never soften a finding.
_SEVERITY_RANK = {sev: i for i, sev in enumerate(factcheck_pack.SEVERITIES)}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

# A default panel is deliberately NOT defined here. Which providers are available
# is a property of the machine (keys registered, models pulled), not of the repo,
# so a hardcoded default would fail on every machine but one. --panel is explicit;
# docs/CRITIC_PROVIDERS.md carries the recommended shapes.


@dataclass(frozen=True)
class PassSpec:
    """One panel pass: a provider plus the model to ask it for.

    ``label`` is what appears in reports and in the certification. It is derived
    from provider+model rather than free text so two passes can never be recorded
    under the same name, and so the record cannot claim a pass that did not run.
    """

    provider: str
    model: str | None

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}" if self.model else self.provider


def parse_panel(spec: str) -> list[PassSpec]:
    """Parse ``--panel`` syntax into passes.

    Syntax is ``provider[=model]`` entries separated by commas::

        opencode=deepseek-v4-flash-free,local=gemma-4-12b,claude=claude-sonnet-5
        opencode,claude                      # each provider's default model

    ``=`` separates provider from model rather than ``:`` because Ollama model
    ids contain colons (``qwen3:8b``) and would split wrong.

    Duplicate labels are rejected: two identically-configured passes are the
    CORRELATED repetition this module exists to avoid, and worse, they would
    inflate ``agreement`` — making one model's opinion look like a consensus.

    A one-entry panel is rejected for the same reason. ``--panel opencode``
    certifies as ``external-layer-c-panel``, and that name is read at the gate
    as "several independent models agreed to look" — but a panel of one is the
    single-critic pass whose false negative INV-7 was rewritten to stop. The
    name must not be mintable by the very thing it claims to have replaced.

    Raises:
        ValueError: On an empty spec, a single-pass spec, an unknown provider,
            or a duplicate pass.
    """
    passes: list[PassSpec] = []
    seen: set[str] = set()
    for chunk in spec.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        provider, _, model = entry.partition("=")
        provider = provider.strip()
        model = model.strip() or None
        critic_providers.get_spec(provider)  # raises ValueError on unknown name
        if model is None and provider == factcheck_pack.DEFAULT_PROVIDER:
            model = factcheck_pack.DEFAULT_CLAUDE_MODEL
        p = PassSpec(provider=provider, model=model)
        if p.label in seen:
            raise ValueError(
                f"duplicate panel pass {p.label!r}; a panel needs INDEPENDENT "
                "passes — repeating one model inflates agreement without adding "
                "coverage")
        seen.add(p.label)
        passes.append(p)
    if not passes:
        raise ValueError("--panel is empty; expected e.g. 'opencode,claude'")
    if len(passes) < 2:
        raise ValueError(
            f"--panel needs at least 2 independent passes, got 1 "
            f"({passes[0].label!r}); a panel of one is a single-critic run "
            "wearing the panel's review_method. Use "
            "`--provider <name> --model <id>` for a single pass, or add a "
            "second provider (e.g. 'opencode,claude')")
    return passes


def _normalize_issue(issue: str) -> str:
    """Collapse a finding's prose to a comparison key.

    Lowercased, punctuation dropped, whitespace collapsed, truncated. Two models
    almost never phrase the same defect identically, so this only catches near-
    verbatim restatements; genuinely different wording stays as separate entries
    (union), and the (qid, severity) grouping is what carries agreement.
    """
    text = re.sub(r"[^a-z0-9 ]+", " ", issue.lower())
    return re.sub(r"\s+", " ", text).strip()[:160]


def merge_findings(per_pass: dict[str, list[dict]]) -> list[dict]:
    """Merge findings from every pass into one annotated UNION.

    ``per_pass`` maps pass label -> that pass's findings (already normalized by
    :func:`factcheck_pack.extract_findings`).

    Grouping is by ``(qid, severity)``. Within a group, near-verbatim restatements
    collapse into one entry carrying every pass that produced it; differently
    worded findings stay separate. Every entry gains:

    * ``sources``   — passes that produced this exact issue text
    * ``agreement`` — how many DISTINCT passes flagged this (qid, severity) at
      all. Measured at the group level, not per issue string, because agreement
      about *what is wrong with question X* is the useful signal and cross-model
      wording never matches.

    Collapsed duplicates keep the worst ``confidence`` seen, so a merge can only
    ever raise the alarm level, never lower it.

    Ordering is deterministic — sorted by qid, then severity rank, then issue —
    so two runs over the same inputs produce identical output regardless of which
    pass finished first.
    """
    groups: dict[tuple[str, str], dict] = {}
    for label, findings in per_pass.items():
        for f in findings:
            key = (str(f.get("qid", "")), str(f.get("severity", "")))
            group = groups.setdefault(key, {"passes": set(), "issues": {}})
            group["passes"].add(label)
            issue_key = _normalize_issue(str(f.get("issue", "")))
            entry = group["issues"].get(issue_key)
            if entry is None:
                group["issues"][issue_key] = {
                    "finding": dict(f),
                    "sources": {label},
                }
            else:
                entry["sources"].add(label)
                # Keep the worst confidence of the duplicates — merging must
                # never soften a finding.
                if (_CONFIDENCE_RANK.get(str(f.get("confidence")), 99)
                        < _CONFIDENCE_RANK.get(
                            str(entry["finding"].get("confidence")), 99)):
                    entry["finding"]["confidence"] = f.get("confidence")
                # Prefer the longer correction: an empty or terse one from a weak
                # model shouldn't win over a specific fix from a stronger one.
                if len(str(f.get("correction", ""))) > len(
                        str(entry["finding"].get("correction", ""))):
                    entry["finding"]["correction"] = f.get("correction")

    merged: list[dict] = []
    for (qid, severity), group in groups.items():
        agreement = len(group["passes"])
        for entry in group["issues"].values():
            out = dict(entry["finding"])
            out["qid"] = qid
            out["severity"] = severity
            out["sources"] = sorted(entry["sources"])
            out["agreement"] = agreement
            merged.append(out)
    merged.sort(key=lambda f: (str(f["qid"]),
                               _SEVERITY_RANK.get(str(f["severity"]), 99),
                               str(f.get("issue", ""))))
    return merged


def solo_qids(merged: list[dict]) -> list[str]:
    """Question ids where NO finding reached agreement from a second pass.

    These are the panel's ambiguous middle: either a weak model's false positive,
    or a real defect only one model was sharp enough to see. Both readings
    deserve a stronger model's opinion, which is what
    :func:`escalate` re-grades. Deliberately NOT a suppression list — the
    findings stay live in the merged union whether or not anyone escalates.
    """
    best: dict[str, int] = {}
    for f in merged:
        qid = str(f.get("qid", ""))
        best[qid] = max(best.get(qid, 0), int(f.get("agreement", 1)))
    return sorted(q for q, a in best.items() if a < 2 and q)


def run_panel(questions: list[dict], passes: list[PassSpec], batch_size: int,
              timeout: int, jobs: int = 1, source_directive: str | None = None,
              context_qids: set[str] | None = None, on_event=None) -> dict:
    """Run every pass over ``questions`` and return the merged panel result.

    Passes run SEQUENTIALLY; concurrency lives inside each pass (``jobs`` batches
    at a time). That is deliberate: running two providers at once would make the
    progress stream uninterpretable and, more practically, stack two providers'
    rate limits on top of each other for no wall-clock win that ``jobs`` doesn't
    already give.

    ``on_event(kind, **info)`` is the INV-1 progress channel — no silent waits.
    It fires as ``("pass_start", label=..., index=..., total=...)``,
    ``("batch", label=..., i=..., n=...)`` and ``("pass_done", label=...,
    findings=..., errors=..., model=...)``. Callers render it however their
    surface wants; nothing here writes to stdout.

    A pass that fails — bad key, provider down, every batch erroring — does NOT
    abort the panel: it is recorded with ``ok: False`` and its errors, and the
    remaining passes still run. A panel that dies because its cheapest member was
    misconfigured would push authors straight back to single-pass review. ``ok``
    means "this pass graded at least one question", which is stricter than "the
    call returned": ``collect_findings`` reports per-batch failures in ``errors``
    rather than raising, so a wholly dead provider returns normally with nothing
    checked.

    Returns a dict with:
      ``passes``               per-pass records (label, provider, requested and
                               OBSERVED model, counts, errors, coverage_ok)
      ``findings``             the merged union, agreement-annotated
      ``errors``/``coverage_gaps``  every pass's, prefixed with its label
      ``questions_unchecked``  MINIMUM across passes — see the module docstring
      ``solo_qids``            qids no second pass corroborated
      ``questions_sent``/``questions_graded``
    """
    per_pass: dict[str, list[dict]] = {}
    records: list[dict] = []
    all_errors: list[str] = []
    all_gaps: list[str] = []
    unchecked_per_pass: list[int] = []
    questions_sent = len(questions)
    questions_graded = questions_sent

    for index, spec in enumerate(passes):
        if on_event:
            on_event("pass_start", label=spec.label, index=index,
                     total=len(passes))

        blocked = critic_providers.preflight(spec.provider, spec.model)
        if blocked:
            records.append({
                "label": spec.label, "provider": spec.provider,
                "model_requested": spec.model, "model_observed": None,
                "findings": 0, "errors": [f"{spec.label}: {blocked}"],
                "coverage_gaps": [], "questions_unchecked": questions_graded,
                "coverage_ok": False, "ok": False,
            })
            all_errors.append(f"{spec.label}: {blocked}")
            unchecked_per_pass.append(questions_graded)
            if on_event:
                on_event("pass_done", label=spec.label, findings=0,
                         errors=1, model=None)
            continue

        def _batch_progress(i: int, n: int, _label: str = spec.label) -> None:
            if on_event:
                on_event("batch", label=_label, i=i, n=n)

        try:
            result = factcheck_pack.collect_findings(
                questions, spec.model, batch_size, timeout,
                on_batch=_batch_progress, source_directive=source_directive,
                jobs=jobs, context_qids=context_qids, provider=spec.provider)
        except (RuntimeError, ValueError) as e:
            message = f"{spec.label}: pass failed: {e}"
            records.append({
                "label": spec.label, "provider": spec.provider,
                "model_requested": spec.model, "model_observed": None,
                "findings": 0, "errors": [message], "coverage_gaps": [],
                "questions_unchecked": questions_graded,
                "coverage_ok": False, "ok": False,
            })
            all_errors.append(message)
            unchecked_per_pass.append(questions_graded)
            if on_event:
                on_event("pass_done", label=spec.label, findings=0, errors=1,
                         model=None)
            continue

        questions_graded = result["questions_graded"]
        # A pass counts as having RUN only if it actually graded something.
        # collect_findings swallows per-batch failures into `errors` rather than
        # raising, so a provider that was down for every batch still returns
        # normally — with zero questions checked. Treating that as a live pass
        # would let a panel whose every member died look merely "incomplete"
        # instead of operationally broken, which is the distinction
        # run_layer_c's "every batch failed" check already draws for one critic.
        ran = (questions_graded == 0
               or result["questions_unchecked"] < questions_graded)
        errors = [f"{spec.label}: {e}" for e in result["errors"]]
        gaps = [f"{spec.label}: {g}" for g in result["coverage_gaps"]]
        per_pass[spec.label] = result["findings"]
        all_errors.extend(errors)
        all_gaps.extend(gaps)
        unchecked_per_pass.append(result["questions_unchecked"])
        records.append({
            "label": spec.label,
            "provider": spec.provider,
            "model_requested": spec.model,
            # OBSERVED — what the provider said it used. None means unknown, and
            # unknown is recorded as unknown rather than back-filled from the
            # request; see critic_providers.CriticReply.
            "model_observed": result["model"],
            "findings": len(result["findings"]),
            "errors": errors,
            "coverage_gaps": gaps,
            "questions_unchecked": result["questions_unchecked"],
            "coverage_ok": factcheck_pack.coverage_ok(result),
            "ok": ran,
        })
        if on_event:
            on_event("pass_done", label=spec.label,
                     findings=len(result["findings"]), errors=len(errors),
                     model=result["model"])

    merged = merge_findings(per_pass)
    return {
        "passes": records,
        "findings": merged,
        "errors": all_errors,
        "coverage_gaps": all_gaps,
        # MIN, not sum or max: one pass covering everything means the pack was
        # reviewed in full at least once, whatever the other passes did.
        "questions_unchecked": min(unchecked_per_pass) if unchecked_per_pass else 0,
        "solo_qids": solo_qids(merged),
        "questions_sent": questions_sent,
        "questions_graded": questions_graded,
    }


def panel_coverage_ok(panel: dict) -> bool:
    """True when AT LEAST ONE pass individually covered every graded question.

    Not ``all()``: requiring every pass to be clean would mean adding a cheap
    third opinion could only ever make a pack harder to certify, which is a
    perverse incentive — authors would stop adding passes. Not ``any(findings)``
    either: a pass that errored out proves nothing about coverage. Exactly one
    complete pass is the honest floor, and the incomplete passes' findings are
    still merged in as bonus signal.
    """
    return any(p.get("coverage_ok") for p in panel.get("passes", []))


def panel_summary(panel: dict) -> dict:
    """Compact, JSON-serializable provenance for the certification block.

    Records what ACTUALLY ran: each pass's provider, requested and observed
    model, and whether it completed. This is the evidence that a pack was
    reviewed by N independent critics; without the observed model it would be a
    claim rather than a record.
    """
    return {
        "passes": [
            {
                "label": p["label"],
                "provider": p["provider"],
                "model_requested": p["model_requested"],
                "model_observed": p["model_observed"],
                "coverage_ok": bool(p.get("coverage_ok")),
            }
            for p in panel.get("passes", [])
        ],
        "passes_completed": sum(1 for p in panel.get("passes", [])
                                if p.get("coverage_ok")),
        "passes_attempted": len(panel.get("passes", [])),
        "solo_qids": panel.get("solo_qids", []),
    }


def duplicate_observed_models(summary: dict) -> list[str]:
    """Observed models that served MORE THAN ONE completed pass, sorted.

    A panel's whole claim is that independent models looked. Distinct *requested*
    models do not prove that: point two ``--panel local=a,local=b`` entries at one
    ``llama-server`` and both are graded by the single GGUF it happens to have
    loaded — one model, twice, minting ``external-layer-c-panel``. That is the
    same defect class as the one-entry panel, just harder to see, because the
    roster looks right and only the observed ids give it away.

    Only *completed* passes count (an errored pass graded nothing, so it cannot
    be a redundant grader), and only non-null observed ids: a provider that does
    not report its model (``opencode``) cannot be PROVEN redundant here, and for
    those :func:`parse_panel`'s distinct-request rule is the available guarantee.
    Silence is not evidence of duplication, so this reports only what it can show.
    """
    seen: dict[str, int] = {}
    for p in summary.get("passes", []):
        if not p.get("coverage_ok"):
            continue
        observed = p.get("model_observed")
        if observed:
            seen[str(observed)] = seen.get(str(observed), 0) + 1
    return sorted(model for model, n in seen.items() if n > 1)


def format_panel_report(panel: dict) -> str:
    """Human-readable panel section: per-pass roster, then agreement counts.

    Sorted most-corroborated first so the findings several independent models
    agree on lead, without any of the solo findings being hidden.
    """
    lines = ["Layer-C panel:"]
    for p in panel["passes"]:
        observed = p["model_observed"] or "unknown"
        status = "ok" if p.get("coverage_ok") else (
            "INCOMPLETE" if p.get("ok") else "FAILED")
        lines.append(
            f"  [{status}] {p['label']} -> observed model: {observed}; "
            f"{p['findings']} finding(s)"
            + (f"; {len(p['errors'])} error(s)" if p["errors"] else ""))
    merged = panel["findings"]
    corroborated = sum(1 for f in merged if int(f.get("agreement", 1)) >= 2)
    lines.append(
        f"  merged: {len(merged)} finding(s) (union) — {corroborated} "
        f"corroborated by 2+ passes, {len(merged) - corroborated} raised by one "
        "pass only")
    if panel["solo_qids"]:
        lines.append(
            f"  uncorroborated qids ({len(panel['solo_qids'])}): "
            + ", ".join(panel["solo_qids"][:20])
            + (" ..." if len(panel["solo_qids"]) > 20 else ""))
        lines.append("  -> re-grade these with a stronger provider before "
                     "accepting or dismissing them.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Standalone panel run: ``critic_panel.py <pack> --panel a,b[=model]``.

    Exists so a panel can be run and inspected on its own, separately from the
    readiness gate in ``verify_pack.py``. Exit codes match
    ``factcheck_pack.main``: 0 clean, 2 blocking findings, 1 operational error.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pack", type=Path)
    ap.add_argument("--panel", required=True,
                    help="Comma-separated provider[=model] passes, e.g. "
                         "'opencode=deepseek-v4-flash-free,local=gemma-4-12b'. "
                         f"Providers: {', '.join(critic_providers.provider_names())}")
    ap.add_argument("--batch-size", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--jobs", type=int, default=factcheck_pack.DEFAULT_JOBS)
    ap.add_argument("--strict", action="store_true",
                    help="Treat every live finding as blocking AND ignore the "
                         "pack's source_directive.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.pack.is_file():
        print(f"error: pack not found: {args.pack}", file=sys.stderr)
        return 1
    try:
        passes = parse_panel(args.panel)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        questions = factcheck_pack.load_questions(args.pack)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read pack: {e}", file=sys.stderr)
        return 1
    if not questions:
        print("error: pack has no questions", file=sys.stderr)
        return 1

    source_directive = (None if args.strict
                        else factcheck_pack.load_source_directive(args.pack))

    def _on_event(kind: str, **info) -> None:
        if args.json:
            return
        if kind == "pass_start":
            print(f"[pass {info['index'] + 1}/{info['total']}] "
                  f"{info['label']}...", file=sys.stderr)
        elif kind == "batch":
            print(f"  {info['label']}: checked batch {info['i'] + 1}/{info['n']}",
                  file=sys.stderr)
        elif kind == "pass_done":
            print(f"  {info['label']}: {info['findings']} finding(s), "
                  f"{info['errors']} error(s), model={info['model'] or 'unknown'}",
                  file=sys.stderr)

    panel = run_panel(questions, passes, args.batch_size, args.timeout,
                      jobs=args.jobs, source_directive=source_directive,
                      on_event=_on_event)

    if not any(p.get("ok") for p in panel["passes"]):
        print("error: every panel pass failed; see messages above", file=sys.stderr)
        for e in panel["errors"]:
            print(f"  ! {e}", file=sys.stderr)
        return 1

    live, waived, hygiene = factcheck_pack._apply_waivers(
        panel["findings"], factcheck_pack.load_waivers(args.pack))
    blocking = factcheck_pack.blocking_findings(live, strict=args.strict)

    if args.json:
        print(json.dumps({
            "panel": panel_summary(panel), "findings": live,
            "blocking": blocking, "waived": waived, "hygiene": hygiene,
            "errors": panel["errors"], "coverage_gaps": panel["coverage_gaps"],
            "coverage_ok": panel_coverage_ok(panel),
            "total": panel["questions_sent"],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_panel_report(panel))
        print()
        print(factcheck_pack.format_report(
            live, panel["questions_sent"], panel["errors"], None, waived,
            hygiene, panel["coverage_gaps"], strict=args.strict))
    return 2 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
