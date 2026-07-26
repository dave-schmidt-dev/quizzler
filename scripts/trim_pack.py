#!/usr/bin/env python3
"""Mechanical first-pass pack trimmer — ~1 question per blueprint topic (CR-9).

Context: a comprehensively-authored pack (multiple candidate questions per
`coverage_blueprint` topic) needs to shrink to the course's LEAN sizing
target (~1 question/topic; see question-packs/sy0-701/BUILD_NOTES.md
"Sizing decision"). This script does the MECHANICAL first pass only:

  - Keeps `min` (default 1) question(s) per topic, using a deterministic,
    metadata-only selection rule — never a judgment of which question reads
    better or is more "correct." Ties are broken by structural balancing
    (prefer the candidate whose `type` or keyed `answer` index is currently
    least represented in the running trimmed set — this is what spreads
    answer indices for lint rule L16 and keeps question-type variety) and,
    failing that, by original file order (the "most canonical" default).
  - NEVER invents a "strongest question" quality ranking. BUILD_NOTES defines
    no such heuristic, and this script does not read prompt/explanation text
    to judge content quality — only `type`/`answer`/original position.
  - Every topic where an alternate was dropped is flagged in the trim report
    as a MANUAL REVIEW item (INV-8): a human confirms the mechanical survivor
    is in fact the strongest available, or swaps in a better one from the
    `_full/` backup. Nothing is silently discarded.
  - The full untrimmed pack is copied to `<course-dir>/_full/<pack-name>`
    before anything is overwritten. `question-packs/*/` is already
    git-ignored (see .gitignore), so `_full/` is committed-ignored, never a
    scratchpad.

This is a first-pass tool, not the whole QA pipeline: run `lint_packs.py` /
`verify_pack.py` on the trimmed output afterward, and do the Layer-C human
review before calling a course "done" (see BUILD_NOTES "Quality gate").

Usage:
  python3 scripts/trim_pack.py question-packs/<course>/<pack>.json
  python3 scripts/trim_pack.py <pack> --dry-run          # preview only, no writes
  python3 scripts/trim_pack.py <pack> --out other.json   # write trimmed copy elsewhere
  python3 scripts/trim_pack.py <pack> --json             # machine-readable report

Exit codes: 0 on success (including --dry-run); 1 on any operational error
(bad pack, no questions, or a backup already exists and --force was not given).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path


# ── core selection logic (pure, no I/O) ─────────────────────────────────────

def blueprint_min_map(blueprint: list | None) -> dict[str, int]:
    """Return {topic: min} from a `coverage_blueprint` array.

    Each entry is either a bare string (min 1) or {"topic": ..., "min": N}.
    Malformed/missing `min` falls back to 1. Duplicate topic entries keep the
    larger of the two `min` values.
    """
    out: dict[str, int] = {}
    for entry in blueprint or []:
        if isinstance(entry, str):
            topic, m = entry, 1
        elif isinstance(entry, dict):
            topic = entry.get("topic")
            if not topic:
                continue
            m = entry.get("min", 1)
            if not isinstance(m, int) or m < 1:
                m = 1
        else:
            continue
        out[topic] = max(out.get(topic, m), m)
    return out


def _topic_order(questions: list, blueprint: list | None) -> list:
    """Blueprint topics first (in blueprint order), then any topic present in
    `questions` but absent from the blueprint, in first-seen order.
    """
    order: list = []
    seen: set = set()
    for entry in blueprint or []:
        topic = entry if isinstance(entry, str) else (
            entry.get("topic") if isinstance(entry, dict) else None)
        if topic and topic not in seen:
            order.append(topic)
            seen.add(topic)
    for q in questions:
        topic = q.get("topic") if isinstance(q, dict) else None
        if topic not in seen:
            order.append(topic)
            seen.add(topic)
    return order


def _answer_index(q: dict):
    """Return q["answer"] if it's an int-valued option index, else None.

    ``bool`` is a subclass of ``int`` in Python, so this explicitly excludes
    it — true_false's boolean `answer` is not a shuffle-position index and
    must never be folded into the L16 index-spreading tally.
    """
    ans = q.get("answer")
    return ans if isinstance(ans, int) and not isinstance(ans, bool) else None


def _rank_key(q: dict, type_counts: Counter, idx_counts: Counter) -> tuple:
    """Lower = preferred. Structural-only: current type/answer-index
    representation in the running trimmed set, then original position.
    """
    idx = _answer_index(q)
    return (
        type_counts.get(q.get("type"), 0),
        idx_counts.get(idx, 0) if idx is not None else 0,
        q["_orig_index"],
    )


def _strip_internal(q: dict) -> dict:
    return {k: v for k, v in q.items() if k != "_orig_index"}


def trim_pack_data(pack: dict) -> dict:
    """Trim `pack["questions"]` to ~1/topic. Returns a report dict:

      {
        "pack": <trimmed pack dict — questions replaced, stale
                 "certification" removed>,
        "dropped_qids": [ids, in topic-then-original-order],
        "manual_review": [{"topic", "kept": [ids], "dropped": [ids]}, ...
                           one entry per topic that had a drop],
        "stats": {original_count, trimmed_count, dropped_count, topics,
                   topics_with_manual_review},
      }

    Never mutates the input `pack`.
    """
    questions = pack.get("questions")
    questions = questions if isinstance(questions, list) else []
    blueprint = pack.get("coverage_blueprint")
    min_map = blueprint_min_map(blueprint)
    order = _topic_order(questions, blueprint)

    by_topic: dict = {}
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        topic = q.get("topic")
        entry = dict(q)
        entry["_orig_index"] = i
        by_topic.setdefault(topic, []).append(entry)

    type_counts: Counter = Counter()
    idx_counts: Counter = Counter()
    survivors_by_index: dict[int, dict] = {}
    dropped_qids: list = []
    manual_review: list = []

    for topic in order:
        candidates = by_topic.get(topic)
        if not candidates:
            continue
        if not topic:
            # Missing/blank `topic` can't be safely grouped against the
            # blueprint at all — there is no topic to confirm coverage for,
            # so trimming these would be a guess dressed up as a rule. Keep
            # everything; lint rule L12 (topic required) is what should catch
            # this pack before it ever reaches the trimmer.
            keep_n = len(candidates)
        else:
            keep_n = max(1, min_map.get(topic, 1))

        if len(candidates) <= keep_n:
            chosen = sorted(candidates, key=lambda q: q["_orig_index"])
            remaining: list = []
        else:
            remaining = sorted(candidates, key=lambda q: q["_orig_index"])
            chosen = []
            for _ in range(keep_n):
                remaining.sort(key=lambda q: _rank_key(q, type_counts, idx_counts))
                pick = remaining.pop(0)
                chosen.append(pick)

        for q in chosen:
            survivors_by_index[q["_orig_index"]] = q
            type_counts[q.get("type")] += 1
            idx = _answer_index(q)
            if idx is not None:
                idx_counts[idx] += 1

        if remaining:
            dropped_sorted = sorted(remaining, key=lambda q: q["_orig_index"])
            kept_sorted = sorted(chosen, key=lambda q: q["_orig_index"])
            dropped_qids.extend(q.get("id") for q in dropped_sorted)
            manual_review.append({
                "topic": topic,
                "kept": [q.get("id") for q in kept_sorted],
                "dropped": [q.get("id") for q in dropped_sorted],
            })

    trimmed_questions = [
        _strip_internal(survivors_by_index[i]) for i in sorted(survivors_by_index)
    ]

    trimmed_pack = dict(pack)
    trimmed_pack["questions"] = trimmed_questions
    trimmed_pack.pop("certification", None)  # stale after content change

    return {
        "pack": trimmed_pack,
        "dropped_qids": dropped_qids,
        "manual_review": manual_review,
        "stats": {
            "original_count": len(questions),
            "trimmed_count": len(trimmed_questions),
            "dropped_count": len(dropped_qids),
            "topics": len(order),
            "topics_with_manual_review": len(manual_review),
        },
    }


# ── I/O helpers ──────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _print_summary(result: dict, *, dry_run: bool, out_path: Path | None,
                    report_path: Path | None) -> None:
    stats = result["stats"]
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}{result['source_pack']}: {stats['original_count']} -> "
          f"{stats['trimmed_count']} questions ({stats['dropped_count']} dropped)")

    if result["dropped_qids"]:
        print("Dropped question ids: " + ", ".join(result["dropped_qids"]))
    else:
        print("Dropped question ids: (none)")

    if result["manual_review"]:
        print("MANUAL REVIEW NEEDED (INV-8 — confirm each survivor is the "
              "strongest available, or swap from the _full/ backup):")
        for entry in result["manual_review"]:
            print(f"  - {entry['topic']}: kept {', '.join(entry['kept'])}; "
                  f"dropped {', '.join(entry['dropped'])}")
    else:
        print("Manual review: none needed (no topic had a dropped alternate).")

    if dry_run:
        print("(dry run — no files written)")
    else:
        print(f"Original backed up to: {result['backup_path']}")
        print(f"Trimmed pack written to: {out_path}")
        print(f"Trim report written to: {report_path}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Mechanical first-pass trimmer: keeps ~1 question per "
        "coverage_blueprint topic (more if a topic's min > 1), preferring "
        "whichever candidate's type/answer-index is least represented so far "
        "(spreads L16 answer-position distribution, preserves type variety) "
        "with original file order as the final tie-break — NEVER a content or "
        "quality ranking. Every topic with a dropped alternate is flagged for "
        "MANUAL human review against the full backup (INV-8).")
    parser.add_argument("pack", type=Path, help="Pack JSON to trim.")
    parser.add_argument("--out", type=Path, default=None,
                        help="Where to write the trimmed pack (default: "
                        "overwrite the input pack in place).")
    parser.add_argument("--full-dir", type=Path, default=None,
                        help="Directory to back up the untrimmed original into "
                        "(default: <course-dir>/_full/ next to the pack). Must "
                        "stay a committed-ignored path — never a scratchpad.")
    parser.add_argument("--report", type=Path, default=None,
                        help="Where to write the JSON trim report (dropped ids "
                        "+ manual-review flags). Default: <full-dir>/"
                        "<pack-stem>.trim_report.json.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and print the trim plan; write nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Proceed even if a full backup already exists at "
                        "the backup path. The existing backup is left "
                        "untouched — this does NOT create a fresh backup of "
                        "what may already be a trimmed file.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the trim report as JSON on stdout instead "
                        "of the human-readable summary.")
    args = parser.parse_args(argv)

    if not args.pack.is_file():
        print(f"error: pack not found: {args.pack}", file=sys.stderr)
        return 1

    try:
        pack = json.loads(args.pack.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read pack: {e}", file=sys.stderr)
        return 1

    if not isinstance(pack, dict) or not isinstance(pack.get("questions"), list) \
            or not pack["questions"]:
        print("error: pack has no questions to trim", file=sys.stderr)
        return 1

    full_dir = args.full_dir or (args.pack.parent / "_full")
    backup_path = full_dir / args.pack.name
    out_path = args.out or args.pack
    report_path = args.report or (full_dir / f"{args.pack.stem}.trim_report.json")

    if backup_path.exists() and not args.force:
        print(f"error: backup already exists at {backup_path} — refusing to "
              "trim what may already be a trimmed pack. Pass --force to trim "
              "the current file anyway (the existing backup is left "
              "untouched).", file=sys.stderr)
        return 1

    result = trim_pack_data(pack)
    result["source_pack"] = str(args.pack)
    result["backup_path"] = str(backup_path)

    if not args.dry_run:
        full_dir.mkdir(parents=True, exist_ok=True)
        if not backup_path.exists():
            shutil.copy2(args.pack, backup_path)
        elif args.force:
            print(f"note: backup already exists at {backup_path}; NOT "
                  "overwritten (--force proceeding to trim the current file "
                  "anyway).", file=sys.stderr)
        _atomic_write_json(out_path, result["pack"])
        _atomic_write_json(report_path, {
            "source_pack": result["source_pack"],
            "backup_path": result["backup_path"],
            "dropped_qids": result["dropped_qids"],
            "manual_review": result["manual_review"],
            "stats": result["stats"],
        })

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(
            result, dry_run=args.dry_run,
            out_path=None if args.dry_run else out_path,
            report_path=None if args.dry_run else report_path,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
