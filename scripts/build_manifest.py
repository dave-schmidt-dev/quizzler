#!/usr/bin/env python3
"""Build question-packs/manifest.json from the question-packs/ folder layout.

Walks each subdirectory of question-packs/, reads optional _course.json for
display metadata, and lists every JSON pack in the folder. The output drives
the home-screen course grid in app/index.html, replacing the old hand-maintained
COURSES array.

Conventions:
  - One subfolder per course under question-packs/ (e.g., question-packs/my-course/).
  - Optional _course.json in the folder with: id, name, description.
  - Any other *.json file is treated as a question pack.
  - Pack module title comes from pack["title"]; description from pack["notes"].
  - Modules sort naturally by filename (mod1.json, mod2.json, ..., mod10.json).

Usage:
  python3 scripts/build_manifest.py
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `import lint_packs` when running build_manifest.py standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_packs  # noqa: E402

PACKS_DIR = Path(__file__).resolve().parent.parent / "question-packs"
MANIFEST = PACKS_DIR / "manifest.json"

# Pack `notes` (used as the module subtitle on the home screen) gets truncated
# in the UI past this length. Warn during build so authors notice before ship.
MAX_NOTES_LENGTH = 120

# Question order is randomized at runtime, so prompts that reference previous
# questions will confuse the user when the follow-up is drawn before the setup.
# Warn on common sequential-coupling phrases so authors rewrite them as
# self-contained scenarios. See AUTHORING.md "Quality Rules" #11.
SEQUENTIAL_COUPLING_PATTERNS = [
    # "Same X scenario" / "Same X-Y scenario" / "Same X Y Z scenario" — allow
    # hyphenated and multi-word qualifiers (e.g. "Same fraud-detection scenario").
    re.compile(r"\bsame\s+[\w\s-]{1,40}?\s*scenario\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+previous\s+question\b", re.IGNORECASE),
    re.compile(r"\bas\s+(discussed|mentioned)\s+(earlier|above|previously)\b", re.IGNORECASE),
    re.compile(r"\breferring\s+to\s+the\s+(prior|previous|earlier)\b", re.IGNORECASE),
    re.compile(r"\bcontinuing\s+from\s+(above|the\s+previous)\b", re.IGNORECASE),
]


def natural_key(name: str) -> list:
    """Sort 'mod10.json' after 'mod9.json'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def read_course_meta(course_dir: Path) -> dict:
    """Read _course.json if present; otherwise derive from the folder name."""
    meta_file = course_dir / "_course.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
            return {
                "id": data.get("id", course_dir.name),
                "name": data.get("name", course_dir.name.upper()),
                "description": data.get("description", ""),
                "sort_order": data.get("sort_order", 100),
            }
        except json.JSONDecodeError as e:
            print(f"warn: {meta_file} has invalid JSON ({e}); using defaults",
                  file=sys.stderr)
    return {
        "id": course_dir.name,
        "name": course_dir.name.upper(),
        "description": "",
        "sort_order": 100,
    }


def read_pack_meta(pack_file: Path) -> dict | None:
    """Extract the manifest entry for one question pack."""
    try:
        data = json.loads(pack_file.read_text())
    except json.JSONDecodeError as e:
        print(f"warn: skipping {pack_file}: invalid JSON ({e})", file=sys.stderr)
        return None
    notes = data.get("notes", "")
    rel = pack_file.relative_to(PACKS_DIR.parent)
    if len(notes) > MAX_NOTES_LENGTH:
        print(
            f"warn: {rel} 'notes' is {len(notes)} chars (>{MAX_NOTES_LENGTH}); "
            f"will be truncated in the UI",
            file=sys.stderr,
        )
    for q in data.get("questions", []):
        prompt = q.get("prompt", "")
        for pattern in SEQUENTIAL_COUPLING_PATTERNS:
            match = pattern.search(prompt)
            if match:
                print(
                    f"warn: {rel} {q.get('id', '?')} prompt contains "
                    f"sequential-coupling phrase '{match.group(0)}'; "
                    f"questions are randomized — rewrite as standalone",
                    file=sys.stderr,
                )
                break
    return {
        "file": pack_file.name,
        "title": data.get("title", pack_file.stem),
        "description": notes,
        "questionCount": len(data.get("questions", [])),
    }


