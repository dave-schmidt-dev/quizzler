#!/usr/bin/env python3
"""Two-pass hybrid Layer-C discovery: the active OpenCode low-tier route performs
an advisory bulk review, then a configured high-capability verifier supplies
campaign evidence.

Rationale (measured, not assumed): the advisory route remains independent from
the high-capability verifier and cannot certify. verify_pack.py already encodes the matching safety
rule structurally: a standalone single ``--provider`` pass reviews and exits 3
(REVIEW PASSED, pack unchanged). This orchestrator keeps its configured,
registered high-capability CLI provider in discovery mode, so direct calls
cannot mint a certification. It calls ``verify_pack.main()`` IN-PROCESS
twice (mirrors scripts/recert_sweep.py's CV-2: a subprocess boundary would
defeat a test's ``patch.object(factcheck_pack, "run_claude")`` mock). The
advisory pass never controls whether the high-verifier discovery pass runs: whenever the
pack is loadable, the high-capability verifier runs after every advisory outcome,
including findings, incomplete coverage, and operational errors:

  1. advisory pass — resolves the current ``opencode-go`` low-tier selector
     from the approved roster at runtime, then invokes it through ``opencode``.
     Cheap advisory review pass. Its findings and errors are retained in the
     combined report but never certify or block the verifier pass.
  2. Verifier pass — runs after any loadable advisory outcome, with the explicitly
     configured high-capability provider/model but no certification designation.
     A clean reviewer result remains exit 3 (reviewed, not certified).

The advisory pass's exit code is advisory only — only the verifier pass's exit code
is returned when the shared pack input was loadable.

The advisory selector is not a Quizzler constant. It is resolved from the
current approved OpenCode Go low-tier roster immediately before the advisory
pass. If that roster cannot be read or does not resolve to OpenCode, the run
fails visibly rather than silently choosing a stale model.

Usage:
  python3 scripts/hybrid_verify.py <pack> --no-certify  # advisory bulk + high-verifier discovery
  python3 scripts/hybrid_verify.py <pack> --no-certify --strict
  python3 scripts/hybrid_verify.py <pack> --no-certify --verifier-profile claude-opus-high
  python3 scripts/hybrid_verify.py <pack> --no-certify --advisory-jobs 3 --verifier-jobs 1
  python3 scripts/hybrid_verify.py <pack> --no-certify --only d1q01,d1q02

Exit code — the configured verifier's own meaning whenever the pack was
loadable enough to invoke it. If the input cannot be loaded, no critic is
invoked and the input error is returned instead. The advisory pass's exit code is
reported, but never decides readiness or certification. A clean live-review
result is always exit 3: it is evidence, not a stamp. Only
``--certify-campaign <ledger>`` can write certification metadata, after it
validates frozen evidence without calling either reviewer.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# scripts/ isn't a package; import verify_pack by path, the same trick
# verify_pack.py itself uses to reach lint_packs/factcheck_pack/pack_cert.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import certification_campaign
import verifier_profiles
import verify_pack

# NOT a fresh import — the exact factcheck_pack module object verify_pack
# imported, so a test's patch.object(factcheck_pack, "run_claude") reaches
# verify_pack.run_layer_c from here too (mirrors recert_sweep.py's rationale).
factcheck_pack = verify_pack.factcheck_pack

ROSTER_TARGET = "opencode-go"
# The high-capability verifier is configurable. Codex Terra/high is the current
# available default; Claude remains selectable when capacity is available.
DEFAULT_VERIFIER_PROFILE = verifier_profiles.DEFAULT_PROFILE
JSON_SCHEMA_VERSION = 3
_CAMPAIGN_SNAPSHOT_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTION_PACKS_DIR = PROJECT_ROOT / "question-packs"
EVIDENCE_LOG_DIR = PROJECT_ROOT / ".logs" / "hybrid_verify"


class AdvisoryRouteError(ValueError):
    """Raised when the approved OpenCode low-tier route cannot be resolved."""


class AdvisoryRoute:
    """The current, roster-selected OpenCode advisory route."""

    __slots__ = ("selector", "variant")

    def __init__(self, *, selector: str, variant: str | None) -> None:
        self.selector = selector
        self.variant = variant


def resolve_advisory_route() -> AdvisoryRoute:
    """Resolve the active OpenCode Go low-tier selector without a model fallback."""
    roster = Path.home() / ".agent" / "bin" / "roster"
    try:
        result = subprocess.run(
            [str(roster), "resolve", ROSTER_TARGET, "low"],
            check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdvisoryRouteError("OpenCode low-tier roster resolution is unavailable") from exc
    if result.returncode != 0:
        raise AdvisoryRouteError("OpenCode low-tier roster resolution failed")
    try:
        resolved = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisoryRouteError("OpenCode low-tier roster returned invalid JSON") from exc
    if not isinstance(resolved, dict):
        raise AdvisoryRouteError("OpenCode low-tier roster returned an invalid route")
    selector = resolved.get("selector")
    variant = resolved.get("variant")
    if (
        resolved.get("target") != ROSTER_TARGET
        or resolved.get("harness") != "opencode"
        or resolved.get("requiredCapability") != "low"
        or not isinstance(selector, str)
        or not selector.strip()
        or (variant is not None and (not isinstance(variant, str) or not variant.strip()))
    ):
        raise AdvisoryRouteError("OpenCode low-tier roster returned an invalid route")
    return AdvisoryRoute(selector=selector, variant=variant)


def _default_evidence_output(pack: Path) -> Path | None:
    """Return the safe default JSON evidence path for a repository pack.

    Temporary/foreign packs keep the historical stdout-only behavior.  Real
    repository packs get durable evidence under ``.logs`` so a redirected
    review report cannot accidentally become an installable ``*.json`` pack.
    """
    try:
        relative = pack.resolve().relative_to(QUESTION_PACKS_DIR.resolve())
    except ValueError:
        return None
    if len(relative.parts) < 2 or relative.suffix != ".json":
        return None
    course = relative.parts[0]
    return EVIDENCE_LOG_DIR / course / f"{relative.stem}.json"


def _validate_evidence_output(path: Path) -> Path:
    """Resolve an evidence destination and reject install-tree paths."""
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(QUESTION_PACKS_DIR.resolve())
    except ValueError:
        return resolved
    raise ValueError(
        "campaign evidence output must not be inside question-packs; "
        "write it under .logs/hybrid_verify/"
    )


def _write_evidence_output(path: Path, report: str) -> None:
    """Write a machine-readable report outside the install tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.rstrip() + "\n", encoding="utf-8")


