#!/usr/bin/env python3
"""Evidence-only campaign ledger for batched pack-certification work.

The ledger coordinates non-certifying discovery and remediation.  It never
writes a pack, creates a certification stamp, or treats its own records as a
certification result.  ``hybrid_verify.py`` remains the only certification
route and must still run a full, live final gate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import course_grounding
import factcheck_pack
import pack_cert
import verifier_profiles

LEDGER_VERSION = 1
LEDGER_KIND = "certification-campaign-evidence"
# Mirrors scripts/hybrid_verify.py's JSON_SCHEMA_VERSION.  Kept local so this
# ledger utility remains a pure consumer of saved JSON rather than importing a
# runner that may perform runtime CLI setup in the future.
HYBRID_JSON_SCHEMA_VERSION = 3


class CampaignError(ValueError):
    """Raised for unsafe campaign inputs or an invalid ledger."""


def _canonical(value: Any) -> str:
    """Return stable JSON or raise a clear fail-closed campaign error."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"campaign value is not JSON-serializable: {exc}") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_pack(pack_path: Path) -> dict:
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read pack: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignError("pack root must be a JSON object")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise CampaignError("pack must contain a non-empty questions list")
    for question in questions:
        if not isinstance(question, dict):
            raise CampaignError("each question must be a JSON object")
    return data


def _question_ids(pack: dict) -> list[str]:
    ids: list[str] = []
    for question in pack["questions"]:
        qid = question.get("id")
        if not isinstance(qid, str) or not qid.strip():
            raise CampaignError("every question requires a non-blank string id")
        if qid in ids:
            raise CampaignError(f"duplicate question id: {qid}")
        ids.append(qid)
    return ids


def _grounding_evidence(pack_path: Path) -> dict:
    """Fingerprint grounding config and source text without retaining either.

    The raw configured path and source excerpt deliberately stay out of the
    ledger.  A changed config still changes its digest; a changed source changes
    its text digest.  A missing optional grounding block is represented
    explicitly, so it cannot be confused with a malformed course file.
    """
    course_path = pack_path.parent / "_course.json"
    raw_grounding: Any = None
    if course_path.exists():
        try:
            course = json.loads(course_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CampaignError(f"cannot read course grounding metadata: {exc}") from exc
        if not isinstance(course, dict):
            raise CampaignError("course metadata root must be a JSON object")
        raw_grounding = course.get("grounding")
        if raw_grounding is not None and not isinstance(raw_grounding, dict):
            raise CampaignError("course grounding must be an object when present")

    try:
        source_text = course_grounding.load_source_text(pack_path)
    except OSError as exc:
        raise CampaignError(f"cannot resolve course source text: {exc}") from exc
    if source_text is not None and not isinstance(source_text, str):
        raise CampaignError("course source text resolver returned a non-string")
    return {
        "configured": raw_grounding is not None,
        "config_digest": _digest(raw_grounding),
        "source_text_digest": _text_digest(source_text) if source_text else None,
    }


def _critic_contract(profile_name: str | None) -> dict:
    name = profile_name or verifier_profiles.DEFAULT_PROFILE
    try:
        profile = verifier_profiles.get_profile(name)
    except (KeyError, ValueError) as exc:
        raise CampaignError(f"unknown verifier profile: {name}") from exc
    return {
        "version": pack_cert.CRITIC_CONTRACT_VERSION,
        "profile": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
    }


def build_snapshot(pack_path: Path, *, verifier_profile: str | None = None) -> dict:
    """Build a portable frozen-input fingerprint for one campaign.

    This is deliberately broader than a certification stamp's question hash:
    waivers, course grounding, and the exact reviewer contract all invalidate a
    campaign.  The snapshot contains only hashes and question ids, never course
    source text or absolute filesystem paths.
    """
    pack = _load_pack(pack_path)
    question_ids = _question_ids(pack)
    # Keep enough detail to prove exactly which question records changed during
    # a frozen remediation batch, without retaining their contents in the
    # evidence ledger.  This is intentionally separate from questions_hash:
    # a question-level diff is not a substitute for the final full-pack gate.
    question_hashes = {
        question_id: _digest(question)
        for question_id, question in zip(question_ids, pack["questions"], strict=True)
    }
    try:
        question_hash = pack_cert.questions_hash(pack)
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"cannot fingerprint questions: {exc}") from exc
    waivers = {
        "lint_waivers_digest": _digest(pack.get("lint_waivers", [])),
        "factcheck_waivers_digest": _digest(pack.get("factcheck_waivers", [])),
    }
    payload = {
        "snapshot_version": LEDGER_VERSION,
        "pack_name": pack_path.name,
        "questions_hash": question_hash,
        "question_ids": question_ids,
        "question_hashes": question_hashes,
        "waivers": waivers,
        "grounding": _grounding_evidence(pack_path),
        "critic_contract": _critic_contract(verifier_profile),
    }
    return {**payload, "fingerprint": _digest(payload)}


