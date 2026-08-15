#!/usr/bin/env python3
"""Legacy stdin adapter for deterministic question-pack linting.

This module is retained for compatibility tests and explicit standalone use. It
is not wired to Claude Code, an editor, or a PostToolUse event; staged packs are
checked by ``.githooks/pre-commit`` and certification freshness by pre-push.
Clean packs (and any non-pack file) exit 0 silently.

Reads the hook event JSON on stdin: {"tool_input": {"file_path": "..."}, ...}.
Defensive by design: any parse/lookup failure or unrelated file exits 0 so the
hook never blocks normal editing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"

# Files inside question-packs/ that are not question packs.
NON_PACK_NAMES = {"_course.json", "manifest.json", "manifest.example.json"}


def _event() -> dict:
    try:
        ev = json.load(sys.stdin)
        return ev if isinstance(ev, dict) else {}
    except Exception:
        return {}


def _edited_path(event: dict) -> Path | None:
    ti = event.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path")
    return Path(fp) if fp else None


def _is_question_pack(path: Path) -> bool:
    """True only for a *.json pack inside a real course subfolder of question-packs/."""
    if path.suffix != ".json" or path.name in NON_PACK_NAMES:
        return False
    try:
        rel = path.resolve().relative_to(PACKS_DIR.resolve())
    except (ValueError, OSError):
        return False
    parts = rel.parts
    # Exactly course-subfolder/pack.json (matches build_manifest's one-level glob);
    # skip files directly in question-packs/ and hidden/archive (./_) course folders.
    return len(parts) == 2 and not parts[0].startswith((".", "_"))


def main() -> int:
    path = _edited_path(_event())
    if path is None or not _is_question_pack(path) or not path.exists():
        return 0

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import lint_packs
    except Exception:
        return 0  # linter unavailable — never block editing

    result = lint_packs.lint_pack(path)
    violations = result.get("violations", [])
    # Criticals and warnings block editing. L23 absent-`coverage_blueprint` is
    # CRITICAL — every pack must declare a blueprint. The non-blocking "advisory"
    # tier (if any rule uses it) rides in `violations` but does not fail the hook.
    crit = [v for v in violations if v.get("severity") == "critical"]
    warn = [v for v in violations if v.get("severity") == "warning"]
    if not crit and not warn:
        return 0  # clean or advisory-only (waived findings already excluded)
    try:
        label = path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        label = path

    out = [
        f"Pack-quality gate FAILED for {label}: {len(crit)} critical, {len(warn)} warning.",
        "Fix every finding before moving on — this pack must lint clean at authoring time.",
        "A genuinely intentional, reviewed finding can be waived with a top-level",
        '  "lint_waivers": [{"rule": "Lxx", "qid": "<id or omit for pack-wide>", "reason": "<why>"}]',
        "entry in the pack JSON. Otherwise, edit the question.",
        "",
    ]
    for v in crit + warn:
        qid = v.get("qid") or "(pack)"
        out.append(f"  [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}")
    print("\n".join(out), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
