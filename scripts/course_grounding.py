#!/usr/bin/env python3
"""Course-grounding resolution — pure, stdlib-only, no external deps.

Resolves a pack's real source-text excerpt from its course's `_course.json`
`grounding` block. This is the SINGLE canonical lookup shared by both sides of
the grounding contract:

  • Layer C (scripts/factcheck_pack.py) uses it at REVIEW time to inject the
    actual chapter text into the critic prompt (see PROMPT_HEADER usage there).
  • Layer A (scripts/lint_packs.py, rule L28) uses it at AUTHORING time to
    verify the pack is actually wired into a course that declares grounding —
    catching the "cited a source but never mapped it" gap before a pack ships.

Both call sites must resolve the exact same file for the exact same pack, so
the resolution logic — including its path-traversal and suffix guards — lives
here once rather than being duplicated and risking drift.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_course_grounding(pack_path: Path) -> dict | None:
    """Return the `grounding` block of the pack's sibling `_course.json`, or None
    if the course has none configured or the file can't be read.

    `_course.json` is course-level, operator-authored config (the same file that
    already carries `syllabus`) — NOT pack content, which is untrusted. Reading it
    defensively (never raising) means a course without grounding configured just
    degrades to the pre-grounding directive-only behavior, never an error."""
    course_path = pack_path.parent / "_course.json"
    try:
        data = json.loads(course_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    grounding = data.get("grounding")
    return grounding if isinstance(grounding, dict) else None


def load_source_text(pack_path: Path) -> str | None:
    """Return this pack's real source-text excerpt for grounding the critic, or
    None if the course has no grounding configured for it.

    The course's `grounding` block (see :func:`load_course_grounding`) maps THIS
    pack's filename to a `.txt` filename inside an operator-set `text_root` — e.g.
    the external, out-of-repo directory a copyrighted textbook chapter lives in
    (see docs/COURSE_BUILD_PLAYBOOK.md). Both the root and the mapping are
    course-level config the author controls; a pack cannot point the critic at an
    arbitrary file by editing its own fields, because the pack's OWN content is
    never consulted to resolve a path — only its filename is used, as a lookup
    key into the operator-authored map.

    Deliberately narrow: reads only a `.txt` file that resolves to live directly
    inside `text_root` (rejects a resolved parent that isn't `text_root` — no
    `..`, no symlink-out — and rejects any non-`.txt` suffix, so the sibling
    `.html` is never read). Never logged, never written into a report or a
    git-tracked file — the text itself may be a copyrighted excerpt that must not
    enter the repo."""
    grounding = load_course_grounding(pack_path)
    if not grounding:
        return None
    text_root = grounding.get("text_root")
    filename = (grounding.get("packs") or {}).get(pack_path.name)
    if not text_root or not filename:
        return None
    root = Path(text_root).expanduser().resolve()
    if not root.is_dir():
        return None
    candidate = (root / filename).resolve()
    if candidate.parent != root or candidate.suffix.lower() != ".txt":
        return None
    try:
        text = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
