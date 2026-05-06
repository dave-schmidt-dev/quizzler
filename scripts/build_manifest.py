#!/usr/bin/env python3
"""Build question-packs/manifest.json from the question-packs/ folder layout.

Walks each subdirectory of question-packs/, reads optional _course.json for
display metadata, and lists every JSON pack in the folder. The output drives
the home-screen course grid in app/index.html, replacing the old hand-maintained
COURSES array.

Conventions:
  - One subfolder per course under question-packs/ (e.g., question-packs/itn213/).
  - Optional _course.json in the folder with: id, name, description.
  - Any other *.json file is treated as a question pack.
  - Pack module title comes from pack["title"]; description from pack["notes"].
  - Modules sort naturally by filename (mod1.json, mod2.json, ..., mod10.json).

Usage:
  python3 scripts/build_manifest.py
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parent.parent / "question-packs"
MANIFEST = PACKS_DIR / "manifest.json"


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
    return {
        "file": pack_file.name,
        "title": data.get("title", pack_file.stem),
        "description": data.get("notes", ""),
        "questionCount": len(data.get("questions", [])),
    }


def build() -> int:
    if not PACKS_DIR.is_dir():
        print(f"error: {PACKS_DIR} does not exist", file=sys.stderr)
        return 1

    courses = []
    for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name):
        if not course_dir.is_dir():
            continue
        # Skip hidden folders (.foo) and archive folders (_foo, e.g. _archived).
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

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "courses": courses,
    }
    MANIFEST.write_text(json.dumps(out, indent=2) + "\n")
    total_packs = sum(len(c["modules"]) for c in courses)
    print(
        f"wrote {MANIFEST.relative_to(PACKS_DIR.parent)}: "
        f"{len(courses)} courses, {total_packs} packs total"
    )
    return 0


if __name__ == "__main__":
    sys.exit(build())
