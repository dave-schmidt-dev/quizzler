#!/usr/bin/env python3
"""Record and verify redacted evidence from the attended Development probe."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_PATH = ROOT / "releases/evidence/development-cloudkit-probe.json"
MAX_AGE = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=5)
CONTAINER_IDENTIFIER = "iCloud.com.zerodelta.quizzler.dev"
ZONE_NAME = "QuizzlerDevelopmentProbe-v1"
RECORD_TYPE = "DevelopmentProbe"
OPERATIONS = ("save", "conflict", "fetch", "replay", "delete")
PROBE_TEST_IDENTIFIER = "CloudKitDevelopmentProbeTests.testProbeLifecycleIsOptInAndReportsMachineReadableTerminalResult"
REQUIRED_KEYS = frozenset({
    "schema_version", "kind", "status", "terminal", "configuration", "signing", "completed_at",
    "operations", "test_identifier",
})


def _xcresult_summary(xcresult_path: Path) -> dict[str, Any]:
    if not xcresult_path.is_dir() or xcresult_path.is_symlink():
        raise ValueError("--xcresult must name a local .xcresult bundle")
    completed = subprocess.run(
        ["xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(xcresult_path), "--format", "json"],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("xcresulttool could not read the XCTest result bundle")
    try:
        summary = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("xcresulttool returned malformed JSON") from exc
    if not isinstance(summary, dict):
        raise ValueError("xcresult summary root must be an object")
    return summary


def _xcresult_tests(xcresult_path: Path) -> dict[str, Any]:
    """Read the detailed XCTest tree, including individual test-case results."""
    completed = subprocess.run(
        ["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", str(xcresult_path), "--format", "json"],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("xcresulttool could not read the XCTest test tree")
    try:
        tests = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("xcresulttool returned malformed XCTest test JSON") from exc
    if not isinstance(tests, dict):
        raise ValueError("xcresult test tree root must be an object")
    return tests


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _passed(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"passed", "pass", "success", "succeeded"}


def _summary_passed(summary: dict[str, Any]) -> None:
    """Reject a result bundle whose overall XCTest result is not successful."""
    result = summary.get("result")
    if result is not None and not _passed(result):
        raise ValueError("XCTest result did not pass")


def _summary_completion(summary: dict[str, Any]) -> datetime:
    for node in _walk(summary):
        for key in ("endTime", "end_time", "finishedTime", "finished_time"):
            if key in node:
                try:
                    value = node[key]
                    if isinstance(value, (int, float)):
                        return datetime.fromtimestamp(value, timezone.utc)
                    return _parse_time(value.replace("+00:00", "Z"))
                except (AttributeError, TypeError, ValueError):
                    continue
        for key in ("finishTime", "finish_time"):
            if key in node:
                try:
                    value = node[key]
                    if isinstance(value, (int, float)):
                        return datetime.fromtimestamp(value, timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    continue
    raise ValueError("XCTest result has no terminal completion time")


def _passed_probe_test_node(summary: dict[str, Any]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for node in _walk(summary):
        names = [node.get(key) for key in ("identifier", "name", "testIdentifier", "testName", "nodeIdentifierURL")]
        identity = " ".join(value for value in names if isinstance(value, str))
        if "CloudKitDevelopmentProbeTests" in identity and "testProbeLifecycleIsOptInAndReportsMachineReadableTerminalResult" in identity:
            if not any(_passed(node.get(key)) for key in ("result", "status", "outcome", "testStatus")):
                raise ValueError("Development probe XCTest did not pass")
            matches.append(node)
    if len(matches) != 1:
        raise ValueError("XCTest result omitted an unambiguous passed Development probe test")
    return matches[0]


def _extract_plist_bytes(output: str) -> bytes:
    """Extract an XML or binary plist after codesign's diagnostic prefix."""
    raw = output.encode()
    starts = [index for marker in (b"<?xml", b"bplist00") if (index := raw.find(marker)) >= 0]
    if not starts:
        raise ValueError("signed app entitlements are malformed")
    start = min(starts)
    if raw[start:].startswith(b"bplist00"):
        return raw[start:]
    end = raw.find(b"</plist>", start)
    if end < 0:
        raise ValueError("signed app entitlements are malformed")
    return raw[start:end + len(b"</plist>")]


def verify_signed_app(app_path: Path) -> None:
    """Verify the signed Debug app and its Development entitlements."""
    if not app_path.is_dir() or app_path.is_symlink():
        raise ValueError("--signed-app must name a local signed app bundle")
    verified = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app_path)],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if verified.returncode != 0:
        raise ValueError("codesign rejected the signed app bundle")
    entitlements = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(app_path)],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if entitlements.returncode != 0:
        raise ValueError("codesign returned no app entitlements")
    try:
        # codesign writes its diagnostic prefix and entitlement plist to stderr.
        # Accept stdout as well for controlled test doubles and future tool changes.
        entitlement_output = "\n".join(value for value in (entitlements.stdout, entitlements.stderr) if value)
        plist = plistlib.loads(_extract_plist_bytes(entitlement_output))
    except (TypeError, ValueError, ExpatError, plistlib.InvalidFileException) as exc:
        raise ValueError("signed app entitlements are malformed") from exc
    if plist.get("com.apple.developer.icloud-container-identifiers") != [CONTAINER_IDENTIFIER] or plist.get("aps-environment") != "development":
        raise ValueError("signed app is not the expected Development entitlement")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("completed_at must be an ISO-8601 UTC timestamp")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("completed_at must use UTC")
    return parsed


