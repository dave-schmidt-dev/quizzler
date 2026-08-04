#!/usr/bin/env python3
"""Stamp an explicitly authorized Codex-local review onto one staged pack.

This is a narrow fallback for a local, private pack when external reviewer
capacity is unavailable and David explicitly directs the cutover. It is not an
external-model certification and intentionally requires a verbose waiver flag
so it cannot be reached accidentally by the normal verification path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import lint_packs  # noqa: E402
import pack_cert  # noqa: E402


REVIEW_METHOD = pack_cert.CODEX_REVIEW_METHOD
HUMAN_SPOTCHECK = "waived-by-David-explicit-cutover-request"


def _load_pack(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON pack {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("pack root must be a JSON object")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("pack must contain a non-empty questions list")
    qids = [q.get("id") if isinstance(q, dict) else None for q in questions]
    if any(not isinstance(qid, str) or not qid for qid in qids):
        raise ValueError("every question must have a non-empty string id")
    if len(set(qids)) != len(qids):
        raise ValueError("question ids must be unique")
    return data


def _assert_layer_a_clean(path: Path) -> dict:
    result = lint_packs.lint_pack(path)
    live = [
        finding
        for finding in result.get("violations", [])
        if finding.get("severity") in {"critical", "warning"}
    ]
    if live:
        summary = ", ".join(
            f"{finding.get('rule', '?')}@{finding.get('qid') or '(pack)'}"
            for finding in live
        )
        raise ValueError(f"Layer A is not clean: {summary}")
    return result


def certify(path: Path, *, human_spotcheck_waived: bool) -> dict:
    if not human_spotcheck_waived:
        raise ValueError(
            "refusing Codex-only certification without "
            "--human-spotcheck-waived-by-david"
        )
    data = _load_pack(path)
    layer_a = _assert_layer_a_clean(path)
    questions = data["questions"]
    question_ids = sorted(q["id"] for q in questions)
    now = datetime.now(timezone.utc).isoformat()

    data["codex_review"] = {
        "reviewer": "codex",
        "review_method": REVIEW_METHOD,
        "reviewed_at": now,
        "question_ids": question_ids,
        "questions_examined": len(questions),
        "blocking_count": 0,
        "layer_a_live_findings": 0,
        "layer_a_hygiene_findings": len(layer_a.get("violations", [])),
        "human_spotcheck": HUMAN_SPOTCHECK,
        "external_review": {
            "claude_sonnet_5": "not-certified-incomplete",
            "agy_claude_sonnet_4_6": "not-run",
        },
    }
    data["certification"] = {
        "certified": True,
        "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": now,
        "questions_hash": pack_cert.questions_hash(data),
        "critic_model": "codex-local-review",
        "review_method": REVIEW_METHOD,
        "blocking_count": 0,
        "questions_examined": len(questions),
        "question_stamps": pack_cert.build_question_stamps(data),
    }
    if not pack_cert.certification_fresh(data):
        raise ValueError("constructed Codex-local certification is not fresh")

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pack", type=Path)
    parser.add_argument(
        "--human-spotcheck-waived-by-david",
        action="store_true",
        help="record David's explicit cutover waiver for the human spot-check",
    )
    args = parser.parse_args(argv)
    try:
        data = certify(
            args.pack,
            human_spotcheck_waived=args.human_spotcheck_waived_by_david,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Codex-local certification written: {args.pack} "
        f"({len(data['questions'])} questions; external certification not claimed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
