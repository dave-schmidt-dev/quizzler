#!/usr/bin/env python3
"""Layer A pack-quality linter — deterministic rules, no external deps.

Implements rules L1, L2, L3, L7, L8, L9 from the QA-pipeline plan at
~/Documents/Projects/.plans/quizzler/2026-05-28-question-quality-gates.md.

Rules:
  L1 — Token leak (matching only): tokens from leftItems[i] appearing in
       rightItems[correctPairs[i]], OR identity-ordered correctPairs.
  L2 — Stem echo (MC / scenario_MC): a distinctive noun from the prompt
       appears in the correct option only, with a vocabulary-pattern exemption.
  L3 — Length tell (MC / scenario_MC): correct option conspicuously longer
       OR shorter than every distractor.
  L7 — Schema (all types): structural validity, no duplicate options.
  L8 — Parenthetical-justification (MC / scenario_MC): correct option's
       parenthetical paraphrases its own pre-parenthesis label.
  L9 — Intra-pack near-duplicate stem (all types): pairwise Jaccard ≥0.5
       WARN, ≥0.7 FAIL.

Exit codes:
  0 — clean
  1 — at least one critical (rule failure)
  2 — warnings only

Usage:
  python3 scripts/lint_packs.py --all                     # lint every shipped pack
  python3 scripts/lint_packs.py path/to/pack.json         # lint one pack
  python3 scripts/lint_packs.py --all --json              # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"

# Stop tokens: matching-style boilerplate + common English fillers.
# Kept tight so distinctive nouns aren't accidentally suppressed.
STOP_TOKENS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "is", "are", "was", "were",
    "be", "been", "being", "by", "for", "with", "from", "or", "and", "that",
    "this", "these", "those", "what", "which", "when", "where", "who", "why",
    "how", "does", "do", "did", "you", "your", "one", "two", "not", "more",
    "most", "best", "first", "second", "third", "also", "can", "may", "will",
    "could", "should", "would", "its", "it", "as", "such", "than", "then",
    "there", "their", "they", "them", "other", "another", "example",
    "following", "describes", "describe", "describing", "term", "statement",
    "statements", "option", "options", "match", "matches", "matched", "each",
    "true", "false", "xx",
}

LENGTH_RATIO = 1.4
LENGTH_GAP_CHARS = 25
JACCARD_WARN = 0.5
JACCARD_CRITICAL = 0.7
VOCAB_STEM_RE = re.compile(
    r"^\s*what\s+(does|is|are)\b.*\b(stand for|mean|means|defined as|abbreviation|abbreviated)\b",
    re.IGNORECASE,
)
# Numeric-prefix leak for matching (e.g., left "2xx" leaks into right "200 OK").
NUM_PREFIX_RE = re.compile(r"^(\d+)x+$", re.IGNORECASE)

KNOWN_TYPES = {"multiple_choice", "scenario_multiple_choice", "matching", "true_false"}
MC_TYPES = {"multiple_choice", "scenario_multiple_choice"}


def tokens(text: str, min_len: int = 3) -> set[str]:
    """Lowercase alphanumeric tokens ≥ min_len, excluding stop tokens."""
    if not text:
        return set()
    return {
        t for t in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(t) >= min_len and t not in STOP_TOKENS
    }


def normalize_option(text: str) -> str:
    """Normalize for duplicate-option detection: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# ─── Per-question rule checks ───────────────────────────────────────────────

