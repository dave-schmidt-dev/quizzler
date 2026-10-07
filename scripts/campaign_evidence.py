#!/usr/bin/env python3
"""Campaign frontier and evidence-source read APIs (INV-7).

Every ledger reader asks the same two questions: which frozen state the
campaign is currently at, and which question content carries clean
configured-verifier evidence.  This module owns both answers so
``certification_campaign.py`` and the deterministic finalizer in
``hybrid_verify.py`` cannot drift apart as quarantine (the frontier's first
rule) and cross-campaign evidence inheritance (the ``inherited`` source)
land.  Nothing here writes a ledger, invokes a reviewer, or certifies.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factcheck_pack
import issuance_receipt
import pack_cert
from campaign_snapshot import (FROZEN_FIELDS, CampaignError, _digest,
                               _validate_snapshot)

# Source labels assigned by ``evidence_sources``.  An ``inherited`` label marks
# clean evidence carried over from a prior certified campaign's census (M6);
# it is minted only by recomputing the ledger's embedded prior copies against
# the pack, never from a stored qid list.
BASE_CENSUS_SOURCE = "base-census"
ROUND_RECHECK_SOURCE = "round-recheck"
INHERITED_SOURCE = "inherited"

# The exact shape of a ledger's ``inheritance`` record (M6): the embedded
# prior ledger and certification block, the digests that bind them, and the
# informational qid lists the recompute below never trusts.
INHERITANCE_RECORD_FIELDS = frozenset({
    "prior_ledger",
    "prior_certification",
    "prior_campaign_snapshot_fingerprint",
    "prior_receipt_digest",
    "inherited_qids",
    "inheritance_recheck_qids",
})


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


def _resolve_pack_data(pack: Path | None, data: Any) -> tuple[Any, str | None]:
    """Return the parsed pack content, preferring the caller's single parse.

    Args:
        pack: Optional on-disk pack the campaign certifies.
        data: Optional already-parsed pack (the M0c finalization parse).

    Returns:
        A ``(pack_data, problem)`` tuple; ``problem`` is None when the pack
        content is available for the inherited recompute.
    """
    if isinstance(data, dict):
        return data, None
    if pack is not None:
        try:
            return json.loads(Path(pack).read_text(encoding="utf-8")), None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, f"cannot read the pack for inherited evidence: {exc}"
    return None, "inherited evidence requires the pack or its parsed data"


def _record_list_problem(value: Any) -> str | None:
    """Return why an inheritance-record qid list is malformed, else None."""
    if (not isinstance(value, list)
            or any(not isinstance(qid, str) or not qid for qid in value)
            or len(set(value)) != len(value)):
        return "must be a list of unique non-blank question ids"
    return None


def inherited_pairs(record: Any, *, base: dict, profile: str,
                    pack_data: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """Recompute the inherited evidence pairs from the embedded prior copies.

    Nothing in the record's qid lists is trusted: the pairs are re-derived
    from the embedded prior ledger and certification block, the digest that
    binds them, and the pack's current question content.  A qid inherits only
    when its pack stamp exists, equals the prior receipt's stamp, equals the
    current content hash of that question, and its full-dict hash still
    equals the prior frontier's hash.  A missing, altered or stale stamp, or
    a new qid, simply leaves that qid uncovered; a tampered embedded copy
    withholds every inherited pair.

    Args:
        record: The ledger's ``inheritance`` record.
        base: The campaign's base snapshot.
        profile: The configured verifier profile that may mint clean evidence.
        pack_data: The parsed pack the campaign certifies.

    Returns:
        A ``(pairs, reasons)`` tuple.  ``pairs`` are the inherited
        ``(qid, content hash)`` pairs; ``reasons`` is non-empty when the
        embedded copies cannot be trusted, in which case ``pairs`` is empty.
    """
    reasons: list[str] = []
    if not isinstance(record, dict) or set(record) != set(INHERITANCE_RECORD_FIELDS):
        return [], ["the ledger's inheritance record is malformed"]
    for name in ("inherited_qids", "inheritance_recheck_qids"):
        problem = _record_list_problem(record[name])
        if problem:
            reasons.append(f"inheritance record {name} {problem}")
    prior = record["prior_ledger"]
    block = record["prior_certification"]
    if not isinstance(prior, dict) or not isinstance(block, dict):
        reasons.append("the embedded prior campaign is malformed")
        return [], reasons
    try:
        from campaign_inheritance import prior_refusal_reasons
        prior_reasons = prior_refusal_reasons(prior, base=base)
    except Exception as exc:
        prior_reasons = [f"the embedded prior campaign is malformed: {exc}"]
    if prior_reasons:
        reasons.extend(prior_reasons)
        return [], reasons
    try:
        frontier = campaign_frontier(prior)
    except (CampaignError, KeyError, TypeError, ValueError) as exc:
        reasons.append(f"the embedded prior campaign is malformed: {exc}")
        return [], reasons
    if record["prior_campaign_snapshot_fingerprint"] != frontier["fingerprint"]:
        reasons.append(
            "the embedded prior frontier does not match the inheritance record")
    if frontier.get("critic_contract", {}).get("profile") != profile:
        reasons.append(
            "inherited evidence must come from the configured verifier profile")
    receipts = prior.get("issuance_receipts")
    if not isinstance(receipts, list) or not receipts:
        reasons.append("the embedded prior campaign carries no issuance receipt")
        return [], reasons
    receipt = receipts[-1]
    try:
        if _digest(receipt) != record["prior_receipt_digest"]:
            reasons.append(
                "the embedded issuance receipt does not match the inheritance record")
    except Exception as exc:
        reasons.append(f"the embedded issuance receipt is malformed: {exc}")
    try:
        if issuance_receipt.header_digest(block) != receipt.get("certification_header_digest"):
            reasons.append(
                "the embedded certification block does not match the issuance receipt")
    except Exception as exc:
        reasons.append(f"the embedded certification block is malformed: {exc}")
    stamps = block.get("question_stamps")
    receipt_stamps = receipt.get("question_stamps")
    if not isinstance(stamps, dict) or not isinstance(receipt_stamps, dict):
        reasons.append("the embedded stamp registries are malformed")
    elif set(stamps) - set(receipt_stamps):
        reasons.append(
            "the embedded stamp registry names a question the receipt does not")
    if reasons:
        return [], reasons
    questions = pack_data.get("questions") if isinstance(pack_data, dict) else None
    if not isinstance(questions, list):
        return [], ["the pack questions are malformed"]
    by_id = {
        question["id"]: question
        for question in questions
        if isinstance(question, dict) and isinstance(question.get("id"), str)
    }
    pairs: list[tuple[str, str]] = []
    for qid in base["question_ids"]:
        stamp = stamps.get(qid)
        question = by_id.get(qid)
        if stamp is None or stamp != receipt_stamps.get(qid) or question is None:
            continue
        try:
            if stamp != pack_cert.question_content_hash(question, pack_data):
                continue
        except (TypeError, ValueError):
            continue
        if frontier["question_hashes"].get(qid) != base["question_hashes"].get(qid):
            continue
        pairs.append((qid, base["question_hashes"][qid]))
    return pairs, []


def evidence_sources(ledger: dict, *, profile: str,
                     pack: Path | None = None, data: Any = None
                     ) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Return the clean-verifier evidence every question content carries.

    Evidence is bound to the exact question content it graded, so pairs are
    keyed by ``(qid, content hash)`` and each pair maps to the source that
    graded it clean: ``base-census`` for a complete base census that raised no
    blocking finding on the question, ``round-recheck`` for a targeted
    recheck in some remediation round that graded it clean at exactly that
    content, or ``inherited`` for evidence a prior certified campaign's
    census carried over at exactly that content.  Every source is validated
    and the cleared set is recomputed from the stored reviewer reports (or,
    for the inherited source, from the embedded prior copies); a stored
    ``valid`` or ``cleared_qids`` field is never trusted.

    Args:
        ledger: A campaign ledger.
        profile: The configured verifier profile that may mint clean evidence.
        pack: Optional on-disk pack the campaign certifies.  The inherited
            source needs the pack's current content; without it (or ``data``)
            a refusal reason is returned.
        data: Optional already-parsed pack (the M0c finalization parse),
            preferred over reading ``pack``.

    Returns:
        A ``(pairs, reasons)`` tuple.  ``pairs`` maps every cleared
        ``(qid, content hash)`` pair to its source label, with a round
        recheck outranking a census that cleared the same content.  ``reasons``
        is non-empty when the ledger carries no complete, blocking-free
        high-verifier base census and no inheritance record, or when the
        inherited source cannot be recomputed; the round-recheck pairs remain
        available so the loose pre-final gate can still ask its
        changed-question question while the strict route withholds everything.
    """
    base = ledger["snapshot"]
    question_ids = set(base["question_ids"])
    pairs: dict[tuple[str, str], str] = {}
    reasons: list[str] = []
    inheritance = ledger.get("inheritance")
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
        if inheritance is None:
            # A chain of rounds is a safe substitute for a second full census
            # only on top of one complete base census; without it nothing may
            # certify.  An inheritance record substitutes its inherited
            # source for that census, so the census reason is withheld and
            # the inherited recompute below owns the refusal instead.
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
    if inheritance is not None:
        pack_data, problem = _resolve_pack_data(pack, data)
        if problem is not None:
            reasons.append(problem)
        else:
            inherited, inheritance_reasons = inherited_pairs(
                inheritance, base=base, profile=profile, pack_data=pack_data)
            reasons.extend(inheritance_reasons)
            for qid, content_hash in inherited:
                pairs.setdefault((qid, content_hash), INHERITED_SOURCE)
    return pairs, reasons
