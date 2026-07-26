#!/usr/bin/env python3
"""Regenerate the human spot-check digest for a question-pack course (INV-8).

INV-8 requires a human spot-check of high-impact exam-course content before
such a course is called "done". The readable digest handed to the reviewer
is a DERIVED artifact -- fully regenerable from the course's pack JSONs plus
an enumerated qid list -- so losing a scratchpad copy is never fatal; this
script is the regeneration path.

For each requested qid this:
  - finds the question across the course directory's pack JSONs (a qid like
    `c19q18` lives in one specific chapter file; this searches all of them),
  - renders a readable markdown block: prompt, every option/pair with the
    correct one(s) clearly marked, explanation, topic, difficulty, and one
    line stating why the qid is in the digest (sourced from BUILD_NOTES.md),
  - reports (not crashes on) any qid that can't be found.

Read-and-render only: no LLM calls, no network, no scoring, no question-
content edits.

Usage:
  python3 scripts/spotcheck_digest.py
  python3 scripts/spotcheck_digest.py c19q18 c4q30
  python3 scripts/spotcheck_digest.py --course-dir question-packs/sy0-701 --out /tmp/digest.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The 11 qids BUILD_NOTES.md flags for human spot-check (net-new / highest-risk
# questions from the INV-8 Opus content review + the c19q18 data-integrity fix
# + the c4q30 round-3 re-cert catch). Used when no qids are passed on the CLI.
DEFAULT_QIDS = [
    "c19q18", "c4q30", "c4q35", "c4q36", "c4q37", "c2q28",
    "c7q31", "c19q20", "c6q28", "c8q19", "c19q21",
]

# One-line "why this qid is in the digest", sourced from BUILD_NOTES.md's
# "Findings remediated" / "Round-3 catch" notes (the INV-8 QA record). A qid
# not listed here still renders fine -- it just gets DEFAULT_REASON instead.
REASONS: dict[str, str] = {
    "c19q18": ("Data-integrity bug fix -- an earlier patch wrote to a `stem` "
               "field the app never renders; rebuilt onto `prompt`, keyed on "
               "the complete absence of a typed password (BUILD_NOTES.md)."),
    "c4q30": ("Round-3 re-cert catch on a pre-existing question -- an "
              "off-axis outlier distractor was swapped for an on-axis one "
              "(BUILD_NOTES.md)."),
    "c4q35": ("Coverage gap added by the Opus content review: key exchange, "
              "obj 1.4 (BUILD_NOTES.md)."),
    "c4q36": ("Coverage gap added by the Opus content review: tokenization "
              "vs. data masking, obj 1.4 (BUILD_NOTES.md)."),
    "c4q37": ("Coverage gap added by the Opus content review: wildcard/SAN "
              "certificates, obj 1.4 (BUILD_NOTES.md)."),
    "c2q28": ("Coverage gap added by the Opus content review: Zero-Trust "
              "threat-scope-reduction + policy-driven access control, "
              "obj 1.2 (BUILD_NOTES.md)."),
    "c7q31": ("Coverage gap added by the Opus content review: XSS, obj 2.3 "
              "(BUILD_NOTES.md)."),
    "c19q20": ("Coverage gap added by the Opus content review: "
               "interoperability, obj 4.6 (BUILD_NOTES.md)."),
    "c6q28": ("Coverage gap added by the Opus content review: unsecure "
              "networks, obj 2.2 (BUILD_NOTES.md)."),
    "c8q19": ("Coverage gap added by the Opus content review: spyware, "
              "obj 2.4 (BUILD_NOTES.md)."),
    "c19q21": ("Coverage gap added by the Opus content review: security "
               "keys, obj 4.6 (BUILD_NOTES.md)."),
}
DEFAULT_REASON = 'Flagged for human spot-check (see BUILD_NOTES.md "Remaining").'

# Fields already surfaced by name in render_question; anything else on a
# recognized question is left out of the raw-field fallback dump on purpose.
_KNOWN_FIELDS = {"id", "type", "topic", "difficulty", "prompt", "explanation",
                  "diagram", "tags"}


def load_course_index(course_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Scan every pack JSON in `course_dir` and index questions by qid.

    Returns ``(index, warnings)``: `index` maps qid -> ``{"question": ...,
    "pack_file": ..., "pack_title": ...}``; `warnings` lists any pack file
    that could not be read (invalid JSON / non-object root) or any duplicate
    qid seen across packs. Never raises on a bad pack file -- it is reported
    and skipped, same convention as ``build_manifest.read_pack_meta``.
    """
    index: dict[str, dict] = {}
    warnings: list[str] = []
    if not course_dir.is_dir():
        return index, [f"course dir not found: {course_dir}"]
    for pack_file in sorted(course_dir.glob("*.json")):
        if pack_file.name == "_course.json":
            continue
        try:
            data = json.loads(pack_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"skipping {pack_file.name}: {e}")
            continue
        if not isinstance(data, dict):
            warnings.append(f"skipping {pack_file.name}: pack root is not a JSON object")
            continue
        pack_title = data.get("title", pack_file.stem)
        questions = data.get("questions", [])
        if not isinstance(questions, list):
            continue
        for q in questions:
            if not isinstance(q, dict) or not q.get("id"):
                continue
            qid = q["id"]
            if qid in index:
                warnings.append(
                    f"duplicate qid {qid!r} in {pack_file.name} "
                    f"(keeping first seen from {index[qid]['pack_file']})"
                )
                continue
            index[qid] = {
                "question": q,
                "pack_file": pack_file.name,
                "pack_title": pack_title,
            }
    return index, warnings


