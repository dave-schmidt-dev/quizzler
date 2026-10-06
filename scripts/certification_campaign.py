#!/usr/bin/env python3
"""Evidence-only campaign ledger for batched pack-certification work.

The ledger coordinates non-certifying discovery and remediation.  It never
writes a pack, creates a certification stamp, or treats its own records as a
certification result.  ``hybrid_verify.py`` remains the only certification
route and must still run a full, live final gate.  Snapshot construction and
validation live in ``campaign_snapshot.py`` and are re-exported here because
``hybrid_verify`` calls ``certification_campaign.build_snapshot``.  The
frontier and evidence-source read APIs live in ``campaign_evidence.py`` and
are imported here so every ledger reader shares them.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_quarantine
import factcheck_pack
# pack_cert is re-exported for callers that reach it through this module.
import pack_cert  # noqa: F401
import verifier_profiles
# The frontier and evidence-source read APIs live in campaign_evidence; the
# finding and round validators are shared with the ledger writers below.
from campaign_evidence import (ROUND_RECHECK_SOURCE, _finding_problem,
                               _normalize_rounds, _remediation_rounds,
                               campaign_frontier, evidence_sources)
from campaign_snapshot import (FROZEN_FIELDS, CampaignError, _canonical,
                               _digest, _validate_snapshot, build_snapshot)
# Re-exported for existing callers; the implementation lives in campaign_snapshot.
from campaign_snapshot import _critic_contract, _grounding_evidence  # noqa: F401

LEDGER_VERSION = 1
LEDGER_KIND = "certification-campaign-evidence"
# Mirrors scripts/hybrid_verify.py's JSON_SCHEMA_VERSION.  Kept local so this
# ledger utility remains a pure consumer of saved JSON rather than importing a
# runner that may perform runtime CLI setup in the future.
HYBRID_JSON_SCHEMA_VERSION = 3


def new_ledger(snapshot: dict) -> dict:
    """Create an empty, evidence-only campaign ledger for ``snapshot``."""
    _validate_snapshot(snapshot)
    return {
        "ledger_version": LEDGER_VERSION,
        "kind": LEDGER_KIND,
        "snapshot": copy.deepcopy(snapshot),
        "discoveries": [],
        "blockers": [],
        "quarantine": None,
        "remediation": None,
        "remediation_rounds": [],
        "final_certification": {
            "required": True,
            "attempts": [],
            "note": "Only hybrid_verify.py can create the certification stamp.",
        },
    }


def _validate_ledger(ledger: Any) -> None:
    if not isinstance(ledger, dict):
        raise CampaignError("ledger must be an object")
    if ledger.get("ledger_version") != LEDGER_VERSION or ledger.get("kind") != LEDGER_KIND:
        raise CampaignError("unsupported campaign ledger")
    _validate_snapshot(ledger.get("snapshot"))
    for name in ("discoveries", "blockers"):
        if not isinstance(ledger.get(name), list):
            raise CampaignError(f"ledger {name} must be a list")
    remediation = ledger.get("remediation")
    if remediation is not None and not isinstance(remediation, dict):
        raise CampaignError("ledger remediation must be an object or null")
    _normalize_rounds(ledger)
    quarantine = ledger.get("quarantine")
    if quarantine is not None:
        quarantined = campaign_quarantine.quarantined_qids(ledger)
        _validate_snapshot(quarantine.get("snapshot"))
        retained = set(quarantine["snapshot"]["question_ids"])
        if retained & set(quarantined):
            raise CampaignError(
                "a quarantined question cannot remain in the quarantine snapshot")
        if not set(quarantined).issubset(set(ledger["snapshot"]["question_ids"])):
            raise CampaignError(
                "quarantined questions must come from the campaign snapshot")


def load_ledger(path: Path) -> dict:
    """Load a ledger from disk and reject altered or malformed state."""
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read ledger: {exc}") from exc
    _validate_ledger(ledger)
    return ledger


def save_ledger(path: Path, ledger: dict) -> None:
    """Persist a validated ledger using deterministic JSON."""
    _validate_ledger(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _report_problem(report: Any, snapshot: dict) -> str | None:
    if not isinstance(report, dict):
        return "report is not an object"
    if report.get("snapshot_fingerprint") != snapshot["fingerprint"]:
        return "report snapshot fingerprint does not match the frozen campaign"
    reviewer = report.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        return "report reviewer is missing"
    if report.get("complete") is not True:
        return "report did not declare complete coverage"
    examined = report.get("examined_qids")
    expected = snapshot["question_ids"]
    if (not isinstance(examined, list) or any(not isinstance(q, str) for q in examined)
            or len(examined) != len(expected) or set(examined) != set(expected)):
        return "report coverage is incomplete or malformed"
    if not isinstance(report.get("findings"), list):
        return "report findings must be a list"
    if not isinstance(report.get("errors", []), list):
        return "report errors must be a list"
    return None


def _blocker_id(kind: str, payload: Any) -> str:
    return f"{kind}:{_digest(payload).split(':', 1)[1][:20]}"


def _is_advisory_reviewer(reviewer: Any) -> bool:
    """Return whether a reviewer is the non-gating OpenCode standard-tier pass."""
    return reviewer in {"opencode-advisory", "opencode-low-advisory"}


def _append_blocker(ledger: dict, *, kind: str, detail: str, source: str,
                    qid: str | None = None) -> None:
    """Append one deduplicated fail-closed blocker.

    ``qid`` is copied only for well-formed question findings.  It lets a
    targeted recheck resolve the precise finding it retested while ensuring
    unscoped and operational evidence can never be cleared by a subset run.
    """
    payload = {"kind": kind, "detail": detail, "source": source, "qid": qid}
    blocker_id = _blocker_id(kind, payload)
    if any(item.get("id") == blocker_id for item in ledger["blockers"]):
        return
    blocker = {
        "id": blocker_id,
        "kind": kind,
        "detail": detail,
        "source": source,
        "status": "open",
    }
    if qid is not None:
        blocker["qid"] = qid
    ledger["blockers"].append(blocker)


def record_discovery(ledger: dict, report: Any) -> dict:
    """Record one non-certifying discovery report, fail-closed on bad input.

    Invalid reports are retained as operational evidence; incomplete coverage
    is not itself a campaign blocker because the final full gate owns coverage.
    Findings from the configured verifier still become blockers under the
    existing readiness threshold (wrong answer or high confidence). OpenCode
    standard-tier evidence is always advisory.
    """
    _validate_ledger(ledger)
    snapshot = ledger["snapshot"]
    problem = _report_problem(report, snapshot)
    if problem:
        reviewer = report.get("reviewer") if isinstance(report, dict) else "unknown"
        advisory = _is_advisory_reviewer(reviewer)
        # Discovery coverage is advisory for campaign progression. The final
        # full Terra gate owns full-pack coverage; preserve incomplete Terra
        # findings here so they still block remediation when applicable.
        incomplete = (
            problem == "report did not declare complete coverage"
            and isinstance(report, dict)
            and report.get("complete") is False
            and report.get("snapshot_fingerprint") == snapshot["fingerprint"]
            and isinstance(report.get("reviewer"), str)
            and bool(report["reviewer"].strip())
            and isinstance(report.get("examined_qids"), list)
            and all(isinstance(qid, str) for qid in report["examined_qids"])
            and isinstance(report.get("findings"), list)
            and isinstance(report.get("errors", []), list)
        )
        if not advisory and not incomplete:
            _append_blocker(ledger, kind="operational", detail=problem, source=str(reviewer))
        entry = {"valid": False, "reviewer": reviewer,
                 "problem": problem, "advisory": advisory}
        if isinstance(report, dict) and isinstance(report.get("findings"), list):
            entry["findings"] = copy.deepcopy(report["findings"])
            question_ids = set(snapshot["question_ids"])
            for finding in report["findings"]:
                finding_problem = _finding_problem(finding, question_ids)
                if finding_problem:
                    if not advisory:
                        _append_blocker(ledger, kind="malformed-finding",
                                        detail=finding_problem, source=str(reviewer))
                elif factcheck_pack.is_blocking(finding) and not advisory:
                    _append_blocker(ledger, kind="finding", detail=_canonical(finding),
                                    source=str(reviewer), qid=finding["qid"])
        ledger["discoveries"].append(entry)
        return ledger

    reviewer = report["reviewer"].strip()
    advisory = _is_advisory_reviewer(reviewer)
    findings = report["findings"]
    entry = {
        "valid": True,
        "reviewer": reviewer,
        "complete": True,
        "snapshot_fingerprint": snapshot["fingerprint"],
        "examined_qids": list(report["examined_qids"]),
        "findings": copy.deepcopy(findings),
        "errors": list(report.get("errors", [])),
        "advisory": advisory,
    }
    ledger["discoveries"].append(entry)
    if report.get("errors") and not advisory:
        _append_blocker(ledger, kind="operational",
                        detail="report contains reviewer operational errors", source=reviewer)

    question_ids = set(snapshot["question_ids"])
    for finding in findings:
        finding_problem = _finding_problem(finding, question_ids)
        if finding_problem:
            if not advisory:
                _append_blocker(ledger, kind="malformed-finding", detail=finding_problem,
                                source=reviewer)
            continue
        if factcheck_pack.is_blocking(finding) and not advisory:
            _append_blocker(ledger, kind="finding", detail=_canonical(finding),
                            source=reviewer, qid=finding["qid"])
    return ledger


def _hybrid_pass_to_report(snapshot: dict, pass_name: str, pass_value: Any,
                           reviewer: str) -> dict:
    """Adapt one fully-structured hybrid pass to a generic discovery report.

    A pass that completed but has coverage gaps becomes a deliberately
    incomplete generic report. Malformed pass envelopes raise; the caller
    decides whether that reviewer is advisory or gating and never invents a
    clean reviewer result.
    """
    if not isinstance(pass_value, dict):
        raise CampaignError(f"hybrid {pass_name} pass must be an object")
    if set(pass_value) - {"exit_code", "report", "report_error", "diagnostic"}:
        raise CampaignError(f"hybrid {pass_name} pass has unknown envelope fields")
    if "diagnostic" in pass_value and not isinstance(pass_value["diagnostic"], str):
        raise CampaignError(f"hybrid {pass_name} pass diagnostic must be a string")
    if "report_error" in pass_value:
        raise CampaignError(f"hybrid {pass_name} pass reported an operational error")
    if type(pass_value.get("exit_code")) is not int:
        raise CampaignError(f"hybrid {pass_name} pass exit_code is missing")
    report = pass_value.get("report")
    if not isinstance(report, dict):
        raise CampaignError(f"hybrid {pass_name} pass report is missing")
    for name, expected_type in (("ready", bool), ("outcome", str),
                                ("partial", bool), ("layer_a", dict),
                                ("layer_c", dict)):
        if not isinstance(report.get(name), expected_type):
            raise CampaignError(f"hybrid {pass_name} report {name} is missing or invalid")
    if report["ready"] is not False:
        raise CampaignError(f"hybrid {pass_name} discovery report unexpectedly claims ready")
    if report["partial"] is not False:
        raise CampaignError(f"hybrid {pass_name} discovery report is partial")
    if report["outcome"] not in {"review_ok", "not_ready"}:
        raise CampaignError(f"hybrid {pass_name} report has unsupported outcome")

    layer_a = report["layer_a"]
    layer_c = report["layer_c"]
    if not isinstance(layer_a.get("live"), list):
        raise CampaignError(f"hybrid {pass_name} report Layer A live findings are missing")
    for name, expected_type in (("live", list), ("errors", list),
                                ("coverage_gaps", list)):
        if not isinstance(layer_c.get(name), expected_type):
            raise CampaignError(f"hybrid {pass_name} report Layer C {name} is missing or invalid")
    if type(layer_c.get("total")) is not int or type(layer_c.get("questions_unchecked")) is not int:
        raise CampaignError(f"hybrid {pass_name} report Layer C coverage counters are missing or invalid")
    expected_total = len(snapshot["question_ids"])
    complete = (not layer_c["errors"] and not layer_c["coverage_gaps"]
                and layer_c["questions_unchecked"] == 0
                and layer_c["total"] == expected_total)
    # Layer A is part of a complete discovery review.  Its live findings are
    # not Layer-C finding objects, so retain them as a fail-closed review error
    # rather than forging a qid/severity schema for a different validator.
    errors = list(layer_c["errors"])
    if layer_a["live"]:
        errors.append("Layer A reported live findings")
    if errors:
        complete = False
    return {
        "snapshot_fingerprint": snapshot["fingerprint"],
        "reviewer": reviewer,
        "complete": complete,
        "examined_qids": list(snapshot["question_ids"]) if complete else [],
        "findings": copy.deepcopy(layer_c["live"]),
        "errors": errors,
    }


def adapt_hybrid_wrapper(snapshot: dict, wrapper: Any) -> list[dict]:
    """Convert a non-certifying full hybrid JSON wrapper into two reports.

    The adapter validates the wrapper *before* creating either reviewer record.
    Wrapper-level schema drift or an accidental certifying wrapper raises
    :class:`CampaignError`; callers must record that as an operational blocker.
    Advisory-pass failures are retained as advisory evidence while the
    configured verifier remains independently validated.
    """
    _validate_snapshot(snapshot)
    if not isinstance(wrapper, dict):
        raise CampaignError("hybrid wrapper is not an object")
    allowed = {"schema_version", "certifying", "verifier_profile", "target_qids",
               "snapshot_fingerprint", "advisory", "verifier", "exit_code"}
    if set(wrapper) - allowed:
        raise CampaignError("hybrid wrapper has unknown fields")
    if wrapper.get("schema_version") != HYBRID_JSON_SCHEMA_VERSION:
        raise CampaignError("hybrid wrapper schema version is unsupported")
    if wrapper.get("certifying") is not False:
        raise CampaignError("hybrid wrapper must be an explicit non-certifying discovery run")
    # Full and targeted evidence both bind to an exact frozen snapshot.  Full
    # census evidence carries null target_qids but must still carry the base
    # fingerprint, otherwise equal-qid pack revisions could be conflated.
    if wrapper.get("target_qids") is not None:
        raise CampaignError("hybrid full discovery must have null target_qids")
    if wrapper.get("snapshot_fingerprint") != snapshot["fingerprint"]:
        raise CampaignError("hybrid full discovery snapshot does not match the frozen campaign")
    expected_profile = snapshot["critic_contract"].get("profile")
    if (not isinstance(wrapper.get("verifier_profile"), str)
            or wrapper["verifier_profile"] != expected_profile):
        raise CampaignError("hybrid wrapper verifier profile does not match the frozen snapshot")
    if type(wrapper.get("exit_code")) is not int:
        raise CampaignError("hybrid wrapper exit_code is missing")
    verifier = wrapper.get("verifier")
    if not isinstance(verifier, dict):
        raise CampaignError("hybrid verifier pass is missing")
    if type(verifier.get("exit_code")) is not int:
        raise CampaignError("hybrid verifier pass exit_code is missing")

    try:
        advisory_report = _hybrid_pass_to_report(snapshot, "advisory", wrapper.get("advisory"),
                                           "opencode-advisory")
    except CampaignError as exc:
        # The advisory route is evidence-only. Preserve its failure in the discovery
        # record without turning a provider timeout/schema defect into a
        # campaign blocker when the configured verifier is usable.
        advisory_report = {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": "opencode-advisory",
            "complete": False,
            "examined_qids": [],
            "findings": [],
            "errors": [str(exc)],
        }
    return [advisory_report, _hybrid_pass_to_report(snapshot, "verifier", verifier,
                                               expected_profile)]


def record_hybrid_discovery(ledger: dict, wrapper: Any) -> dict:
    """Record hybrid discovery evidence, turning adapter rejection into a blocker."""
    _validate_ledger(ledger)
    try:
        reports = adapt_hybrid_wrapper(ledger["snapshot"], wrapper)
    except CampaignError as exc:
        _append_blocker(ledger, kind="operational", detail=f"hybrid wrapper rejected: {exc}",
                        source="hybrid-wrapper")
        ledger["discoveries"].append({"valid": False, "reviewer": "hybrid-wrapper",
                                      "problem": str(exc)})
        return ledger
    for report in reports:
        record_discovery(ledger, report)
    return ledger


def _remediation_snapshot(ledger: dict) -> dict | None:
    """Return the newest remediation round's snapshot, or None before round 1."""
    rounds = _remediation_rounds(ledger)
    return rounds[-1]["snapshot"] if rounds else None


