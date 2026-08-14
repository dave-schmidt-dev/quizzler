#!/usr/bin/env python3
"""Credential-free source identity helpers for Quizzler release candidates.

The release archive is built from the ``app/`` tree.  That scope deliberately
includes every tracked native source, project setting, asset, release adapter,
and any future bundled content placed under ``app/``.  The Xcode project is
rejected if it references an input outside that tree, so unrelated root work
(such as question-pack authoring) cannot block a native candidate or be
silently omitted from one.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


SOURCE_SCOPE_PREFIX = "app/"
PROJECT_PATH = "app/Quizzler.xcodeproj/project.pbxproj"
SNAPSHOT_POLICY_VERSION = "quizzler-app-tree-v1"
GENERATED_NON_INPUT_PREFIXES = (
    "app/build/",
    "app/releases/state/",
    "app/releases/evidence/",
)


class CandidateSourceError(ValueError):
    """Stable rejections for source-snapshot construction."""


@dataclass(frozen=True)
class SourceSnapshot:
    """A deterministic committed app-tree identity."""

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


def is_candidate_scope_path(path: str) -> bool:
    """Return whether a git path can alter the native archive input tree."""

    normalized = path.replace("\\", "/")
    return normalized.startswith(SOURCE_SCOPE_PREFIX) and not normalized.startswith(GENERATED_NON_INPUT_PREFIXES)


def relevant_dirty_paths(porcelain: str) -> tuple[str, ...]:
    """Extract changed ``app/`` paths from porcelain-v1 output.

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
        dirty.extend(item for item in paths if is_candidate_scope_path(item))
    return tuple(sorted(set(dirty)))


def assert_candidate_scope_clean(
    root: Path,
    *,
    command: Callable[[list[str]], str] | None = None,
) -> None:
    """Reject a release candidate when any native archive input is dirty."""

    runner = command or (lambda args: _run_git(root, args))
    dirty = relevant_dirty_paths(runner(["status", "--porcelain=v1", "-z", "--untracked-files=all"]))
    if dirty:
        raise CandidateSourceError("candidate-working-tree-dirty")


def _validate_project_scope(project_text: str) -> None:
    """Fail closed if committed Xcode inputs leave the declared app scope."""

    # A project file can point at a sibling/root path through an absolute path
    # or ``..``.  The all-app snapshot is only safe while neither is present.
    if re.search(r"sourceTree = <absolute>;", project_text):
        raise CandidateSourceError("candidate-project-external-input")
    for raw in re.findall(r"\bpath = (?:\"([^\"]+)\"|([^;]+));", project_text):
        value = (raw[0] or raw[1]).strip()
        if value.startswith("/") or ".." in Path(value).parts:
            raise CandidateSourceError("candidate-project-external-input")


def _tree_entries(tree: str) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for item in tree.split("\0"):
        if not item:
            continue
        try:
            metadata, path = item.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except ValueError as exc:
            raise CandidateSourceError("candidate-source-tree-invalid") from exc
        if object_type != "blob" or not re.fullmatch(r"[0-9a-f]{40,64}", object_id) or not is_candidate_scope_path(path):
            raise CandidateSourceError("candidate-source-tree-invalid")
        entries.append((path, mode, object_id))
    if not entries or not any(path == PROJECT_PATH for path, _, _ in entries):
        raise CandidateSourceError("candidate-source-tree-invalid")
    return tuple(sorted(entries))


def source_snapshot(
    root: Path,
    revision: str,
    *,
    command: Callable[[list[str]], str] | None = None,
) -> SourceSnapshot:
    """Hash all committed native/archive inputs deterministically at ``revision``."""

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise CandidateSourceError("candidate-git-revision-invalid")
    runner = command or (lambda args: _run_git(root, args))
    project = runner(["show", f"{revision}:{PROJECT_PATH}"])
    _validate_project_scope(project)
    entries = _tree_entries(runner(["ls-tree", "-rz", revision, "--", "app"]))
    digest = hashlib.sha256(
        _canonical(
            {
                "policyVersion": SNAPSHOT_POLICY_VERSION,
                "revision": revision,
                "entries": [
                    {"path": path, "mode": mode, "object": object_id}
                    for path, mode, object_id in entries
                ],
            }
        )
    ).hexdigest()
    return SourceSnapshot(revision=revision, digest=digest, entries=entries)


def identity_proof(snapshot: SourceSnapshot, marketing_version: str, build_number: str, adapter_digest: str) -> str:
    """Return the canonical proof binding project versions to a source snapshot."""

    return hashlib.sha256(
        _canonical(
            {
                "policyVersion": SNAPSHOT_POLICY_VERSION,
                "gitRevision": snapshot.revision,
                "sourceDigest": snapshot.digest,
                "marketingVersion": marketing_version,
                "buildNumber": build_number,
                "adapterDigest": adapter_digest,
            }
        )
    ).hexdigest()