def check_l1_matching_leak(q: dict) -> list[dict]:
    """L1: token leak in matching. Also flags identity-ordered correctPairs."""
    if q.get("type") != "matching":
        return []
    left = q.get("leftItems") or []
    right = q.get("rightItems") or []
    pairs = q.get("correctPairs") or []
    if not left or not right or not pairs:
        return []
    if len(pairs) != len(left):
        return []  # L7 will catch this
    out = []

    # Identity-ordered: correctPairs == [0, 1, ..., n-1] → answer-by-position
    # at the JSON layer. Warning (not critical) because the runtime renderer
    # at app/index.html:1600 shuffles rightItems for display, so users never
    # see [0..n-1] at quiz time. Author-side hygiene only: scramble at write
    # time as defense-in-depth against future shuffler regressions.
    if pairs == list(range(len(pairs))):
        out.append({
            "rule": "L1",
            "severity": "warning",
            "detail": "correctPairs is identity-ordered [0..n-1]; shuffler covers this at runtime but scramble at author time as defense-in-depth",
        })

    # Per-pair token leak.
    for i, j in enumerate(pairs):
        if not isinstance(j, int) or not (0 <= j < len(right)):
            continue
        left_text = str(left[i])
        right_text = str(right[j])
        right_lower = right_text.lower()
        for t in tokens(left_text, min_len=2):
            # Numeric-prefix special case: left "2xx" leaks into right "200".
            m = NUM_PREFIX_RE.match(t)
            if m:
                prefix = m.group(1)
                # Look for the digit prefix as the start of a number in the right.
                if re.search(rf"\b{prefix}\d+\b", right_lower):
                    out.append({
                        "rule": "L1",
                        "severity": "critical",
                        "detail": f"left '{left_text}' numeric prefix '{prefix}' leaks into right '{right_text}'",
                    })
                continue
            if len(t) < 3:
                continue
            if t in right_lower:
                out.append({
                    "rule": "L1",
                    "severity": "critical",
                    "detail": f"token '{t}' from left '{left_text}' appears in right '{right_text}'",
                })
    return out


def check_l2_stem_echo(q: dict) -> list[dict]:
    """L2: distinctive prompt noun appears in the correct option only."""
    if q.get("type") not in MC_TYPES:
        return []
    prompt = q.get("prompt") or ""
    if VOCAB_STEM_RE.match(prompt):
        return []  # exemption: vocabulary-definition stems
    options = q.get("options") or []
    answer = q.get("answer")
    if not options or not isinstance(answer, int) or not (0 <= answer < len(options)):
        return []

    # Distinctive nouns: longer threshold for scenario stems.
    min_len = 6 if q.get("type") == "scenario_multiple_choice" else 4
    prompt_tokens_all = re.findall(r"[A-Za-z0-9]+", prompt.lower())
    counts: dict[str, int] = {}
    for t in prompt_tokens_all:
        counts[t] = counts.get(t, 0) + 1
    distinctive = [
        t for t, c in counts.items()
        if c == 1 and len(t) >= min_len and t not in STOP_TOKENS
    ]

    out = []
    options_lower = [str(o).lower() for o in options]
    for n in distinctive:
        in_opts = [i for i, o in enumerate(options_lower) if n in o]
        if in_opts == [answer]:
            out.append({
                "rule": "L2",
                "severity": "critical",
                "detail": f"distinctive prompt noun '{n}' appears only in the correct option",
            })
    return out


def check_l3_length_tell(q: dict) -> list[dict]:
    """L3: correct option conspicuously longer or shorter than every distractor."""
    if q.get("type") not in MC_TYPES:
        return []
    options = q.get("options") or []
    answer = q.get("answer")
    if not options or not isinstance(answer, int) or not (0 <= answer < len(options)):
        return []
    others = [len(str(o)) for i, o in enumerate(options) if i != answer]
    if not others:
        return []
    correct_len = len(str(options[answer]))
    max_other = max(others)
    min_other = min(others)
    out = []
    # Long-correct.
    if correct_len > max_other * LENGTH_RATIO and correct_len - max_other > LENGTH_GAP_CHARS:
        out.append({
            "rule": "L3",
            "severity": "critical",
            "detail": f"correct option is {correct_len} chars; longest distractor is {max_other} (>{LENGTH_RATIO}× and >{LENGTH_GAP_CHARS} char gap)",
        })
    # Short-correct (symmetric).
    if correct_len * LENGTH_RATIO < min_other and min_other - correct_len > LENGTH_GAP_CHARS:
        out.append({
            "rule": "L3",
            "severity": "critical",
            "detail": f"correct option is {correct_len} chars; shortest distractor is {min_other} (>{LENGTH_RATIO}× shorter and >{LENGTH_GAP_CHARS} char gap)",
        })
    return out