def begin_remediation(ledger: dict, current_snapshot: dict,
                      changed_qids: list[str]) -> dict:
    """Freeze the next batched, question-only remediation round.

    Full discovery stays attached to the original snapshot.  A round is allowed
    only when all non-question certification inputs remain identical, the
    question ordering is stable, and callers declare *exactly* the question
    records that changed since the round this one chains from -- the previous
    round's snapshot, or the base snapshot for round 1.  The changed set is
    always recomputed from question hashes, never trusted from the caller.

    Rounds chain because a census grades each question whole, not only the bytes
    a remediation touched: a recheck can legitimately return a *different*
    finding on a question it was asked to re-read, and that needs another round
    rather than leaving the campaign with no legal path to a stamp.  This ledger
    action never invokes a reviewer or stamps a pack.
    """
    _validate_ledger(ledger)
    rounds = _remediation_rounds(ledger)
    _validate_snapshot(current_snapshot)
    if (not isinstance(changed_qids, list) or not changed_qids
            or any(not isinstance(qid, str) or not qid for qid in changed_qids)
            or len(set(changed_qids)) != len(changed_qids)):
        raise CampaignError("changed_qids must be non-empty unique strings")

    baseline = ledger["snapshot"]
    for field in FROZEN_FIELDS:
        if current_snapshot.get(field) != baseline.get(field):
            raise CampaignError(f"remediation cannot change {field}")
    # Question ids and their ordering are structural rather than a frozen
    # certification input, but a round still cannot add, drop, or reorder them.
    if current_snapshot.get("question_ids") != baseline.get("question_ids"):
        raise CampaignError("remediation cannot change question_ids")
    anchor = rounds[-1]["snapshot"] if rounds else baseline
    actual_changed = [
        qid for qid in baseline["question_ids"]
        if anchor["question_hashes"][qid] != current_snapshot["question_hashes"][qid]
    ]
    if not actual_changed:
        raise CampaignError(
            "a remediation round requires at least one changed question "
            "relative to the round it chains from")
    declared = sorted(changed_qids)
    if declared != sorted(actual_changed):
        raise CampaignError("declared changed_qids do not match question-content changes")

    rounds.append({
        "round": len(rounds) + 1,
        "base_snapshot_fingerprint": anchor["fingerprint"],
        "snapshot": copy.deepcopy(current_snapshot),
        "declared_changed_qids": actual_changed,
        "targeted_rechecks": [],
    })
    _normalize_rounds(ledger)
    return ledger