def new_ledger(snapshot: dict) -> dict:
    """Create an empty, evidence-only campaign ledger for ``snapshot``."""
    _validate_snapshot(snapshot)
    return {
        "ledger_version": LEDGER_VERSION,
        "kind": LEDGER_KIND,
        "snapshot": copy.deepcopy(snapshot),
        "discoveries": [],
        "blockers": [],
        "remediation": None,
        "final_certification": {
            "required": True,
            "attempts": [],
            "note": "Only hybrid_verify.py can create the certification stamp.",
        },
    }


def _validate_snapshot(snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        raise CampaignError("snapshot must be an object")
    expected = {k: snapshot.get(k) for k in snapshot if k != "fingerprint"}
    fingerprint = snapshot.get("fingerprint")
    if not isinstance(fingerprint, str) or fingerprint != _digest(expected):
        raise CampaignError("snapshot fingerprint is missing or does not match its contents")
    ids = snapshot.get("question_ids")
    if (not isinstance(ids, list) or not ids or any(not isinstance(q, str) or not q for q in ids)
            or len(set(ids)) != len(ids)):
        raise CampaignError("snapshot question_ids must be non-empty unique strings")
    question_hashes = snapshot.get("question_hashes")
    if (not isinstance(question_hashes, dict) or set(question_hashes) != set(ids)
            or any(not isinstance(value, str) or not value.startswith("sha256:")
                   for value in question_hashes.values())):
        raise CampaignError("snapshot question_hashes must map every question id to a digest")
    contract = snapshot.get("critic_contract")
    if (not isinstance(contract, dict) or not isinstance(contract.get("profile"), str)
            or not contract["profile"].strip()):
        raise CampaignError("snapshot critic contract must name a verifier profile")


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


def _blocker_id(kind: str, payload: Any) -> str:
    return f"{kind}:{_digest(payload).split(':', 1)[1][:20]}"


def _is_advisory_reviewer(reviewer: Any) -> bool:
    """Return whether a reviewer is the non-gating OpenCode low-tier pass."""
    return reviewer == "opencode-low-advisory"


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
    low-tier evidence is always advisory.
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
                                           "opencode-low-advisory")
    except CampaignError as exc:
        # The advisory route is evidence-only. Preserve its failure in the discovery
        # record without turning a provider timeout/schema defect into a
        # campaign blocker when the configured verifier is usable.
        advisory_report = {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": "opencode-low-advisory",
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
    """Return the active remediation snapshot after validating its shape."""
    remediation = ledger.get("remediation")
    if remediation is None:
        return None
    if not isinstance(remediation, dict):
        raise CampaignError("ledger remediation must be an object or null")
    snapshot = remediation.get("snapshot")
    _validate_snapshot(snapshot)
    declared = remediation.get("declared_changed_qids")
    if (not isinstance(declared, list) or not declared
            or any(not isinstance(qid, str) or not qid for qid in declared)
            or len(set(declared)) != len(declared)):
        raise CampaignError("remediation declared_changed_qids must be non-empty unique strings")
    if not set(declared).issubset(set(snapshot["question_ids"])):
        raise CampaignError("remediation changed ids are outside its snapshot")
    if not isinstance(remediation.get("targeted_rechecks"), list):
        raise CampaignError("remediation targeted_rechecks must be a list")
    return snapshot


def begin_remediation(ledger: dict, current_snapshot: dict,
                      changed_qids: list[str]) -> dict:
    """Freeze one batched, question-only remediation transition.

    Full discovery stays attached to the original snapshot.  A transition is
    allowed only when all non-question certification inputs remain identical,
    the question ordering is stable, and callers declare *exactly* the changed
    question records.  This ledger action never invokes a reviewer or stamps a
    pack.
    """
    _validate_ledger(ledger)
    if ledger.get("remediation") is not None:
        raise CampaignError("a remediation transition is already active")
    _validate_snapshot(current_snapshot)
    if (not isinstance(changed_qids, list) or not changed_qids
            or any(not isinstance(qid, str) or not qid for qid in changed_qids)
            or len(set(changed_qids)) != len(changed_qids)):
        raise CampaignError("changed_qids must be non-empty unique strings")

    baseline = ledger["snapshot"]
    for field in ("pack_name", "question_ids", "waivers", "grounding", "critic_contract"):
        if current_snapshot.get(field) != baseline.get(field):
            raise CampaignError(f"remediation cannot change {field}")
    actual_changed = [
        qid for qid in baseline["question_ids"]
        if baseline["question_hashes"][qid] != current_snapshot["question_hashes"][qid]
    ]
    declared = sorted(changed_qids)
    if declared != sorted(actual_changed):
        raise CampaignError("declared changed_qids do not match question-content changes")

    ledger["remediation"] = {
        "base_snapshot_fingerprint": baseline["fingerprint"],
        "snapshot": copy.deepcopy(current_snapshot),
        "declared_changed_qids": actual_changed,
        "targeted_rechecks": [],
    }
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
            snapshot, "advisory", wrapper.get("advisory"), "opencode-low-advisory", target_qids)
    except CampaignError as exc:
        advisory_report = {
            "reviewer": "opencode-low-advisory",
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
    """Resolve scoped finding blockers after the configured verifier is clean."""
    remediation = ledger["remediation"]
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
    remediation = ledger["remediation"]
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
    if target_set != declared_set:
        _append_blocker(
            ledger,
            kind="operational",
            detail="targeted recheck includes qids outside declared remediation",
            source="hybrid-wrapper",
        )
        record["problem"] = "targeted recheck includes qids outside declared remediation"
        return ledger
    blocking = False
    for report in reports:
        advisory = _is_advisory_reviewer(report["reviewer"])
        for finding in report["findings"]:
            problem = _finding_problem(finding, set(snapshot["question_ids"]))
            if problem:
                if not advisory:
                    _append_blocker(ledger, kind="malformed-finding", detail=problem,
                                    source=report["reviewer"])
                    blocking = True
            elif finding["qid"] not in target_set:
                if not advisory:
                    _append_blocker(ledger, kind="operational",
                                    detail="targeted recheck returned a finding outside its targets",
                                    source=report["reviewer"])
                    blocking = True
            elif factcheck_pack.is_blocking(finding) and not advisory:
                _append_blocker(ledger, kind="finding", detail=_canonical(finding),
                                source=report["reviewer"], qid=finding["qid"])
                blocking = True
    if blocking:
        record["problem"] = "targeted recheck retained blocking findings"
        return ledger
    record["valid"] = True
    # Keep the complete, high-verifier evidence needed by the deterministic
    # certification route.  The compact reviewer list remains for compatibility
    # with older ledgers; this richer copy is still JSON-only and contains no
    # prompts or provider stderr.
    record["reviewer_reports"] = [
        {
            "reviewer": report["reviewer"],
            "complete": report["complete"],
            "examined_qids": list(report["examined_qids"]),
            "findings": copy.deepcopy(report["findings"]),
        }
        for report in reports
    ]
    _resolve_targeted_findings(ledger, target_qids=target_qids, record_id=record_id)
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


def _clean_targeted_recheck_record(
    ledger: dict, record: Any, *, profile: str, snapshot: dict
) -> bool:
    """Return whether a stored targeted record is complete, clean evidence.

    The record is checked independently of its ``valid`` flag so a manually
    altered ledger cannot turn a resolution note into certification evidence.
    """
    remediation = ledger.get("remediation")
    if not isinstance(remediation, dict) or not isinstance(record, dict):
        return False
    if record.get("valid") is not True:
        return False
    targets = record.get("target_qids")
    if (record.get("snapshot_fingerprint") != snapshot["fingerprint"]
            or not isinstance(targets, list)
            or not targets
            or any(not isinstance(qid, str) or not qid for qid in targets)
            or len(set(targets)) != len(targets)
            or set(targets) != set(remediation["declared_changed_qids"])
            or not isinstance(record.get("reviewer_reports"), list)):
        return False
    high_reports = [
        report for report in record["reviewer_reports"]
        if isinstance(report, dict) and report.get("reviewer") == profile
    ]
    question_ids = set(snapshot["question_ids"])
    return any(
        report.get("complete") is True
        and report.get("examined_qids") == targets
        and isinstance(report.get("findings"), list)
        and all(
            _finding_problem(finding, question_ids) is None
            and not factcheck_pack.is_blocking(finding)
            for finding in report["findings"]
        )
        for report in high_reports
    )


def _base_finding_has_targeted_resolution(
    ledger: dict, entry: dict, finding: dict, *, snapshot: dict, profile: str
) -> bool:
    """Require a valid targeted record for a blocking base-census finding."""
    remediation = ledger.get("remediation")
    if not isinstance(remediation, dict):
        return False
    expected_targets = set(remediation["declared_changed_qids"])
    for blocker in ledger["blockers"]:
        if (blocker.get("kind") != "finding"
                or blocker.get("status") != "resolved"
                or blocker.get("qid") != finding.get("qid")
                or blocker.get("detail") != _canonical(finding)
                or blocker.get("source") != entry.get("reviewer")):
            continue
        evidence = blocker.get("resolution_evidence")
        evidence_targets = evidence.get("target_qids") if isinstance(evidence, dict) else None
        if (not isinstance(evidence, dict)
                or evidence.get("kind") != "two-review-targeted-recheck"
                or not isinstance(evidence.get("record_id"), str)
                or evidence.get("snapshot_fingerprint") != snapshot["fingerprint"]
                or not isinstance(evidence_targets, list)
                or any(not isinstance(qid, str) or not qid for qid in evidence_targets)
                or set(evidence_targets) != expected_targets
                or finding.get("qid") not in expected_targets):
            continue
        record = next(
            (candidate for candidate in remediation["targeted_rechecks"]
             if isinstance(candidate, dict)
             and candidate.get("id") == evidence["record_id"]),
            None,
        )
        if _clean_targeted_recheck_record(
            ledger, record, profile=profile, snapshot=snapshot
        ):
            return True
    return False


def eligibility(ledger: dict, *, current_snapshot: dict | None = None) -> tuple[bool, list[str]]:
    """Return whether a final live certification attempt may be *started*.

    This is intentionally not a certification result. Advisory discovery is
    retained as advisory evidence but cannot gate this decision.  The
    configured high-capability verifier must have discovery evidence with no
    open evidence blockers; full coverage remains enforced by the final full
    runtime gate in ``hybrid_verify.py``.
    """
    _validate_ledger(ledger)
    reasons: list[str] = []
    remediation_snapshot = _remediation_snapshot(ledger)
    expected_snapshot = remediation_snapshot or ledger["snapshot"]
    if current_snapshot is None:
        reasons.append("a current pack snapshot is required")
    else:
        try:
            _validate_snapshot(current_snapshot)
        except CampaignError:
            reasons.append("current pack snapshot is malformed")
        else:
            if current_snapshot["fingerprint"] != expected_snapshot["fingerprint"]:
                reasons.append("the frozen campaign snapshot no longer matches the pack")
    verifier_name = expected_snapshot["critic_contract"]["profile"]
    verifier_reports = [
        entry for entry in ledger["discoveries"]
        if entry.get("reviewer") == verifier_name
    ]
    if not verifier_reports:
        reasons.append("configured verifier discovery evidence is required")
    if any(item.get("status") != "resolved" for item in ledger["blockers"]):
        reasons.append("open campaign blockers remain")
    for blocker in ledger["blockers"]:
        if blocker.get("kind") in {"finding", "malformed-finding"}:
            evidence = blocker.get("resolution_evidence")
            if blocker.get("status") != "resolved" or not isinstance(evidence, dict):
                reasons.append("content blockers require targeted evidence resolution")
                break
    if remediation_snapshot is not None:
        declared = set(ledger["remediation"]["declared_changed_qids"])
        covered: set[str] = set()
        for record in ledger["remediation"]["targeted_rechecks"]:
            if (record.get("valid") is True
                    and record.get("snapshot_fingerprint") == remediation_snapshot["fingerprint"]):
                targets = record.get("target_qids")
                if isinstance(targets, list):
                    covered.update(qid for qid in targets if isinstance(qid, str))
        if not declared.issubset(covered):
            reasons.append("every changed question requires a successful two-review targeted recheck")
    final = ledger.get("final_certification")
    if not isinstance(final, dict) or final.get("required") is not True:
        reasons.append("final full certification gate is not required")
    return not reasons, reasons


def certification_eligibility(
    ledger: dict, *, current_snapshot: dict | None = None
) -> tuple[bool, list[str]]:
    """Return strict eligibility for frozen-evidence certification.

    Unlike :func:`eligibility`, this is the final no-LLM route's contract:
    one complete high-verifier full discovery must exist on the base snapshot;
    blocking findings in that census may be cleared only by their valid clean
    targeted recheck; every declared remediation qid must have a clean complete
    targeted high recheck; and no evidence may be partial, malformed, or out of
    scope.
    """
    _validate_ledger(ledger)
    reasons: list[str] = []
    base = ledger["snapshot"]
    remediation_snapshot = _remediation_snapshot(ledger)
    expected = remediation_snapshot or base

    if current_snapshot is None:
        reasons.append("a current pack snapshot is required")
    else:
        try:
            _validate_snapshot(current_snapshot)
        except CampaignError:
            reasons.append("current pack snapshot is malformed")
        else:
            if current_snapshot["fingerprint"] != expected["fingerprint"]:
                reasons.append("the frozen campaign snapshot no longer matches the pack")

    profile = base["critic_contract"]["profile"]
    full = [
        entry for entry in ledger["discoveries"]
        if entry.get("reviewer") == profile
        and entry.get("snapshot_fingerprint") == base["fingerprint"]
    ]
    def clean_or_resolved_base_census(entry: dict) -> bool:
        if (entry.get("valid") is not True
                or entry.get("complete") is not True
                or entry.get("examined_qids") != base["question_ids"]
                or not isinstance(entry.get("findings"), list)
                or entry.get("errors")):
            return False
        question_ids = set(base["question_ids"])
        for finding in entry["findings"]:
            if _finding_problem(finding, question_ids) is not None:
                return False
            if (factcheck_pack.is_blocking(finding)
                    and not _base_finding_has_targeted_resolution(
                        ledger, entry, finding, snapshot=expected, profile=profile
                    )):
                return False
        return True

    if not any(clean_or_resolved_base_census(entry) for entry in full):
        reasons.append(
            "complete high-verifier discovery evidence without unresolved blocking findings is required"
        )

    if any(item.get("status") != "resolved" for item in ledger["blockers"]):
        reasons.append("open campaign blockers remain")

    for blocker in ledger["blockers"]:
        if blocker.get("kind") in {"finding", "malformed-finding"}:
            if (blocker.get("status") != "resolved"
                    or not isinstance(blocker.get("resolution_evidence"), dict)):
                reasons.append("content blockers require targeted evidence resolution")
                break

    if remediation_snapshot is not None:
        declared = set(ledger["remediation"]["declared_changed_qids"])
        covered: set[str] = set()
        for record in ledger["remediation"]["targeted_rechecks"]:
            targets = record.get("target_qids")
            if (
                record.get("valid") is True
                and record.get("snapshot_fingerprint") == remediation_snapshot["fingerprint"]
                and isinstance(targets, list)
                and targets
                and set(targets) == declared
                and isinstance(record.get("reviewer_reports"), list)
            ):
                if _clean_targeted_recheck_record(
                    ledger, record, profile=profile, snapshot=expected
                ):
                    covered.update(targets)
        if covered != declared:
            reasons.append(
                "every changed question requires a clean complete targeted high-verifier recheck"
            )

    return not reasons, reasons


def record_final_attempt(ledger: dict, *, snapshot_fingerprint: str,
                         outcome: str) -> dict:
    """Record final-gate provenance without declaring certification.

    ``operational-error`` is intentionally retryable on the unchanged frozen
    snapshot.  A successful stamp is not inferred from this evidence ledger and
    must be established by the pack certification authority itself.
    """
    _validate_ledger(ledger)
    expected_snapshot = _remediation_snapshot(ledger) or ledger["snapshot"]
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
    remediate = sub.add_parser("begin-remediation", help="freeze one question-only fix batch")
    remediate.add_argument("--ledger", type=Path, required=True)
    remediate.add_argument("--pack", type=Path, required=True)
    remediate.add_argument("--changed-ids", required=True,
                           help="Comma-separated ids changed in this batch")
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
