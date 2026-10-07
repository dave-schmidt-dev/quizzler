#!/usr/bin/env python3
"""Pack-level quarantine: set aside questions in an installed pack (M3, INV-7).

Campaign quarantine (``scripts/campaign_quarantine.py``) shrinks a campaign's
certification frontier in the evidence ledger.  This module is the pack-file
counterpart: it removes the quarantined questions from the installed pack so
the pack on disk matches the reduced frontier, and it records what was removed
so the authored pack can be restored.

The removal is recorded twice, by design:

* A sidecar at ``question-packs/<course>/_quarantine/<pack-file>`` holds the
  removed questions (with their original positions) and the free-text reason.
  The reason lives ONLY there; the pack and its marker stay machine-shaped.
* An in-pack top-level ``partial_install`` marker carries exactly
  ``{authored_count, installed_count, quarantined_ids, record_digest}`` --
  the authored and installed counts, the removed ids, and the SHA-256 of the
  sidecar record -- so any consumer can tell a partial pack from a whole one
  without reading the sidecar.

The marker ships with the pack: lint L29 and QuizzlerKit's
``PackManifest.validate()`` both check its shape, and the app labels the pack
as partial from it.  ``install_gate.evaluate`` -- the one place the gate is
wired -- refuses a partial pack outright unless the caller explicitly allowed
partial installs (Debug builds only), and even then the retained subset is
still subject to every quality bar: lint criticals (including L23 blueprint
coverage), the course-level distribution aggregates, and a fresh
certification over the retained questions.

Workflow (each step refuses rather than half-applying)::

    python3 scripts/pack_quarantine.py quarantine --pack P --qid X --reason "..."
    python3 scripts/certification_campaign.py begin-quarantine --ledger L --pack P
    # later, to put the questions back:
    python3 scripts/pack_quarantine.py restore --pack P
    python3 scripts/certification_campaign.py release-quarantine --ledger L

``begin-quarantine`` is where the campaign checks that a valid base evidence
source exists and that the reduced pack keeps every frozen field.

This module never writes or refreshes certification metadata: the only route
that may write a final certification stamp is ``certify_campaign`` (INV-7).
Removing questions makes the pack's existing certification stale by
construction, which is exactly what the gate should see until the reduced pack
is re-stamped.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

QUARANTINE_DIRNAME = "_quarantine"
MARKER_KEY = "partial_install"
MARKER_FIELDS = frozenset({
    "authored_count", "installed_count", "quarantined_ids", "record_digest",
})


class PackQuarantineError(Exception):
    """A quarantine or restore cannot be performed, or a record is untrustworthy."""


def sidecar_path(pack_path: Path) -> Path:
    """Return the sidecar path that records one pack's removed questions.

    Args:
        pack_path: The pack file, whose parent is the course directory.

    Returns:
        ``<course>/_quarantine/<pack-file>``. The ``_`` prefix keeps the
        sidecar out of pack discovery, so it is never gated or bundled.
    """
    return pack_path.parent / QUARANTINE_DIRNAME / pack_path.name


def record_digest(record: object) -> str:
    """Return the ``sha256:<hex>`` digest of a sidecar record.

    The digest is taken over the record's canonical JSON value (sorted keys,
    compact separators), not its file bytes, so reformatting the sidecar
    cannot break a marker that names it.

    Args:
        record: The parsed sidecar record.

    Returns:
        The digest string in the form ``sha256:<hex>``.
    """
    canonical = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_count(value: object) -> bool:
    """Return whether ``value`` is a non-negative integer (never a bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: object) -> bool:
    """Return whether ``value`` is a ``sha256:<64 lowercase hex>`` string."""
    return (isinstance(value, str)
            and len(value) == 71
            and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:]))


