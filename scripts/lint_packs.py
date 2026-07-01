#!/usr/bin/env python3
"""Layer A pack-quality linter — deterministic rules, no external deps.

Implements rules L1-L3, L7-L10, L12-L17 and L20 from the QA-pipeline plan
at ~/Documents/Projects/.plans/quizzler/2026-05-28-question-quality-gates.md
and the 2026-06-29 pack-QA audit candidates in TASKS.md (Tasks 14-21).

Token matching is WORD-BOUNDARY based (see `_word_in`), not raw substring, so
"port" no longer matches "Reporting" and "attack" no longer matches "attacker"
(L1/L2/L10 precision pass, Task 18). L1 keeps substring matching only for short
all-caps acronym left-tokens so an acronym that is a prefix of a longer term
(DNS -> DNSSEC) still flags.

Rules:
  L1 — Token leak (matching only): tokens from leftItems[i] appearing in
       rightItems[correctPairs[i]], OR identity-ordered correctPairs.
  L2 — Stem echo (MC / scenario_MC): a distinctive noun from the prompt
       appears in the correct option only, with a vocabulary-pattern exemption.
  L3 — Length tell (MC / scenario_MC): correct option conspicuously longer
       OR shorter than every distractor (CRITICAL), plus a softer WARNING tier
       when the correct option is the single strictly-longest and exceeds the
       MEAN distractor length by >=25%.
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
  L14 — Meta-distractor (MC / scenario_MC): an "all/none/both/any of the
       above/following" option → WARNING (gameable). A position-referential
       option ("Both A and B", "A and C", "options 1 and 3") → CRITICAL: the
       renderer shuffles options at display time, so a position reference points
       at the wrong option — a real correctness bug.
  L15 — Matching near-duplicate options (matching): pairwise token-Jaccard over
       leftItems and over rightItems (the L9 machinery applied to options).
       WARN at moderate overlap, CRITICAL at high; a min-token guard skips very
       short items so 2-3-word options don't false-fire.
  L16 — Answer-position distribution (pack-level): within an option-count group
       (e.g. all 4-option MC), a highly non-uniform correct-index distribution
       (>70% in one slot with >=5 items) → WARNING. NEVER critical — the
       renderer shuffles at display time, so this is an authoring-hygiene smell.
  L17 — true_false tells + balance: (a) an absolute qualifier (always/never/
       all/none/every/only/cannot/guaranteed) in a False-keyed true_false
       statement → WARNING (the "absolutes are false" giveaway). (b) pack-level
       T/F key imbalance (minority share <30% with >=5 items) → WARNING. Both
       advisory, never critical.
  L20 — Acronym-expansion leak (matching): the correctly paired right item
       embeds the left acronym's EXPANSION (MD5 -> "message-digest",
       ECC -> "curve") though the acronym STRING itself is absent, so L1's
       literal check misses it. WARNING — a curated-dictionary surface-overlap
       heuristic (see ACRONYM_EXPANSIONS); unknown acronyms and synonym-leaks
       route to Layer B/C.
  L21 — Low-priority deterministic (MC / scenario_MC): (a) a
       scenario_multiple_choice prompt below SCENARIO_MIN_WORDS words → WARNING
       (bare recall mislabeled as a scenario); (b) a diagram that leaks the
       answer (a distinctive correct-option token in the markup but not the
       distractors) → CRITICAL, or a diagram with no `diagram_alt` → WARNING.
       The article a/an agreement sub-check is DEFERRED (no pack exercises it).

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
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"

# Stop tokens: matching-style boilerplate + common English fillers.
# Kept tight so distinctive nouns aren't accidentally suppressed. The second
# block (use..some) was added in the Task-18 precision pass: tech-filler verbs
# and connectives that, with word-boundary matching, would otherwise survive as
# "distinctive" L2 nouns or count as L10 distractor coverage.
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
    # Task-18 additions: filler verbs/connectives.
    "use", "used", "using", "via", "per", "because", "between", "through",
    "during", "before", "after", "while", "about", "into", "given", "make",
    "made", "both", "only", "many", "some",
}

LENGTH_RATIO = 1.4
LENGTH_GAP_CHARS = 25
# L3 warning tier (Task 19): a softer signal than the critical. Pairs a
# mean-relative ratio with a modest absolute floor (half the critical's 25-char
# gap) so trivial-length differences (e.g. 15 vs 12 chars) don't fire.
LENGTH_WARN_RATIO = 1.25
LENGTH_WARN_GAP_CHARS = 12
JACCARD_WARN = 0.5
JACCARD_CRITICAL = 0.7
# L9 min-token guard (Task 19): a stem with fewer than this many content tokens
# (after stop-removal) cannot reach the CRITICAL tier on a couple of shared
# words — it is capped at WARNING instead.
L9_MIN_CRIT_TOKENS = 5
# L15 matching near-duplicate options. Tuned higher than L9's 0.5/0.7 because
# matching options are short and naturally share a domain noun ("digital
# signature", "private key"); the min-token guard skips items below the floor.
JACCARD_L15_WARN = 0.6
JACCARD_L15_CRITICAL = 0.8
L15_MIN_TOKENS = 3
# L16 answer-position distribution.
L16_MIN_GROUP = 5
L16_SKEW = 0.70
# L17 true_false: absolute-qualifier wordlist + balance thresholds.
TF_ABSOLUTES = ("always", "never", "all", "none", "every", "only", "cannot", "guaranteed")
L17_MIN_TF = 5
L17_MINORITY = 0.30
# L21(a) scenario floor: a scenario_multiple_choice prompt below this word count
# is almost certainly bare recall mislabeled as a scenario. Set well under the
# live corpus minimum (28 words) so it only catches genuinely bare prompts.
SCENARIO_MIN_WORDS = 15
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
# imply distractor coverage. These are PHRASE-LEVEL substrings chosen to fire on
# real contrast prose ("address other threats", "unlike a stream cipher") while
# staying quiet on single-answer explanations.
#
# Task-19 tightening: the over-broad generic cues that used to rescue genuinely
# uncovered single-answer explanations were DROPPED — "differ", "rather",
# "instead", "others", "the other", "while the". Kept are the phrase-level cues
# (which only appear in real contrast prose) plus the calibrated "other threat"
# phrase that legitimately rescues paraphrase coverage (e.g. c5q4's "encryption,
# port hardening, and password rules address other threats").
CONTRAST_CUES = (
    "unlike", "whereas", "by contrast", "in contrast", "as opposed",
    "not because", "other threat", "other option", "other answer", "other choice",
)

# ─── L14 meta-distractor patterns ───────────────────────────────────────────
# "all/none/both/any of the (above|following)" — gameable meta-options (WARNING).
META_OPTION_RE = re.compile(
    r"^(all|none|both|any)\s+of\s+(the\s+|these\s+)?(above|following)$", re.IGNORECASE,
)
# Position-referential options ("Both A and B", "A and C", "options 1 and 3",
# "1 and 3"). CRITICAL: shuffleOptions reorders options at render, so a position
# reference points at the wrong option. Bare-number form is restricted to single
# digits so multi-digit numeric answers ("16 and 32") don't false-fire.
POSITION_REF_RE = re.compile(
    r"^\s*(both\s+[a-d]\s+and\s+[a-d]"
    r"|[a-d]\s+and\s+[a-d]"
    r"|options?\s+\d+\s+and\s+\d+"
    r"|[1-9]\s+and\s+[1-9])\s*$",
    re.IGNORECASE,
)

# ─── L20 curated acronym -> distinctive expansion keywords ──────────────────
# This is the linter's ONE piece of domain data: a small security/networking
# table used to catch matching leaks where the correct right-item embeds the
# left acronym's EXPANSION (so the pair is guessable by surface overlap though
# the acronym STRING is absent and L1's literal check misses it). WARNING, not
# critical: it is a surface-overlap heuristic (a few keywords like "standard" /
# "mail" are generic), and unknown acronyms / synonym-leaks (verify/confirm)
# route to Layer B/C. Extend per-course as new acronym families appear; an
# acronym absent from this table is simply not checked (under-fire, never a
# false-fire). Empirically FP-free across the live + archived corpus.
ACRONYM_EXPANSIONS = {
    "MD5": ("message", "digest"),
    "ECC": ("elliptic", "curve"),
    "AES": ("advanced", "standard"),
    "DES": ("data",),
    "RSA": ("rivest", "shamir", "adleman"),
    "SHA": ("hash",),
    "SRTP": ("real-time", "realtime"),
    "SMIME": ("mail",),
    "TPM": ("trusted", "platform"),
    "HSM": ("hardware", "module"),
    "SSH": ("shell",),
    "HTTPS": ("hypertext",),
    "IPSEC": ("internet",),
    "OCSP": ("status",),
}


def tokens(text: str, min_len: int = 3) -> set[str]:
    """Lowercase alphanumeric tokens ≥ min_len, excluding stop tokens."""
    if not text:
        return set()
    return {
        t for t in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(t) >= min_len and t not in STOP_TOKENS
    }


def _word_in(text_lower: str, token: str) -> bool:
    """Whole-word presence test (word-boundary regex), not raw substring.

    The Task-18 precision fix: a raw ``token in text`` containment test fires on
    coincidental substrings — "port" inside "Reporting", "host" inside "Ghost",
    "attack" inside "attacker", "port" inside "important". Anchoring the token
    with ``\\b`` matches it only as a whole word. ``text_lower`` and ``token``
    are expected to already be lowercased.
    """
    return re.search(r"\b" + re.escape(token) + r"\b", text_lower) is not None


def _word_prefix_in(text_lower: str, tok: str) -> bool:
    """Prefix-anchored word-boundary test: ``\\b<tok>`` matches tok at the START
    of a word but does not require a word boundary after it.

    Used for short all-caps acronym tokens so that DNS matches DNSSEC (prefix
    match intended) while ARP does NOT match "sharp" (``\\barp`` requires a word
    boundary before 'a', which is absent inside "sharp").
    ``text_lower`` and ``tok`` are expected to already be lowercased.
    """
    return re.search(r"\b" + re.escape(tok), text_lower) is not None


def is_int_not_bool(x) -> bool:
    """True iff x is an int but not a bool (bool is a subclass of int in Python).

    Used everywhere an answer index or correctPairs index is validated so that
    ``answer: true`` (YAML/JSON boolean) is rejected instead of silently coercing
    to index 1.
    """
    return isinstance(x, int) and not isinstance(x, bool)


def is_acronym(raw: str) -> bool:
    """True iff *raw* (original-case token) looks like a short all-caps acronym.

    Factored from the L1 inline test (all-caps, alphabetic, <=5 chars) so the
    L1 acronym branch has a named predicate. L20 does NOT use this: it needs the
    looser ``raw.isupper()`` gate on its own so slash-acronyms like S/MIME (which
    fail ``isalpha()``) still qualify.
    """
    return raw.isupper() and raw.isalpha() and len(raw) <= 5


def normalize_option(text: str) -> str:
    """Normalize for duplicate-option detection: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _prompt_str(q: dict) -> str:
    """Coerce q['prompt'] to str, returning '' when absent or non-string."""
    return str(q.get("prompt") or "")


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

    # Per-pair token leak. Word-boundary matching (Task 18) so "port" no longer
    # leaks into "Reporting"; the EXCEPTION is short all-caps acronym left-tokens,
    # which keep substring matching so an acronym that is a prefix of a longer
    # term (DNS -> DNSSEC) still flags. Iterating raw tokens (case preserved)
    # rather than tokens() lets us detect the all-caps acronym shape.
    for i, j in enumerate(pairs):
        if not is_int_not_bool(j) or not (0 <= j < len(right)):
            continue
        left_text = str(left[i])
        right_text = str(right[j])
        right_lower = right_text.lower()
        seen: set[str] = set()
        for raw in re.findall(r"[A-Za-z0-9]+", left_text):
            t = raw.lower()
            if t in seen:
                continue
            seen.add(t)
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
            if len(t) < 3 or t in STOP_TOKENS:
                continue
            # is_acronym uses isalpha() so slash-acronyms (S/MIME) are excluded here
            # and handled via the L20 path instead. _word_prefix_in gives a leading-\b
            # anchor so \barp fires on standalone ARP/ARPA but not on "sharp".
            leaked = _word_prefix_in(right_lower, t) if is_acronym(raw) else _word_in(right_lower, t)
            if leaked:
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
    prompt = _prompt_str(q)
    if VOCAB_STEM_RE.match(prompt):
        return []  # exemption: vocabulary-definition stems
    options = q.get("options") or []
    answer = q.get("answer")
    if not options or not is_int_not_bool(answer) or not (0 <= answer < len(options)):
        return []

    # Distinctive nouns: longer threshold for scenario stems. The plain-MC floor
    # was bumped 4 -> 5 (Task 18) so generic 4-char words ("data", "user") stop
    # registering as distinctive after the switch to word-boundary matching.
    min_len = 6 if q.get("type") == "scenario_multiple_choice" else 5
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
        # Word-boundary (Task 18): the noun must appear as a whole word, not as a
        # coincidental substring of a longer option word.
        in_opts = [i for i, o in enumerate(options_lower) if _word_in(o, n)]
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
    if not options or not is_int_not_bool(answer) or not (0 <= answer < len(options)):
        return []
    others = [len(str(o)) for i, o in enumerate(options) if i != answer]
    if not others:
        return []
    correct_len = len(str(options[answer]))
    max_other = max(others)
    min_other = min(others)
    out = []
    # Long-correct (critical).
    long_critical = correct_len > max_other * LENGTH_RATIO and correct_len - max_other > LENGTH_GAP_CHARS
    if long_critical:
        out.append({
            "rule": "L3",
            "severity": "critical",
            "detail": f"correct option is {correct_len} chars; longest distractor is {max_other} (>{LENGTH_RATIO}× and >{LENGTH_GAP_CHARS} char gap)",
        })
    # Short-correct (symmetric, critical).
    if correct_len * LENGTH_RATIO < min_other and min_other - correct_len > LENGTH_GAP_CHARS:
        out.append({
            "rule": "L3",
            "severity": "critical",
            "detail": f"correct option is {correct_len} chars; shortest distractor is {min_other} (>{LENGTH_RATIO}× shorter and >{LENGTH_GAP_CHARS} char gap)",
        })
    # Warning tier (Task 19): correct option is the SINGLE strictly-longest AND
    # exceeds the MEAN distractor length by >=25% (plus a modest absolute floor).
    # Suppressed when the long-critical already fired (it would be redundant).
    if not long_critical:
        mean_other = sum(others) / len(others)
        # `others` excludes the correct option, so `max_other` is the longest
        # DISTRACTOR; the correct option is the strict single-longest exactly
        # when it is longer than every distractor.
        is_single_longest = correct_len > max_other
        if (is_single_longest
                and correct_len >= mean_other * LENGTH_WARN_RATIO
                and correct_len - mean_other >= LENGTH_WARN_GAP_CHARS):
            out.append({
                "rule": "L3",
                "severity": "warning",
                "detail": (
                    f"correct option is {correct_len} chars and the single longest; "
                    f"mean distractor is {mean_other:.0f} (>={LENGTH_WARN_RATIO}× the mean)"
                ),
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
            if not is_int_not_bool(answer) or not (0 <= answer < len(options)):
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
                if not is_int_not_bool(j) or not (0 <= j < len(right)):
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
    if not options or not is_int_not_bool(answer) or not (0 <= answer < len(options)):
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
    if not options or not is_int_not_bool(answer) or not (0 <= answer < len(options)):
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
        # correct option). When the distractor fully overlaps the correct option
        # (dtoks is empty after the set-difference), skip it entirely — the proxy
        # cannot assess it. The old fallback to the distractor's own tokens was
        # wrong: it let the correct-answer explanation "cover" the distractor by
        # referencing correct-option tokens that the distractor merely echoes.
        dtoks = tokens(dtext) - correct_tokens
        if not dtoks:
            continue  # skip — proxy can't assess (fully overlaps correct option)
        checkable += 1
        # Word-boundary (Task 18): a distractor counts as addressed only when one
        # of its tokens appears as a WHOLE word in the explanation. The old
        # substring test wrongly counted "Replay attack" as covered because
        # "attack" appears inside "attacker", and "port scan" as covered because
        # "port" appears inside "important".
        if any(_word_in(expl_lower, t) for t in dtoks):
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


def check_l14_meta_distractor(q: dict) -> list[dict]:
    """L14: meta-options and position-referential options (MC / scenario_MC).

      • "all/none/both/any of the (above|following)" → WARNING. These are
        gameable: a test-wise student picks "all of the above" when any two
        options look right, and they interact badly with option shuffling.
      • a position-referential option ("Both A and B", "A and C", "options 1 and
        3", "1 and 3") → CRITICAL. The renderer's shuffleOptions reorders options
        at display time, so a reference to a fixed position points at the WRONG
        option once shuffled — a genuine correctness bug, not just a style smell.
    """
    if q.get("type") not in MC_TYPES:
        return []
    out = []
    for o in q.get("options") or []:
        s = re.sub(r"\s+", " ", str(o).strip())
        if META_OPTION_RE.match(s):
            out.append({
                "rule": "L14", "severity": "warning",
                "detail": f"meta-option {s!r} is gameable (all/none/both-of-the-above style)",
            })
        elif POSITION_REF_RE.match(s):
            out.append({
                "rule": "L14", "severity": "critical",
                "detail": (
                    f"position-referential option {s!r}: shuffleOptions reorders "
                    "options at render time, so a position reference points at the "
                    "wrong option"
                ),
            })
    return out


def check_l15_matching_near_dup(q: dict) -> list[dict]:
    """L15: near-duplicate options within a matching question.

    Level 4 says reject a matching set whose choices are so similar the learner
    guesses between wording variants rather than concepts. L7 only catches exact
    duplicate rightItems and L9 runs on prompts, so L15 applies the L9 Jaccard
    machinery to leftItems and to rightItems: WARN at moderate overlap, CRITICAL
    at high. A min-token guard (skip items with <L15_MIN_TOKENS content tokens)
    keeps 2-3-word options — which naturally share a domain noun — from
    false-firing; the thresholds are tuned higher than L9 for the same reason.
    """
    if q.get("type") != "matching":
        return []
    out = []
    for side in ("leftItems", "rightItems"):
        items = q.get(side) or []
        recs = [(i, tokens(str(x))) for i, x in enumerate(items)]
        label = side[:-5]  # "leftItems" -> "left", "rightItems" -> "right"
        for (i, ti), (j, tj) in combinations(recs, 2):
            if len(ti) < L15_MIN_TOKENS or len(tj) < L15_MIN_TOKENS:
                continue  # too short to judge reliably
            union = ti | tj
            if not union:
                continue
            jaccard = len(ti & tj) / len(union)
            if jaccard >= JACCARD_L15_CRITICAL:
                sev = "critical"
            elif jaccard >= JACCARD_L15_WARN:
                sev = "warning"
            else:
                continue
            out.append({
                "rule": "L15", "severity": sev,
                "detail": (
                    f"{side}[{i}] and [{j}] token-Jaccard {jaccard:.2f}; "
                    f"near-duplicate {label} options make the learner pick between "
                    "wording variants rather than concepts"
                ),
            })
    return out


def check_l17_true_false_tell(q: dict) -> list[dict]:
    """L17(a): absolute qualifier in a False-keyed true_false statement → WARNING.

    "Absolutes are usually false" is one of the oldest test-taking heuristics: an
    always/never/all/none/every/only/cannot/guaranteed in a statement that is
    keyed False lets a student guess correctly without knowing the material. The
    detection is deterministic but the gameability inference is heuristic, so this
    is advisory (WARNING), never critical. (A True-keyed absolute is fine — the
    statement may be legitimately absolute, e.g. "a one-time pad key is never
    reused".)
    """
    if q.get("type") != "true_false" or q.get("answer") is not False:
        return []
    prompt_lower = _prompt_str(q).lower()
    hits = sorted({w for w in TF_ABSOLUTES if _word_in(prompt_lower, w)})
    if hits:
        return [{
            "rule": "L17", "severity": "warning",
            "detail": (
                f"absolute qualifier(s) {hits} in a False-keyed true_false statement; "
                "'absolutes are usually false' is a common giveaway"
            ),
        }]
    return []


def check_l20_acronym_expansion_leak(q: dict) -> list[dict]:
    """L20: matching leak where the correct right item embeds the left acronym's
    EXPANSION (matching only) → WARNING.

    L1 catches a literal acronym string leaking across a pair, but misses the
    common case where the right side paraphrases the acronym's expansion — MD5 ->
    "message-digest hash", ECC -> "curve mathematics", SRTP -> "real-time", S/MIME
    -> "mail". The pair is then guessable by surface overlap with no domain
    knowledge, even though the acronym string itself is absent.

    This is the linter's only domain-aware rule: a curated ACRONYM_EXPANSIONS
    table maps each known acronym to distinctive expansion keywords, and the rule
    flags a correctly paired right item that contains one as a whole word. It is a
    surface-overlap heuristic — a few keywords are generic ("standard", "mail") —
    so it is WARNING, not critical, and it is deliberately INCOMPLETE: an acronym
    absent from the table is not checked (under-fire, never a false-fire), and the
    semantic synonym-leak variant (e.g. left "verify" vs right "confirm") is out
    of scope for Layer A and routes to the Layer B/C critic.
    """
    if q.get("type") != "matching":
        return []
    left = q.get("leftItems") or []
    right = q.get("rightItems") or []
    pairs = q.get("correctPairs") or []
    if not left or not right or not pairs or len(pairs) != len(left):
        return []
    out = []
    for i, j in enumerate(pairs):
        if not is_int_not_bool(j) or not (0 <= j < len(right)):
            continue
        right_lower = str(right[j]).lower()
        seen: set[str] = set()
        for raw in re.findall(r"[A-Za-z0-9/]+", str(left[i])):
            if not raw.isupper():
                continue  # only acronym-shaped tokens; .isupper() rejects Capitalized words
            for key in {raw.upper(), re.sub(r"[^A-Z0-9]", "", raw.upper())}:
                if key in seen or not (2 <= len(key) <= 6):
                    continue
                seen.add(key)
                expansions = ACRONYM_EXPANSIONS.get(key)
                if not expansions:
                    continue
                leaked = [k for k in expansions if _word_in(right_lower, k)]
                if leaked:
                    out.append({
                        "rule": "L20", "severity": "warning",
                        "detail": (
                            f"acronym '{raw}' leaks its expansion {leaked} into the "
                            f"correctly paired right item '{right[j]}'; pairable by "
                            "surface overlap without domain knowledge"
                        ),
                    })
    return out


def _diagram_markup(diagram) -> str:
    """Flatten a diagram field to searchable text.

    A diagram may be a raw string (SVG / Mermaid / plain text) or an object with
    `svg` / `text` / `mermaid` (and similar) string fields. Returns the
    concatenated string content, or "" when there is nothing to search.
    """
    if isinstance(diagram, str):
        return diagram
    if isinstance(diagram, dict):
        return " ".join(str(v) for v in diagram.values() if isinstance(v, str))
    if isinstance(diagram, list):
        return " ".join(_diagram_markup(v) for v in diagram)
    return ""


def check_l21_low_priority(q: dict) -> list[dict]:
    """L21: low-priority deterministic checks (Task 21).

      (a) A scenario_multiple_choice prompt below SCENARIO_MIN_WORDS words →
          WARNING: a "scenario" that does not set up a situation is bare recall
          mislabeled as a scenario.
      (b) Diagram answer-leak (MC / scenario_MC with a diagram). When a diagram
          is present, a distinctive token of the CORRECT option that appears in
          the diagram markup but in none of the distractors leaks the answer →
          CRITICAL. A diagram with no `diagram_alt` text → WARNING (accessibility
          + a prompt to review the visual for leaks). Latent today — no shipped
          pack uses diagrams — but enforced the moment one does.

    (c) Article a/an agreement (a stem ending in a standalone "a"/"an" before a
    blank where exactly one option agrees) is DEFERRED: no shipped pack exercises
    it, so shipping untested code would add dead weight. Revisit when an
    article-blank stem first appears. # TODO(L21c)
    """
    out = []
    qtype = q.get("type")

    # (a) scenario word-count floor.
    if qtype == "scenario_multiple_choice":
        word_count = len(_prompt_str(q).split())
        if word_count < SCENARIO_MIN_WORDS:
            out.append({
                "rule": "L21", "severity": "warning",
                "detail": (
                    f"scenario_multiple_choice prompt is only {word_count} words "
                    f"(<{SCENARIO_MIN_WORDS}); a scenario should set up a situation — "
                    "this looks like bare recall mislabeled as a scenario"
                ),
            })

    # (b) diagram answer-leak + missing alt-text.
    diagram = q.get("diagram")
    if diagram:
        markup = _diagram_markup(diagram).lower()
        if markup and qtype in MC_TYPES:
            options = q.get("options") or []
            answer = q.get("answer")
            if options and is_int_not_bool(answer) and 0 <= answer < len(options):
                distractor_tokens: set[str] = set()
                for i, o in enumerate(options):
                    if i != answer:
                        distractor_tokens |= tokens(str(o))
                leaked = sorted(
                    t for t in tokens(str(options[answer]))
                    if t not in distractor_tokens and _word_in(markup, t)
                )
                if leaked:
                    out.append({
                        "rule": "L21", "severity": "critical",
                        "detail": (
                            f"diagram markup contains distinctive token(s) {leaked} of "
                            "the correct option but none of the distractors — the "
                            "diagram leaks the answer"
                        ),
                    })
        if not (q.get("diagram_alt") and str(q.get("diagram_alt")).strip()):
            out.append({
                "rule": "L21", "severity": "warning",
                "detail": "question has a diagram but no `diagram_alt` text",
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
        prompt = _prompt_str(q)
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
        # Min-token guard (Task 19): a short stem (<5 content tokens) cannot reach
        # CRITICAL on a couple of shared words — two terse prompts like "What is
        # phishing?" / "What is pharming?" overlap heavily by token ratio without
        # being true duplicates. Such pairs are capped at WARNING.
        short_stem = len(tok1) < L9_MIN_CRIT_TOKENS or len(tok2) < L9_MIN_CRIT_TOKENS
        if jaccard >= JACCARD_CRITICAL and not short_stem:
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


def check_l16_answer_position(questions: list[dict]) -> list[dict]:
    """L16: non-uniform correct-answer position within an option-count group.

    A constant per-chapter answer index is a reliable rushed-batch smell and
    becomes gameable on any surface that bypasses the renderer's shuffle (export,
    seeded review, print). Within each option-count group (all 4-option MC, all
    5-option MC, …) with at least L16_MIN_GROUP items, if more than L16_SKEW of
    the correct indices fall in one slot, emit a WARNING. NEVER critical — the
    renderer shuffles at display time, so this is authoring hygiene, not a
    live-play exploit. Attributed to the pack (qid=None).
    """
    out = []
    groups: dict[int, list[int]] = defaultdict(list)
    for q in questions:
        if q.get("type") in MC_TYPES:
            options = q.get("options") or []
            answer = q.get("answer")
            if is_int_not_bool(answer) and options and 0 <= answer < len(options):
                groups[len(options)].append(answer)
    for n, indices in sorted(groups.items()):
        if len(indices) < L16_MIN_GROUP:
            continue
        slot, count = Counter(indices).most_common(1)[0]
        share = count / len(indices)
        if share > L16_SKEW:
            out.append({
                "qid": None, "rule": "L16", "severity": "warning",
                "detail": (
                    f"{count}/{len(indices)} ({share:.0%}) of {n}-option questions key "
                    f"slot {slot}; non-uniform answer-position distribution "
                    "(advisory — the renderer shuffles at display time)"
                ),
            })
    return out


def check_l17_tf_balance(questions: list[dict]) -> list[dict]:
    """L17(b): imbalanced true_false key split (pack-level) → WARNING.

    With at least L17_MIN_TF true_false items, if the minority answer (True or
    False) holds less than L17_MINORITY of them, the pack is guessable by always
    picking the majority. Advisory only (never critical); attributed to the pack.
    """
    tf = [q for q in questions if q.get("type") == "true_false" and isinstance(q.get("answer"), bool)]
    if len(tf) < L17_MIN_TF:
        return []
    true_count = sum(1 for q in tf if q.get("answer") is True)
    false_count = len(tf) - true_count
    minority = min(true_count, false_count)
    share = minority / len(tf)
    if share < L17_MINORITY:
        return [{
            "qid": None, "rule": "L17", "severity": "warning",
            "detail": (
                f"true_false key split is imbalanced: {true_count} True / {false_count} "
                f"False ({share:.0%} minority share across {len(tf)} items); a lopsided "
                "mix is guessable"
            ),
        }]
    return []


# ─── Pack driver ─────────────────────────────────────────────────────────────

PER_QUESTION_CHECKS = [
    check_l1_matching_leak,
    check_l2_stem_echo,
    check_l3_length_tell,
    check_l7_schema,
    check_l8_parenthetical,
    check_l10_distractor_coverage,
    check_l12_explanation_and_meta,
    check_l14_meta_distractor,
    check_l15_matching_near_dup,
    check_l17_true_false_tell,
    check_l20_acronym_expansion_leak,
    check_l21_low_priority,
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
        matched = [i for i, w in enumerate(waivers) if _waiver_matches(w, v)]
        if not matched:
            live.append(v)
        else:
            used.update(matched)
            # Attribute the waived reason from the first match (most-specific wins
            # when the caller lists specific-before-broad, otherwise first-listed).
            waived.append({**v, "waived_reason": waivers[matched[0]].get("reason", "")})
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

    `violations` is the blocking set: per-question (L1/L2/L3/L7/L8/L10/L12/L14/
    L15/L17a/L20/L21) and pack-level (L9/L13/L16/L17b) findings, minus anything
    matched by the pack's `lint_waivers`, plus WAIVER hygiene warnings. Waived
    findings are returned separately in `waived` with the author's justification.
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
    if not isinstance(data, dict):
        out["violations"].append({
            "qid": None, "rule": "L7", "severity": "critical",
            "detail": f"pack root must be a JSON object, got {type(data).__name__}",
        })
        return out
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        out["violations"].append({
            "qid": None, "rule": "L7", "severity": "critical",
            "detail": f"pack `questions` must be a JSON array, got {type(questions).__name__}",
        })
        return out
    raw: list[dict] = []
    valid_questions: list[dict] = []
    for q in questions:
        if not isinstance(q, dict):
            raw.append({
                "qid": None, "rule": "L7", "severity": "critical",
                "detail": "question entry is not an object",
            })
            continue
        valid_questions.append(q)
        qid = q.get("id")
        for check in PER_QUESTION_CHECKS:
            for v in check(q):
                v["qid"] = qid
                raw.append(v)
    raw.extend(check_l9_near_duplicate_stems(valid_questions))
    raw.extend(check_l13_duplicate_ids(valid_questions))
    raw.extend(check_l16_answer_position(valid_questions))
    raw.extend(check_l17_tf_balance(valid_questions))
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
