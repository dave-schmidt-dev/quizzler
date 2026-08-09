#!/usr/bin/env python3
"""Build a deterministic, hash-bound index of native local question packs."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

INSTALLABLE = {"multiple_choice", "scenario_multiple_choice", "multiple_select"}
KNOWN = INSTALLABLE | {"true_false", "matching"}

def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def content_digest(value):
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()

def validate_pack(value, legacy_digests=frozenset()):
    if not isinstance(value, dict) or not isinstance(value.get("pack_id"), str) or not value["pack_id"].strip():
        raise ValueError("malformed pack metadata")
    questions = value.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions must be a non-empty array")
    ids = set()
    for q in questions:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"].strip(): raise ValueError("malformed question metadata")
        if q["id"] in ids: raise ValueError(f"duplicate question id: {q['id']}")
        ids.add(q["id"])
        kind = q.get("type")
        if kind not in KNOWN: raise ValueError(f"unknown question type: {kind}")
        if kind not in INSTALLABLE and content_digest(value) not in legacy_digests: raise ValueError("legacy question type requires an exact allowlisted digest")
        if kind in {"multiple_choice", "scenario_multiple_choice"}:
            opts, answer = q.get("options"), q.get("answer")
            if not isinstance(opts, list) or len(opts) < 2 or not isinstance(answer, int) or not 0 <= answer < len(opts): raise ValueError("invalid answer index")
        elif kind == "multiple_select":
            opts, answers = q.get("options"), q.get("answers")
            if not isinstance(opts, list) or len(opts) < 2 or not isinstance(answers, list) or len(answers) < 2 or len(set(answers)) != len(answers) or not all(isinstance(i, int) and 0 <= i < len(opts) for i in answers): raise ValueError("invalid answer indexes")
    return value

def build(input_dir: Path, legacy_digests=frozenset()):
    rows = []
    for path in sorted(input_dir.rglob("*.json")):
        if path.name.startswith("_") or path.name in {"manifest.json", "manifest.example.json", "pack-template.json"} or "_archive" in path.parts or "_staging" in path.parts: continue
        value = json.loads(path.read_text(encoding="utf-8"))
        value = validate_pack(value, legacy_digests)
        rows.append({"course_id": path.parent.name, "pack_id": value["pack_id"], "path": path.relative_to(input_dir).as_posix(), "content_digest": content_digest(value)})
    rows.sort(key=lambda row: (row["course_id"], row["path"]))
    return {"contract_version": 1, "packs": rows}

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("input", type=Path); parser.add_argument("output", type=Path); parser.add_argument("--legacy-digest", action="append", default=[]); args = parser.parse_args(argv)
    try: result = build(args.input, frozenset(args.legacy_digest))
    except (OSError, ValueError, json.JSONDecodeError) as exc: print(f"error: {exc}", file=sys.stderr); return 1
    args.output.write_bytes(canonical_bytes(result) + b"\n"); return 0

if __name__ == "__main__": raise SystemExit(main())
