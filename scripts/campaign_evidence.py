#!/usr/bin/env python3
"""Campaign frontier and evidence-source read APIs (INV-7).

Every ledger reader asks the same two questions: which frozen state the
campaign is currently at, and which question content carries clean
configured-verifier evidence.  This module owns both answers so
``certification_campaign.py`` and the deterministic finalizer in
``hybrid_verify.py`` cannot drift apart as quarantine (the frontier's first
rule) and cross-campaign evidence inheritance (a later ``inherited`` source)
land.  Nothing here writes a ledger, invokes a reviewer, or certifies.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factcheck_pack
from campaign_snapshot import CampaignError, _validate_snapshot

# Source labels assigned by ``evidence_sources``.  An ``inherited`` label is
# reserved for the later cross-campaign carryover and is not minted yet.
BASE_CENSUS_SOURCE = "base-census"
ROUND_RECHECK_SOURCE = "round-recheck"


def _finding_problem(finding: Any, question_ids: set[str]) -> str | None:
    if not isinstance(finding, dict):
        return "finding is not an object"
    qid = finding.get("qid")
    if not isinstance(qid, str) or not qid.strip():
        return "finding qid is missing"
    # A qid-less sentinel represents a real but unscoped critic finding.  It is
    # deliberately preserved as a malformed blocker instead of ignored or
    # treated as an advisory finding.
    if qid == "(no-qid)":
        return "finding qid is unscoped"
    if qid not in question_ids:
        return f"finding qid {qid!r} is outside the frozen snapshot"
    if not isinstance(finding.get("issue"), str) or not finding["issue"].strip():
        return "finding issue is missing"
    if finding.get("severity") not in factcheck_pack.SEVERITIES:
        return "finding severity is unrecognized"
    if finding.get("confidence") not in {"high", "medium", "low"}:
        return "finding confidence is unrecognized"
    return None


def _normalize_rounds(ledger: dict) -> list[dict]:
    """Return the ledger's remediation rounds, normalizing the pre-chain shape.

    A ledger written before chained remediation carries a single ``remediation``
    object.  It is read as round 1 so existing campaigns keep loading, and
    ``remediation`` is kept aliased to the newest round so readers that predate
    the chain -- including an older ``hybrid_verify.py`` -- still see the state
    they expect.  ``remediation_rounds`` is the canonical list.
    """
    rounds = ledger.get("remediation_rounds")
    if rounds is None:
        legacy = ledger.get("remediation")
        rounds = [legacy] if isinstance(legacy, dict) else []
    if not isinstance(rounds, list):
        raise CampaignError("ledger remediation_rounds must be a list")
    for index, entry in enumerate(rounds, start=1):
        if not isinstance(entry, dict):
            raise CampaignError("each remediation round must be an object")
        entry.setdefault("round", index)
        if entry["round"] != index:
            raise CampaignError("remediation rounds must be numbered consecutively from 1")
    ledger["remediation_rounds"] = rounds
    ledger["remediation"] = rounds[-1] if rounds else None
    return rounds


def _remediation_rounds(ledger: dict) -> list[dict]:
    """Return every remediation round after validating each one's shape."""
    rounds = _normalize_rounds(ledger)
    for entry in rounds:
        snapshot = entry.get("snapshot")
        _validate_snapshot(snapshot)
        declared = entry.get("declared_changed_qids")
        if (not isinstance(declared, list) or not declared
                or any(not isinstance(qid, str) or not qid for qid in declared)
                or len(set(declared)) != len(declared)):
            raise CampaignError(
                "remediation declared_changed_qids must be non-empty unique strings")
        if not set(declared).issubset(set(snapshot["question_ids"])):
            raise CampaignError("remediation changed ids are outside its snapshot")
        if not isinstance(entry.get("targeted_rechecks"), list):
            raise CampaignError("remediation targeted_rechecks must be a list")
    return rounds


def _recheck_cleared_qids(record: Any, *, snapshot: dict, profile: str) -> set[str]:
    """Return the qids one stored recheck proves clean, recomputed from evidence.

    The record's own ``valid`` and ``cleared_qids`` fields are deliberately not
    trusted: a hand-altered ledger must not be able to assert clean evidence it
    does not carry.  Everything is re-derived from the configured verifier's
    stored report, so a resolution note can never stand in for a review.
    """
    if not isinstance(record, dict):
        return set()
    if record.get("snapshot_fingerprint") != snapshot["fingerprint"]:
        return set()
    question_ids = set(snapshot["question_ids"])
    targets = record.get("target_qids")
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(qid, str) or not qid for qid in targets)
            or len(set(targets)) != len(targets)
            or not set(targets).issubset(question_ids)):
        return set()
    reports = record.get("reviewer_reports")
    if not isinstance(reports, list):
        return set()
    target_set = set(targets)
    cleared: set[str] = set()
    for report in reports:
        if (not isinstance(report, dict)
                or report.get("reviewer") != profile
                or report.get("complete") is not True
                or report.get("examined_qids") != targets):
            continue
        findings = report.get("findings")
        if not isinstance(findings, list):
            continue
        blocking: set[str] = set()
        scoped = True
        for finding in findings:
            if (_finding_problem(finding, question_ids) is not None
                    or finding["qid"] not in target_set):
                scoped = False
                break
            if factcheck_pack.is_blocking(finding):
                blocking.add(finding["qid"])
        if scoped:
            cleared |= target_set - blocking
    return cleared


