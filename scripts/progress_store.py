"""SQLite-backed progress store for shared-progress server-authoritative mode.

A single-row ``progress_state`` table holds the canonical document. An
``operation_records`` table provides idempotency keys so replaying a
previously-acknowledged mutation returns the stored response instead of
mutating state again.

All writes are serialized through a process-wide ``threading.Lock`` because
the server (``serve.py``) uses one thread per request.

stdlib only — no external packages.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RevisionConflictError(Exception):
    """The expected revision does not match the current database revision."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"Revision conflict: expected a different revision, current is {current_revision}")


class LockTimeoutError(Exception):
    """Could not acquire the write lock within the configured timeout."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        super().__init__(f"Could not acquire write lock within {timeout_s:.1f}s")


class CorruptDatabaseError(Exception):
    """Database integrity check failed."""

    pass


class FutureSchemaError(Exception):
    """Database was created by a newer version of the software."""

    pass


class OperationConflictError(Exception):
    """An operation with the same idempotency key but different body already exists."""

    pass


class PayloadTooLargeError(Exception):
    """The payload exceeds the configured size limit."""

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"Payload size {size} exceeds limit {limit}")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
MAX_IMPORT_PAYLOAD = 2 * 1024 * 1024  # 2 MB
MAX_MUTATION_PAYLOAD = 512 * 1024  # 512 KB
MAX_SESSIONS = 200
MAX_OPERATION_RECORDS = 4096
OPERATION_RECORD_AGE_DAYS = 30
DEFAULT_DB_PATH = "./.data/quizzler.sqlite3"
MAX_BACKUPS = 5
WRITE_LOCK_TIMEOUT = 5.0  # seconds
BUSY_TIMEOUT = 5000  # ms

SRS_INTERVALS_MS: dict[int, int] = {
    1: 86_400_000,      # 1 day
    2: 259_200_000,     # 3 days
    3: 604_800_000,     # 7 days
    4: 1_209_600_000,   # 14 days
    5: 2_592_000_000,   # 30 days
    6: 5_184_000_000,   # 60 days
    7: 10_368_000_000,  # 120 days
}

_write_lock = threading.Lock()

DB_SQL = """
CREATE TABLE IF NOT EXISTS progress_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    schema_version INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    document_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_operation ON operation_records(endpoint, operation_id);
"""

EMPTY_DOCUMENT: dict[str, Any] = {
    "schema_version": 1,
    "sessions": [],
    "mastery": {},
    "srs": {},
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_plain_obj(v: Any) -> bool:
    return isinstance(v, dict)


def validate_normalized_doc(doc: dict[str, Any]) -> tuple[bool, str]:
    """Validate a normalized document matches the expected shape."""
    if not _is_plain_obj(doc):
        return False, "Not an object"
    if doc.get("schema_version") != 1:
        return False, "Missing or wrong schema_version"
    if not isinstance(doc.get("sessions"), list):
        return False, "sessions must be an array"
    if not _is_plain_obj(doc.get("mastery")):
        return False, "mastery must be an object"
    if not _is_plain_obj(doc.get("srs")):
        return False, "srs must be an object"

    for cid, course_mastery in doc["mastery"].items():
        if not _is_plain_obj(course_mastery):
            return False, f"mastery[{cid}] must be an object (course->pack nesting)"
        for pid, pack_mastery in course_mastery.items():
            if not _is_plain_obj(pack_mastery):
                return False, f"mastery[{cid}][{pid}] must be an object"
            if not _is_plain_obj(pack_mastery.get("seen")) or not _is_plain_obj(pack_mastery.get("correct")):
                return False, f"mastery[{cid}][{pid}] missing seen/correct objects"

    for cid, srs_entry in doc["srs"].items():
        if not _is_plain_obj(srs_entry):
            return False, f"srs[{cid}] must be an object"
        if "schema_version" not in srs_entry:
            return False, f"srs[{cid}] missing schema_version"
        if not _is_plain_obj(srs_entry.get("questions")):
            return False, f"srs[{cid}].questions must be an object"

    return True, ""


def validate_mutation_payload(data: bytes, operation: str) -> None:
    size = len(data)
    limit = MAX_IMPORT_PAYLOAD if operation in ("import_progress",) else MAX_MUTATION_PAYLOAD
    if size > limit:
        raise PayloadTooLargeError(size, limit)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _hash_request(body: dict[str, Any] | None) -> str:
    """sha256 of canonical JSON (sorted keys)."""
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _open_db(path: str) -> sqlite3.Connection:
    db_dir = os.path.dirname(path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _current_ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Init / Migrate
# ---------------------------------------------------------------------------


def _check_integrity(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if row and row[0] != "ok":
        raise CorruptDatabaseError(f"integrity_check failed: {row[0]}")


def _check_user_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA user_version").fetchone()
    version = row[0] if row else 0
    if version > SCHEMA_VERSION:
        raise FutureSchemaError(f"Database user_version {version} > current {SCHEMA_VERSION}")


def init_db(path: str = DEFAULT_DB_PATH) -> None:
    conn = _open_db(path)
    try:
        conn.executescript(DB_SQL)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        _check_integrity(conn)
        _check_user_version(conn)
    finally:
        conn.close()


def migrate_db(path: str = DEFAULT_DB_PATH) -> None:
    """Forward-only migrations based on PRAGMA user_version."""
    backup_db(path)

    conn = _open_db(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]

        if version > SCHEMA_VERSION:
            conn.close()
            raise FutureSchemaError(f"Database user_version {version} > current {SCHEMA_VERSION}")

        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def get_progress(path: str = DEFAULT_DB_PATH) -> tuple[int, dict[str, Any]]:
    """Return ``(revision, document_dict)``. Returns ``(0, empty_doc)`` on empty DB."""
    conn = _open_db(path)
    try:
        row = conn.execute(
            "SELECT revision, document_json FROM progress_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return 0, _copy_empty_doc()
        return row[0], json.loads(row[1])
    finally:
        conn.close()


def get_revision(path: str = DEFAULT_DB_PATH) -> int:
    """Return current revision, or 0 if the DB is empty."""
    conn = _open_db(path)
    try:
        row = conn.execute("SELECT revision FROM progress_state WHERE id = 1").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_document(path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Return the normalized document dict."""
    _, doc = get_progress(path)
    return doc


