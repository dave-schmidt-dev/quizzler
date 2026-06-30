#!/usr/bin/env python3
"""Layer A pack-quality linter — deterministic rules, no external deps.

Implements rules L1, L2, L3, L7, L8, L9, L10, L12, L13 from the QA-pipeline plan
at ~/Documents/Projects/.plans/quizzler/2026-05-28-question-quality-gates.md.

Rules:
  L1 — Token leak (matching only): tokens from leftItems[i] appearing in
       rightItems[correctPairs[i]], OR identity-ordered correctPairs.
  L2 — Stem echo (MC / scenario_MC): a distinctive noun from the prompt
       appears in the correct option only, with a vocabulary-pattern exemption.
  L3 — Length tell (MC / scenario_MC): correct option conspicuously longer
       OR shorter than every distractor.
  L7 — Schema (all types): structural validity, no duplicate options. For
       matching, rightItems MAY be shorter than leftItems (several left items
       can share one right answer via a reused index in correctPairs); but
       rightItems must contain no duplicate entries after normalization.
  L8 — Parenthetical-justification (MC / scenario_MC): correct option's
       parenthetical paraphrases its own pre-parenthesis label.
  L9 — Intra-pack near-duplicate stem (all types): pairwise Jaccard ≥0.5
       WARN, ≥0.7 FAIL.
  L10 — Distractor coverage (MC / scenario_MC): the explanation should say
       why the wrong answers are wrong, not only why the right one is right.
       Heuristic proxy — does the explanation reference each distractor?
       Addresses NONE + no contrast language → CRITICAL; addresses SOME but
       not all → WARN. A contrast-cue guard rescues paraphrase-style coverage
       from the critical tier (see check_l10 docstring).
  L12 — Explanation presence + topic/difficulty hygiene. Missing/blank
       `explanation` on MC / scenario_MC / matching → CRITICAL (closes the
       Level-1 "reject if missing explanation" gap; L12 owns the empty-
       explanation defect that L10 deliberately ignores). Missing/blank
       `topic` or `difficulty`, or a `difficulty` outside {easy,medium,hard}
       → WARNING (advisory, so metadata gaps can't break the ratchet).
  L13 — Duplicate question `id` within a pack (pack-level): any id appearing
       more than once → CRITICAL, attributed to the duplicated id.

Waivers:
  A pack may carry an optional top-level `lint_waivers` array of
  {"rule": "Lxx", "qid": "<id>"|omitted, "reason": "<why>"} entries. A waiver
  suppresses matching findings (they move from `violations` to a separate
  `waived` list, preserving the justification) so an intentional, reviewed
  finding does not block the authoring gate. Omit `qid` to waive a rule
  pack-wide. Waivers that match nothing (stale) or carry no `reason` are
  reported back as WAIVER-rule warnings so the suppression list stays honest.

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
# Types for which an `explanation` is required (L12). true_false is excluded by
# design — its correctness is self-evident and the schema does not require one.
EXPLAINED_TYPES = {"multiple_choice", "scenario_multiple_choice", "matching"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}

# L10 contrast cues: comparative phrases that signal the explanation is
# distinguishing the correct answer FROM the other options. Deliberately NOT
# bare negations ("not", "never") — those routinely appear inside a
# correct-answer description ("the key is never reused") and would falsely
# imply distractor coverage. These are multi-token/word-boundary-ish substrings
# chosen to fire on real contrast prose ("address other threats", "unlike a
# stream cipher") while staying quiet on single-answer explanations.
CONTRAST_CUES = (
    "unlike", "rather", "whereas", "instead", "by contrast", "as opposed",
    "differ", "other threat", "other option", "the other", "others",
    "not because", "while the", "in contrast",
)


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
        # NOTE: rightItems MAY be shorter than leftItems — AUTHORING.md and
        # VALIDATION_RULES Level 1/2 explicitly allow several left items to share
        # one right answer by reusing its index in correctPairs. So length
        # equality is NOT required (removing a former false-critical here). What
        # IS required: no duplicate entries in rightItems (reuse indices instead).
        if isinstance(right, list) and right:
            seen_right: dict[str, int] = {}
            for i, o in enumerate(right):
                norm_r = normalize_option(str(o))
                if norm_r in seen_right:
                    out.append({
                        "rule": "L7", "severity": "critical",
                        "detail": f"rightItems [{seen_right[norm_r]}] and [{i}] are duplicates after normalization; reuse the index in correctPairs instead of repeating the entry",
                    })
                else:
                    seen_right[norm_r] = i
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


def check_l10_distractor_coverage(q: dict) -> list[dict]:
    """L10: does the explanation say why the wrong answers are wrong?

    A learner torn between two plausible options is helped only when the
    explanation addresses the distractor, not just justifies the key. This is
    fundamentally a semantic property, so Layer A uses a deterministic proxy:
    for each distractor, does a distinctive token from it appear in the
    explanation?

    Severity model (calibrated against a live 69-question course pack):
      • addresses NONE of the checkable distractors:
          - and the explanation has a contrast cue (e.g. "address other
            threats") → assume paraphrase coverage, do not flag. This guard
            exists because a token match cannot see paraphrase: an explanation
            that says "encryption, port hardening, and password rules" instead
            of echoing the option text "HTTPS / closing ports / complex
            passwords" is good prose a literal check would wrongly fail. The
            guard errs toward NOT blocking — the safe direction for a critical.
          - otherwise → CRITICAL (the explanation justifies the key but is
            silent on every alternative).
      • addresses SOME but not all → WARNING. No cue rescue here: warnings are
        advisory and high recall is wanted, so partials surface even when a
        contrast cue is present (asymmetry with the critical tier is deliberate).

    "Checkable" distractors are those with at least one usable token; options
    that are purely numeric/symbolic (e.g. "16", "8") carry no tokens the proxy
    can assess and are excluded from the denominator rather than counted as
    unaddressed — otherwise every numeric-answer MC would false-critical.
    """
    if q.get("type") not in MC_TYPES:
        return []
    options = q.get("options") or []
    answer = q.get("answer")
    if not options or not isinstance(answer, int) or not (0 <= answer < len(options)):
        return []  # L7 owns structural validity
    explanation = q.get("explanation")
    if not explanation:
        return []  # empty-explanation defect is owned by L12, not L10
    expl_lower = str(explanation).lower()
    correct_tokens = tokens(str(options[answer]))

    addressed = 0
    checkable = 0
    unaddressed: list[str] = []
    for i, o in enumerate(options):
        if i == answer:
            continue
        dtext = str(o)
        # Prefer tokens distinctive to this distractor (not shared with the
        # correct option); fall back to all of its tokens when it fully overlaps.
        dtoks = tokens(dtext) - correct_tokens
        if not dtoks:
            dtoks = tokens(dtext)
        if not dtoks:
            continue  # no usable tokens — proxy can't assess this distractor
        checkable += 1
        if any(t in expl_lower for t in dtoks):
            addressed += 1
        else:
            unaddressed.append(dtext)

    if checkable == 0:
        return []  # nothing the token proxy can evaluate

    has_cue = any(cue in expl_lower for cue in CONTRAST_CUES)

    if addressed == 0:
        if has_cue:
            return []  # paraphrase rescue (critical tier only)
        return [{
            "rule": "L10", "severity": "critical",
            "detail": (
                f"explanation addresses none of the {checkable} distractor(s) and "
                "uses no contrast language; it justifies the correct answer but not "
                "why the others are wrong"
            ),
        }]
    if addressed < checkable:
        preview = "; ".join(d[:40] for d in unaddressed)
        return [{
            "rule": "L10", "severity": "warning",
            "detail": (
                f"explanation appears to address {addressed}/{checkable} distractors; "
                f"not obviously addressed: {preview}"
            ),
        }]
    return []


def check_l12_explanation_and_meta(q: dict) -> list[dict]:
    """L12: explanation presence (MC / scenario_MC / matching) + topic/difficulty hygiene.

    Closes a real Level-1 gap. VALIDATION_RULES Level 1 and docs/QUESTION_SCHEMA.md
    mark `explanation` required ("reject if missing explanation"), yet no rule
    checked it — and check_l10_distractor_coverage silently disables itself on an
    empty explanation. L12 now owns that defect:

      • explanation missing/blank (after strip) on an explained type → CRITICAL.
      • topic missing/blank, difficulty missing/blank, or difficulty not in
        {easy, medium, hard} → WARNING (all question types). Kept advisory so a
        pack lacking metadata can't break the no-new-criticals ratchet.
    """
    out = []
    if q.get("type") in EXPLAINED_TYPES:
        explanation = q.get("explanation")
        if not (explanation and str(explanation).strip()):
            out.append({
                "rule": "L12", "severity": "critical",
                "detail": "missing or blank `explanation` (required for multiple_choice / scenario_multiple_choice / matching)",
            })
    topic = q.get("topic")
    if not (topic and str(topic).strip()):
        out.append({"rule": "L12", "severity": "warning", "detail": "missing or blank `topic`"})
    difficulty = q.get("difficulty")
    if not (difficulty and str(difficulty).strip()):
        out.append({"rule": "L12", "severity": "warning", "detail": "missing or blank `difficulty`"})
    elif difficulty not in VALID_DIFFICULTIES:
        out.append({
            "rule": "L12", "severity": "warning",
            "detail": f"`difficulty` is {difficulty!r}; expected one of {sorted(VALID_DIFFICULTIES)}",
        })
    return out


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


def check_l13_duplicate_ids(questions: list[dict]) -> list[dict]:
    """L13: duplicate question `id` within a single pack → CRITICAL.

    Level-1 schema validation requires unique ids ("reject if duplicate question
    IDs"). L7 only validates each id is a non-empty string per question;
    uniqueness is a pack-level property, so it lives here alongside L9. Findings
    are attributed to the duplicated id itself.
    """
    out = []
    counts: dict = {}
    for q in questions:
        qid = q.get("id")
        if qid is None:
            continue  # L7 already flags missing/non-string ids per question
        counts[qid] = counts.get(qid, 0) + 1
    for qid, count in counts.items():
        if count > 1:
            out.append({
                "qid": qid, "rule": "L13", "severity": "critical",
                "detail": f"duplicate question id {qid!r} appears {count} times in the pack",
            })
    return out


# ─── Pack driver ─────────────────────────────────────────────────────────────

PER_QUESTION_CHECKS = [
    check_l1_matching_leak,
    check_l2_stem_echo,
    check_l3_length_tell,
    check_l7_schema,
    check_l8_parenthetical,
    check_l10_distractor_coverage,
    check_l12_explanation_and_meta,
]


def _waiver_matches(w: dict, v: dict) -> bool:
    """A waiver matches a violation when the rule matches and either the waiver
    omits `qid` (pack-wide for that rule) or the qids are equal.

    The optional `qid` filter is read with `.get()` and an explicit ``is None``
    test (NOT key-presence), so an explicit ``"qid": null`` is treated the same as
    an omitted qid — pack-wide — rather than as a filter that compares against
    None and silently matches nothing. (The null-filter bug fixed in
    factcheck_pack._waiver_matches never applied here: lint's only optional filter
    is qid, and ``None`` is already its "no filter" sentinel.)"""
    if not isinstance(w, dict) or w.get("rule") != v.get("rule"):
        return False
    wq = w.get("qid")
    return wq is None or wq == v.get("qid")


def _apply_waivers(violations: list[dict], raw_waivers) -> tuple[list, list, list]:
    """Partition `violations` by the pack's `lint_waivers`.

    Returns (live, waived, hygiene):
      • live    — findings that still block (no waiver matched them).
      • waived  — findings suppressed by a waiver, annotated with `waived_reason`.
      • hygiene — WAIVER-rule warnings for stale (matched nothing) or
                  unjustified (no `reason`) waivers, so the list can't rot.
    A waiver entry: {"rule": "L10", "qid": "c1q1"|omitted, "reason": "..."}.
    """
    raw = raw_waivers if isinstance(raw_waivers, list) else []
    hygiene = []
    # A malformed entry (e.g. the bare-string mistake `["L1"]` instead of
    # `[{"rule": "L1", ...}]`) suppresses nothing AND would otherwise vanish
    # silently — flag it so the list can't rot.
    waivers = []
    for idx, w in enumerate(raw):
        if isinstance(w, dict):
            waivers.append(w)
        else:
            hygiene.append({
                "qid": None, "rule": "WAIVER", "severity": "warning",
                "detail": f"lint_waivers[{idx}] is not an object (got {type(w).__name__}); "
                          'ignored — use {"rule": "Lxx", "qid": "...", "reason": "..."}',
            })
    used: set[int] = set()
    live, waived = [], []
    for v in violations:
        idx = next((i for i, w in enumerate(waivers) if _waiver_matches(w, v)), None)
        if idx is None:
            live.append(v)
        else:
            used.add(idx)
            waived.append({**v, "waived_reason": waivers[idx].get("reason", "")})
    for i, w in enumerate(waivers):
        loc = w.get("qid")
        if i not in used:
            hygiene.append({
                "qid": loc, "rule": "WAIVER", "severity": "warning",
                "detail": f"stale lint_waiver for {w.get('rule')!r} matched no finding; remove it",
            })
        elif not (w.get("reason") and str(w.get("reason")).strip()):
            hygiene.append({
                "qid": loc, "rule": "WAIVER", "severity": "warning",
                "detail": f"lint_waiver for {w.get('rule')!r} has no `reason`; add a justification",
            })
    return live, waived, hygiene


def lint_pack(pack_path: Path) -> dict:
    """Return {pack, violations: [...], waived: [...]}.

    `violations` is the blocking set: per-question (L1/L2/L3/L7/L8/L10/L12) and
    pack-level (L9/L13) findings, minus anything matched by the pack's
    `lint_waivers`, plus WAIVER hygiene warnings. Waived findings are returned
    separately in `waived` with the author's justification.
    """
    # Render a repo-relative label when possible; fall back to the raw path for
    # out-of-tree inputs (e.g. tmp fixtures in tests) instead of crashing.
    try:
        rel = pack_path.relative_to(PROJECT_ROOT) if pack_path.is_absolute() else pack_path
    except ValueError:
        rel = pack_path
    out = {"pack": str(rel), "violations": [], "waived": []}
    try:
        data = json.loads(pack_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        out["violations"].append({
            "qid": None, "rule": "L7", "severity": "critical",
            "detail": f"could not load pack: {e}",
        })
        return out
    questions = data.get("questions") or []
    raw: list[dict] = []
    for q in questions:
        qid = q.get("id")
        for check in PER_QUESTION_CHECKS:
            for v in check(q):
                v["qid"] = qid
                raw.append(v)
    raw.extend(check_l9_near_duplicate_stems(questions))
    raw.extend(check_l13_duplicate_ids(questions))
    live, waived, hygiene = _apply_waivers(raw, data.get("lint_waivers"))
    out["violations"] = live + hygiene
    out["waived"] = waived
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
    total_waived = 0
    for r in results:
        crit = [v for v in r["violations"] if v.get("severity") == "critical"]
        warn = [v for v in r["violations"] if v.get("severity") == "warning"]
        waived = r.get("waived", [])
        total_crit += len(crit)
        total_warn += len(warn)
        total_waived += len(waived)
        waived_note = f" ({len(waived)} waived)" if waived else ""
        if not crit and not warn:
            lines.append(f"  ✓  {r['pack']}: clean{waived_note}")
        else:
            lines.append(f"  ✗  {r['pack']}: {len(crit)} critical, {len(warn)} warning{waived_note}")
            for v in crit + warn:
                qid = v.get("qid") or "(pack)"
                lines.append(f"       [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}")
        for v in waived:
            qid = v.get("qid") or "(pack)"
            reason = v.get("waived_reason") or "(no reason given)"
            lines.append(f"       [waived  ] {v['rule']} @ {qid}: {reason}")
    lines.append("")
    summary = f"Total: {total_crit} critical, {total_warn} warning across {len(results)} pack(s)."
    if total_waived:
        summary += f" ({total_waived} waived)"
    lines.append(summary)
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