def _targeted_hybrid_pass_to_report(snapshot: dict, pass_name: str,
                                    pass_value: Any, reviewer: str,
                                    target_qids: list[str]) -> dict:
    """Adapt one non-certifying targeted verifier pass, fail-closed."""
    if not isinstance(pass_value, dict):
        raise CampaignError(f"hybrid {pass_name} pass must be an object")
    if set(pass_value) - {"exit_code", "report", "report_error", "diagnostic"}:
        raise CampaignError(f"hybrid {pass_name} pass has unknown envelope fields")
    if "report_error" in pass_value:
        raise CampaignError(f"hybrid {pass_name} pass reported an operational error")
    if type(pass_value.get("exit_code")) is not int:
        raise CampaignError(f"hybrid {pass_name} pass exit_code is missing")
    report = pass_value.get("report")
    if not isinstance(report, dict):
        raise CampaignError(f"hybrid {pass_name} pass report is missing")
    for name, expected_type in (("ready", bool), ("outcome", str),
                                ("partial", bool), ("layer_a", dict),
                                ("layer_c", dict)):
        if not isinstance(report.get(name), expected_type):
            raise CampaignError(f"hybrid {pass_name} report {name} is missing or invalid")
    if report["ready"] is not False or report["partial"] is not True:
        raise CampaignError(f"hybrid {pass_name} target report is not non-certifying")
    if report["outcome"] not in {"review_ok", "not_ready", "subset_ok"}:
        raise CampaignError(f"hybrid {pass_name} target report has unsupported outcome")
    layer_a, layer_c = report["layer_a"], report["layer_c"]
    if not isinstance(layer_a.get("live"), list):
        raise CampaignError(f"hybrid {pass_name} report Layer A live findings are missing")
    for name, expected_type in (("live", list), ("errors", list),
                                ("coverage_gaps", list)):
        if not isinstance(layer_c.get(name), expected_type):
            raise CampaignError(f"hybrid {pass_name} report Layer C {name} is missing or invalid")
    if type(layer_c.get("total")) is not int or type(layer_c.get("questions_unchecked")) is not int:
        raise CampaignError(f"hybrid {pass_name} report Layer C coverage counters are missing or invalid")
    complete = (not layer_a["live"] and not layer_c["errors"]
                and not layer_c["coverage_gaps"]
                and layer_c["questions_unchecked"] == 0
                and layer_c["total"] == len(target_qids))
    return {
        "reviewer": reviewer,
        "complete": complete,
        "examined_qids": list(target_qids) if complete else [],
        "findings": copy.deepcopy(layer_c["live"]),
    }