# ---------------------------------------------------------------------------
# Operation records
# ---------------------------------------------------------------------------


def _build_operation_record(
    endpoint: str,
    operation_id: str,
    request_body: dict[str, Any],
    response_body: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    request_hash = _hash_request(request_body)
    response_json = json.dumps(response_body, sort_keys=True)
    record = {
        "endpoint": endpoint,
        "operation_id": operation_id,
        "request_hash": request_hash,
        "response_json": response_json,
        "created_at": _current_ts(),
    }
    return request_hash, record


# ---------------------------------------------------------------------------
# Write operations (require _write_lock)
# ---------------------------------------------------------------------------


def _acquire_write_lock() -> bool:
    return _write_lock.acquire(timeout=WRITE_LOCK_TIMEOUT)


def _prune_operation_records(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=OPERATION_RECORD_AGE_DAYS)).isoformat()
    conn.execute("DELETE FROM operation_records WHERE created_at < ?", (cutoff,))

    count = conn.execute("SELECT COUNT(*) FROM operation_records").fetchone()[0]
    if count > MAX_OPERATION_RECORDS:
        excess = count - MAX_OPERATION_RECORDS
        conn.execute(
            "DELETE FROM operation_records WHERE id IN "
            "(SELECT id FROM operation_records ORDER BY id ASC LIMIT ?)",
            (excess,),
        )


def _merge_mastery_delta(
    mastery: dict[str, Any],
    course_id: str,
    pack_id: str,
    delta: dict[str, Any],
) -> None:
    course = mastery.setdefault(course_id, {})
    pack = course.setdefault(pack_id, {"seen": {}, "correct": {}, "consecutive": {}})

    for field in ("seen", "correct", "consecutive"):
        if field in delta and _is_plain_obj(delta[field]):
            pack.setdefault(field, {})
            for key, value in delta[field].items():
                if field == "consecutive":
                    pack[field][key] = int(value)
                else:
                    pack[field][key] = value