def _mark(is_correct: bool) -> str:
    return "[CORRECT]" if is_correct else "[ ]"


def render_options_block(q: dict) -> list[str]:
    """Render the option/answer shape for the pack schema's scored question
    types (multiple_choice / scenario_multiple_choice / multiple_select /
    true_false / matching). An unrecognized type falls back to a raw-field
    dump instead of crashing, so a future question type still renders."""
    qtype = q.get("type")
    lines: list[str] = []
    if qtype in ("multiple_choice", "scenario_multiple_choice"):
        options = q.get("options") or []
        answer = q.get("answer")
        for i, opt in enumerate(options):
            lines.append(f"- {_mark(i == answer)} {opt}")
    elif qtype == "multiple_select":
        options = q.get("options") or []
        answers = set(q.get("answers") or [])
        for i, opt in enumerate(options):
            lines.append(f"- {_mark(i in answers)} {opt}")
    elif qtype == "true_false":
        answer = q.get("answer")
        for val in (True, False):
            lines.append(f"- {_mark(val == answer)} {val}")
    elif qtype == "matching":
        left = q.get("leftItems") or []
        right = q.get("rightItems") or []
        pairs = q.get("correctPairs") or []
        for i, l in enumerate(left):
            r_idx = pairs[i] if i < len(pairs) else None
            if isinstance(r_idx, int) and 0 <= r_idx < len(right):
                r_text = right[r_idx]
            else:
                r_text = "(unresolved)"
            lines.append(f"- {l} -> {r_text}")
    else:
        raw = {k: v for k, v in q.items() if k not in _KNOWN_FIELDS}
        lines.append(f"- (unrecognized question type {qtype!r}; raw fields: {raw})")
    return lines


def render_question(qid: str, entry: dict, reason: str) -> str:
    """One readable markdown block for a resolved qid."""
    q = entry["question"]
    lines = [
        f"## {qid}",
        "",
        f"- **Pack:** {entry['pack_title']} (`{entry['pack_file']}`)",
        f"- **Topic:** {q.get('topic', '(none)')}",
        f"- **Difficulty:** {q.get('difficulty', '(none)')}",
        f"- **Type:** {q.get('type', '(none)')}",
        "",
        f"**Prompt:** {q.get('prompt', '(missing prompt)')}",
        "",
    ]
    lines.extend(render_options_block(q))
    lines.append("")
    lines.append(f"**Explanation:** {q.get('explanation', '(missing explanation)')}")
    lines.append("")
    lines.append(f"**Why in this digest:** {reason}")
    return "\n".join(lines)


def render_missing(qid: str) -> str:
    """A block for a qid that could not be resolved -- reported, not fatal."""
    return "\n".join([
        f"## {qid}",
        "",
        "**MISSING** -- no question with this id was found in the course's pack files.",
    ])


def build_digest(course_dir: Path, qids: list[str]) -> tuple[str, list[str]]:
    """Build the full digest markdown for `qids` against `course_dir`.

    Returns ``(markdown, warnings)``. Missing qids are rendered inline as
    MISSING blocks (never raise) and also rolled into `warnings`.
    """
    index, warnings = load_course_index(course_dir)
    blocks = [
        "# Spot-Check Digest",
        "",
        f"Course: `{course_dir}` -- regenerated by `scripts/spotcheck_digest.py`.",
        f"{len(qids)} question(s) requested.",
        "",
    ]
    missing = []
    for qid in qids:
        if qid in index:
            blocks.append(render_question(qid, index[qid], REASONS.get(qid, DEFAULT_REASON)))
        else:
            blocks.append(render_missing(qid))
            missing.append(qid)
        blocks.append("")
        blocks.append("---")
        blocks.append("")
    if missing:
        warnings.append(f"{len(missing)} qid(s) not found: {', '.join(missing)}")
    return "\n".join(blocks).rstrip() + "\n", warnings


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate a readable human spot-check digest for a set "
        "of qids in a question-pack course (INV-8). Read-and-render only -- "
        "no LLM calls, no network, no scoring.")
    ap.add_argument("qids", nargs="*",
                     help=f"Question ids to include (default: the "
                     f"{len(DEFAULT_QIDS)} qids BUILD_NOTES.md flags for "
                     f"spot-check).")
    ap.add_argument("--course-dir", type=Path,
                     default=Path("question-packs/sy0-701"),
                     help="Course folder containing the pack JSONs "
                     "(default: question-packs/sy0-701).")
    ap.add_argument("--out", type=Path, default=None,
                     help="Output markdown path (default: "
                     "<course-dir>/SPOTCHECK_DIGEST.md).")
    args = ap.parse_args(argv)

    qids = args.qids if args.qids else list(DEFAULT_QIDS)
    out_path = args.out if args.out is not None else args.course_dir / "SPOTCHECK_DIGEST.md"

    digest, warnings = build_digest(args.course_dir, qids)
    for w in warnings:
        print(f"warn: {w}", file=sys.stderr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(digest, encoding="utf-8")
    print(f"wrote {out_path} ({len(qids)} qid(s) requested, {len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
