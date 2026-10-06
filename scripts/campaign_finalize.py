"""Deterministic campaign finalization: stamp a pack from frozen evidence.

``certify_campaign`` is the only route that writes a final certification stamp
(INV-7). It never invokes a reviewer: it holds a per-pack lock, reads the pack
once, re-checks frozen campaign evidence, reruns Layer A on that same parse and
writes the stamp. ``hybrid_verify`` re-exports it for its CLI.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_evidence
import campaign_quarantine
import certification_campaign
import verifier_profiles
import verify_pack

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# M0c: per-pack finalization locks. Created on demand; .logs/ is gitignored.
LOCK_DIR = PROJECT_ROOT / ".logs" / "locks"


def _finalization_lock_path(pack: Path) -> Path:
    """Return the exclusive finalization lock path for one pack.

    The lock is keyed by the pack's course directory and filename — the same
    identity the campaign snapshot freezes — and lives under ``.logs/locks/``
    so it can never collide with discovery evidence or an installable pack.
    """
    return LOCK_DIR / f"{pack.parent.name}__{pack.name}.lock"


def certify_campaign(pack: Path, ledger_path: Path) -> tuple[int, str]:
    """Stamp from frozen campaign evidence without invoking either reviewer.

    Every question must carry clean high-verifier evidence for its *current*
    content: a complete base census that raised no blocking finding on it, or a
    targeted recheck in some remediation round that graded it clean at exactly
    that content. This route reruns only deterministic Layer-A structure checks
    and recomputes the exact current snapshot before writing provenance-bound
    certification metadata, which names the chained round that produced it.

    M0c (finding 2) — finalization reads the pack once and is serialized per
    pack. A non-blocking exclusive ``flock`` under ``.logs/locks/`` refuses a
    concurrent finalizer with "finalization in progress" instead of queueing a
    second writer behind it. The pack bytes are read exactly once: the
    eligibility snapshot, Layer A, and the stamp registry are all built from
    that single parse, and the pack bytes plus the course grounding inputs are
    re-read immediately before the atomic replace, so a mid-run edit can only
    refuse the stamp. The remaining window is stated explicitly: an edit that
    lands between that final re-check and the replace is overwritten — a lost
    update, never a stamp over unreviewed content.
    """
    try:
        lock_path = _finalization_lock_path(pack)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 2, "campaign certification refused: finalization in progress"
            return _certify_campaign_locked(pack, ledger_path)
    except (certification_campaign.CampaignError, KeyError, OSError,
            UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return 2, f"campaign certification refused: {exc}"


def _certify_campaign_locked(pack: Path, ledger_path: Path) -> tuple[int, str]:
    """Run the deterministic finalization while holding the pack's lock.

    The pack is read exactly once here; every downstream consumer (the
    eligibility snapshot, Layer A, the stamp registry) receives that parse,
    and ``verify_pack._write_certification`` re-reads the pack bytes and the
    course grounding inputs immediately before the atomic replace.
    """
    ledger = certification_campaign.load_ledger(ledger_path)
    profile_name = ledger["snapshot"]["critic_contract"]["profile"]
    profile = verifier_profiles.get_profile(profile_name)
    raw = pack.read_bytes()
    raw_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    current = verify_pack.build_snapshot_from_data(
        pack, data, verifier_profile=profile_name)
    eligible, reasons = certification_campaign.certification_eligibility(
        ledger, current_snapshot=current
    )
    if not eligible:
        return 2, "campaign certification refused: " + "; ".join(reasons)
    # The stamp boundary recomputes the per-question evidence from the ledger
    # itself instead of trusting the eligibility decision above, and is the
    # call site where the later pack-derived ``inherited`` source plugs in.
    evidence_reasons = campaign_evidence.evidence_sources(
        ledger, profile=profile.name, pack=pack)[1]
    if evidence_reasons:
        return 2, "campaign certification refused: " + "; ".join(evidence_reasons)
    structure = verify_pack.run_layer_a(pack, parsed_data=data)
    if not isinstance(structure, dict) or structure.get("live"):
        return 2, "campaign certification refused: deterministic structure checks are not clean"
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != len(current["question_ids"]):
        return 2, "campaign certification refused: current question coverage is malformed"
    # Eligibility already proved the pack matches the campaign's frontier, so
    # the stamp binds to that frontier -- the state that stays correct as
    # quarantine becomes part of the chain.
    frontier = campaign_evidence.campaign_frontier(ledger)
    provenance = {
        "kind": "frozen-campaign-evidence",
        "evidence_policy": "no-new-llm-call",
        "campaign_snapshot_fingerprint": frontier["fingerprint"],
        "base_snapshot_fingerprint": ledger["snapshot"]["fingerprint"],
        "verifier_profile": profile.name,
        "verifier_provider": profile.provider,
        "verifier_model": profile.model,
        "remediation_qids": sorted({
            qid
            for entry in ledger.get("remediation_rounds") or []
            for qid in entry["declared_changed_qids"]
        }),
    }
    # A stamp written from a quarantined frontier names the questions it set
    # aside, so a reader can tell which reviewed content the certification
    # deliberately excludes. The key stays absent when no quarantine is active.
    quarantined = campaign_quarantine.quarantined_qids(ledger)
    if quarantined:
        provenance["quarantined_qids"] = sorted(quarantined)
    rounds = len(ledger.get("remediation_rounds") or [])
    if rounds:
        provenance["remediation_round"] = rounds
    verify_pack._write_certification(
        pack,
        model=profile.model,
        questions_examined=len(questions),
        provider=profile.provider,
        requested_model=profile.model,
        reasoning_effort=profile.reasoning_effort,
        provenance=provenance,
        data=data,
        expected_sha256=raw_sha,
        expected_fingerprint=current["fingerprint"],
    )
    return 0, json.dumps({"certified": True, "provenance": provenance}, indent=2)
