#!/usr/bin/env python3
"""Credential-free source identity helpers for Quizzler release candidates."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_PATH = "app/Quizzler.xcodeproj/project.pbxproj"
ADAPTER_CONFIG_PATH = ".release/release-adapter.json"
SNAPSHOT_POLICY_VERSION = "quizzler-native-inputs-v2"


class CandidateSourceError(ValueError):
    """Stable rejections for source-snapshot construction."""


@dataclass(frozen=True)
class SourceSnapshot:
    """A deterministic committed native-input identity."""

    revision: str
    digest: str
    entries: tuple[tuple[str, str, str], ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _run_git(root: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CandidateSourceError("candidate-git-command-failed")
    return completed.stdout


def _parse_configured_paths(encoded: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse one exact disjoint archive/tool identity declaration."""

    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise CandidateSourceError("candidate-source-config-invalid") from exc
    source = value.get("sourcePaths") if isinstance(value, dict) else None
    tools = value.get("nonSourcePaths") if isinstance(value, dict) else None
    if not isinstance(source, list) or not source or not isinstance(tools, list) or not tools:
        raise CandidateSourceError("candidate-source-config-invalid")
    combined = source + tools
    if any(not isinstance(item, str) or not item or "\\" in item for item in combined):
        raise CandidateSourceError("candidate-source-config-invalid")
    normalized = tuple(Path(item).as_posix() for item in combined)
    if any(Path(item).is_absolute() or ".." in Path(item).parts or item != normalized[index]
           for index, item in enumerate(combined)):
        raise CandidateSourceError("candidate-source-config-invalid")
    if len(set(normalized)) != len(normalized):
        raise CandidateSourceError("candidate-source-config-invalid")
    normalized_source = tuple(normalized[:len(source)])
    normalized_tools = tuple(normalized[len(source):])
    if ADAPTER_CONFIG_PATH not in normalized_tools:
        raise CandidateSourceError("candidate-source-config-invalid")
    for index, path in enumerate(normalized):
        prefix = path.rstrip("/") + "/"
        if any(other.startswith(prefix) for other in normalized[index + 1:]) or any(
            path.startswith(other.rstrip("/") + "/") for other in normalized[index + 1:]
        ):
            raise CandidateSourceError("candidate-source-config-invalid")
    return normalized_source, normalized_tools


