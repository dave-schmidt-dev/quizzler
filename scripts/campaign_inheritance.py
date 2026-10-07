#!/usr/bin/env python3
"""Cross-campaign census inheritance for a new campaign ledger (M6, INV-7).

A prior campaign that ended in a real certification stamp carries, question
by question, exactly the evidence a new campaign would otherwise pay a full
census for.  ``inherit_ledger`` admits that prior census into a brand-new
ledger under narrow limits: the prior must be a version-2 campaign that is
itself certification-eligible at its frontier, must never have been
inherited or quarantined, must carry an issuance receipt binding the pack's
certification block to that frontier, and must agree with the new base
snapshot on every frozen input.  Any change to the subject, source
directive, waivers, grounding, verifier profile or model, or pack identity
refuses the inheritance entirely.

Per question, a qid inherits only when its pack stamp exists, equals the
receipt's stamp, equals the question's current content hash, and its
full-dict hash still equals the prior frontier's hash.  A missing, deleted,
altered or stale stamp, or a new qid, leaves that qid uncovered without
aborting the inheritance; uncovered qids get a tool-computed round 1 of kind
``inheritance-recheck`` anchored on the base, so only they pay a reviewer
call.  The prior ledger and certification block are embedded verbatim --
the pack block is overwritten at the next stamp -- and every later reader
recomputes the inherited evidence from those copies (see
``campaign_evidence.inherited_pairs``), never from the stored qid lists.

This module never invokes a reviewer, writes a pack, or stamps a
certification; the only stamping route stays ``campaign_finalize``.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_evidence
import certification_campaign
import issuance_receipt
import pack_cert
import pack_quarantine
from campaign_snapshot import (FROZEN_FIELDS, SNAPSHOT_VERSION, CampaignError,
                               _digest, build_snapshot_from_data)

# The remediation-round kind that marks a round as tool-computed inheritance
# coverage rather than a question fix.  The finalizer's provenance excludes
# these qids from ``remediation_qids``.
RECHECK_ROUND_KIND = "inheritance-recheck"


def _load_pack_data(pack_path: Path) -> dict:
    """Return the parsed pack, refusing unreadable or non-object input.

    Args:
        pack_path: The pack file to read once.

    Returns:
        The parsed pack object.

    Raises:
        CampaignError: If the pack cannot be read as a JSON object.
    """
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read pack: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignError("pack root must be a JSON object")
    return data


def _pack_block(data: dict) -> dict:
    """Return the pack's certification block, refusing an uncertified pack.

    Args:
        data: The parsed pack the new campaign certifies.

    Returns:
        The certification block the prior campaign wrote.

    Raises:
        CampaignError: If the pack carries no current, provenance-bound,
            non-quarantined certification block.
    """
    block = data.get("certification")
    if not isinstance(block, dict):
        raise CampaignError("the pack carries no certification block to inherit")
    if block.get("certified") is not True:
        raise CampaignError("the pack's certification block is not certified")
    if block.get("hash_schema_version") != pack_cert.HASH_SCHEMA_VERSION:
        raise CampaignError(
            "the pack's certification hash schema version is not current")
    if block.get("critic_contract_version") != pack_cert.CRITIC_CONTRACT_VERSION:
        raise CampaignError(
            "the pack's certification critic contract version is not current")
    provenance = block.get("provenance")
    if not isinstance(provenance, dict):
        raise CampaignError(
            "the pack's certification block carries no campaign provenance")
    if "quarantined_qids" in provenance:
        raise CampaignError("a quarantined certification cannot be inherited")
    if not isinstance(block.get("question_stamps"), dict):
        raise CampaignError("the pack's certification stamp registry is malformed")
    return block


def _refuse_quarantined_pack(pack_path: Path, data: dict) -> None:
    """Refuse inheritance for a pack that ever set questions aside.

    Args:
        pack_path: The pack file the new campaign certifies.
        data: The parsed pack.

    Raises:
        CampaignError: If the pack carries a ``partial_install`` marker or a
            quarantine sidecar.
    """
    if data.get(pack_quarantine.MARKER_KEY) is not None:
        raise CampaignError("a partially installed pack cannot be inherited")
    if pack_quarantine.sidecar_path(pack_path).exists():
        raise CampaignError("a pack with a quarantine sidecar cannot be inherited")


def inherit_ledger(pack_path: Path, prior_ledger_path: Path, *,
                   verifier_profile: str | None = None) -> dict:
    """Return a new campaign ledger that inherits a prior certified census.

    The pack is read once; the prior ledger is loaded and validated with the
    standard ledger loader.  Inheritance is refused entirely unless every
    whole-inheritance condition holds; on refusal nothing is minted and the
    caller must start a fresh campaign that pays one census.

    Args:
        pack_path: The pack the new campaign certifies.  It still carries the
            prior campaign's certification block.
        prior_ledger_path: The prior campaign's evidence ledger.
        verifier_profile: Optional verifier profile for the new campaign;
            it must match the prior campaign's frozen critic contract.

    Returns:
        The new ledger, carrying an ``inheritance`` record with the prior
        ledger and certification block embedded verbatim, plus a round 1 of
        kind ``inheritance-recheck`` anchored on the base when any question
        is uncovered.

    Raises:
        CampaignError: If inheritance is refused, or the pack or the prior
            ledger cannot be read.
    """
    data = _load_pack_data(pack_path)
    base = build_snapshot_from_data(pack_path, data,
                                    verifier_profile=verifier_profile)
    prior = certification_campaign.load_ledger(prior_ledger_path)
    if prior["snapshot"].get("snapshot_version") != SNAPSHOT_VERSION:
        raise CampaignError(
            "only a current campaign snapshot can be inherited; the prior "
            "campaign must restart with a fresh census")
    if prior.get("inheritance") is not None:
        raise CampaignError("an inherited campaign cannot be inherited again")
    if prior.get("quarantine") is not None:
        raise CampaignError("a quarantined campaign cannot be inherited")
    _refuse_quarantined_pack(pack_path, data)
    block = _pack_block(data)
    frontier = campaign_evidence.campaign_frontier(prior)
    eligible, reasons = certification_campaign.certification_eligibility(
        prior, current_snapshot=frontier)
    if not eligible:
        raise CampaignError(
            "the prior campaign is not certification-eligible: "
            + "; ".join(reasons))
    for field in FROZEN_FIELDS:
        if frontier.get(field) != base.get(field):
            raise CampaignError(f"inheritance cannot accept a changed {field}")
    receipts = prior.get("issuance_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise CampaignError(
            "the prior campaign carries no issuance receipt; its stamp "
            "cannot be inherited")
    receipt = receipts[-1]
    if receipt["campaign_snapshot_fingerprint"] != frontier["fingerprint"]:
        raise CampaignError(
            "the prior campaign's issuance receipt does not match its frontier")
    if issuance_receipt.header_digest(block) != receipt["certification_header_digest"]:
        raise CampaignError(
            "the pack's certification block does not match the prior "
            "campaign's issuance receipt")
    if set(block["question_stamps"]) - set(receipt["question_stamps"]):
        raise CampaignError(
            "the pack's stamp registry names a question the prior campaign's "
            "receipt does not")
    record = {
        "prior_ledger": copy.deepcopy(prior),
        "prior_certification": copy.deepcopy(block),
        "prior_campaign_snapshot_fingerprint": frontier["fingerprint"],
        "prior_receipt_digest": _digest(receipt),
        "inherited_qids": [],
        "inheritance_recheck_qids": [],
    }
    pairs, pair_reasons = campaign_evidence.inherited_pairs(
        record, base=base, profile=base["critic_contract"]["profile"],
        pack_data=data)
    if pair_reasons:
        raise CampaignError("; ".join(pair_reasons))
    inherited = [qid for qid, _content_hash in pairs]
    uncovered = [qid for qid in base["question_ids"] if qid not in set(inherited)]
    record["inherited_qids"] = inherited
    record["inheritance_recheck_qids"] = uncovered
    ledger = certification_campaign.new_ledger(base)
    ledger["inheritance"] = record
    if uncovered:
        ledger["remediation_rounds"].append({
            "round": 1,
            "kind": RECHECK_ROUND_KIND,
            "base_snapshot_fingerprint": base["fingerprint"],
            "snapshot": copy.deepcopy(base),
            "declared_changed_qids": list(uncovered),
            "targeted_rechecks": [],
        })
        campaign_evidence._normalize_rounds(ledger)
    return ledger
