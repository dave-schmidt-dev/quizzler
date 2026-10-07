#!/usr/bin/env python3
"""Hybrid-wrapper adapters for campaign discovery and remediation evidence.

This module adapts non-certifying JSON output from hybrid verification passes
into normalized reports for campaign ledger discovery and targeted rechecks.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign_snapshot import CampaignError, _validate_snapshot

# Mirrors scripts/hybrid_verify.py's JSON_SCHEMA_VERSION.  Kept local so this
# ledger utility remains a pure consumer of saved JSON rather than importing a
# runner that may perform runtime CLI setup in the future.
HYBRID_JSON_SCHEMA_VERSION = 3


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