def campaign_frontier(ledger: dict) -> dict:
    """Return the campaign's frontier: the newest state a final gate must match.

    The frontier is the quarantine snapshot when the ledger carries one, else
    the newest remediation round's snapshot, else the base snapshot.  A
    present-but-malformed quarantine fails closed rather than silently
    falling back to an older state.

    Args:
        ledger: A campaign ledger.

    Returns:
        The frozen snapshot the campaign is currently at.

    Raises:
        CampaignError: If a present quarantine entry is malformed, or a
            remediation round has an invalid shape.
    """
    quarantine = ledger.get("quarantine")
    if quarantine is not None:
        if not isinstance(quarantine, dict):
            raise CampaignError("ledger quarantine must be an object")
        snapshot = quarantine.get("snapshot")
        if snapshot is None:
            raise CampaignError("ledger quarantine requires a frozen snapshot")
        _validate_snapshot(snapshot)
        return snapshot
    rounds = _remediation_rounds(ledger)
    if rounds:
        return rounds[-1]["snapshot"]
    return ledger["snapshot"]


def evidence_sources(ledger: dict, *, profile: str,
                     pack: Path | None = None
                     ) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Return the clean-verifier evidence every question content carries.

    Evidence is bound to the exact question content it graded, so pairs are
    keyed by ``(qid, content hash)`` and each pair maps to the source that
    graded it clean: ``base-census`` for a complete base census that raised no
    blocking finding on the question, or ``round-recheck`` for a targeted
    recheck in some remediation round that graded it clean at exactly that
    content.  Every source is validated and the cleared set is recomputed from
    the stored reviewer reports; a stored ``valid`` or ``cleared_qids`` field
    is never trusted.

    Args:
        ledger: A campaign ledger.
        profile: The configured verifier profile that may mint clean evidence.
        pack: Optional on-disk pack the campaign certifies.  Reserved for the
            later ``inherited`` cross-campaign source; no inherited evidence
            is minted yet.

    Returns:
        A ``(pairs, reasons)`` tuple.  ``pairs`` maps every cleared
        ``(qid, content hash)`` pair to its source label, with a round
        recheck outranking a census that cleared the same content.  ``reasons``
        is non-empty when the ledger carries no complete, blocking-free
        high-verifier base census; the round-recheck pairs remain available
        so the loose pre-final gate can still ask its changed-question
        question while the strict route withholds everything.
    """
    base = ledger["snapshot"]
    question_ids = set(base["question_ids"])
    pairs: dict[tuple[str, str], str] = {}
    reasons: list[str] = []
    censuses = [
        entry for entry in ledger["discoveries"]
        if entry.get("reviewer") == profile
        and entry.get("snapshot_fingerprint") == base["fingerprint"]
        and entry.get("valid") is True
        and entry.get("complete") is True
        and entry.get("examined_qids") == base["question_ids"]
        and not entry.get("errors")
        and isinstance(entry.get("findings"), list)
        and all(_finding_problem(finding, question_ids) is None
                for finding in entry["findings"])
    ]
    if not censuses:
        # A chain of rounds is a safe substitute for a second full census only
        # on top of one complete base census; without it nothing may certify.
        reasons.append(
            "complete high-verifier discovery evidence without unresolved blocking findings is required")
    else:
        # Union the blocking findings across every usable census: a question
        # any census flagged needs its own clean recheck, even if another
        # census read it as clean.
        base_blocking = {
            finding["qid"] for entry in censuses for finding in entry["findings"]
            if factcheck_pack.is_blocking(finding)
        }
        for qid in base["question_ids"]:
            if qid not in base_blocking:
                pairs[(qid, base["question_hashes"][qid])] = BASE_CENSUS_SOURCE
    for entry in _remediation_rounds(ledger):
        snapshot = entry["snapshot"]
        for record in entry["targeted_rechecks"]:
            for qid in _recheck_cleared_qids(record, snapshot=snapshot, profile=profile):
                # A recheck is the newest evidence for the content it graded,
                # so its label outranks a census that cleared the same pair.
                pairs[(qid, snapshot["question_hashes"][qid])] = ROUND_RECHECK_SOURCE
    return pairs, reasons
