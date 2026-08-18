#!/usr/bin/env python3
"""Stage and verify the reviewed central Apple release runtime offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTHORITY = ROOT / "app" / "design-authority-manifest.json"
DEFAULT_DESTINATION = ROOT / "app" / "vendor" / "apple-release" / "runtime"
HEX64 = frozenset("0123456789abcdef")


class SyncError(ValueError):
    """A stable fail-closed synchronization error."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SyncError("release-source-unreadable") from exc
    return digest.hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - HEX64)


def _relative(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SyncError("release-source-path-invalid")
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SyncError("release-source-path-invalid")
    return path


def _central_relative(value: object) -> Path:
    """Validate the portable source-checkout reference."""

    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SyncError("release-source-path-invalid")
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", "."} for part in path.parts):
        raise SyncError("release-source-path-invalid")
    if ".." in path.parts and path.parts[0] != "..":
        raise SyncError("release-source-path-invalid")
    if path.parts.count("..") > 1 or any(part == ".." for part in path.parts[1:]):
        raise SyncError("release-source-path-invalid")
    return path


def _contains_symlink(path: Path, *, below: Path | None = None) -> bool:
    """Return whether a path traverses a symlink below a trusted base path."""

    absolute = path.absolute()
    if below is None:
        return absolute.is_symlink()
    base = below.absolute()
    try:
        parts = absolute.relative_to(base).parts
    except ValueError:
        return True
    current = base
    for part in parts:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _authority_repository_root(authority_path: Path) -> Path:
    """Return the repository root for an app-level authority manifest."""

    authority = authority_path.absolute()
    if _contains_symlink(authority):
        raise SyncError("authority-manifest-unreadable")
    return authority.parent.parent


def _central_root(authority: dict[str, Any], authority_path: Path) -> Path:
    """Resolve the portable source relation without arbitrary workspace escape."""

    repository = _authority_repository_root(authority_path)
    candidate = (repository / _central_relative(authority["centralSource"]["path"])).absolute()
    workspace = repository.parent.absolute()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise SyncError("release-source-path-invalid") from exc
    if _contains_symlink(candidate, below=workspace) or not candidate.is_dir():
        raise SyncError("central-source-drift")
    return candidate


def _central_child(root: Path, relative: Path, error: str) -> Path:
    """Resolve a declared central file while rejecting symlink substitution."""

    candidate = root / relative
    if _contains_symlink(candidate, below=root) or not candidate.is_file():
        raise SyncError(error)
    return candidate


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(code) from exc
    if not isinstance(value, dict):
        raise SyncError(code)
    return value


def load_authority(path: Path = DEFAULT_AUTHORITY) -> dict[str, Any]:
    document = _load_json(path, "authority-manifest-unreadable")
    if set(document) != {"formatVersion", "centralSource", "designAuthorities"} or document.get("formatVersion") != "2.0.0":
        raise SyncError("authority-manifest-invalid")
    central = document.get("centralSource")
    reports = document.get("designAuthorities")
    if not isinstance(central, dict) or set(central) != {"path", "files"}:
        raise SyncError("authority-manifest-invalid")
    try:
        _central_relative(central.get("path"))
    except SyncError as exc:
        raise SyncError("authority-manifest-invalid") from exc
    if not isinstance(central.get("path"), str):
        raise SyncError("authority-manifest-invalid")
    files = central.get("files")
    if not isinstance(files, list) or not files:
        raise SyncError("authority-manifest-invalid")
    names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise SyncError("authority-manifest-invalid")
        relative = _relative(entry["path"]).as_posix()
        if relative in names or not _digest(entry["sha256"]):
            raise SyncError("authority-manifest-invalid")
        names.add(relative)
    if not isinstance(reports, list) or len(reports) != 2:
        raise SyncError("authority-manifest-invalid")
    for report in reports:
        if not isinstance(report, dict) or set(report) != {"path", "sha256"} or not _digest(report.get("sha256")):
            raise SyncError("authority-manifest-invalid")
        try:
            _relative(report.get("path"))
        except SyncError as exc:
            raise SyncError("authority-manifest-invalid") from exc
        if not isinstance(report.get("path"), str):
            raise SyncError("authority-manifest-invalid")
    return document


def verify_central(authority: dict[str, Any], authority_path: Path = DEFAULT_AUTHORITY) -> None:
    """Verify exact central source and design-authority bytes offline.

    Content hashes, not the central checkout's revision: pinning a revision made
    an independently-evolving repository gate this one, so a commit that touched
    none of the vendored files failed the build. The hashes below describe the
    exact bytes that get copied, which is the property that matters. Only
    ``sync_runtime`` reaches outside this repository at all; the release path
    verifies the vendored runtime through ``verify_runtime``.
    """

    central = authority["centralSource"]
    root = _central_root(authority, authority_path)
    for entry in central["files"]:
        path = _central_child(root, _relative(entry["path"]), "central-source-drift")
        if sha256(path) != entry["sha256"]:
            raise SyncError("central-source-drift")
    for entry in authority["designAuthorities"]:
        path = _central_child(root, _relative(entry["path"]), "design-authority-drift")
        if sha256(path) != entry["sha256"]:
            raise SyncError("design-authority-drift")


def _runtime_manifest(authority: dict[str, Any]) -> dict[str, Any]:
    body = {
        "formatVersion": "2.0.0",
        "centralSource": authority["centralSource"],
        "designAuthorities": authority["designAuthorities"],
    }
    body["manifestSha256"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return body


def verify_runtime(destination: Path, authority_path: Path = DEFAULT_AUTHORITY) -> dict[str, Any]:
    authority = load_authority(authority_path)
    manifest = _load_json(destination / "sync-manifest.json", "runtime-manifest-unreadable")
    expected = _runtime_manifest(authority)
    if manifest != expected or (destination / "sync-manifest.json").read_bytes() != canonical_bytes(expected) + b"\n":
        raise SyncError("runtime-manifest-drift")
    expected_names = {entry["path"] for entry in expected["centralSource"]["files"]} | {"sync-manifest.json"}
    actual_names = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_names != expected_names:
        raise SyncError("runtime-file-set-drift")
    for entry in expected["centralSource"]["files"]:
        path = destination / _relative(entry["path"])
        if path.is_symlink() or sha256(path) != entry["sha256"]:
            raise SyncError("runtime-file-drift")
    return manifest


def sync_runtime(
    destination: Path = DEFAULT_DESTINATION,
    *,
    authority_path: Path = DEFAULT_AUTHORITY,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Atomically stage exact reviewed bytes and verify the result."""

    authority = load_authority(authority_path)
    if on_status:
        on_status("release-runtime-source-verification-started")
    verify_central(authority, authority_path)
    if on_status:
        on_status("release-runtime-source-verification-ok")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise SyncError("runtime-destination-symlink")
    temporary = Path(tempfile.mkdtemp(prefix=".apple-release-stage-", dir=destination.parent))
    try:
        central_root = _central_root(authority, authority_path)
        for entry in authority["centralSource"]["files"]:
            relative = _relative(entry["path"])
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(central_root / relative, target, follow_symlinks=False)
        manifest = _runtime_manifest(authority)
        (temporary / "sync-manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
        verify_runtime(temporary, authority_path)
        if destination.exists():
            backup = destination.with_name(f".{destination.name}.previous-{os.getpid()}")
            if backup.exists():
                raise SyncError("runtime-backup-collision")
            destination.rename(backup)
            try:
                temporary.rename(destination)
            except Exception:
                backup.rename(destination)
                raise
            shutil.rmtree(backup)
        else:
            temporary.rename(destination)
        if on_status:
            on_status("release-runtime-sync-ok")
        return verify_runtime(destination, authority_path)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    status = lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True)
    try:
        if args.verify_only:
            status("release-runtime-verification-started")
            verify_runtime(args.destination, args.authority)
            status("release-runtime-verification-ok")
        else:
            sync_runtime(args.destination, authority_path=args.authority, on_status=status)
    except SyncError as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