def certify_campaign(pack: Path, ledger_path: Path) -> tuple[int, str]:
    """Stamp from frozen campaign evidence without invoking either reviewer.

    The ledger must contain a complete clean high-verifier census on its base
    snapshot. If remediation is present, its declared qids must have one clean
    complete targeted high-verifier recheck as well. This route reruns only
    deterministic Layer-A structure checks and recomputes the exact current
    snapshot before writing provenance-bound certification metadata.
    """
    try:
        ledger = certification_campaign.load_ledger(ledger_path)
        profile_name = ledger["snapshot"]["critic_contract"]["profile"]
        profile = verifier_profiles.get_profile(profile_name)
        current = certification_campaign.build_snapshot(
            pack, verifier_profile=profile_name
        )
        eligible, reasons = certification_campaign.certification_eligibility(
            ledger, current_snapshot=current
        )
        if not eligible:
            return 2, "campaign certification refused: " + "; ".join(reasons)
        structure = verify_pack.run_layer_a(pack)
        if not isinstance(structure, dict) or structure.get("live"):
            return 2, "campaign certification refused: deterministic structure checks are not clean"
        data = json.loads(pack.read_text(encoding="utf-8"))
        questions = data.get("questions")
        if not isinstance(questions, list) or len(questions) != len(current["question_ids"]):
            return 2, "campaign certification refused: current question coverage is malformed"
        provenance = {
            "kind": "frozen-campaign-evidence",
            "evidence_policy": "no-new-llm-call",
            "campaign_snapshot_fingerprint": current["fingerprint"],
            "base_snapshot_fingerprint": ledger["snapshot"]["fingerprint"],
            "verifier_profile": profile.name,
            "verifier_provider": profile.provider,
            "verifier_model": profile.model,
            "remediation_qids": (
                list(ledger["remediation"]["declared_changed_qids"])
                if ledger.get("remediation") else []
            ),
        }
        verify_pack._write_certification(
            pack,
            model=profile.model,
            questions_examined=len(questions),
            provider=profile.provider,
            requested_model=profile.model,
            reasoning_effort=profile.reasoning_effort,
            provenance=provenance,
        )
        return 0, json.dumps({"certified": True, "provenance": provenance}, indent=2)
    except (certification_campaign.CampaignError, KeyError, OSError,
            UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return 2, f"campaign certification refused: {exc}"

# JSON discovery reports are written to durable campaign evidence.  Do not
# copy captured stderr into that evidence: provider CLIs can echo prompts,
# responses, or credentials in failure paths.  These deliberately small,
# fixed summaries retain the operational signal needed for diagnosis without
# retaining any stderr content.
_STDERR_DIAGNOSTIC_CATEGORIES = (
    (("timed out", "timeout"), "stderr indicated a timeout"),
    (("rate limit", "too many requests", "429"), "stderr indicated rate limiting"),
    (("unauthorized", "forbidden", "authentication", "permission denied", "401", "403"),
     "stderr indicated an authentication or permission failure"),
    (("network", "connection", "dns", "socket", "econn"),
     "stderr indicated a network or connection failure"),
    (("not found", "no such file", "command not found"),
     "stderr indicated a missing command or file"),
)

# The low-tier advisory route's outcomes are retained for reporting only.
_ADVISORY_OUTCOME_LABELS = {0: "READY (advisory only)", 1: "ERROR",
                      2: "NOT READY", 3: "REVIEWED (advisory only)"}
_VERIFIER_OUTCOME_LABELS = {0: "REVIEWED (not certified)", 2: "NOT READY", 1: "ERROR"}
_IN_PROCESS_PASS_ERRORS = {
    "system_exit": (
        "in-process verify_pack.main terminated with SystemExit",
        "pass terminated unexpectedly; details redacted",
    ),
    "exception": (
        "in-process verify_pack.main raised an unexpected exception",
        "pass failed unexpectedly; details redacted",
    ),
}


def _loadable_pack(pack: Path) -> tuple[bool, int, str]:
    """Return whether both critics can be invoked for ``pack``.

    Critic/provider failures are advisory for the low-tier route, but the second pass
    cannot run when the shared input is absent, unreadable, malformed JSON, or
    has no questions. Mirror verify_pack's input exit codes here so a advisory
    operational error for a valid pack still proceeds to certification.
    """
    if not pack.is_file():
        return False, 1, f"error: pack not found: {pack}"
    try:
        data = json.loads(pack.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, 1, f"error: could not read pack: {exc}"
    if not isinstance(data, dict):
        return False, 1, "error: could not read pack: top-level JSON must be an object"
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return False, 2, "error: pack has no questions"
    if any(not isinstance(question, dict) for question in questions):
        return False, 1, "error: could not read pack: questions must be JSON objects"
    return True, 0, ""


def _run_pass(argv: list[str], *, certifying: str | None = None) -> tuple[int, str, str, str]:
    """Call verify_pack.main(argv) IN-PROCESS and capture its printed report
    instead of letting it hit this process's real stdout/stderr.

    The in-process boundary must not let a provider or verifier exception abort
    the hybrid workflow.  Keep KeyboardInterrupt uncaught so an operator can
    still interrupt a live run; all other unexpected failures become a safe,
    fixed marker consumed by the JSON report formatter below.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = verify_pack.main(argv, _hybrid_certifier=certifying)
    except SystemExit:
        message, diagnostic = _IN_PROCESS_PASS_ERRORS["system_exit"]
        safe = json.dumps({"_hybrid_pass_error": "system_exit"})
        return 1, message, safe, diagnostic
    except Exception:  # noqa: BLE001 — isolate an in-process provider failure
        message, diagnostic = _IN_PROCESS_PASS_ERRORS["exception"]
        safe = json.dumps({"_hybrid_pass_error": "exception"})
        return 1, message, safe, diagnostic
    stdout = out.getvalue()
    report = stdout
    stderr = err.getvalue()
    if stderr:
        report += ("\n" if report and not report.endswith("\n") else "") + stderr
    return rc, report, stdout, stderr


def _redacted_stderr_diagnostic(stderr: str, *, exit_code: int) -> str:
    """Return a fixed, non-sensitive classification of captured stderr.

    This must never return stderr itself.  Discovery JSON can be saved into a
    campaign ledger, while provider stderr is not a safe evidence format.
    """
    normalized = stderr.casefold()
    for needles, summary in _STDERR_DIAGNOSTIC_CATEGORIES:
        if any(needle in normalized for needle in needles):
            return summary
    if stderr.strip():
        return "stderr was emitted; content redacted"
    return f"pass exited {exit_code} without captured stderr"


def _canonical_target_qids(only: str | None) -> list[str]:
    """Normalize a comma-separated target list for discovery evidence.

    The same normalized value is forwarded to both critics.  Empty entries and
    duplicates are intentionally discarded, while unknown non-empty IDs remain
    for ``verify_pack`` to reject against the loaded pack.
    """
    if only is None:
        return []
    return sorted({qid.strip() for qid in only.split(",") if qid.strip()})


def _validate_campaign_snapshot(snapshot: str | None, *, target_qids: list[str],
                                json_output: bool) -> None:
    """Require an explicit snapshot for every machine-readable census.

    Targeted and full JSON discovery are both durable evidence; the latter
    must bind to the exact pack revision even when ``target_qids`` is null.
    """
    if snapshot is not None and not json_output:
        raise ValueError(
            "--campaign-snapshot is valid only with --json discovery"
        )
    if json_output:
        if snapshot is None:
            raise ValueError(
                "JSON discovery requires --campaign-snapshot sha256:<digest>"
            )
        if not _CAMPAIGN_SNAPSHOT_RE.fullmatch(snapshot):
            raise ValueError(
                "--campaign-snapshot must be sha256: followed by 64 lowercase hex characters"
            )


def _campaign_snapshot_binding(
    pack: Path, *, verifier_profile: str, campaign_snapshot: str
) -> tuple[verifier_profiles.VerifierProfile, str]:
    """Resolve the verifier and bind JSON evidence to the loaded pack."""
    try:
        profile = verifier_profiles.get_profile(verifier_profile)
        actual = certification_campaign.build_snapshot(
            pack, verifier_profile=profile.name
        )["fingerprint"]
    except (certification_campaign.CampaignError, KeyError, OSError,
            UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"could not compute campaign snapshot: {exc}") from exc
    if actual != campaign_snapshot:
        raise ValueError(
            "campaign snapshot mismatch: supplied "
            f"{campaign_snapshot}, actual {actual} for verifier profile "
            f"{profile.name}"
        )
    return profile, actual


def _json_pass_result(exit_code: int, stdout: str, stderr: str) -> dict:
    """Return one pass's JSON result without trusting malformed critic output."""
    try:
        marker = json.loads(stdout)
    except json.JSONDecodeError:
        marker = None
    if isinstance(marker, dict) and marker.get("_hybrid_pass_error") in _IN_PROCESS_PASS_ERRORS:
        report_error, diagnostic = _IN_PROCESS_PASS_ERRORS[marker["_hybrid_pass_error"]]
        return {"exit_code": exit_code, "report_error": report_error,
                "diagnostic": diagnostic}
    needs_diagnostic = exit_code != 0
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result = {
            "exit_code": exit_code,
            "report_error": f"pass did not emit valid JSON: {exc.msg}",
        }
        needs_diagnostic = True
        result["diagnostic"] = _redacted_stderr_diagnostic(
            stderr, exit_code=exit_code)
        return result
    if not isinstance(parsed, dict):
        result = {
            "exit_code": exit_code,
            "report_error": "pass JSON report must be an object",
        }
        result["diagnostic"] = _redacted_stderr_diagnostic(
            stderr, exit_code=exit_code)
        return result
    result = {"exit_code": exit_code, "report": parsed}
    if needs_diagnostic:
        result["diagnostic"] = _redacted_stderr_diagnostic(
            stderr, exit_code=exit_code)
    return result


def run_hybrid(pack: Path, *, advisory_model: str, advisory_variant: str | None,
               verifier_profile: str = DEFAULT_VERIFIER_PROFILE,
               batch_size: int, timeout: int, jobs: int, strict: bool,
               only: str | None = None,
               certifying: bool = False,
               json_output: bool = False,
               campaign_snapshot: str | None = None,
               advisory_jobs: int | None = None, verifier_jobs: int | None = None,
               advisory_batch_size: int | None = None,
               verifier_batch_size: int | None = None,
               skip_advisory: bool = False,
               progress=lambda msg: None) -> tuple[int, str]:
    """Run the advisory pass, then the configured discovery verifier.

    Returns ``(exit_code, combined_report_text)`` using the verifier's own
    result whenever the pack is loadable. The advisory result is included in the
    report but never controls certification. The verifier is deliberately
    invoked without hybrid designation, so it cannot stamp and a clean review
    remains exit 3. ``certifying=True`` is a retired internal route
    and fails before either reviewer runs. When ``only`` is supplied, both
    passes receive the same comma-separated IDs. `progress(msg)` fires at each pass
    boundary — INV-1: two LLM-backed passes can each take minutes, so the
    caller must see which pass is running rather than wait on a silent block.

    ``batch_size`` remains the shared fallback for backward compatibility. When
    set, ``advisory_batch_size`` and ``verifier_batch_size`` control only their
    respective pass; omitted per-pass values inherit ``batch_size``. Likewise,
    ``jobs`` remains the verifier fallback; omitted ``advisory_jobs`` defaults to 1.
    """
    if certifying:
        raise ValueError(
            "live reviewer certification is retired; run discovery, ingest its "
            "frozen evidence, then use --certify-campaign <ledger>"
        )
    target_qids = _canonical_target_qids(only)
    high_only_census = skip_advisory and json_output and not target_qids
    if skip_advisory and not high_only_census and (json_output or target_qids):
        raise ValueError(
            "--skip-advisory requires a full non-JSON diagnostic run, or a "
            "non-certifying full JSON census (without --only)"
        )
    _validate_campaign_snapshot(campaign_snapshot, target_qids=target_qids,
                                json_output=json_output)

    advisory_common = ["--batch-size", str(batch_size if advisory_batch_size is None else advisory_batch_size),
                 "--timeout", str(timeout),
                 "--jobs", str(1 if advisory_jobs is None else advisory_jobs),
                 # The low-tier route is advisory. Do not turn one timed-out batch into
                 # a second expensive process launch; the high verifier still
                 # receives its normal retry/full-coverage gate below.
                 "--no-retry-incomplete"]
    verifier_common = ["--batch-size", str(batch_size if verifier_batch_size is None else verifier_batch_size),
                       "--timeout", str(timeout),
                       "--jobs", str(jobs if verifier_jobs is None else verifier_jobs)]
    if strict:
        advisory_common.append("--strict")
        verifier_common.append("--strict")
    only_args = ["--only", ",".join(target_qids)] if target_qids else []
    json_args = ["--json"] if json_output else []

    loadable, input_rc, input_report = _loadable_pack(pack)
    if not loadable:
        progress(f"[1/1] high-verifier census skipped: {input_report}"
                 if skip_advisory else
                 f"[1/2] advisory pass skipped: {input_report}")
        return input_rc, input_report

    profile = None
    if json_output:
        # Bind machine-readable campaign evidence before constructing or
        # invoking either reviewer.  The verifier route is part of the frozen
        # campaign contract, not merely output metadata.
        profile, campaign_snapshot = _campaign_snapshot_binding(
            pack,
            verifier_profile=verifier_profile,
            campaign_snapshot=campaign_snapshot,
        )

    if skip_advisory:
        if profile is None:
            profile = verifier_profiles.get_profile(verifier_profile)
        progress(f"[1/1] advisory pass skipped; {profile.name} "
                 f"({profile.provider}/{profile.model}"
                 + (f", effort={profile.reasoning_effort}" if profile.reasoning_effort else "")
                 + ") — non-certifying full-pack census...")
        verifier_argv = [str(pack), "--provider", profile.provider,
                         "--model", profile.model]
        if profile.reasoning_effort:
            verifier_argv += ["--variant", profile.reasoning_effort]
        verifier_argv += verifier_common + json_args
        verifier_rc, verifier_report, _verifier_stdout, _verifier_stderr = _run_pass(
            verifier_argv, certifying=None)
        if json_output:
            verifier_result = _json_pass_result(
                verifier_rc, _verifier_stdout, _verifier_stderr
            )
            effective_rc = 3 if "report_error" in verifier_result else verifier_rc
            return effective_rc, json.dumps({
                "schema_version": JSON_SCHEMA_VERSION,
                "certifying": False,
                "target_qids": None,
                "snapshot_fingerprint": campaign_snapshot,
                "verifier_profile": profile.name,
                "advisory": {
                    "exit_code": 3,
                    "report_error": "advisory pass explicitly skipped",
                    "diagnostic": "advisory pass skipped by operator",
                },
                "verifier": verifier_result,
                "exit_code": effective_rc,
            }, indent=2, ensure_ascii=False)
        combined = verifier_report
        combined += ("\n" if combined and not combined.endswith("\n") else "")
        combined += ("note: the advisory pass was explicitly skipped for this "
                     "non-certifying full-pack census. Certification requires "
                     "the deterministic --certify-campaign ledger route.")
        progress(f"[1/1] {profile.name} pass: "
                 f"{_VERIFIER_OUTCOME_LABELS.get(verifier_rc, f'exit {verifier_rc}')}")
        return verifier_rc, combined

    route_label = advisory_model if advisory_variant is None else f"{advisory_model}, variant={advisory_variant}"
    progress(f"[1/2] advisory OpenCode low-tier pass ({route_label}) — reviewing; "
             "findings are campaign evidence only...")
    advisory_argv = [str(pack), "--provider", "opencode", "--model", advisory_model]
    if advisory_variant is not None:
        advisory_argv += ["--variant", advisory_variant]
    advisory_argv += advisory_common + only_args + json_args
    advisory_rc, advisory_report, advisory_stdout, advisory_stderr = _run_pass(advisory_argv)

    advisory_label = _ADVISORY_OUTCOME_LABELS.get(advisory_rc, f"exit {advisory_rc}")
    progress(f"[1/2] advisory pass: {advisory_label}; continuing to the discovery verifier.")
    if profile is None:
        profile = verifier_profiles.get_profile(verifier_profile)
    progress(f"[2/2] {profile.name} ({profile.provider}/{profile.model}"
             + (f", effort={profile.reasoning_effort}" if profile.reasoning_effort else "")
             + ") — non-certifying discovery pass...")
    verifier_argv = [str(pack), "--provider", profile.provider,
                     "--model", profile.model]
    if profile.reasoning_effort:
        verifier_argv += ["--variant", profile.reasoning_effort]
    verifier_argv += verifier_common + only_args + json_args
    verifier_rc, verifier_report, verifier_stdout, verifier_stderr = _run_pass(
        verifier_argv, certifying=None)

    if json_output:
        advisory_result = _json_pass_result(advisory_rc, advisory_stdout, advisory_stderr)
        verifier_result = _json_pass_result(verifier_rc, verifier_stdout, verifier_stderr)
        # JSON mode is discovery-only. A malformed pass report cannot be
        # treated as a successful discovery even when a mocked or future
        # verifier returns success independently of its stdout.
        effective_rc = 3 if (
            "report_error" in advisory_result or "report_error" in verifier_result
        ) else verifier_rc
        return effective_rc, json.dumps({
            "schema_version": JSON_SCHEMA_VERSION,
            "certifying": False,
            "target_qids": target_qids or None,
            "snapshot_fingerprint": campaign_snapshot,
            "verifier_profile": profile.name,
            "advisory": advisory_result,
            "verifier": verifier_result,
            "exit_code": effective_rc,
        }, indent=2, ensure_ascii=False)

    combined = advisory_report
    if verifier_report:
        combined += ("\n" if combined and not combined.endswith("\n") else "") + verifier_report
    combined += "\n\nnote: the advisory pass is advisory and does not certify. "
    combined += ("The high-capability verifier ran in discovery mode without "
                 "certification designation; this run cannot write a "
                 "certification stamp.")

    progress(f"[2/2] {profile.name} pass: "
             f"{_VERIFIER_OUTCOME_LABELS.get(verifier_rc, f'exit {verifier_rc}')}")
    return verifier_rc, combined


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Two-pass hybrid Layer-C verify: the active OpenCode low-tier "
        "pass reviews first (never certifies or blocks); a configured high-capability "
        "verifier always runs afterward for a loadable pack as discovery "
        "evidence. Only --certify-campaign can stamp. Touches none of "
        "verify_pack.py's public command boundary. See this script's own "
        "module docstring for the full exit-code contract.")
    ap.add_argument("pack", type=Path, help="Question pack JSON to verify.")
    ap.add_argument(
        "--certify-campaign", type=Path, default=None,
        help="Stamp only from a previously recorded campaign ledger. This route "
        "runs deterministic structure/snapshot checks, makes no reviewer/LLM "
        "call, and fails closed on stale, partial, malformed, or open evidence.",
    )
    ap.add_argument("--verifier-profile", default=DEFAULT_VERIFIER_PROFILE,
                    choices=tuple(verifier_profiles.PROFILES),
                    help="Registered high-capability verifier route "
                    f"(default: {DEFAULT_VERIFIER_PROFILE}).")
    ap.add_argument("--batch-size", type=int, default=12,
                    help="Questions per Layer-C LLM call (default 12), "
                    "forwarded to both passes when --advisory-batch-size or "
                    "--verifier-batch-size is omitted.")
    ap.add_argument("--advisory-batch-size", type=int, default=None,
                    help="Questions per advisory OpenCode low-tier LLM call; defaults "
                    "to --batch-size.")
    ap.add_argument("--verifier-batch-size", type=int, default=None,
                    help="Questions per high-verifier LLM call; defaults "
                    "to --batch-size.")
    ap.add_argument("--timeout", type=int, default=180,
                    help="Per-batch Layer-C timeout in seconds (default "
                    "180), forwarded to both passes.")
    ap.add_argument("--jobs", type=int, default=factcheck_pack.DEFAULT_JOBS,
                    help="Concurrent Layer-C batches (default "
                    f"{factcheck_pack.DEFAULT_JOBS}), forwarded to the "
                    "verifier pass when --verifier-jobs is omitted.")
    ap.add_argument("--advisory-jobs", type=int, default=None,
                    help="Concurrent advisory OpenCode low-tier batches; defaults to "
                    "1 when omitted.")
    ap.add_argument("--verifier-jobs", type=int, default=None,
                    help="Concurrent high-verifier batches; defaults to "
                    "--jobs.")
    ap.add_argument("--only", default=None,
                    help="Comma-separated question ids for a targeted recheck. "
                    "Forwarded to both passes; an exit-3 subset recheck is "
                    "never a whole-pack certification.")
    ap.add_argument("--no-certify", action="store_true",
                    help="Deprecated compatibility flag: all live reviewer "
                    "runs are discovery-only and cannot write a stamp.")
    ap.add_argument("--skip-advisory", action="store_true",
        help="Skip the advisory route only for an explicit non-certifying full JSON "
                     "census; rejects targeted and non-JSON modes.")
    ap.add_argument("--json", action="store_true",
                    help="Emit the two-pass wrapper result as JSON.")
    ap.add_argument("--campaign-snapshot", default=None,
                    help="Required for full or targeted --json discovery: the frozen "
                    "campaign snapshot fingerprint as sha256:<64 lowercase hex>.")
    ap.add_argument("--evidence-output", type=Path, default=None,
                    help="Write JSON discovery evidence to this path (must not be "
                    "inside question-packs; repository-pack defaults live under "
                    ".logs/hybrid_verify/).")
    ap.add_argument("--strict", action="store_true",
                    help="Gate both passes on EVERY live Layer-C finding, "
                    "not just blocking ones. Forwarded to both passes.")
    return ap


def main(argv: list[str]) -> int:
    args = build_arg_parser().parse_args(argv)

    target_qids = _canonical_target_qids(args.only)
    if args.certify_campaign is not None:
        if (args.only or args.no_certify or args.json or args.skip_advisory
                or args.evidence_output is not None):
            print("error: --certify-campaign cannot be combined with reviewer-run options",
                  file=sys.stderr)
            return 1
        rc, report = certify_campaign(args.pack, args.certify_campaign)
        print(report)
        return rc
    high_only_census = args.skip_advisory and args.no_certify and args.json and not target_qids
    if args.skip_advisory and not high_only_census \
            and (args.no_certify or args.json or target_qids):
        print(
            "error: --skip-advisory requires a full non-JSON diagnostic run, "
            "or a non-certifying full JSON census (without --only)",
            file=sys.stderr,
        )
        return 1

    if args.json and not args.no_certify:
        print(
            "error: --json requires --no-certify; JSON is limited to "
            "non-certifying discovery evidence.",
            file=sys.stderr,
        )
        return 1
    if args.evidence_output is not None and not args.json:
        print("error: --evidence-output requires --json discovery", file=sys.stderr)
        return 1

    try:
        evidence_output = (
            _validate_evidence_output(args.evidence_output)
            if args.evidence_output is not None
            else (_default_evidence_output(args.pack) if args.json else None)
        )
        _validate_campaign_snapshot(args.campaign_snapshot,
                                    target_qids=target_qids,
                                    json_output=args.json)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        route = None if args.skip_advisory else resolve_advisory_route()
        rc, report = run_hybrid(
            args.pack,
            advisory_model=route.selector if route is not None else "advisory-skipped",
            advisory_variant=route.variant if route is not None else None,
            verifier_profile=args.verifier_profile,
            batch_size=args.batch_size,
            timeout=args.timeout, jobs=args.jobs, advisory_jobs=args.advisory_jobs,
            verifier_jobs=args.verifier_jobs,
            advisory_batch_size=args.advisory_batch_size,
            verifier_batch_size=args.verifier_batch_size,
            strict=args.strict,
            only=args.only,
            certifying=False,
            json_output=args.json,
            campaign_snapshot=args.campaign_snapshot,
            skip_advisory=args.skip_advisory,
            progress=progress)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if evidence_output is not None:
        try:
            _write_evidence_output(evidence_output, report)
        except OSError as exc:
            print(f"error: could not write evidence output: {exc}", file=sys.stderr)
            return 1
    print(report)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