def adapt_hybrid_targeted_wrapper(snapshot: dict, wrapper: Any) -> tuple[list[str], list[dict]]:
    """Adapt a ``--no-certify --json --only`` wrapper into targeted evidence.

    The runner must emit ``target_qids`` and the campaign snapshot fingerprint.
    Those small metadata fields bind a saved JSON output to its exact bounded
    review without placing pack or source contents in the ledger.
    """
    _validate_snapshot(snapshot)
    if not isinstance(wrapper, dict):
        raise CampaignError("hybrid wrapper is not an object")
    allowed = {"schema_version", "certifying", "verifier_profile", "target_qids",
               "snapshot_fingerprint", "advisory", "verifier", "exit_code"}
    if set(wrapper) - allowed:
        raise CampaignError("hybrid targeted wrapper has unknown fields")
    if wrapper.get("schema_version") != HYBRID_JSON_SCHEMA_VERSION:
        raise CampaignError("hybrid wrapper schema version is unsupported")
    if wrapper.get("certifying") is not False:
        raise CampaignError("hybrid targeted wrapper must be non-certifying")
    if wrapper.get("snapshot_fingerprint") != snapshot["fingerprint"]:
        raise CampaignError("hybrid targeted wrapper snapshot does not match remediation")
    if wrapper.get("verifier_profile") != snapshot["critic_contract"]["profile"]:
        raise CampaignError("hybrid wrapper verifier profile does not match remediation")
    target_qids = wrapper.get("target_qids")
    if (not isinstance(target_qids, list) or not target_qids
            or any(not isinstance(qid, str) or not qid for qid in target_qids)
            or len(set(target_qids)) != len(target_qids)
            or not set(target_qids).issubset(set(snapshot["question_ids"]))):
        raise CampaignError("hybrid targeted wrapper target_qids are invalid")
    if type(wrapper.get("exit_code")) is not int:
        raise CampaignError("hybrid wrapper exit_code is missing")
    verifier = wrapper.get("verifier")
    if not isinstance(verifier, dict):
        raise CampaignError("hybrid verifier pass is missing")
    if type(verifier.get("exit_code")) is not int:
        raise CampaignError("hybrid verifier pass exit_code is missing")
    try:
        advisory_report = _targeted_hybrid_pass_to_report(
            snapshot, "advisory", wrapper.get("advisory"), "opencode-advisory", target_qids)
    except CampaignError as exc:
        advisory_report = {
            "reviewer": "opencode-advisory",
            "complete": False,
            "examined_qids": [],
            "findings": [],
            "errors": [str(exc)],
        }
    reports = [
        advisory_report,
        _targeted_hybrid_pass_to_report(snapshot, "verifier", verifier,
                                       snapshot["critic_contract"]["profile"], target_qids),
    ]
    return target_qids, reports


