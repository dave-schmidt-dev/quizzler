"""Canonical product state, active-candidate selection, and file locks."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import canonical_product_state_home
from .iterative_release import (
    CandidateIdentityV2,
    ReleaseStateError,
    canonical_bytes,
    freeze_candidate_v2,
    hash_bytes,
    load_candidate_manifest,
    read_candidate_ledger_v2,
)


TERMINAL_TRANSITIONS = frozenset(
    {"failed", "superseded", "cancelled", "internalTestFlightReceipted"}
)
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


class ProductStateError(ValueError):
    """A stable product-state or lock rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


class StateLock:
    """A process/thread-safe lock with diagnosable interrupted-holder metadata."""

    def __init__(self, path: Path | str, *, kind: str, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.kind = kind
        self.timeout = timeout
        self._handle = None
        self._local: threading.Lock | None = None
        self._token = uuid.uuid4().hex

    def __enter__(self) -> "StateLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._local = _local_lock(self.path)
        if not self._local.acquire(timeout=self.timeout):
            raise ProductStateError(f"{self.kind}-lock-held")
        handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    self._local.release()
                    self._local = None
                    raise ProductStateError(f"{self.kind}-lock-held")
                time.sleep(0.01)
        handle.seek(0)
        prior_raw = handle.read()
        if prior_raw:
            try:
                prior = json.loads(prior_raw)
            except json.JSONDecodeError:
                prior = {}
            if isinstance(prior, dict) and prior.get("token") and not prior.get("releasedAt"):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                self._local.release()
                self._local = None
                raise ProductStateError(f"{self.kind}-lock-stale")
        holder = {
            "formatVersion": 2,
            "kind": self.kind,
            "pid": os.getpid(),
            "token": self._token,
            "acquiredAt": _timestamp(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(canonical_bytes(holder) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is not None:
            released = {
                "formatVersion": 2,
                "kind": self.kind,
                "pid": os.getpid(),
                "token": self._token,
                "releasedAt": _timestamp(),
            }
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(canonical_bytes(released) + b"\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
        if self._local is not None:
            self._local.release()
            self._local = None


class ProductLock(StateLock):
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        super().__init__(path, kind="product", timeout=timeout)


class CandidateLock(StateLock):
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        super().__init__(path, kind="candidate", timeout=timeout)


def diagnose_lock(path: Path | str, *, kind: str) -> str:
    """Return available, held, or stale without modifying lock bytes."""

    lock_path = Path(path)
    if not lock_path.exists():
        return "available"
    handle = lock_path.open("rb")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return f"{kind}-lock-held"
        raw = handle.read()
        if not raw:
            return "available"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return f"{kind}-lock-stale"
        return "available" if value.get("releasedAt") else f"{kind}-lock-stale"
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def recover_stale_lock(path: Path | str, *, kind: str) -> None:
    """Mark only an unlocked interrupted-holder record as recovered."""

    lock_path = Path(path)
    local = _local_lock(lock_path)
    if not local.acquire(blocking=False):
        raise ProductStateError(f"{kind}-lock-held")
    try:
        handle = lock_path.open("a+b")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ProductStateError(f"{kind}-lock-held") from exc
            value = {
                "formatVersion": 2,
                "kind": kind,
                "recoveredAt": _timestamp(),
                "releasedAt": _timestamp(),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
    finally:
        local.release()


@dataclass(frozen=True)
class CandidateSelection:
    manifest_path: Path
    candidate_id: str
    created: bool


class ProductState:
    """One canonical product state shared across worktrees and harnesses."""

    def __init__(self, home: Path | str, product_key: str) -> None:
        self.home = Path(home)
        self.product_key = product_key

    @classmethod
    def for_repository(
        cls,
        repository_root: Path | str,
        product_key: str,
        *,
        non_git_home: Path | str | None = None,
    ) -> "ProductState":
        return cls(
            canonical_product_state_home(
                repository_root, product_key, non_git_home=non_git_home
            ),
            product_key,
        )

    @property
    def lock_path(self) -> Path:
        return self.home / "product.lock"

    @property
    def active_path(self) -> Path:
        return self.home / "active-candidate.json"

    @property
    def candidates_directory(self) -> Path:
        return self.home / "candidates"

    def initialize(self) -> None:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.candidates_directory.mkdir(mode=0o700, exist_ok=True)
        metadata = {"formatVersion": 2, "productKey": self.product_key}
        encoded = canonical_bytes(metadata) + b"\n"
        path = self.home / "product.json"
        if path.exists():
            if path.read_bytes() != encoded:
                raise ProductStateError("product-state-identity-mismatch")
            return
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ProductStateError("product-state-identity-mismatch")
            return
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def _active(self) -> dict | None:
        if not self.active_path.exists():
            return None
        try:
            value = json.loads(self.active_path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductStateError("active-candidate-corrupt") from exc
        body = dict(value) if isinstance(value, dict) else {}
        declared = body.pop("pointerSha256", None)
        if value.get("formatVersion") != 2 or hash_bytes(canonical_bytes(body)) != declared:
            raise ProductStateError("active-candidate-corrupt")
        return value

    def _write_active(self, candidate: str, release_train: str) -> None:
        body = {
            "formatVersion": 2,
            "candidateId": candidate,
            "releaseTrain": release_train,
        }
        body["pointerSha256"] = hash_bytes(canonical_bytes(body))
        temporary = self.home / f".active-{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(canonical_bytes(body) + b"\n")
        temporary.chmod(0o600)
        os.replace(temporary, self.active_path)

    def select_or_create(
        self,
        *,
        release_train: str,
        source_digest: str,
        adapter_digest: str,
        allocate: Callable[[], CandidateIdentityV2],
        created_at: str | None = None,
    ) -> CandidateSelection:
        """Select a duplicate trigger or atomically allocate/freeze one candidate."""

        self.initialize()
        with ProductLock(self.lock_path):
            active = self._active()
            if active is not None:
                candidate = str(active["candidateId"])
                manifest_path = self.candidates_directory / candidate / "manifest.json"
                try:
                    manifest = load_candidate_manifest(manifest_path)
                    terminal = any(
                        record["transition"] in TERMINAL_TRANSITIONS
                        for record in read_candidate_ledger_v2(manifest_path.parent)
                    )
                except ReleaseStateError as exc:
                    raise ProductStateError("active-candidate-corrupt") from exc
                if not terminal:
                    if active["releaseTrain"] != release_train:
                        raise ProductStateError("active-candidate-conflict")
                    if manifest["sourceSnapshot"]["sha256"] != source_digest or manifest["adapter"]["sha256"] != adapter_digest:
                        raise ProductStateError("active-candidate-input-mismatch")
                    return CandidateSelection(manifest_path, candidate, False)
            identity = allocate()
            if not isinstance(identity, CandidateIdentityV2):
                raise ProductStateError("identity-allocation-result-invalid")
            if identity.source_digest != source_digest or identity.adapter_digest != adapter_digest:
                raise ProductStateError("identity-allocation-binding-mismatch")
            manifest_path = freeze_candidate_v2(
                self.candidates_directory,
                identity,
                product_identifier=self.product_key,
                created_at=created_at,
            )
            candidate = manifest_path.parent.name
            self._write_active(candidate, release_train)
            return CandidateSelection(manifest_path, candidate, True)


def select_active_candidate(
    state: ProductState,
    **kwargs,
) -> CandidateSelection:
    return state.select_or_create(**kwargs)


__all__ = [
    "CandidateLock",
    "CandidateSelection",
    "ProductLock",
    "ProductState",
    "ProductStateError",
    "StateLock",
    "TERMINAL_TRANSITIONS",
    "diagnose_lock",
    "recover_stale_lock",
    "select_active_candidate",
]