def validate_evidence(
    evidence: Any,
    *,
    now: datetime | None = None,
    xcresult_path: Path | None = None,
    signed_app_path: Path | None = None,
) -> list[str]:
    """Return fail-closed validation errors for one decoded evidence object."""
    if not isinstance(evidence, dict):
        return ["evidence root must be an object"]
    errors: list[str] = []
    missing, unknown = REQUIRED_KEYS - set(evidence), set(evidence) - REQUIRED_KEYS
    if missing:
        errors.append("missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        errors.append("unknown fields: " + ", ".join(sorted(unknown)))
    if evidence.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if evidence.get("kind") != "cloudkit_development_probe_evidence":
        errors.append("invalid evidence kind")
    if evidence.get("status") != "complete" or evidence.get("terminal") is not True:
        errors.append("probe must have complete terminal status")
    if evidence.get("configuration") != "Debug" or evidence.get("signing") != "Development":
        errors.append("evidence must bind a signed Debug Development build")
    if evidence.get("operations") != list(OPERATIONS):
        errors.append("operations must contain the complete disposable-zone lifecycle")
    if evidence.get("test_identifier") != PROBE_TEST_IDENTIFIER:
        errors.append("evidence does not identify the passed Development probe test")
    try:
        completed_at = _parse_time(evidence.get("completed_at"))
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    else:
        current = now or datetime.now(timezone.utc)
        if current - completed_at > MAX_AGE:
            errors.append("evidence is stale")
        if completed_at - current > FUTURE_SKEW:
            errors.append("completed_at is in the future")
    if (xcresult_path is None) != (signed_app_path is None):
        errors.append("both signed app and xcresult sources are required together")
    elif xcresult_path is None:
        errors.append("independent signed app/xcresult sources are required")
    else:
        try:
            summary = _xcresult_summary(xcresult_path)
            tests = _xcresult_tests(xcresult_path)
            _summary_passed(summary)
            _passed_probe_test_node(tests)
            finished = _summary_completion(summary)
            if evidence.get("completed_at") != finished.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"):
                errors.append("completed_at does not match the supplied XCTest result")
            verify_signed_app(signed_app_path)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"artifact source verification failed ({exc})")
    return errors


def record_evidence(evidence_path: Path, xcresult_path: Path, signed_app_path: Path) -> None:
    """Write evidence derived from a passed XCTest result and verified app."""
    summary = _xcresult_summary(xcresult_path)
    tests = _xcresult_tests(xcresult_path)
    _summary_passed(summary)
    _passed_probe_test_node(tests)
    finished = _summary_completion(summary)
    verify_signed_app(signed_app_path)
    evidence = {
        "schema_version": 1,
        "kind": "cloudkit_development_probe_evidence",
        "status": "complete",
        "terminal": True,
        "configuration": "Debug",
        "signing": "Development",
        "completed_at": finished.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operations": list(OPERATIONS),
        "test_identifier": PROBE_TEST_IDENTIFIER,
    }
    errors = validate_evidence(evidence, xcresult_path=xcresult_path, signed_app_path=signed_app_path)
    if errors:
        raise ValueError("refusing to write invalid evidence: " + "; ".join(errors))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{evidence_path.name}.", dir=evidence_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(evidence, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_name, evidence_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def verify_file(path: Path, *, xcresult_path: Path | None = None, signed_app_path: Path | None = None) -> list[str]:
    if not path.is_file():
        return ["evidence file is absent"]
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"evidence is not valid JSON ({exc.__class__.__name__})"]
    return validate_evidence(evidence, xcresult_path=xcresult_path, signed_app_path=signed_app_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--xcresult", type=Path)
    parser.add_argument("--signed-app", type=Path)
    parser.add_argument("--evidence-path", type=Path, default=Path(os.environ.get("QUIZZLER_DEVELOPMENT_PROBE_EVIDENCE_PATH", DEFAULT_EVIDENCE_PATH)))
    args = parser.parse_args(argv)
    if args.record:
        if args.xcresult is None or args.signed_app is None:
            parser.error("--record requires --xcresult and --signed-app")
        try:
            record_evidence(args.evidence_path.expanduser().resolve(), args.xcresult.expanduser().resolve(), args.signed_app.expanduser().resolve())
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"FAIL: Development probe evidence: {exc}")
            return 1
        print(f"Development probe evidence recorded at {args.evidence_path}")
        return 0
    if not args.verify:
        parser.error("--verify or --record is required")
    if (args.xcresult is None) != (args.signed_app is None):
        parser.error("--verify requires both --xcresult and --signed-app")
    errors = verify_file(
        args.evidence_path.expanduser().resolve(),
        xcresult_path=args.xcresult.expanduser().resolve() if args.xcresult else None,
        signed_app_path=args.signed_app.expanduser().resolve() if args.signed_app else None,
    )
    if errors:
        print("FAIL: Development probe evidence: " + "; ".join(errors))
        return 1
    print("Development probe evidence verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