def _resolve_targeted_findings(ledger: dict, *, target_qids: list[str],
                               record_id: str) -> None:
    """Resolve scoped finding blockers for the qids a recheck graded clean.

    ``target_qids`` is the *cleared* subset of a recheck's targets, not its whole
    target list.  A recheck that comes back clean on five of six questions still
    clears those five, and the blockers it clears may have been raised by the
    base census or by any earlier round.
    """
    remediation = ledger["remediation_rounds"][-1]
    evidence = {
        "kind": "two-review-targeted-recheck",
        "record_id": record_id,
        "snapshot_fingerprint": remediation["snapshot"]["fingerprint"],
        "target_qids": list(target_qids),
    }
    for blocker in ledger["blockers"]:
        if (blocker.get("status") == "open" and blocker.get("kind") == "finding"
                and blocker.get("qid") in target_qids):
            blocker["status"] = "resolved"
            # This machine-generated note is append-only at the ledger API
            # layer.  Manual resolution remains separate and cannot overwrite
            # the evidence that justified this automatic transition.
            blocker["resolution_evidence"] = copy.deepcopy(evidence)


def record_hybrid_recheck(ledger: dict, wrapper: Any) -> dict:
    """Record both targeted reviews and gate resolution on the verifier pass."""
    _validate_ledger(ledger)
    snapshot = _remediation_snapshot(ledger)
    if snapshot is None:
        raise CampaignError("begin remediation before recording a targeted recheck")
    remediation = ledger["remediation_rounds"][-1]
    try:
        target_qids, reports = adapt_hybrid_targeted_wrapper(snapshot, wrapper)
    except CampaignError as exc:
        _append_blocker(ledger, kind="operational", detail=f"targeted wrapper rejected: {exc}",
                        source="hybrid-wrapper")
        remediation["targeted_rechecks"].append({
            "valid": False,
            "problem": str(exc),
        })
        return ledger

    evidence_digest = _digest(wrapper)
    record_id = _blocker_id("targeted-recheck", {
        "snapshot_fingerprint": snapshot["fingerprint"],
        "target_qids": target_qids,
        "evidence_digest": evidence_digest,
    })
    record = {
        "id": record_id,
        "snapshot_fingerprint": snapshot["fingerprint"],
        "target_qids": list(target_qids),
        "evidence_digest": evidence_digest,
        "reviewers": [{"reviewer": report["reviewer"],
                       "examined_qids": report["examined_qids"]} for report in reports],
        "valid": False,
    }
    remediation["targeted_rechecks"].append(record)
    verifier_report = next(
        report for report in reports
        if report["reviewer"] == snapshot["critic_contract"]["profile"]
    )
    if not verifier_report["complete"]:
        _append_blocker(ledger, kind="operational",
                        detail="configured verifier targeted recheck has errors or incomplete coverage",
                        source=verifier_report["reviewer"])
        record["problem"] = "configured verifier targeted recheck has errors or incomplete coverage"
        return ledger

    target_set = set(target_qids)
    declared_set = set(remediation["declared_changed_qids"])
    if not declared_set.issubset(target_set):
        _append_blocker(
            ledger,
            kind="operational",
            detail="targeted recheck does not cover every declared remediation qid",
            source="hybrid-wrapper",
        )
        record["problem"] = "targeted recheck does not cover every declared remediation qid"
        return ledger
    # Keep the complete, high-verifier evidence the deterministic certification
    # route needs.  It is stored whether or not this recheck came back clean: a
    # recheck that blocks on one question is still the only proof that its other
    # questions were graded clean at this round's content, and making a later
    # round re-derive that proof costs another reviewer pass for no new
    # information.  The compact reviewer list remains for older readers; this
    # richer copy is still JSON-only and contains no prompts or provider stderr.
    record["reviewer_reports"] = [
        {
            "reviewer": report["reviewer"],
            "complete": report["complete"],
            "examined_qids": list(report["examined_qids"]),
            "findings": copy.deepcopy(report["findings"]),
        }
        for report in reports
    ]
    unscoped = False
    blocking_qids: set[str] = set()
    for report in reports:
        advisory = _is_advisory_reviewer(report["reviewer"])
        for finding in report["findings"]:
            problem = _finding_problem(finding, set(snapshot["question_ids"]))
            if problem:
                if not advisory:
                    _append_blocker(ledger, kind="malformed-finding", detail=problem,
                                    source=report["reviewer"])
                    # A finding that cannot be attributed to a question taints
                    # the whole recheck: there is no way to say which targets it
                    # was about, so none of them may be credited as clean.
                    unscoped = True
            elif finding["qid"] not in target_set:
                if not advisory:
                    _append_blocker(ledger, kind="operational",
                                    detail="targeted recheck returned a finding outside its targets",
                                    source=report["reviewer"])
                    unscoped = True
            elif factcheck_pack.is_blocking(finding) and not advisory:
                _append_blocker(ledger, kind="finding", detail=_canonical(finding),
                                source=report["reviewer"], qid=finding["qid"])
                blocking_qids.add(finding["qid"])
    cleared = [] if unscoped else sorted(target_set - blocking_qids)
    record["cleared_qids"] = list(cleared)
    if unscoped:
        record["problem"] = "targeted recheck evidence cannot be scoped to its questions"
        return ledger
    if blocking_qids:
        # A partially blocking recheck is not a dead round.  Its clean questions
        # keep their evidence and need no further review; the blocking ones are
        # fixed in the next round.
        record["problem"] = ("targeted recheck retained blocking findings on "
                             + ", ".join(sorted(blocking_qids)))
    else:
        record["valid"] = True
    if cleared:
        _resolve_targeted_findings(ledger, target_qids=cleared, record_id=record_id)
    return ledger


