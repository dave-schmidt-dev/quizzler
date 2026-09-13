#!/usr/bin/env python3
"""Report the effect of a proposed question-stamp projection extension.

This is a dry-run migration aid.  It reads one installed pack and compares its
current per-question stamps with stamps produced if ``diagram`` and
``diagram_alt`` joined :mod:`scripts.pack_cert`'s projection.  It never writes
the pack, certification metadata, or a campaign ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

if __package__:
    from scripts import pack_cert
else:  # Supports the documented script-style invocation too.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pack_cert


CANDIDATE_FIELDS = ("diagram", "diagram_alt")
_MISSING = object()


def _extended_fields() -> tuple[str, ...]:
    """Return the proposed projection without duplicating an existing member."""
    return (*pack_cert.RELEVANT_FIELDS, *(
        field for field in CANDIDATE_FIELDS if field not in pack_cert.RELEVANT_FIELDS
    ))


def _null_aware_project(question: dict[str, Any]) -> dict[str, Any]:
    """Project present, non-null fields using the temporarily patched field set."""
    projected: dict[str, Any] = {}
    for field in pack_cert.RELEVANT_FIELDS:
        value = question.get(field, _MISSING)
        if value is not _MISSING and value is not None:
            projected[field] = value
    return projected


def _changed_ids(baseline: dict, candidate: dict) -> list[str]:
    """Return sorted IDs whose stamps differ between two projections."""
    return sorted(
        question_id
        for question_id in baseline.keys() | candidate.keys()
        if baseline.get(question_id) != candidate.get(question_id)
    )


def project_identity_report(pack: dict[str, Any]) -> dict[str, Any]:
    """Compute membership- and value-aware stamp differences for one pack.

    The candidate field binding is patched only around each candidate build.
    ``pack_cert`` imports ``RELEVANT_FIELDS`` into its own module namespace, so
    patching that module binding is essential; patching ``factcheck_pack`` is
    inert for the stamp functions.
    """
    baseline = pack_cert.build_question_stamps(pack)
    fields = _extended_fields()
    with patch.object(pack_cert, "RELEVANT_FIELDS", fields):
        candidate = pack_cert.build_question_stamps(pack)
    with patch.object(pack_cert, "RELEVANT_FIELDS", fields), patch.object(
        pack_cert, "_project_question", _null_aware_project
    ):
        null_aware_candidate = pack_cert.build_question_stamps(pack)

    changed_ids = _changed_ids(baseline, candidate)
    null_aware_changed_ids = _changed_ids(baseline, null_aware_candidate)
    return {
        "changed_ids": changed_ids,
        "changed_count": len(changed_ids),
        "changed_count_null_aware": len(null_aware_changed_ids),
        "null_aware_changed_ids": null_aware_changed_ids,
        "total": len(baseline),
    }


def load_pack(path: Path) -> dict[str, Any]:
    """Load one JSON-object pack, with actionable CLI errors for bad input."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read pack {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in pack {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"pack {path} must contain a JSON object")
    return data


def render_report(path: Path, report: dict[str, Any]) -> str:
    """Render stable, machine-readable output for a pack comparison."""
    lines = [f"pack={path}"]
    if report["changed_ids"]:
        lines.extend(f"changed_id={question_id}" for question_id in report["changed_ids"])
    else:
        lines.append("status=byte-identical, 0 to re-review")
    lines.append(f"changed_count={report['changed_count']} total={report['total']}")
    lines.extend(
        f"null_aware_changed_id={question_id}"
        for question_id in report["null_aware_changed_ids"]
    )
    lines.append(
        f"changed_count_null_aware={report['changed_count_null_aware']}"
    )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the sole, explicit pack path accepted by this read-only report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True, help="JSON pack to inspect")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the report and return a conventional CLI status code."""
    args = parse_args(argv)
    try:
        report = project_identity_report(load_pack(args.pack))
    except (TypeError, ValueError) as error:
        raise SystemExit(f"migrate-identity-report: {error}") from error
    print(render_report(args.pack, report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