def check_l7_schema(q: dict) -> list[dict]:
    """L7: structural validity per QUESTION_SCHEMA.md."""
    out = []
    qid = q.get("id")
    if not qid or not isinstance(qid, str):
        out.append({"rule": "L7", "severity": "critical", "detail": "missing or non-string `id`"})
    qtype = q.get("type")
    if qtype not in KNOWN_TYPES:
        out.append({"rule": "L7", "severity": "critical", "detail": f"unknown type {qtype!r}; expected one of {sorted(KNOWN_TYPES)}"})
        return out  # downstream checks depend on type
    if not q.get("prompt"):
        out.append({"rule": "L7", "severity": "critical", "detail": "missing or empty `prompt`"})

    if qtype in MC_TYPES:
        options = q.get("options")
        answer = q.get("answer")
        if not isinstance(options, list) or len(options) < 2:
            out.append({"rule": "L7", "severity": "critical", "detail": "`options` must be a list of ≥2 entries"})
        else:
            # Duplicate option text after normalization.
            seen: dict[str, int] = {}
            for i, o in enumerate(options):
                norm = normalize_option(str(o))
                if norm in seen:
                    out.append({
                        "rule": "L7", "severity": "critical",
                        "detail": f"options [{seen[norm]}] and [{i}] are duplicates after normalization",
                    })
                else:
                    seen[norm] = i
            if not isinstance(answer, int) or not (0 <= answer < len(options)):
                out.append({
                    "rule": "L7", "severity": "critical",
                    "detail": f"`answer` ({answer!r}) is not a valid index into options[len={len(options)}]",
                })
    elif qtype == "matching":
        left = q.get("leftItems")
        right = q.get("rightItems")
        pairs = q.get("correctPairs")
        if not isinstance(left, list) or not left:
            out.append({"rule": "L7", "severity": "critical", "detail": "`leftItems` must be a non-empty list"})
        if not isinstance(right, list) or not right:
            out.append({"rule": "L7", "severity": "critical", "detail": "`rightItems` must be a non-empty list"})
        if isinstance(left, list) and isinstance(right, list) and len(left) != len(right):
            out.append({
                "rule": "L7", "severity": "critical",
                "detail": f"matching pairs unbalanced: {len(left)} left vs {len(right)} right",
            })
        if not isinstance(pairs, list):
            out.append({"rule": "L7", "severity": "critical", "detail": "`correctPairs` must be a list of right-side indices"})
        elif isinstance(left, list) and len(pairs) != len(left):
            out.append({
                "rule": "L7", "severity": "critical",
                "detail": f"`correctPairs` length ({len(pairs)}) ≠ leftItems length ({len(left)})",
            })
        elif isinstance(right, list):
            for i, j in enumerate(pairs):
                if not isinstance(j, int) or not (0 <= j < len(right)):
                    out.append({
                        "rule": "L7", "severity": "critical",
                        "detail": f"correctPairs[{i}] = {j!r} is not a valid index into rightItems[len={len(right)}]",
                    })
                    break
    elif qtype == "true_false":
        if not isinstance(q.get("answer"), bool):
            out.append({
                "rule": "L7", "severity": "critical",
                "detail": f"true_false `answer` must be a boolean (got {type(q.get('answer')).__name__})",
            })
    return out


def check_l8_parenthetical(q: dict) -> list[dict]:
    """L8: correct option's parenthetical paraphrases its own label."""
    if q.get("type") not in MC_TYPES:
        return []
    options = q.get("options") or []
    answer = q.get("answer")
    if not options or not isinstance(answer, int) or not (0 <= answer < len(options)):
        return []
    text = str(options[answer])
    paren_match = re.search(r"\(([^)]+)\)", text)
    if not paren_match:
        return []
    pre = text[:paren_match.start()].strip()
    inside = paren_match.group(1)
    pre_tokens = tokens(pre)
    inside_tokens = tokens(inside)
    shared = pre_tokens & inside_tokens
    if len(shared) >= 3:
        return [{
            "rule": "L8", "severity": "critical",
            "detail": f"correct option parenthetical shares {len(shared)} content words with its label ({sorted(shared)})",
        }]
    return []


# ─── Pack-level rule checks ─────────────────────────────────────────────────