def resolve_blocker(ledger: dict, blocker_id: str, *, resolution: str) -> dict:
    """Mark one evidence blocker resolved after an explicit remediation review."""
    _validate_ledger(ledger)
    if not isinstance(resolution, str) or not resolution.strip():
        raise CampaignError("resolution must be non-blank")
    for blocker in ledger["blockers"]:
        if blocker.get("id") == blocker_id:
            blocker["status"] = "resolved"
            blocker["resolution"] = resolution.strip()
            return ledger
    raise CampaignError(f"unknown blocker id: {blocker_id}")


def _cleared_question_hashes(ledger: dict, *, profile: str) -> tuple[set[tuple[str, str]], list[str]]:
    """Return every (qid, content hash) pair that carries clean verifier evidence.

    Evidence is bound to the exact question content it graded.  A complete base
    census clears the questions it raised no blocking finding on, at their base
    hashes; each remediation round's targeted recheck clears the questions it
    graded clean, at that round's hashes.

    Hash binding is the whole safety property that lets a chain of rounds stand
    in for a second full census: editing a question after it was cleared
    silently invalidates its evidence, so no question can reach a stamp without
    the configured verifier having read its *current* content.  The pairs and
    their validation are owned by ``campaign_evidence.evidence_sources``; a
    missing base census withholds everything here.
    """
    pairs, reasons = evidence_sources(ledger, profile=profile)
    if reasons:
        return set(), reasons
    return set(pairs), reasons


def _evidence_reasons(ledger: dict, probe: dict, *, profile: str) -> list[str]:
    """Return why ``probe``'s questions lack clean evidence at their content."""
    cleared, reasons = _cleared_question_hashes(ledger, profile=profile)
    if reasons:
        return reasons
    missing = [
        qid for qid in probe["question_ids"]
        if (qid, probe["question_hashes"][qid]) not in cleared
    ]
    if not missing:
        return []
    shown = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
    return [f"{len(missing)} question(s) lack clean high-verifier evidence "
            f"at their current content: {shown}"]