def _load_pack(pack_path: Path) -> dict:
    """Parse one pack file, refusing unreadable or non-object input.

    Args:
        pack_path: The pack file to read.

    Returns:
        The parsed pack object.

    Raises:
        PackQuarantineError: If the file cannot be read as a JSON object.
    """
    try:
        data = json.loads(pack_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PackQuarantineError(f"cannot read pack: {error}") from error
    if not isinstance(data, dict):
        raise PackQuarantineError("pack root must be a JSON object")
    return data


def _question_ids(questions: list) -> list[str]:
    """Return the questions' ids in pack order, validating each is a string.

    Args:
        questions: The pack's ``questions`` list.

    Returns:
        The ids in order.

    Raises:
        PackQuarantineError: If any question is not an object carrying a
            non-empty string ``id``.
    """
    ids: list[str] = []
    for question in questions:
        if (not isinstance(question, dict)
                or not isinstance(question.get("id"), str)
                or not question["id"]):
            raise PackQuarantineError(
                "every question must carry a non-empty string id")
        ids.append(question["id"])
    return ids


def marker_shape_reasons(data: dict) -> list[str]:
    """Return why a pack's ``partial_install`` marker is malformed.

    These are the self-contained rules lint L29 and QuizzlerKit's
    ``PackManifest.validate()`` share: exact keys, counts that match the
    installed questions, unique quarantined ids disjoint from them, and a
    ``sha256:`` record digest. The sidecar is not consulted.

    Args:
        data: A parsed pack carrying a ``partial_install`` key.

    Returns:
        A list of human-readable reasons, empty when the marker is well formed.
    """
    marker = data.get(MARKER_KEY)
    if not isinstance(marker, dict):
        return ["partial_install marker must be an object"]
    reasons: list[str] = []
    if set(marker) != MARKER_FIELDS:
        reasons.append(
            "partial_install must carry exactly "
            + ", ".join(sorted(MARKER_FIELDS))
            + f", got {sorted(marker)!r}"
        )

    questions = data.get("questions")
    if not isinstance(questions, list):
        reasons.append("partial pack questions must be a list")
        return reasons
    installed_ids: list[str] = []
    for question in questions:
        if (not isinstance(question, dict)
                or not isinstance(question.get("id"), str)
                or not question["id"]):
            reasons.append(
                "partial pack questions must carry non-empty string ids")
            installed_ids = []
            break
        installed_ids.append(question["id"])

    authored = marker.get("authored_count")
    installed = marker.get("installed_count")
    if not _is_count(authored) or not _is_count(installed):
        reasons.append("partial_install counts must be non-negative integers")
    else:
        if installed != len(questions):
            reasons.append(
                f"partial_install installed_count must equal the "
                f"{len(questions)} installed question(s), got {installed}")
        if authored <= installed:
            reasons.append(
                "partial_install authored_count must exceed installed_count")

    qids = marker.get("quarantined_ids")
    if (not isinstance(qids, list)
            or any(not isinstance(qid, str) or not qid for qid in qids)
            or len(set(qids)) != len(qids)):
        reasons.append(
            "partial_install quarantined_ids must be unique non-empty strings")
        qids = None
    elif _is_count(authored) and _is_count(installed):
        if len(qids) != authored - installed:
            reasons.append(
                "partial_install quarantined_ids must name every removed "
                "question")
    if qids is not None and installed_ids:
        overlap = sorted(set(qids) & set(installed_ids))
        if overlap:
            reasons.append(
                f"quarantined ids are still installed: {overlap!r}")

    digest = marker.get("record_digest")
    if not _is_sha256(digest):
        reasons.append("partial_install record_digest must be 'sha256:<hex>'")

    return reasons


def _record_reasons(pack_path: Path, data: dict) -> list[str]:
    """Return every reason the partial record cannot be trusted.

    Validates the marker's exact shape (the four fields, the count relations,
    unique quarantined ids, the digest form) and its consistency with the
    sidecar (presence, self-description, the removed questions, and the
    record digest). A pack whose marker or sidecar was hand-edited fails
    closed: it is refused everywhere, including under ``--allow-partial``.

    Args:
        pack_path: The pack file, used to locate the sidecar.
        data: The parsed pack carrying a ``partial_install`` marker.

    Returns:
        A list of human-readable reasons, empty when the record is valid.
    """
    reasons = marker_shape_reasons(data)
    marker = data.get(MARKER_KEY)
    if not isinstance(marker, dict):
        return reasons
    authored = marker.get("authored_count")
    qids = marker.get("quarantined_ids")
    if not isinstance(qids, list) or len(set(map(str, qids))) != len(qids):
        qids = None
    digest = marker.get("record_digest")

    sidecar = sidecar_path(pack_path)
    try:
        record = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError):
        reasons.append(f"quarantine sidecar missing or unreadable ({sidecar})")
        return reasons
    if not isinstance(record, dict):
        reasons.append("quarantine sidecar must be an object")
        return reasons
    if (record.get("course") != pack_path.parent.name
            or record.get("pack") != pack_path.name):
        reasons.append("quarantine sidecar does not describe this pack")
    if not isinstance(record.get("reason"), str) or not record["reason"].strip():
        reasons.append("quarantine sidecar reason must be a non-blank string")

    removed = record.get("removed_questions")
    if not isinstance(removed, list):
        reasons.append("quarantine sidecar removed_questions must be a list")
    else:
        indices: list[int] = []
        removed_ids: list[str] = []
        well_formed = True
        for entry in removed:
            question = entry.get("question") if isinstance(entry, dict) else None
            if (not isinstance(entry, dict)
                    or not _is_count(entry.get("index"))
                    or not isinstance(question, dict)
                    or not isinstance(question.get("id"), str)
                    or not question["id"]):
                reasons.append(
                    "quarantine sidecar removed_questions entries must carry "
                    "an index and a question with an id")
                well_formed = False
                break
            indices.append(entry["index"])
            removed_ids.append(question["id"])
        if well_formed:
            if indices != sorted(indices) or len(set(indices)) != len(indices):
                reasons.append(
                    "quarantine sidecar removed_questions must be in "
                    "ascending original order")
            if _is_count(authored) and any(
                    index >= authored for index in indices):
                reasons.append(
                    "quarantine sidecar indices must fall inside the "
                    "authored pack")
            if qids is not None and set(removed_ids) != set(qids):
                reasons.append(
                    "quarantine sidecar removed_questions must match "
                    "quarantined_ids")

    if _is_sha256(digest) and record_digest(record) != digest:
        reasons.append(
            "partial_install record_digest does not match the quarantine "
            "sidecar")
    return reasons


