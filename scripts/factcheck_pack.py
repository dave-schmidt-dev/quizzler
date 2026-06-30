#!/usr/bin/env python3
"""Layer C — LLM factual critic for question packs.

The Layer-A linter (scripts/lint_packs.py) is deterministic and token-based: it
checks a question's STRUCTURE (schema, answer-leak tells, distractor coverage,
duplicate stems) but has no domain knowledge and cannot judge whether a claim is
factually TRUE. Factual correctness is the job of this Layer-C critic, which
sends each question's keyed answer + explanation to an LLM (via the `claude` CLI)
and reports suspect factual claims with a suggested correction.

This is NOT wired into the PostToolUse hook — an LLM pass is slow (~seconds per
batch) and costs money (~$0.10+/call), so it is a deliberate, on-demand authoring
step, run before a new or substantially-changed pack is considered done. It is
also PROBABILISTIC: an LLM can be wrong (both false positives and misses), so its
output is a review aid, not a gate verdict. Treat findings as "verify this,"
spot-check exam-critical content, and cite a source before acting.

Waivers:
  A pack may carry an optional top-level `factcheck_waivers` array of
  {"qid": "<id>", "severity": "<sev>"|omitted, "issue_contains": "<text>"|omitted,
  "reason": "<why>"} entries, mirroring Layer A's `lint_waivers`. A waiver
  suppresses matching findings (they move from the blocking `findings` set to a
  separate `waived` list, preserving the justification) so a genuine critic
  false-positive does not block the readiness gate. `severity` narrows the waiver
  to one finding class; `issue_contains` (case-insensitive substring of the
  finding's `issue`) targets one specific finding on a qid without waiving every
  finding the critic raises for it. Waivers that match nothing (stale) or carry
  no `reason` are reported back as hygiene warnings so the list can't rot.

Usage:
  python3 scripts/factcheck_pack.py question-packs/<course>/<pack>.json
  python3 scripts/factcheck_pack.py <pack> --batch-size 12 --model sonnet
  python3 scripts/factcheck_pack.py <pack> --dry-run     # print prompts, no LLM call
  python3 scripts/factcheck_pack.py <pack> --json        # machine-readable findings

Exit codes:
  0 — no LIVE suspect findings (or --dry-run); some findings may be waived
  2 — LIVE suspect findings reported
  1 — operational error (pack unreadable, claude CLI missing, all batches failed)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Fields handed to the critic — everything it needs to judge correctness, nothing
# it doesn't (diagram SVG, tags, etc. are dropped to keep the prompt lean).
RELEVANT_FIELDS = (
    "id", "type", "topic", "prompt", "options", "answer",
    "leftItems", "rightItems", "correctPairs", "explanation",
)

SEVERITIES = ("wrong-answer", "misleading-explanation", "ambiguous", "nit")

PROMPT_HEADER = """\
You are a CompTIA Security+ (SY0-701) subject-matter expert fact-checking exam-prep \
questions. For EACH question below, judge ONLY factual correctness:
- Is the marked-correct answer actually correct? (`answer` is the 0-based index of the \
correct option; matching uses correctPairs[i] = the rightItems index that matches leftItems[i]; \
true_false uses a boolean `answer`.)
- Is every claim in the explanation true, including the rebuttals of the wrong options?
- Could another option also be defensibly correct (ambiguous)?

Rely on established Security+ / standard infosec knowledge. Be precise and skeptical, \
but do NOT flag acceptable textbook simplifications. Only report PROBLEMS — say nothing \
about sound questions.

Output ONLY a JSON object, no prose, no markdown fences:
{"findings": [{"qid": "...", "severity": "wrong-answer|misleading-explanation|ambiguous|nit", \
"issue": "<what is wrong>", "correction": "<the fix>", "confidence": "high|medium|low"}], \
"checked": <number of questions you checked>}