def _round_coverage_reasons(ledger: dict, probe: dict, *, profile: str) -> list[str]:
    """Return why a remediated question still lacks its targeted recheck.

    This is the loose check used before a *live* final gate, which owns full-pack
    coverage itself.  It therefore asks only the question a live gate cannot
    answer for free: has every question some round declared changed actually been
    re-read at the content it now has?
    """
    rounds = _remediation_rounds(ledger)
    if not rounds:
        return []
    declared = {qid for entry in rounds for qid in entry["declared_changed_qids"]}
    # Only a round recheck re-reads a changed question at its new content, so
    # this loose check counts round-recheck pairs alone.  The census reasons
    # are deliberately ignored: the final full runtime gate owns census
    # coverage, and a missing census must not fake a recheck gap here.
    pairs, _census_reasons = evidence_sources(ledger, profile=profile)
    cleared = {pair for pair, source in pairs.items()
               if source == ROUND_RECHECK_SOURCE}
    probe_hashes = probe["question_hashes"]
    missing = sorted(
        qid for qid in declared
        if qid not in probe_hashes or (qid, probe_hashes[qid]) not in cleared
    )
    if not missing:
        return []
    shown = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
    return ["every changed question requires a successful two-review targeted "
            f"recheck at its current content: {shown}"]


def _evidence_probe(ledger: dict, current_snapshot: dict | None) -> dict:
    """Return the snapshot to measure evidence against, fail-closed."""
    if current_snapshot is not None:
        try:
            _validate_snapshot(current_snapshot)
        except CampaignError:
            pass
        else:
            return current_snapshot
    return campaign_frontier(ledger)


def _snapshot_match_reasons(ledger: dict, current_snapshot: dict | None) -> list[str]:
    """Return why the pack on disk is not the campaign's newest frozen state."""
    expected = campaign_frontier(ledger)
    if current_snapshot is None:
        return ["a current pack snapshot is required"]
    try:
        _validate_snapshot(current_snapshot)
    except CampaignError:
        return ["current pack snapshot is malformed"]
    if current_snapshot["fingerprint"] != expected["fingerprint"]:
        return ["the frozen campaign snapshot no longer matches the pack"]
    return []


def _scoped_to_quarantine(blocker: Any, quarantined: set[str]) -> bool:
    """Return whether a blocker names a question outside the frontier."""
    qid = blocker.get("qid") if isinstance(blocker, dict) else None
    return isinstance(qid, str) and qid in quarantined


def _blocker_reasons(ledger: dict) -> list[str]:
    """Return why open or unevidenced campaign blockers prevent certification.

    Blockers scoped to a quarantined question are skipped: that question is
    outside the certification frontier, so its defects cannot block the stamp.
    Unscoped, malformed and operational blockers always block, and a blocker on
    a retained question still blocks.
    """
    quarantined = set(campaign_quarantine.quarantined_qids(ledger))
    active = [item for item in ledger["blockers"]
              if not _scoped_to_quarantine(item, quarantined)]
    reasons: list[str] = []
    if any(item.get("status") != "resolved" for item in active):
        reasons.append("open campaign blockers remain")
    for blocker in active:
        if blocker.get("kind") in {"finding", "malformed-finding"}:
            if (blocker.get("status") != "resolved"
                    or not isinstance(blocker.get("resolution_evidence"), dict)):
                reasons.append("content blockers require targeted evidence resolution")
                break
    return reasons


def eligibility(ledger: dict, *, current_snapshot: dict | None = None) -> tuple[bool, list[str]]:
    """Return whether a final live certification attempt may be *started*.

    This is intentionally not a certification result. Advisory discovery is
    retained as advisory evidence but cannot gate this decision.  The
    configured high-capability verifier must have discovery evidence with no
    open evidence blockers; full coverage remains enforced by the final full
    runtime gate in ``hybrid_verify.py``.
    """
    _validate_ledger(ledger)
    profile = campaign_frontier(ledger)["critic_contract"]["profile"]
    reasons = _snapshot_match_reasons(ledger, current_snapshot)
    if not any(entry.get("reviewer") == profile for entry in ledger["discoveries"]):
        reasons.append("configured verifier discovery evidence is required")
    reasons.extend(_blocker_reasons(ledger))
    reasons.extend(
        _round_coverage_reasons(ledger, _evidence_probe(ledger, current_snapshot),
                                profile=profile)
    )
    final = ledger.get("final_certification")
    if not isinstance(final, dict) or final.get("required") is not True:
        reasons.append("final full certification gate is not required")
    return not reasons, reasons


def certification_eligibility(
    ledger: dict, *, current_snapshot: dict | None = None
) -> tuple[bool, list[str]]:
    """Return strict eligibility for frozen-evidence certification.

    Unlike :func:`eligibility`, this is the final no-LLM route's contract. It
    reduces to one per-question rule: every question in the pack must carry
    clean configured-verifier evidence *for its current content* -- either from
    a complete base census that raised no blocking finding on it, or from a
    targeted recheck in some remediation round that graded it clean at exactly
    the content it now has.  No evidence may be partial, malformed, or out of
    scope, and no campaign blocker may remain open.
    """
    _validate_ledger(ledger)
    base = ledger["snapshot"]
    profile = base["critic_contract"]["profile"]
    reasons = _snapshot_match_reasons(ledger, current_snapshot)
    reasons.extend(
        _evidence_reasons(ledger, _evidence_probe(ledger, current_snapshot), profile=profile)
    )
    reasons.extend(_blocker_reasons(ledger))
    return not reasons, reasons