def _configured_paths(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Load and validate the working-tree identity declaration and paths."""

    try:
        encoded = (root / ADAPTER_CONFIG_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        raise CandidateSourceError("candidate-source-config-invalid") from exc
    source, tools = _parse_configured_paths(encoded)
    for path in source + tools:
        resolved = (root / path).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise CandidateSourceError("candidate-source-config-invalid") from exc
        if resolved.is_symlink() or not resolved.exists():
            raise CandidateSourceError("candidate-source-path-missing")
    return source, tools


def configured_identity_paths(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the validated archive and release-tool path partitions."""

    return _configured_paths(root.resolve())


def is_candidate_scope_path(path: str, source_paths: tuple[str, ...]) -> bool:
    """Return whether ``path`` belongs to one exact configured source path."""

    normalized = path.replace("\\", "/")
    return any(normalized == declared or normalized.startswith(declared.rstrip("/") + "/") for declared in source_paths)


def relevant_dirty_paths(porcelain: str, source_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Extract changed configured-source paths from porcelain-v1 output.

    The caller uses ``-z`` so filenames cannot be confused with delimiters.
    Rename/copy records contain a second, unprefixed old path; both paths are
    checked because either side can move an archive input in or out of scope.
    """

    fields = porcelain.split("\0")
    dirty: list[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise CandidateSourceError("candidate-git-status-invalid")
        status, path = record[:2], record[3:]
        paths = [path]
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise CandidateSourceError("candidate-git-status-invalid")
            paths.append(fields[index])
            index += 1
        dirty.extend(item for item in paths if is_candidate_scope_path(item, source_paths))
    return tuple(sorted(set(dirty)))


def assert_candidate_scope_clean(
    root: Path,
    *,
    command: Callable[[list[str]], str] | None = None,
) -> None:
    """Reject a release candidate when any native archive input is dirty."""

    runner = command or (lambda args: _run_git(root, args))
    source_paths, _ = _configured_paths(root)
    inspection_paths = source_paths + (ADAPTER_CONFIG_PATH,)
    dirty = relevant_dirty_paths(
        runner(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *inspection_paths]),
        inspection_paths,
    )
    if dirty:
        raise CandidateSourceError("candidate-working-tree-dirty")


def _validate_project_scope(project_text: str) -> None:
    """Fail closed if committed Xcode inputs leave the declared app scope."""

    # A project file can point at a sibling/root path through an absolute path
    # or ``..``. The configured snapshot is safe only while neither is present.
    if re.search(r"sourceTree = <absolute>;", project_text):
        raise CandidateSourceError("candidate-project-external-input")
    for raw in re.findall(r"\bpath = (?:\"([^\"]+)\"|([^;]+));", project_text):
        value = (raw[0] or raw[1]).strip()
        if value.startswith("/") or ".." in Path(value).parts:
            raise CandidateSourceError("candidate-project-external-input")


def _tree_entries(
    tree: str,
    source_paths: tuple[str, ...],
    declared_paths: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    all_entries: list[tuple[str, str, str]] = []
    for item in tree.split("\0"):
        if not item:
            continue
        try:
            metadata, path = item.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except ValueError as exc:
            raise CandidateSourceError("candidate-source-tree-invalid") from exc
        if object_type != "blob" or not re.fullmatch(r"[0-9a-f]{40,64}", object_id) or not is_candidate_scope_path(path, declared_paths):
            raise CandidateSourceError("candidate-source-tree-invalid")
        all_entries.append((path, mode, object_id))
    resolved = {declared: False for declared in declared_paths}
    for path, _, _ in all_entries:
        for declared in declared_paths:
            if path == declared or path.startswith(declared.rstrip("/") + "/"):
                resolved[declared] = True
    entries = tuple(entry for entry in all_entries if is_candidate_scope_path(entry[0], source_paths))
    if not entries or not any(path == PROJECT_PATH for path, _, _ in entries) or not all(resolved.values()):
        raise CandidateSourceError("candidate-source-tree-invalid")
    return tuple(sorted(entries))


def source_snapshot(
    root: Path,
    revision: str,
    *,
    command: Callable[[list[str]], str] | None = None,
    source_paths: tuple[str, ...] | None = None,
) -> SourceSnapshot:
    """Hash all committed native/archive inputs deterministically at ``revision``."""

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise CandidateSourceError("candidate-git-revision-invalid")
    runner = command or (lambda args: _run_git(root, args))
    if source_paths is None:
        try:
            source_paths, tool_paths = _parse_configured_paths(
                runner(["show", f"{revision}:{ADAPTER_CONFIG_PATH}"])
            )
        except CandidateSourceError:
            raise
        declared_paths = source_paths + tool_paths
    else:
        declared_paths = source_paths
    project = runner(["show", f"{revision}:{PROJECT_PATH}"])
    _validate_project_scope(project)
    entries = _tree_entries(
        runner(["ls-tree", "-rz", revision, "--", *declared_paths]),
        source_paths,
        declared_paths,
    )
    digest = hashlib.sha256(
        _canonical(
            {
                "policyVersion": SNAPSHOT_POLICY_VERSION,
                "entries": [
                    {"path": path, "mode": mode, "object": object_id}
                    for path, mode, object_id in entries
                ],
            }
        )
    ).hexdigest()
    return SourceSnapshot(revision=revision, digest=digest, entries=entries)


def compose_source_digest(tracked_source_digest: str, pack_manifest_digest: str) -> str:
    """Compose the two independently reproducible archive-input identities."""

    if not re.fullmatch(r"[0-9a-f]{64}", tracked_source_digest) or not re.fullmatch(r"[0-9a-f]{64}", pack_manifest_digest):
        raise CandidateSourceError("candidate-pack-digest-invalid")
    return hashlib.sha256(_canonical({
        "policyVersion": SNAPSHOT_POLICY_VERSION,
        "trackedSourceDigest": tracked_source_digest,
        "packManifestDigest": pack_manifest_digest,
    })).hexdigest()


def archive_source_digest(snapshot: SourceSnapshot, pack_manifest_digest: str) -> str:
    """Bind tracked native inputs to the installed-pack manifest identity."""

    return compose_source_digest(snapshot.digest, pack_manifest_digest)


def identity_proof(
    snapshot: SourceSnapshot,
    marketing_version: str,
    build_number: str,
    adapter_digest: str,
    *,
    source_digest: str | None = None,
) -> str:
    """Return the canonical proof binding project versions to a source snapshot."""

    return hashlib.sha256(
        _canonical(
            {
                "policyVersion": SNAPSHOT_POLICY_VERSION,
                "gitRevision": snapshot.revision,
                "sourceDigest": source_digest or snapshot.digest,
                "marketingVersion": marketing_version,
                "buildNumber": build_number,
                "adapterDigest": adapter_digest,
            }
        )
    ).hexdigest()