def check_l9_near_duplicate_stems(questions: list[dict]) -> list[dict]:
    """L9: intra-pack pairwise Jaccard on prompt tokens.

    ≥0.5 → WARN, ≥0.7 → CRITICAL. Findings are attributed to BOTH questions
    in the pair so authors can see them under either qid.
    """
    out = []
    records = []
    for q in questions:
        prompt = q.get("prompt") or ""
        if not prompt:
            continue
        records.append((q.get("id"), q.get("type", "?"), prompt, tokens(prompt)))
    for (id1, t1, p1, tok1), (id2, t2, p2, tok2) in combinations(records, 2):
        if not tok1 or not tok2:
            continue
        union = tok1 | tok2
        if not union:
            continue
        jaccard = len(tok1 & tok2) / len(union)
        if jaccard >= JACCARD_CRITICAL:
            sev = "critical"
        elif jaccard >= JACCARD_WARN:
            sev = "warning"
        else:
            continue
        detail = f"prompt Jaccard {jaccard:.2f} with {id2!r} ({t2}); near-duplicate stems may feel like a repeat to users"
        out.append({"qid": id1, "rule": "L9", "severity": sev, "detail": detail})
        out.append({
            "qid": id2, "rule": "L9", "severity": sev,
            "detail": f"prompt Jaccard {jaccard:.2f} with {id1!r} ({t1}); near-duplicate stems may feel like a repeat to users",
        })
    return out


# ─── Pack driver ─────────────────────────────────────────────────────────────

PER_QUESTION_CHECKS = [
    check_l1_matching_leak,
    check_l2_stem_echo,
    check_l3_length_tell,
    check_l7_schema,
    check_l8_parenthetical,
]


def lint_pack(pack_path: Path) -> dict:
    """Return {pack, violations: [{qid, rule, severity, detail}, ...]}."""
    rel = pack_path.relative_to(PROJECT_ROOT) if pack_path.is_absolute() else pack_path
    out = {"pack": str(rel), "violations": []}
    try:
        data = json.loads(pack_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["violations"].append({
            "qid": None, "rule": "L7", "severity": "critical",
            "detail": f"could not load pack: {e}",
        })
        return out
    questions = data.get("questions") or []
    for q in questions:
        qid = q.get("id")
        for check in PER_QUESTION_CHECKS:
            for v in check(q):
                v["qid"] = qid
                out["violations"].append(v)
    out["violations"].extend(check_l9_near_duplicate_stems(questions))
    return out


def severity_to_exit(violations: list[dict]) -> int:
    if any(v.get("severity") == "critical" for v in violations):
        return 1
    if any(v.get("severity") == "warning" for v in violations):
        return 2
    return 0


def format_human(results: list[dict]) -> str:
    lines = []
    total_crit = 0
    total_warn = 0
    for r in results:
        crit = [v for v in r["violations"] if v.get("severity") == "critical"]
        warn = [v for v in r["violations"] if v.get("severity") == "warning"]
        total_crit += len(crit)
        total_warn += len(warn)
        if not crit and not warn:
            lines.append(f"  ✓  {r['pack']}: clean")
            continue
        lines.append(f"  ✗  {r['pack']}: {len(crit)} critical, {len(warn)} warning")
        for v in crit + warn:
            qid = v.get("qid") or "(pack)"
            lines.append(f"       [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}")
    lines.append("")
    lines.append(f"Total: {total_crit} critical, {total_warn} warning across {len(results)} pack(s).")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Layer A pack-quality linter.")
    parser.add_argument("paths", nargs="*", type=Path, help="Pack JSON files to lint.")
    parser.add_argument("--all", action="store_true",
                        help="Lint every pack under question-packs/")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    args = parser.parse_args(argv)

    pack_paths: list[Path] = []
    if args.all:
        for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name):
            if not course_dir.is_dir() or course_dir.name.startswith((".", "_")):
                continue
            for f in sorted(course_dir.glob("*.json")):
                if f.name == "_course.json":
                    continue
                pack_paths.append(f)
    pack_paths.extend(args.paths)

    if not pack_paths:
        parser.error("specify --all or one or more pack paths")

    results = [lint_pack(p) for p in pack_paths]
    exit_code = max((severity_to_exit(r["violations"]) for r in results), default=0)

    if args.json:
        print(json.dumps({"results": results, "exit_code": exit_code}, indent=2))
    else:
        print(format_human(results))

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