def record_final_attempt(ledger: dict, *, snapshot_fingerprint: str,
                         outcome: str) -> dict:
    """Record final-gate provenance without declaring certification.

    ``operational-error`` is intentionally retryable on the unchanged frozen
    snapshot.  A successful stamp is not inferred from this evidence ledger and
    must be established by the pack certification authority itself.
    """
    _validate_ledger(ledger)
    expected_snapshot = campaign_frontier(ledger)
    if snapshot_fingerprint != expected_snapshot["fingerprint"]:
        raise CampaignError("final attempt snapshot does not match the campaign")
    if outcome not in {"operational-error", "blocked", "completed"}:
        raise CampaignError("unknown final attempt outcome")
    ledger["final_certification"]["attempts"].append({
        "snapshot_fingerprint": snapshot_fingerprint,
        "outcome": outcome,
    })
    return ledger


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read {label}: {exc}") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a frozen evidence ledger")
    init.add_argument("pack", type=Path)
    init.add_argument("--ledger", type=Path, required=True)
    init.add_argument("--verifier-profile", default=verifier_profiles.DEFAULT_PROFILE,
                      choices=tuple(verifier_profiles.PROFILES))
    ingest = sub.add_parser("ingest", help="record one structured discovery report")
    ingest.add_argument("--ledger", type=Path, required=True)
    ingest.add_argument("--report", type=Path, required=True)
    ingest_hybrid = sub.add_parser(
        "ingest-hybrid", help="record a non-certifying hybrid JSON discovery wrapper")
    ingest_hybrid.add_argument("--ledger", type=Path, required=True)
    ingest_hybrid.add_argument("--report", type=Path, required=True)
    remediate = sub.add_parser(
        "begin-remediation",
        help="freeze the next question-only fix round, chained to the previous one")
    remediate.add_argument("--ledger", type=Path, required=True)
    remediate.add_argument("--pack", type=Path, required=True)
    remediate.add_argument("--changed-ids", required=True,
                           help="Comma-separated ids changed since the previous round")
    quarantine = sub.add_parser(
        "begin-quarantine",
        help="freeze a reduced question subset as the campaign frontier")
    quarantine.add_argument("--ledger", type=Path, required=True)
    quarantine.add_argument("--pack", type=Path, required=True)
    release = sub.add_parser(
        "release-quarantine",
        help="drop the active quarantine and restore the previous frontier")
    release.add_argument("--ledger", type=Path, required=True)
    ingest_targeted = sub.add_parser(
        "ingest-recheck", help="record a non-certifying hybrid targeted recheck")
    ingest_targeted.add_argument("--ledger", type=Path, required=True)
    ingest_targeted.add_argument("--report", type=Path, required=True)
    resolve = sub.add_parser("resolve", help="record a blocker remediation")
    resolve.add_argument("--ledger", type=Path, required=True)
    resolve.add_argument("--blocker", required=True)
    resolve.add_argument("--resolution", required=True)
    eligible = sub.add_parser("eligible", help="check if final full gate may start")
    eligible.add_argument("--ledger", type=Path, required=True)
    eligible.add_argument("--pack", type=Path, required=True)
    attempt = sub.add_parser("record-final", help="record final-gate provenance")
    attempt.add_argument("--ledger", type=Path, required=True)
    attempt.add_argument("--snapshot", required=True)
    attempt.add_argument("--outcome", required=True,
                         choices=("operational-error", "blocked", "completed"))
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "init":
            ledger = new_ledger(build_snapshot(args.pack, verifier_profile=args.verifier_profile))
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        ledger = load_ledger(args.ledger)
        if args.command == "ingest":
            record_discovery(ledger, _read_json(args.report, "discovery report"))
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "ingest-hybrid":
            record_hybrid_discovery(ledger, _read_json(args.report, "hybrid discovery report"))
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "begin-remediation":
            profile = ledger["snapshot"]["critic_contract"]["profile"]
            changed_qids = [qid.strip() for qid in args.changed_ids.split(",") if qid.strip()]
            begin_remediation(ledger, build_snapshot(args.pack, verifier_profile=profile),
                              changed_qids)
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "begin-quarantine":
            profile = ledger["snapshot"]["critic_contract"]["profile"]
            campaign_quarantine.begin_quarantine(
                ledger, build_snapshot(args.pack, verifier_profile=profile))
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "release-quarantine":
            campaign_quarantine.release_quarantine(ledger)
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "ingest-recheck":
            record_hybrid_recheck(ledger, _read_json(args.report, "hybrid targeted report"))
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "resolve":
            resolve_blocker(ledger, args.blocker, resolution=args.resolution)
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        if args.command == "record-final":
            record_final_attempt(ledger, snapshot_fingerprint=args.snapshot,
                                 outcome=args.outcome)
            save_ledger(args.ledger, ledger)
            print(json.dumps(ledger, indent=2, ensure_ascii=False))
            return 0
        profile = ledger["snapshot"]["critic_contract"]["profile"]
        current_snapshot = build_snapshot(args.pack, verifier_profile=profile)
        permitted, reasons = eligibility(ledger, current_snapshot=current_snapshot)
        print(json.dumps({"final_attempt_permitted": permitted,
                          "reasons": reasons,
                          "note": "This ledger never certifies a pack."}, indent=2))
        return 0 if permitted else 2
    except CampaignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