def gate_reasons(
    pack_path: Path,
    data: object,
    *,
    allow_partial: bool = False,
) -> list[str]:
    """Return the install-gate reasons a partial pack cannot be admitted.

    Wired once, into ``install_gate.evaluate``, so the manifest builder and
    the native bundler cannot diverge on partials. A pack without a
    ``partial_install`` marker gates exactly as before. A pack carrying one is
    refused unless the record is valid AND the caller explicitly allowed
    partial installs; a malformed marker, a missing or tampered sidecar, or a
    digest mismatch is refused regardless of ``allow_partial``.

    Args:
        pack_path: The pack file, used to locate the sidecar.
        data: The parsed pack.
        allow_partial: Whether partial installs are allowed at all (the
            bundler passes this only for Debug builds).

    Returns:
        A list of human-readable gate reasons, empty when the pack is whole
        or the partial is valid and allowed.
    """
    if not isinstance(data, dict) or MARKER_KEY not in data:
        return []
    reasons = _record_reasons(pack_path, data)
    if reasons:
        return reasons
    if not allow_partial:
        marker = data[MARKER_KEY]
        return [
            f"partial install ({marker['installed_count']}/"
            f"{marker['authored_count']} questions) requires --allow-partial"
        ]
    return []


def quarantine(pack_path: Path, quarantined_qids: list[str], reason: str) -> dict:
    """Remove questions from an installed pack and record the removal.

    The named questions are removed from the pack file, the full removed
    set (with original positions) and the free-text reason are written to the
    sidecar, and a ``partial_install`` marker is added to the pack. The
    pack's certification is left untouched: removing questions makes it stale
    by construction, and the only route that may write a certification stamp
    is ``certify_campaign`` (INV-7).

    Args:
        pack_path: The pack file to reduce. Its parent is the course
            directory.
        quarantined_qids: The ids of the questions to remove. At least one,
            each present in the pack, and never all of them.
        reason: The free-text reason for the removal. Stored only in the
            sidecar.

    Returns:
        The ``partial_install`` marker written into the pack.

    Raises:
        PackQuarantineError: If the pack cannot be read, is already partial,
            the ids are malformed, unknown, or cover every question, or the
            reason is blank.
    """
    data = _load_pack(pack_path)
    if MARKER_KEY in data:
        raise PackQuarantineError(
            "pack is already partial; restore it before quarantining again")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise PackQuarantineError("pack questions must be a non-empty list")
    ids = _question_ids(questions)
    if not isinstance(quarantined_qids, list) or not quarantined_qids:
        raise PackQuarantineError(
            "quarantined_qids must name at least one question")
    if (any(not isinstance(qid, str) or not qid for qid in quarantined_qids)
            or len(set(quarantined_qids)) != len(quarantined_qids)):
        raise PackQuarantineError(
            "quarantined_qids must be unique non-empty strings")
    unknown = [qid for qid in quarantined_qids if qid not in ids]
    if unknown:
        raise PackQuarantineError(
            f"unknown question id(s): {', '.join(unknown)}")
    if len(set(quarantined_qids)) >= len(questions):
        raise PackQuarantineError(
            "quarantine must retain at least one question")
    if not isinstance(reason, str) or not reason.strip():
        raise PackQuarantineError("reason must be a non-blank string")

    dropped = set(quarantined_qids)
    removed = [
        {"index": index, "question": question}
        for index, question in enumerate(questions)
        if question["id"] in dropped
    ]
    retained = [question for question in questions
                if question["id"] not in dropped]
    record = {
        "course": pack_path.parent.name,
        "pack": pack_path.name,
        "reason": reason,
        "removed_questions": removed,
    }
    marker = {
        "authored_count": len(questions),
        "installed_count": len(retained),
        "quarantined_ids": [entry["question"]["id"] for entry in removed],
        "record_digest": record_digest(record),
    }
    # Sidecar first: a marker naming a missing sidecar is refused by the gate,
    # while a sidecar without a marker is inert, so a failed pack write can
    # only leave the pack whole.
    sidecar = sidecar_path(pack_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    data["questions"] = retained
    data[MARKER_KEY] = marker
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return marker


def restore(pack_path: Path) -> dict:
    """Reinsert the quarantined questions and clear the partial record.

    The removed questions return at their original positions, the
    ``partial_install`` marker is removed, and the sidecar is deleted, so the
    pack is whole again. The pack's certification is left untouched: it went
    stale when the questions were removed and must be re-earned (INV-7).

    Args:
        pack_path: The partial pack file to restore.

    Returns:
        A record with ``restored_qids`` (in original pack order) and the
        restored ``question_count``.

    Raises:
        PackQuarantineError: If the pack carries no marker, or the marker or
            sidecar fails validation (missing, tampered, or inconsistent), or
            a removed question is already installed again.
    """
    data = _load_pack(pack_path)
    if MARKER_KEY not in data:
        raise PackQuarantineError("no active pack quarantine to restore")
    reasons = _record_reasons(pack_path, data)
    if reasons:
        raise PackQuarantineError("; ".join(reasons))
    questions = data["questions"]
    present_ids = set(_question_ids(questions))
    sidecar = sidecar_path(pack_path)
    record = json.loads(sidecar.read_text())
    restored: list[str] = []
    for entry in record["removed_questions"]:
        question = entry["question"]
        if question["id"] in present_ids:
            raise PackQuarantineError(
                f"question {question['id']!r} is already installed")
        index = entry["index"]
        if index > len(questions):
            raise PackQuarantineError(
                f"sidecar index {index} falls outside the pack")
        questions.insert(index, question)
        present_ids.add(question["id"])
        restored.append(question["id"])
    del data[MARKER_KEY]
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    sidecar.unlink()
    return {"restored_qids": restored, "question_count": len(questions)}


def main(argv: list[str] | None = None) -> int:
    """Run the ``quarantine`` or ``restore`` command; progress goes to stderr."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    set_aside = sub.add_parser("quarantine", help="remove questions into the sidecar")
    set_aside.add_argument("--pack", type=Path, required=True)
    set_aside.add_argument("--qid", action="append", required=True)
    set_aside.add_argument("--reason", required=True)
    put_back = sub.add_parser("restore", help="reinsert the sidecar's questions")
    put_back.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "quarantine":
            print(f"pack_quarantine: quarantining {len(args.qid)} question(s) in {args.pack}",
                  file=sys.stderr, flush=True)
            result = quarantine(args.pack, args.qid, args.reason)
        else:
            print(f"pack_quarantine: restoring {args.pack}", file=sys.stderr, flush=True)
            result = restore(args.pack)
    except PackQuarantineError as error:
        print(f"pack_quarantine: REFUSED {error}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