def build(strict: bool = False) -> int:
    """Build manifest.json. `strict=True` aborts on Layer-A critical violations.

    Default (warn-mode) ships a manifest even with critical lint violations
    so legacy packs don't brick the build. Strict mode is intended for CI
    once packs are cleaned up. CLI: `--strict` or env `QUIZZLER_LINT_STRICT=1`.
    """
    if not PACKS_DIR.is_dir():
        print(f"error: {PACKS_DIR} does not exist", file=sys.stderr)
        return 1

    courses = []
    for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name):
        if not course_dir.is_dir():
            continue
        # Skip hidden folders (.foo) and archive folders (_foo, e.g. _archive).
        if course_dir.name.startswith((".", "_")):
            continue

        meta = read_course_meta(course_dir)
        modules = []
        pack_files = sorted(
            (p for p in course_dir.glob("*.json") if p.name != "_course.json"),
            key=lambda p: natural_key(p.name),
        )
        for pack_file in pack_files:
            entry = read_pack_meta(pack_file)
            if entry is not None:
                modules.append(entry)

        if not modules:
            print(f"warn: {course_dir.name} has no valid packs; skipping",
                  file=sys.stderr)
            continue

        meta["modules"] = modules
        courses.append(meta)

    # Sort by explicit sort_order (lower = earlier), then by name.
    courses.sort(key=lambda c: (c.get("sort_order", 100), c["name"].lower()))
    # Drop sort_order from output — it's an authoring detail, not runtime data.
    for c in courses:
        c.pop("sort_order", None)

    # ── Layer A quality-gate: lint every pack before writing the manifest ──────
    all_pack_paths = [
        PACKS_DIR / course_dir.name / pack_file["file"]
        for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name)
        if course_dir.is_dir() and not course_dir.name.startswith((".", "_"))
        for pack_file in next(
            (c["modules"] for c in courses if c["id"] == course_dir.name), []
        )
    ]
    lint_criticals = 0
    lint_warnings = 0
    for pack_path in all_pack_paths:
        result = lint_packs.lint_pack(pack_path)
        crits = [v for v in result["violations"] if v.get("severity") == "critical"]
        warns = [v for v in result["violations"] if v.get("severity") == "warning"]
        lint_criticals += len(crits)
        lint_warnings += len(warns)
        if crits or warns:
            rel = pack_path.relative_to(PACKS_DIR.parent)
            print(
                f"lint: {rel}: {len(crits)} critical, {len(warns)} warning",
                file=sys.stderr,
            )
            for v in crits + warns:
                qid = v.get("qid") or "(pack)"
                print(
                    f"  [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}",
                    file=sys.stderr,
                )
    if lint_criticals and strict:
        print(
            f"error: lint found {lint_criticals} critical violation(s) across packs; "
            f"manifest not written (strict mode)",
            file=sys.stderr,
        )
        return 1
    # ── Write manifest ─────────────────────────────────────────────────────────
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "courses": courses,
    }
    MANIFEST.write_text(json.dumps(out, indent=2) + "\n")
    total_packs = sum(len(c["modules"]) for c in courses)
    summary = f"wrote {MANIFEST.relative_to(PACKS_DIR.parent)}: {len(courses)} courses, {total_packs} packs total"
    if lint_criticals or lint_warnings:
        summary += f" (lint: {lint_criticals} critical, {lint_warnings} warning)"
    print(summary)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        default=os.environ.get("QUIZZLER_LINT_STRICT") == "1",
        help="Abort with exit 1 if Layer-A lint reports any critical violation "
        "(default: warn-only so legacy packs don't break the build).",
    )
    args = parser.parse_args()
    sys.exit(build(strict=args.strict))
