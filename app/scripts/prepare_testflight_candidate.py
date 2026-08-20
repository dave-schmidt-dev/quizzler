#!/usr/bin/env python3
"""Freeze one Quizzler v2 source candidate without Apple or credential access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_adapter import AdapterError, V2_FORMAT, freeze_release
from release_candidate import (
    CandidateSourceError,
    assert_candidate_scope_clean,
    identity_proof,
    source_snapshot,
)
from sync_release_tool import DEFAULT_DESTINATION


ROOT = Path(__file__).resolve().parents[2]
PROJECT_PATH = "app/Quizzler.xcodeproj/project.pbxproj"
CONFIG_PATH = "app/release-config.toml"
READINESS_PATH = "app/releases/state/current-readiness.json"
ZERO_DIGEST = "0" * 64


class CandidatePreparationError(ValueError):
    """Stable candidate-bootstrap rejection."""


def _run_git(root: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CandidatePreparationError("candidate-git-command-failed")
    return completed.stdout


def _committed_versions(project_text: str) -> tuple[str, str]:
    """Read Release versions from the committed QuizzleriOS target settings."""

    target = re.search(
        r"(?ms)^\s*[0-9A-F]+ /\* QuizzleriOS \*/ = \{.*?isa = PBXNativeTarget;.*?buildConfigurationList = ([0-9A-F]+) /\* Build configuration list for PBXNativeTarget \"QuizzleriOS\" \*/;.*?^\s*\};",
        project_text,
    )
    if target is None:
        raise CandidatePreparationError("candidate-project-target-unreadable")
    list_id = target.group(1)
    configurations = re.search(
        rf"(?ms)^\s*{list_id} /\* Build configuration list for PBXNativeTarget \"QuizzleriOS\" \*/ = \{{.*?buildConfigurations = \((.*?)\);.*?^\s*\}};",
        project_text,
    )
    if configurations is None:
        raise CandidatePreparationError("candidate-project-target-unreadable")
    release_id = None
    for identifier, name in re.findall(r"\s*([0-9A-F]+) /\* (Debug|Release) \*/,", configurations.group(1)):
        if name == "Release":
            release_id = identifier
    if release_id is None:
        raise CandidatePreparationError("candidate-project-target-unreadable")
    configuration = re.search(
        rf"(?ms)^\s*{release_id} /\* Release \*/ = \{{.*?isa = XCBuildConfiguration;.*?buildSettings = \{{(.*?)^\s*\}};.*?^\s*\}};",
        project_text,
    )
    if configuration is None:
        raise CandidatePreparationError("candidate-project-target-unreadable")
    settings = configuration.group(1)
    marketing = re.search(r"(?m)^\s*MARKETING_VERSION = ([^;]+);", settings)
    build = re.search(r"(?m)^\s*CURRENT_PROJECT_VERSION = ([^;]+);", settings)
    if marketing is None or build is None:
        raise CandidatePreparationError("candidate-project-version-missing")
    marketing_version = marketing.group(1).strip().strip('"')
    build_number = build.group(1).strip().strip('"')
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", marketing_version):
        raise CandidatePreparationError("candidate-marketing-version-invalid")
    if not re.fullmatch(r"[1-9][0-9]*", build_number):
        raise CandidatePreparationError("candidate-build-number-invalid")
    return marketing_version, build_number


def _load_config(root: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CandidatePreparationError("candidate-release-config-invalid") from exc
    if not isinstance(value, dict):
        raise CandidatePreparationError("candidate-release-config-invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidatePreparationError("candidate-adapter-unreadable") from exc


def _write_private_new(path: Path, value: dict[str, Any]) -> None:
    encoded = _canonical(value) + b"\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CandidatePreparationError("candidate-readiness-already-exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _existing_created_at(path: Path) -> str | None:
    """Reuse the frozen timestamp on an exact candidate retry only."""

    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidatePreparationError("candidate-manifest-unreadable") from exc
    created_at = value.get("createdAt") if isinstance(value, dict) else None
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise CandidatePreparationError("candidate-manifest-unreadable")
    return created_at


def _readiness_skeleton(root: Path, manifest: Path) -> dict[str, Any]:
    config = _load_config(root)
    inv8_path = config.get("release_inv8_evidence_path")
    if not isinstance(inv8_path, str) or not inv8_path or Path(inv8_path).is_absolute() or ".." in Path(inv8_path).parts:
        raise CandidatePreparationError("candidate-release-config-invalid")
    return {
        "formatVersion": V2_FORMAT,
        "candidateManifest": manifest.resolve().relative_to(root.resolve()).as_posix(),
        "evidence": {
            "inv8Certification": {"path": inv8_path, "sha256": ZERO_DIGEST},
            "productionSchema": {"path": "app/releases/evidence/production-schema.json", "sha256": ZERO_DIGEST},
            "device": {"path": "app/releases/evidence/device.json", "sha256": ZERO_DIGEST},
        },
    }


def _ensure_readiness_skeleton(root: Path, manifest: Path) -> Path:
    path = root / READINESS_PATH
    expected = _readiness_skeleton(root, manifest)
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CandidatePreparationError("candidate-readiness-unreadable") from exc
        existing_evidence = value.get("evidence") if isinstance(value, dict) else None
        evidence_shape_ok = (
            isinstance(existing_evidence, dict)
            and set(existing_evidence) == set(expected["evidence"])
            and all(
                isinstance(existing_evidence[name], dict)
                and existing_evidence[name].get("path") == expected["evidence"][name]["path"]
                and isinstance(existing_evidence[name].get("sha256"), str)
                and len(existing_evidence[name]["sha256"]) == 64
                and all(character in "0123456789abcdef" for character in existing_evidence[name]["sha256"])
                for name in expected["evidence"]
            )
        )
        if (
            not isinstance(value, dict)
            or value.get("formatVersion") != V2_FORMAT
            or value.get("candidateManifest") != expected["candidateManifest"]
            or not evidence_shape_ok
        ):
            raise CandidatePreparationError("candidate-readiness-identity-drift")
        # Existing evidence may have been bound after an earlier interrupted
        # run.  It is immutable candidate state, never a bootstrap overwrite.
        return path
    _write_private_new(path, expected)
    return path


def prepare_candidate(
    root: Path = ROOT,
    *,
    runtime: Path = DEFAULT_DESTINATION,
    git: Callable[[list[str]], str] | None = None,
    freezer: Callable[..., Path] = freeze_release,
    on_status: Callable[[str], None] | None = None,
) -> tuple[Path, Path]:
    """Create/resume the v2 candidate and only then create its readiness shell."""

    root = root.resolve()
    runner = git or (lambda arguments: _run_git(root, arguments))
    if on_status:
        on_status("candidate-source-cleanliness-started")
    assert_candidate_scope_clean(root, command=runner)
    if on_status:
        on_status("candidate-source-clean")
        on_status("candidate-source-snapshot-started")
    revision = runner(["rev-parse", "HEAD"]).strip()
    snapshot = source_snapshot(root, revision, command=runner)
    project = runner(["show", f"{revision}:{PROJECT_PATH}"])
    marketing_version, build_number = _committed_versions(project)
    config = _load_config(root)
    if (
        config.get("release_candidate_format") != V2_FORMAT
        or config.get("release_lane") != "standard"
        or config.get("release_inv8_evidence_path") != "app/releases/evidence/inv8-certification.json"
        or config.get("release_inv8_required_packs") != ["question-packs/cissp/cissp-core.json"]
        or config.get("release_device_evidence_count") != 1
        or config.get("release_prebuild_requirements") != ["production-schema", "device-acceptance"]
        or config.get("release_readiness_requirements") != ["production-schema", "device-acceptance", "asc-build", "testflight-receipt"]
        or not isinstance(config.get("release_state_directory"), str)
    ):
        raise CandidatePreparationError("candidate-release-config-invalid")
    adapter = root / "app" / "scripts" / "release_adapter.py"
    adapter_digest = _sha256(adapter)
    candidate_id = f"{marketing_version}-{build_number}"
    state_directory = root / config["release_state_directory"]
    existing = _existing_created_at(state_directory / "candidates" / candidate_id / "manifest.json")
    request = {
        "formatVersion": V2_FORMAT,
        "marketingVersion": marketing_version,
        "buildNumber": build_number,
        "gitRevision": revision,
        "sourceDigest": snapshot.digest,
        "adapterDigest": adapter_digest,
        "identityProofSha256": identity_proof(snapshot, marketing_version, build_number, adapter_digest),
        "lane": "standard",
        "readinessRequirements": config["release_readiness_requirements"],
        "createdAt": existing or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    request_path = state_directory / ".candidate-request.json"
    _write_private_new(request_path, request)
    try:
        if on_status:
            on_status("candidate-freeze-started")
        manifest = freezer(
            request_path,
            state_directory=state_directory,
            repository_root=root,
            runtime=runtime,
            on_status=on_status,
        )
    finally:
        request_path.unlink(missing_ok=True)
    if on_status:
        on_status("candidate-readiness-skeleton-started")
    readiness = _ensure_readiness_skeleton(root, manifest)
    if on_status:
        on_status("candidate-bootstrap-complete")
    return manifest, readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args(argv)
    status = lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True)
    try:
        manifest, readiness = prepare_candidate(args.repository, runtime=args.runtime, on_status=status)
    except (CandidateSourceError, CandidatePreparationError, AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps({"candidateManifest": str(manifest), "readiness": str(readiness)}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