Questions:
"""


def load_questions(pack_path: Path) -> list[dict]:
    """Return the pack's questions, slimmed to the fields the critic needs."""
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    out = []
    for q in data.get("questions", []):
        out.append({k: q[k] for k in RELEVANT_FIELDS if k in q})
    return out


def batched(items: list, size: int) -> list[list]:
    """Split items into chunks of at most `size` (size <= 0 → one chunk)."""
    if size <= 0:
        return [list(items)]
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_prompt(questions: list[dict]) -> str:
    """The critic prompt for one batch."""
    return PROMPT_HEADER + json.dumps(questions, ensure_ascii=False, indent=2)


def parse_envelope(stdout: str) -> str:
    """Extract the model's text from `claude --output-format json` output.

    The envelope is {"type":"result", "result":"<text>", ...}. If stdout is not
    the envelope (e.g. raw text from a different mode), return it unchanged.
    """
    stdout = stdout.strip()
    if not stdout:
        return ""
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(env, dict) and "result" in env:
        return str(env["result"])
    return stdout


def extract_model(stdout: str) -> str | None:
    """Best-effort: the model the `claude` CLI actually used for a call, read from
    the envelope's `modelUsage` map (e.g. 'claude-opus-4-8[1m]'). None if unknown.
    Surfaced in the report so the model is never a guess; override with --model."""
    try:
        env = json.loads(stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(env, dict):
        return None
    mu = env.get("modelUsage")
    if isinstance(mu, dict) and mu:
        return ", ".join(sorted(mu.keys()))
    return env.get("model")


def extract_findings(result_text: str) -> dict:
    """Parse the critic's JSON object out of its reply, tolerating ```json fences
    and surrounding prose. Returns {"findings": [...], "checked": int|None}.
    Raises ValueError if no JSON object can be located.

    A finding that carries an `issue` but no `qid` is NOT silently dropped — in a
    mandatory gate a dropped finding is a false pass — it is kept LIVE under the
    sentinel qid "(no-qid)" (which no real waiver can accidentally match). Only
    non-dict entries and entirely-empty findings (no qid AND no issue) are
    skipped."""
    text = result_text.strip()
    if text.startswith("```"):
        # strip a leading ```json / ``` fence and the trailing ```
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in critic reply: {result_text[:200]!r}")
        obj = json.loads(text[start:end + 1])
    findings = obj.get("findings", []) if isinstance(obj, dict) else []
    norm = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        qid = f.get("qid")
        issue = str(f.get("issue", "")).strip()
        if not qid:
            if not issue:
                continue  # entirely empty / nothing actionable — safe to skip
            qid = "(no-qid)"  # keep it LIVE rather than drop a real finding
        sev = f.get("severity", "nit")
        norm.append({
            "qid": qid,
            "severity": sev if sev in SEVERITIES else "nit",
            "issue": issue,
            "correction": str(f.get("correction", "")).strip(),
            "confidence": f.get("confidence", "medium"),
        })
    checked = obj.get("checked") if isinstance(obj, dict) else None
    return {"findings": norm, "checked": checked}


def load_waivers(pack_path: Path) -> list:
    """Return the pack's top-level `factcheck_waivers` array (default []).

    Tolerant by design: a missing key or a non-list value yields [] rather than
    raising, so a malformed waivers field never breaks the critic. The pack JSON
    is re-read here (load_questions slims to RELEVANT_FIELDS and drops it)."""
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("factcheck_waivers", [])
    return raw if isinstance(raw, list) else []


def _waiver_matches(w: dict, f: dict) -> bool:
    """A waiver matches a finding when the qids are equal and any declared
    `severity` / `issue_contains` filters also match.

    `severity` (optional) narrows the waiver to one finding class.
    `issue_contains` (optional) is a case-insensitive substring of the finding's
    `issue`, letting one waiver target a single finding on a qid without
    suppressing every finding the critic raises for that qid.

    The optional filters are applied by VALUE, not key-presence: an explicit
    ``"severity": null`` / ``"issue_contains": null`` means "no filter" (matches
    by qid alone), never an active filter that compares against None and so
    silently matches nothing."""
    if not isinstance(w, dict) or w.get("qid") != f.get("qid"):
        return False
    if w.get("severity") is not None and w.get("severity") != f.get("severity"):
        return False
    issue_filter = w.get("issue_contains")
    if issue_filter and str(issue_filter).lower() not in str(f.get("issue", "")).lower():
        return False
    return True


def _apply_waivers(findings: list[dict], raw_waivers) -> tuple[list, list, list]:
    """Partition `findings` by the pack's `factcheck_waivers`.

    Returns (live, waived, hygiene), mirroring lint_packs._apply_waivers:
      • live    — findings that still block (no waiver matched them).
      • waived  — findings suppressed by a waiver, annotated with `waived_reason`.
      • hygiene — warnings for malformed (non-object), stale (matched nothing),
                  or unjustified (no `reason`) waivers, so the list can't rot.
    A waiver entry: {"qid": "c1q1", "severity": "wrong-answer"|omit,
    "issue_contains": "..."|omit, "reason": "..."}.
    """
    raw = raw_waivers if isinstance(raw_waivers, list) else []
    hygiene = []
    # A malformed entry (e.g. the bare-string mistake `["c1q1"]` instead of
    # `[{"qid": "c1q1", ...}]`) suppresses nothing AND would otherwise vanish
    # silently — flag it so the list can't rot.
    waivers = []
    for idx, w in enumerate(raw):
        if isinstance(w, dict):
            waivers.append(w)
        else:
            hygiene.append({
                "qid": None, "severity": "warning",
                "issue": f"factcheck_waivers[{idx}] is not an object (got {type(w).__name__}); "
                         'ignored — use {"qid": "...", "reason": "..."}',
            })
    used: set[int] = set()
    live, waived = [], []
    for f in findings:
        idx = next((i for i, w in enumerate(waivers) if _waiver_matches(w, f)), None)
        if idx is None:
            live.append(f)
        else:
            used.add(idx)
            waived.append({**f, "waived_reason": waivers[idx].get("reason", "")})
    for i, w in enumerate(waivers):
        loc = w.get("qid")
        if i not in used:
            hygiene.append({
                "qid": loc, "severity": "warning",
                "issue": f"stale factcheck_waiver for {loc!r} matched no finding (stale?); remove it",
            })
        elif not (w.get("reason") and str(w.get("reason")).strip()):
            hygiene.append({
                "qid": loc, "severity": "warning",
                "issue": f"factcheck_waiver for {loc!r} has no reason; add a justification",
            })
        elif w.get("severity") is None and not w.get("issue_contains"):
            # Blanket qid-only waiver: it suppresses EVERY finding the critic
            # raises for this qid, including a future genuine error it hasn't
            # raised yet. Non-blocking nudge to narrow it so it can't become a
            # silent mute button.
            hygiene.append({
                "qid": loc, "severity": "warning",
                "issue": f"factcheck_waiver for {loc!r} suppresses ALL findings on this "
                         "qid; narrow it with `issue_contains` so a future genuine error "
                         "isn't masked",
            })
    return live, waived, hygiene


def run_claude(prompt: str, model: str | None, timeout: int) -> str:
    """Invoke `claude -p --output-format json`, prompt on stdin. Returns stdout.
    Raises RuntimeError on non-zero exit or timeout."""
    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude call timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def collect_findings(questions: list[dict], model: str | None, batch_size: int,
                     timeout: int, on_batch=None) -> dict:
    """Run the Layer-C critic over `questions` in batches — the SINGLE canonical
    batch loop shared by ``main`` and ``verify_pack.run_layer_c`` (it used to be
    copy-pasted into both, and only one of the copies fed the readiness verdict).

    For each batch it accumulates the critic's findings and records a per-batch
    error string if the call fails (timeout, non-zero exit, unparseable reply). It
    ALSO records a *coverage gap* when the critic self-reports inspecting fewer
    questions than were sent (a non-None ``checked`` < ``len(batch)``): a partial
    inspection that must NOT be mistaken for "checked all, found nothing". Both
    classes feed ``questions_unchecked`` (an upper bound on questions the critic
    did not actually judge).

    ``on_batch``, if given, is called as ``on_batch(i, n)`` after each batch
    (0-based ``i``, ``n`` total batches) so a caller can print progress.

    Returns ``{"findings", "errors", "coverage_gaps", "questions_unchecked",
    "model", "questions_sent"}``. A caller treats the run as fully covered only
    when :func:`coverage_ok` — i.e. no errors AND no coverage gaps."""
    batches = batched(questions, batch_size)
    all_findings: list[dict] = []
    errors: list[str] = []
    coverage_gaps: list[str] = []
    unchecked = 0
    model_used: str | None = None
    for i, b in enumerate(batches):
        try:
            stdout = run_claude(build_prompt(b), model, timeout)
            if model_used is None:
                model_used = extract_model(stdout)
            parsed = extract_findings(parse_envelope(stdout))
            all_findings.extend(parsed["findings"])
            checked = parsed.get("checked")
            # `checked` is the critic's self-reported count; a number below the
            # batch size means it inspected only a subset.
            if (isinstance(checked, (int, float)) and not isinstance(checked, bool)
                    and checked < len(b)):
                coverage_gaps.append(
                    f"batch {i + 1}/{len(batches)}: critic reported "
                    f"checked={int(checked)} of {len(b)} questions")
                unchecked += max(0, min(len(b), len(b) - int(checked)))
        except (RuntimeError, ValueError) as e:
            qids = ", ".join(q.get("id", "?") for q in b)
            errors.append(f"batch {i + 1}/{len(batches)} [{qids}]: {e}")
            unchecked += len(b)  # a failed batch checked none of its questions
        if on_batch is not None:
            on_batch(i, len(batches))
    return {
        "findings": all_findings,
        "errors": errors,
        "coverage_gaps": coverage_gaps,
        "questions_unchecked": unchecked,
        "model": model_used,
        "questions_sent": len(questions),
    }


def coverage_ok(result: dict) -> bool:
    """True when a :func:`collect_findings` run covered every question — no batch
    errors AND no self-reported coverage gaps. The readiness gate requires this;
    ``main`` reports gaps but does not gate on them (its exit-code contract is
    unchanged)."""
    return not result.get("errors") and not result.get("coverage_gaps")


SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def format_report(findings: list[dict], total: int, errors: list[str],
                  model: str | None = None, waived: list[dict] | None = None,
                  hygiene: list[dict] | None = None,
                  coverage_gaps: list[str] | None = None) -> str:
    """Render the human report. `findings` is the LIVE (blocking) set; `waived`,
    `hygiene`, and `coverage_gaps` render as clearly-labeled NON-blocking trailing
    sections (in this standalone tool a coverage gap is advisory — the readiness
    gate in verify_pack is where it actually blocks)."""
    waived = waived or []
    hygiene = hygiene or []
    coverage_gaps = coverage_gaps or []
    lines = []
    if model:
        lines.append(f"Layer-C fact-check via {model}.")
        lines.append("")
    if errors:
        lines.append("Batch errors (these questions were NOT checked):")
        lines.extend(f"  ! {e}" for e in errors)
        lines.append("")
    if not findings:
        lines.append(f"Layer-C fact-check: no suspect findings across {total} question(s).")
    else:
        sorted_findings = sorted(
            findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["qid"]))
        lines.append(f"Layer-C fact-check: {len(sorted_findings)} suspect finding(s) across {total} question(s).")
        lines.append("(Probabilistic — verify each against a source before editing.)")
        lines.append("")
        for f in sorted_findings:
            lines.append(f"  [{f['severity']:22s}] {f['qid']} (confidence: {f['confidence']})")
            lines.append(f"      issue:      {f['issue']}")
            if f["correction"]:
                lines.append(f"      correction: {f['correction']}")
    if waived:
        lines.append("")
        lines.append(f"Waived (reviewed false-positives) — {len(waived)} finding(s), non-blocking:")
        for f in waived:
            reason = f.get("waived_reason") or "(no reason given)"
            lines.append(f"  [{f['severity']:22s}] {f['qid']}: {f['issue']}")
            lines.append(f"      reason: {reason}")
    if hygiene:
        lines.append("")
        lines.append("Waiver hygiene (clean these up; non-blocking):")
        for h in hygiene:
            qid = h.get("qid") or "(pack)"
            lines.append(f"  ! {qid}: {h['issue']}")
    if coverage_gaps:
        lines.append("")
        lines.append("Coverage note (critic inspected fewer questions than sent; "
                     "non-blocking here — blocks in verify_pack):")
        for g in coverage_gaps:
            lines.append(f"  ! {g}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pack", type=Path, help="Question pack JSON to fact-check.")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per LLM call (default 12).")
    ap.add_argument("--model", default="claude-sonnet-5",
                    help="Model for the critic (default: claude-sonnet-5 — Standard "
                    "tier handles factual recall/verification well; pinned to the full "
                    "ID for reproducibility. Pass --model opus to escalate, or an alias "
                    "like 'sonnet'/'opus' to track the CLI's latest).")
    ap.add_argument("--timeout", type=int, default=180, help="Per-batch timeout (s).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the prompts and exit; never calls the LLM.")
    ap.add_argument("--json", action="store_true", help="Emit findings as JSON.")
    args = ap.parse_args(argv)

    if not args.pack.is_file():
        print(f"error: pack not found: {args.pack}", file=sys.stderr)
        return 1
    try:
        questions = load_questions(args.pack)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read pack: {e}", file=sys.stderr)
        return 1
    if not questions:
        print("error: pack has no questions", file=sys.stderr)
        return 1

    batches = batched(questions, args.batch_size)

    if args.dry_run:
        for i, b in enumerate(batches):
            print(f"--- batch {i + 1}/{len(batches)} ({len(b)} questions) ---")
            print(build_prompt(b))
        return 0

    if not shutil.which("claude"):
        print("error: `claude` CLI not on PATH; cannot run the Layer-C critic.",
              file=sys.stderr)
        return 1

    # The canonical batch loop now lives in collect_findings (shared with
    # verify_pack.run_layer_c). main keeps its existing behavior: per-batch
    # progress on stderr (human mode), error/clean reporting, and exit 2 iff
    # there are LIVE findings.
    progress = None if args.json else (
        lambda i, n: print(f"  checked batch {i + 1}/{n}...", file=sys.stderr))
    result = collect_findings(questions, args.model, args.batch_size, args.timeout,
                              on_batch=progress)
    all_findings = result["findings"]
    errors = result["errors"]
    coverage_gaps = result["coverage_gaps"]
    model_used = result["model"]

    if errors and not all_findings and len(errors) == len(batches):
        print("error: every batch failed; see messages above", file=sys.stderr)
        for e in errors:
            print(f"  ! {e}", file=sys.stderr)
        return 1

    # Apply the pack's factcheck_waivers: live findings still block (exit 2),
    # waived findings are reported but non-blocking, hygiene warnings keep the
    # waiver list honest. The total/clean-message logic uses LIVE findings only.
    live, waived, hygiene = _apply_waivers(all_findings, load_waivers(args.pack))

    if args.json:
        print(json.dumps({"model": model_used, "findings": live,
                          "waived": waived, "hygiene": hygiene,
                          "errors": errors, "coverage_gaps": coverage_gaps,
                          "total": len(questions)},
                         indent=2, ensure_ascii=False))
    else:
        print(format_report(live, len(questions), errors, model_used, waived,
                            hygiene, coverage_gaps))

    return 2 if live else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
