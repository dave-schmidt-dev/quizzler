#!/usr/bin/env python3
"""Campaign quarantine: shrink a frontier to a retained question subset (INV-7).

Quarantine lets a campaign certify the questions that carry clean verifier
evidence while setting aside the ones that do not.  The reduced frontier must
keep every frozen non-question certification input identical to the campaign's
anchor -- pack name, pack identity (course and pack_id), question context
(subject and source_directive), waivers, grounding and critic contract -- and
may only drop questions, in order, without editing the ones it keeps.  This
module only edits the evidence ledger: it never invokes a reviewer, writes a
pack, or stamps a certification.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_evidence
from campaign_snapshot import FROZEN_FIELDS, CampaignError, _validate_snapshot


def _validate_ledger(ledger: Any) -> None:
    """Raise :class:`CampaignError` unless the ledger can anchor a quarantine.

    This is the subset of the ledger contract quarantine needs; the full
    validator lives with the ledger writers in ``certification_campaign.py``,
    which imports this module and therefore cannot be imported back.
    """
    if not isinstance(ledger, dict):
        raise CampaignError("ledger must be an object")
    _validate_snapshot(ledger.get("snapshot"))
    if not isinstance(ledger.get("discoveries"), list):
        raise CampaignError("ledger discoveries must be a list")
    if not isinstance(ledger.get("blockers"), list):
        raise CampaignError("ledger blockers must be a list")


def _is_ordered_subset(anchor_ids: list[str], reduced_ids: list[str]) -> bool:
    """Return whether ``reduced_ids`` is an in-order subsequence of the anchor."""
    index = 0
    for qid in anchor_ids:
        if index < len(reduced_ids) and reduced_ids[index] == qid:
            index += 1
    return index == len(reduced_ids)


def _has_valid_base_source(ledger: dict, *, profile: str) -> bool:
    """Return whether a valid base evidence source cleared at least one question.

    The precondition is a valid base source -- the base census now, the later
    cross-campaign ``inherited`` source when it lands -- not a *complete* base
    census.  A census that blocks every question mints no base pair and cannot
    anchor a quarantine.  Round-recheck pairs are deliberately not counted:
    they are scoped to a remediation round, not to the campaign's frozen base.
    """
    pairs, _reasons = campaign_evidence.evidence_sources(ledger, profile=profile)
    return any(source == campaign_evidence.BASE_CENSUS_SOURCE
               for source in pairs.values())


def quarantined_qids(ledger: dict) -> tuple[str, ...]:
    """Return the ledger's quarantined question ids, validating the record.

    Returns an empty tuple when no quarantine is active.  A present quarantine
    whose ``quarantined_qids`` is missing or malformed raises
    :class:`CampaignError`, so a hand-altered ledger cannot silently widen the
    certification frontier or hide a retained question's blocker.

    Args:
        ledger: A campaign ledger.

    Returns:
        The quarantined question ids in the order they were recorded.

    Raises:
        CampaignError: If a present quarantine record is malformed.
    """
    quarantine = ledger.get("quarantine")
    if quarantine is None:
        return ()
    if not isinstance(quarantine, dict):
        raise CampaignError("ledger quarantine must be an object")
    qids = quarantine.get("quarantined_qids")
    if (not isinstance(qids, list) or not qids
            or any(not isinstance(qid, str) or not qid for qid in qids)
            or len(set(qids)) != len(qids)):
        raise CampaignError(
            "ledger quarantine requires non-empty unique quarantined_qids")
    return tuple(qids)


def begin_quarantine(ledger: dict, current_snapshot: dict,
                     quarantined_qids: list[str] | None = None) -> dict:
    """Freeze a reduced question subset as the campaign's certification frontier.

    The reduced snapshot must keep every :data:`FROZEN_FIELDS` value identical
    to the campaign's anchor (the current frontier): pack name, pack identity,
    question context, waivers, grounding and critic contract.  Only
    ``question_ids``, ``question_hashes`` and ``questions_hash`` may differ,
    and they may only shrink with the anchor's order preserved; every retained
    question must keep the exact content hash it had at the anchor.  A valid
    base evidence source must already exist, so quarantine cannot be used to
    set aside an entire un-reviewed campaign.

    Args:
        ledger: The campaign ledger to reduce.
        current_snapshot: Snapshot of the pack with the quarantined questions
            removed.
        quarantined_qids: Optional explicit list of dropped question ids.  When
            given it must name exactly the base-snapshot questions the reduced
            snapshot omits, so a caller cannot quarantine a question the
            retained pack still carries.

    Returns:
        The mutated ledger with its ``quarantine`` record set.

    Raises:
        CampaignError: If the reduction changes a frozen input, is not a
            strict in-order shrink, edits a retained question, names the wrong
            quarantined ids, or has no valid base evidence source.
    """
    _validate_ledger(ledger)
    _validate_snapshot(current_snapshot)
    anchor = campaign_evidence.campaign_frontier(ledger)
    for field in FROZEN_FIELDS:
        if current_snapshot.get(field) != anchor.get(field):
            raise CampaignError(f"quarantine cannot change {field}")
    anchor_ids = anchor["question_ids"]
    reduced_ids = current_snapshot["question_ids"]
    if (len(reduced_ids) >= len(anchor_ids)
            or not _is_ordered_subset(anchor_ids, reduced_ids)):
        raise CampaignError(
            "quarantine must drop at least one question, preserving the "
            "anchor's question order")
    for qid in reduced_ids:
        if (current_snapshot["question_hashes"].get(qid)
                != anchor["question_hashes"].get(qid)):
            raise CampaignError(
                f"quarantine cannot change the content of retained question {qid}")
    profile = ledger["snapshot"]["critic_contract"]["profile"]
    if not _has_valid_base_source(ledger, profile=profile):
        raise CampaignError("quarantine requires a valid base evidence source")
    reduced_set = set(reduced_ids)
    dropped = [qid for qid in ledger["snapshot"]["question_ids"]
               if qid not in reduced_set]
    if quarantined_qids is not None:
        if (not isinstance(quarantined_qids, list)
                or any(not isinstance(qid, str) or not qid
                       for qid in quarantined_qids)
                or len(set(quarantined_qids)) != len(quarantined_qids)
                or set(quarantined_qids) != set(dropped)):
            raise CampaignError(
                "quarantined_qids must name exactly the questions the reduced "
                "snapshot drops")
    ledger["quarantine"] = {
        "anchor_snapshot_fingerprint": anchor["fingerprint"],
        "snapshot": copy.deepcopy(current_snapshot),
        "quarantined_qids": dropped,
    }
    return ledger


def release_quarantine(ledger: dict) -> dict:
    """Drop the active quarantine so the campaign's previous frontier returns.

    Release does not itself re-admit the quarantined questions: the pack on
    disk must again match the restored frontier before any evidence or stamp is
    accepted.  Releasing a ledger with no active quarantine is refused so an
    operator mistake cannot be mistaken for a successful release.

    Args:
        ledger: The campaign ledger carrying an active quarantine.

    Returns:
        The mutated ledger with no quarantine record.

    Raises:
        CampaignError: If the ledger is not an object or carries no active
            quarantine.
    """
    if not isinstance(ledger, dict):
        raise CampaignError("ledger must be an object")
    if not isinstance(ledger.get("quarantine"), dict):
        raise CampaignError("no active quarantine to release")
    ledger["quarantine"] = None
    return ledger