def save_progress(
    expected_revision: int,
    document: dict[str, Any],
    operation_record: dict[str, Any] | None,
    path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Atomically save a new document revision with idempotency guard.

    ``operation_record`` is a dict with keys: endpoint, operation_id,
    request_hash, response_json, created_at. May be None for operations that
    don't need idempotency (e.g. bootstrapping).

    Returns the response that should be returned to the client (from the
    operation_record if this is a replay).
    """
    acquired = _acquire_write_lock()
    if not acquired:
        raise LockTimeoutError(WRITE_LOCK_TIMEOUT)

    conn = _open_db(path)
    try:
        with contextlib.closing(conn):
            conn.execute("BEGIN EXCLUSIVE")
            try:
                if operation_record is not None:
                    existing = conn.execute(
                        "SELECT request_hash, response_json FROM operation_records "
                        "WHERE endpoint = ? AND operation_id = ?",
                        (operation_record["endpoint"], operation_record["operation_id"]),
                    ).fetchone()

                    if existing is not None:
                        if existing[0] != operation_record["request_hash"]:
                            raise OperationConflictError(
                                f"Operation {operation_record['operation_id']} already "
                                f"exists with a different request body"
                            )
                        return json.loads(existing[1])

                row = conn.execute(
                    "SELECT revision FROM progress_state WHERE id = 1"
                ).fetchone()
                current_revision = row[0] if row else 0

                if expected_revision != current_revision:
                    raise RevisionConflictError(current_revision)

                valid, reason = validate_normalized_doc(document)
                if not valid:
                    raise ValueError(f"Invalid document: {reason}")

                document_json = json.dumps(document, sort_keys=True)
                ts = _current_ts()

                if current_revision == 0:
                    conn.execute(
                        "INSERT INTO progress_state (id, schema_version, revision, document_json, updated_at) "
                        "VALUES (1, ?, ?, ?, ?)",
                        (SCHEMA_VERSION, 1, document_json, ts),
                    )
                else:
                    conn.execute(
                        "UPDATE progress_state SET revision = ?, document_json = ?, updated_at = ? WHERE id = 1",
                        (current_revision + 1, document_json, ts),
                    )

                if operation_record is not None:
                    conn.execute(
                        "INSERT INTO operation_records (endpoint, operation_id, request_hash, response_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            operation_record["endpoint"],
                            operation_record["operation_id"],
                            operation_record["request_hash"],
                            operation_record["response_json"],
                            operation_record["created_at"],
                        ),
                    )

                _prune_operation_records(conn)
                conn.execute("COMMIT")
                return json.loads(operation_record["response_json"]) if operation_record else {}

            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
        _write_lock.release()


# ---------------------------------------------------------------------------
# Backup / Restore
# ---------------------------------------------------------------------------


def backup_db(path: str = DEFAULT_DB_PATH) -> str:
    """Create a timestamped backup; retain max 5. Returns the backup path."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.backup-{ts}"

    acquired = _acquire_write_lock()
    if not acquired:
        raise LockTimeoutError(WRITE_LOCK_TIMEOUT)

    try:
        src = sqlite3.connect(path)
        try:
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        _prune_backups(path)

        return backup_path
    finally:
        _write_lock.release()


def _prune_backups(path: str) -> None:
    parent = os.path.dirname(path) or "."
    base = os.path.basename(path)
    backups = sorted(
        [os.path.join(parent, f) for f in os.listdir(parent)
         if f.startswith(f"{base}.backup-")],
        key=lambda p: os.path.getmtime(p),
    )
    while len(backups) > MAX_BACKUPS:
        os.remove(backups.pop(0))


def restore_db(path: str = DEFAULT_DB_PATH, backup_path: str | None = None) -> None:
    """Restore from a backup. If ``backup_path`` is None, use the most recent."""
    acquired = _acquire_write_lock()
    if not acquired:
        raise LockTimeoutError(WRITE_LOCK_TIMEOUT)

    try:
        if backup_path is None:
            parent = os.path.dirname(path) or "."
            base = os.path.basename(path)
            candidates = sorted(
                [os.path.join(parent, f) for f in os.listdir(parent)
                 if f.startswith(f"{base}.backup-")],
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            if not candidates:
                raise FileNotFoundError(f"No backups found for {path}")
            backup_path = candidates[0]

        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        if os.path.exists(path):
            corrupt_path = f"{path}.corrupt-{int(time.time())}"
            os.rename(path, corrupt_path)

        shutil.copy2(backup_path, path)

        conn = _open_db(path)
        try:
            _check_integrity(conn)
        except CorruptDatabaseError:
            conn.close()
            os.rename(path, f"{path}.corrupt-{int(time.time())}")
            if os.path.exists(corrupt_path):
                os.rename(corrupt_path, path)
            raise
        finally:
            conn.close()

        os.remove(backup_path)
    finally:
        _write_lock.release()


# ---------------------------------------------------------------------------
# Semantic mutations
# ---------------------------------------------------------------------------


def _copy_empty_doc() -> dict[str, Any]:
    return json.loads(json.dumps(EMPTY_DOCUMENT))


def quiz_completed(
    path: str,
    session: dict[str, Any],
    mastery_delta: dict[str, Any],
    course_id: str,
    pack_id: str,
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically append a session and merge a mastery delta.

    The session is prepended to the sessions array (cap at 200).

    ``expected_revision`` is the client-side revision the mutation expects
    the DB to be at.  If ``None`` (callers that don't carry a revision),
    the current DB revision is used.  When the caller provides a value,
    ``save_progress`` will check it — detecting conflicts *and* allowing
    idempotent replays to succeed before the revision gate fires.
    """
    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev

    sessions = doc.get("sessions", [])
    sessions.insert(0, session)
    if len(sessions) > MAX_SESSIONS:
        sessions = sessions[:MAX_SESSIONS]
    doc["sessions"] = sessions

    mastery = doc.setdefault("mastery", {})
    _merge_mastery_delta(mastery, course_id, pack_id, mastery_delta)

    request_body = {
        "course_id": course_id,
        "pack_id": pack_id,
        "operation_id": operation_id,
        "session": session,
        "mastery_delta": mastery_delta,
    }
    op_hash, op_record = _build_operation_record(
        "quiz_completed", operation_id, request_body, {"revision": current_rev + 1}
    )
    return save_progress(expected_revision, doc, op_record, path)


def srs_rated(
    path: str,
    course_id: str,
    composite_key: str,
    rating: str,
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Rate a question using SRS. Returns ``{old_tier, new_tier}``.

    ``composite_key`` is ``"{course_id}::{pack_id}::{question_id}"``.
    Rating is one of "again" (1), "hard" (2), "good" (3), "easy" (4).

    ``expected_revision`` is the client-side revision the mutation expects
    the DB to be at.  If ``None`` (callers that don't carry a revision),
    the current DB revision is used.  When the caller provides a value,
    ``save_progress`` will check it — detecting conflicts *and* allowing
    idempotent replays to succeed before the revision gate fires.
    """
    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev

    srs_state = doc.get("srs", {}).get(course_id)
    if srs_state is None:
        srs_state = {
            "schema_version": 1,
            "updated_at": _current_ts(),
            "questions": {},
        }
        doc.setdefault("srs", {})[course_id] = srs_state

    questions = srs_state.setdefault("questions", {})
    existing = questions.get(composite_key, {})
    is_unassigned = (
        not existing
        or existing.get("review_count", 0) == 0
        or existing.get("tier", 0) == 0
        or "next_due_at" not in existing
    )

    if is_unassigned:
        current_tier = existing.get("tier") or 1
        current_tier = max(1, min(7, current_tier))
    else:
        current_tier = max(1, min(7, existing.get("tier", 1)))

    old_tier = current_tier

    now_ms = int(time.time() * 1000)

    if rating == "again":
        new_tier = max(1, current_tier - 2)
        next_due_at = _ms_to_iso(now_ms + 600_000)
        last_result = "again"
    elif rating == "hard":
        new_tier = current_tier
        interval = SRS_INTERVALS_MS.get(current_tier, SRS_INTERVALS_MS[1])
        next_due_at = _ms_to_iso(now_ms + round(interval * 0.75))
        last_result = "hard"
    elif rating == "good":
        new_tier = min(7, current_tier + 1)
        interval = SRS_INTERVALS_MS.get(new_tier, SRS_INTERVALS_MS[1])
        next_due_at = _ms_to_iso(now_ms + round(interval))
        last_result = "good"
    elif rating == "easy":
        new_tier = min(7, current_tier + 2)
        interval = SRS_INTERVALS_MS.get(new_tier, SRS_INTERVALS_MS[1])
        next_due_at = _ms_to_iso(now_ms + round(interval * 1.25))
        last_result = "easy"
    else:
        new_tier = current_tier
        interval = SRS_INTERVALS_MS.get(current_tier, SRS_INTERVALS_MS[1])
        next_due_at = _ms_to_iso(now_ms + round(interval))
        last_result = str(rating)

    existing["tier"] = new_tier
    existing["next_due_at"] = next_due_at
    existing["last_result"] = last_result
    existing["last_reviewed_at"] = _ms_to_iso(now_ms)
    existing["review_count"] = existing.get("review_count", 0) + 1
    if rating == "again":
        existing["lapse_count"] = existing.get("lapse_count", 0) + 1

    questions[composite_key] = existing
    srs_state["updated_at"] = _current_ts()

    request_body = {
        "course_id": course_id,
        "composite_key": composite_key,
        "rating": rating,
        "operation_id": operation_id,
    }
    response_body = {"old_tier": old_tier, "new_tier": new_tier}
    op_hash, op_record = _build_operation_record(
        "srs_rated", operation_id, request_body, response_body
    )
    return save_progress(expected_revision, doc, op_record, path)


def import_progress(
    path: str,
    document: dict[str, Any],
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Import a full normalized document, replacing existing state."""
    document_json = json.dumps(document, sort_keys=True)
    validate_mutation_payload(document_json.encode("utf-8"), "import_progress")

    valid, reason = validate_normalized_doc(document)
    if not valid:
        raise ValueError(f"Invalid import document: {reason}")

    current_rev = get_revision(path)
    if expected_revision is None:
        expected_revision = current_rev

    request_body = {
        "operation_id": operation_id,
        "document_schema_version": document.get("schema_version"),
    }
    response_body = {"revision": current_rev + 1}
    op_hash, op_record = _build_operation_record(
        "import_progress", operation_id, request_body, response_body
    )
    return save_progress(expected_revision, document, op_record, path)


def save_sessions(
    path: str,
    sessions: list[dict[str, Any]],
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Replace the session history without modifying mastery or SRS data."""
    if not isinstance(sessions, list):
        raise ValueError("sessions must be a list")

    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev
    doc["sessions"] = sessions[:MAX_SESSIONS]

    valid, reason = validate_normalized_doc(doc)
    if not valid:
        raise ValueError(f"Invalid sessions: {reason}")

    request_body = {"operation_id": operation_id, "sessions": doc["sessions"]}
    op_hash, op_record = _build_operation_record(
        "save_sessions", operation_id, request_body, {"revision": current_rev + 1}
    )
    return save_progress(expected_revision, doc, op_record, path)


def save_srs_state(
    path: str,
    course_id: str,
    state: dict[str, Any],
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Replace the SRS state for one course without modifying other progress."""
    if not course_id or not isinstance(state, dict):
        raise ValueError("course_id and SRS state are required")

    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev
    doc.setdefault("srs", {})[course_id] = state

    valid, reason = validate_normalized_doc(doc)
    if not valid:
        raise ValueError(f"Invalid SRS state: {reason}")

    request_body = {
        "operation_id": operation_id,
        "course_id": course_id,
        "state": state,
    }
    op_hash, op_record = _build_operation_record(
        "save_srs_state", operation_id, request_body, {"revision": current_rev + 1}
    )
    return save_progress(expected_revision, doc, op_record, path)


def reset_progress(
    path: str,
    operation_id: str,
    *,
    clear_srs_course_id: str | None = None,
    clear_mastery: bool = False,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Reset progress: clear history OR clear SRS for a specific course."""
    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev

    if clear_srs_course_id is not None:
        doc.setdefault("srs", {}).pop(clear_srs_course_id, None)
        request_body = {
            "operation_id": operation_id,
            "action": "srs_reset",
            "course_id": clear_srs_course_id,
        }
    elif clear_mastery:
        doc["mastery"] = {}
        request_body = {
            "operation_id": operation_id,
            "action": "clear_mastery",
        }
    else:
        doc["sessions"] = []
        doc["mastery"] = {}
        request_body = {
            "operation_id": operation_id,
            "action": "clear_history",
        }

    response_body = {"revision": current_rev + 1}
    op_hash, op_record = _build_operation_record(
        "reset_progress", operation_id, request_body, response_body
    )
    return save_progress(expected_revision, doc, op_record, path)


def cleanup_orphans(
    path: str,
    active_course_ids: list[str],
    operation_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Remove mastery and sessions for courses not in ``active_course_ids``."""
    current_rev, doc = get_progress(path)
    if expected_revision is None:
        expected_revision = current_rev

    active = set(active_course_ids)
    mastery = doc.get("mastery", {})
    removed_courses = [c for c in list(mastery.keys()) if c not in active]
    for c in removed_courses:
        del mastery[c]

    sessions = doc.get("sessions", [])
    original_len = len(sessions)
    doc["sessions"] = [s for s in sessions if s.get("course") in active]
    removed_sessions = original_len - len(doc["sessions"])

    request_body = {
        "operation_id": operation_id,
        "active_course_ids": active_course_ids,
    }
    response_body = {
        "revision": current_rev + 1,
        "mastery_courses_removed": len(removed_courses),
        "sessions_removed": removed_sessions,
    }
    op_hash, op_record = _build_operation_record(
        "cleanup_orphans", operation_id, request_body, response_body
    )
    return save_progress(expected_revision, doc, op_record, path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ms_to_iso(epoch_ms: int) -> str:
    return datetime.datetime.fromtimestamp(
        epoch_ms / 1000.0, tz=datetime.timezone.utc
    ).isoformat()
