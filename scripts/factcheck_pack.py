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

Usage:
  python3 scripts/factcheck_pack.py question-packs/<course>/<pack>.json
  python3 scripts/factcheck_pack.py <pack> --batch-size 12 --model sonnet
  python3 scripts/factcheck_pack.py <pack> --dry-run     # print prompts, no LLM call
  python3 scripts/factcheck_pack.py <pack> --json        # machine-readable findings

Exit codes:
  0 — no suspect findings (or --dry-run)
  2 — suspect findings reported
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


def extract_findings(result_text: str) -> dict:
    """Parse the critic's JSON object out of its reply, tolerating ```json fences
    and surrounding prose. Returns {"findings": [...], "checked": int|None}.
    Raises ValueError if no JSON object can be located."""
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
        if not isinstance(f, dict) or not f.get("qid"):
            continue
        sev = f.get("severity", "nit")
        norm.append({
            "qid": f["qid"],
            "severity": sev if sev in SEVERITIES else "nit",
            "issue": str(f.get("issue", "")).strip(),
            "correction": str(f.get("correction", "")).strip(),
            "confidence": f.get("confidence", "medium"),
        })
    checked = obj.get("checked") if isinstance(obj, dict) else None
    return {"findings": norm, "checked": checked}


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


SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def format_report(findings: list[dict], total: int, errors: list[str]) -> str:
    lines = []
    if errors:
        lines.append("Batch errors (these questions were NOT checked):")
        lines.extend(f"  ! {e}" for e in errors)
        lines.append("")
    if not findings:
        lines.append(f"Layer-C fact-check: no suspect findings across {total} question(s).")
        return "\n".join(lines)
    findings = sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["qid"]))
    lines.append(f"Layer-C fact-check: {len(findings)} suspect finding(s) across {total} question(s).")
    lines.append("(Probabilistic — verify each against a source before editing.)")
    lines.append("")
    for f in findings:
        lines.append(f"  [{f['severity']:22s}] {f['qid']} (confidence: {f['confidence']})")
        lines.append(f"      issue:      {f['issue']}")
        if f["correction"]:
            lines.append(f"      correction: {f['correction']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pack", type=Path, help="Question pack JSON to fact-check.")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per LLM call (default 12).")
    ap.add_argument("--model", default=None,
                    help="claude --model override (e.g. sonnet, opus).")
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

    all_findings: list[dict] = []
    errors: list[str] = []
    for i, b in enumerate(batches):
        try:
            stdout = run_claude(build_prompt(b), args.model, args.timeout)
            parsed = extract_findings(parse_envelope(stdout))
            all_findings.extend(parsed["findings"])
        except (RuntimeError, ValueError) as e:
            qids = ", ".join(q.get("id", "?") for q in b)
            errors.append(f"batch {i + 1}/{len(batches)} [{qids}]: {e}")
        if not args.json:
            print(f"  checked batch {i + 1}/{len(batches)}...", file=sys.stderr)

    if errors and not all_findings and len(errors) == len(batches):
        print("error: every batch failed; see messages above", file=sys.stderr)
        for e in errors:
            print(f"  ! {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"findings": all_findings, "errors": errors,
                          "total": len(questions)}, indent=2, ensure_ascii=False))
    else:
        print(format_report(all_findings, len(questions), errors))

    return 2 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
