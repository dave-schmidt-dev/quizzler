#!/usr/bin/env python3
"""Ingest in-app question issue reports into feedback intake markdown files.

Reads exported issue reports (issue-inbox-v1.json), validates them against
REPORT_SCHEMA v1, deduplicates against an ingested-issues ledger, and appends
new entries into per-course feedback intake files (.logs/feedback/<course>/pending.md).
"""

from __future__ import annotations

import argparse
import datetime
import errno
import fcntl
import json
import logging
import logging.handlers
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging (WARNING+ to RotatingFileHandler, DEBUG with --debug, never stdout)
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_logger = logging.getLogger("quizzler.ingest")

REPO_ROOT = Path(__file__).resolve().parent.parent

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
COURSE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ALLOWED_QUESTION_TYPES = frozenset({
    "multiple_choice",
    "scenario_multiple_choice",
    "multiple_select",
    "true_false",
    "matching",
})

ALLOWED_ISSUE_KEYS = frozenset({
    "schema_version",
    "issue_id",
    "course_id",
    "pack_id",
    "question_id",
    "question_type",
    "app_version",
    "build",
    "selected_response",
    "description",
})

REQUIRED_ISSUE_KEYS = frozenset({
    "schema_version",
    "issue_id",
    "course_id",
    "pack_id",
    "question_id",
    "question_type",
    "app_version",
    "build",
    "description",
})

MAX_ID_LEN = 256
MAX_VERSION_LEN = 128
MAX_SELECTED_RESPONSE_LEN = 512
MAX_DESCRIPTION_LEN = 2000
MIN_TIMESTAMP_MS = 0
MAX_TIMESTAMP_MS = 253402300799999

_HEADING_RE = re.compile(
    r"^###\s+\S+\s+—\s+`([^`]+)`\s+—\s+source:\s+in-app report,\s+pack\s+`([^`]+)`,\s+issue\s+`([^`]+)`"
)


def _setup_logging(log_dir: Path | str, debug: bool = False) -> None:
    """Configure rotating file logging mirroring scripts/serve.py.

    Args:
        log_dir: Directory where quizzler.log will be written.
        debug: Whether to set log level to DEBUG instead of WARNING.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "quizzler.log")
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if debug else logging.WARNING)


def is_safe_id(identifier: Any) -> bool:
    """Check if identifier matches the safe id regex pattern.

    Args:
        identifier: Candidate identifier to test.

    Returns:
        True if identifier is a safe non-empty alphanumeric string with
        allowed punctuation and max length 256.
    """
    if not isinstance(identifier, str):
        return False
    return bool(SAFE_ID_RE.match(identifier))


def clean_report_text(text: str) -> str:
    """Normalize line endings, strip control characters, and trim trailing whitespace.

    Args:
        text: Raw report or response text to sanitize.

    Returns:
        Sanitized text with CRLF/CR normalized to LF, non-LF/TAB control
        characters stripped, and trailing whitespace removed per line.
    """
    normalized = (
        text.replace("\u2028", "\n")
        .replace("\u2029", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    cleaned_chars: list[str] = []
    for ch in normalized:
        if ch in ("\n", "\t"):
            cleaned_chars.append(ch)
        elif ord(ch) < 32 or ord(ch) == 127:
            continue
        elif unicodedata.category(ch).startswith("C"):
            continue
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars)
    lines = [line.rstrip(" \t") for line in cleaned.split("\n")]
    return "\n".join(lines)


def format_fenced_block(text: str) -> str:
    """Format sanitized text into an indented markdown fenced block.

    Args:
        text: Sanitize-ready text to fence.

    Returns:
        Fenced block indented two spaces under list item with info string text.
    """
    cleaned = clean_report_text(text)
    lines = cleaned.split("\n")
    runs = re.findall(r"`+", cleaned)
    max_run = max((len(r) for r in runs), default=0)
    fence_len = max(3, max_run + 1)
    fence = "`" * fence_len

    block = [f"  {fence}text"]
    for line in lines:
        block.append(f"  {line}")
    block.append(f"  {fence}")
    return "\n".join(block)


def format_entry(wrapper: dict[str, Any]) -> str:
    """Format an issue report into a markdown pending entry.

    Args:
        wrapper: Valid issue wrapper dictionary containing timestamps and issue.

    Returns:
        Formatted markdown entry block ending with a newline.
    """
    reported_at_ms = wrapper["reported_at_ms"]
    received_at_ms = wrapper["received_at_ms"]
    issue = wrapper["issue"]

    reported_dt = datetime.datetime.fromtimestamp(
        reported_at_ms / 1000.0, tz=datetime.timezone.utc
    )
    reported_date = reported_dt.strftime("%Y-%m-%d")
    reported_iso = reported_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    received_dt = datetime.datetime.fromtimestamp(
        received_at_ms / 1000.0, tz=datetime.timezone.utc
    )
    received_iso = received_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    qid = issue["question_id"]
    pack_id = issue["pack_id"]
    issue_id = issue["issue_id"]
    app_version = issue["app_version"]
    build = issue["build"]
    qtype = issue["question_type"]

    lines = [
        f"### {reported_date} — `{qid}` — source: in-app report, pack `{pack_id}`, issue `{issue_id}`",
        "",
        f"- Reported: {reported_iso}; received on Mac: {received_iso}",
        f"- App: {app_version} (build {build}); question type: `{qtype}`",
    ]

    selected_resp = issue.get("selected_response")
    if selected_resp is not None:
        lines.append("- Selected response:")
        lines.append(format_fenced_block(selected_resp))
    else:
        lines.append("- Selected response: (none)")

    lines.append("- Report:")
    lines.append(format_fenced_block(issue["description"]))
    lines.append("")

    return "\n".join(lines)


def validate_top_level(data: Any) -> tuple[bool, str]:
    """Validate top-level issue inbox protocol format.

    Args:
        data: Parsed JSON data to validate.

    Returns:
        A tuple of (is_valid, error_reason).
    """
    if not isinstance(data, dict):
        return False, "root must be a JSON object"
    if data.get("protocol") != "quizzler-issue-inbox":
        return False, "protocol must equal 'quizzler-issue-inbox'"
    version = data.get("version")
    if isinstance(version, bool) or version != 1:
        return False, "version must be integer 1"
    change_token = data.get("change_token")
    if change_token is not None and not isinstance(change_token, str):
        return False, "change_token must be null or string"
    issues = data.get("issues")
    if not isinstance(issues, dict):
        return False, "issues must be a JSON object"
    return True, ""


def validate_issue(map_key: Any, wrapper: Any) -> tuple[bool, str]:
    """Validate an individual issue wrapper and its issue payload.

    Args:
        map_key: The dictionary key in the issues mapping.
        wrapper: The wrapper dictionary containing timestamps and issue.

    Returns:
        A tuple of (is_valid, error_reason).
    """
    if not isinstance(wrapper, dict):
        return False, "wrapper must be a JSON object"
    expected_wrapper_keys = {"reported_at_ms", "received_at_ms", "issue"}
    if set(wrapper.keys()) != expected_wrapper_keys:
        return False, "wrapper keys must be exactly reported_at_ms, received_at_ms, issue"

    reported_at = wrapper["reported_at_ms"]
    if isinstance(reported_at, bool) or not isinstance(reported_at, int):
        return False, "reported_at_ms must be an integer"
    if not (MIN_TIMESTAMP_MS <= reported_at <= MAX_TIMESTAMP_MS):
        return False, "timestamp out of range"

    received_at = wrapper["received_at_ms"]
    if isinstance(received_at, bool) or not isinstance(received_at, int):
        return False, "received_at_ms must be an integer"
    if not (MIN_TIMESTAMP_MS <= received_at <= MAX_TIMESTAMP_MS):
        return False, "timestamp out of range"

    issue = wrapper["issue"]
    if not isinstance(issue, dict):
        return False, "issue must be a JSON object"

    keys = set(issue.keys())
    if not keys.issubset(ALLOWED_ISSUE_KEYS):
        return False, "issue contains unknown keys"
    if not REQUIRED_ISSUE_KEYS.issubset(keys):
        return False, "issue is missing required keys"

    schema_version = issue["schema_version"]
    if isinstance(schema_version, bool) or schema_version != 1:
        return False, "schema_version must be integer 1"

    issue_id = issue["issue_id"]
    if not is_safe_id(issue_id):
        return False, "issue_id is not a safe id"

    if map_key != issue_id:
        return False, "map key does not match issue.issue_id"

    pack_id = issue["pack_id"]
    if not is_safe_id(pack_id):
        return False, "pack_id is not a safe id"

    question_id = issue["question_id"]
    if not is_safe_id(question_id):
        return False, "question_id is not a safe id"

    course_id = issue["course_id"]
    if not isinstance(course_id, str) or not course_id.strip() or len(course_id) > MAX_ID_LEN:
        return False, "course_id must be non-blank string <= 256 chars"

    question_type = issue["question_type"]
    if question_type not in ALLOWED_QUESTION_TYPES:
        return False, f"question_type '{question_type}' is not supported"

    app_version = issue["app_version"]
    if not isinstance(app_version, str) or not app_version.strip() or len(app_version) > MAX_VERSION_LEN:
        return False, "app_version must be non-blank string <= 128 chars"

    build = issue["build"]
    if not isinstance(build, str) or not build.strip() or len(build) > MAX_VERSION_LEN:
        return False, "build must be non-blank string <= 128 chars"

    selected_response = issue.get("selected_response")
    if selected_response is not None:
        if not isinstance(selected_response, str) or not selected_response.strip() or len(selected_response) > MAX_SELECTED_RESPONSE_LEN:
            return False, "selected_response must be non-blank string <= 512 chars"

    description = issue["description"]
    if not isinstance(description, str) or not description.strip() or len(description) > MAX_DESCRIPTION_LEN:
        return False, "description must be non-blank string <= 2000 chars"

    return True, ""


def resolve_target(
    course_id: str, courses_root: Path, feedback_root: Path
) -> tuple[str, Path, str, bool]:
    """Resolve target path for an issue based on course existence.

    Args:
        course_id: Course ID declared in the issue report.
        courses_root: Directory containing course folders.
        feedback_root: Root directory for feedback logs.

    Returns:
        Tuple of (course_name, target_path, target_rel_str, is_unrouted).
    """
    is_valid_course = (
        bool(COURSE_ID_RE.match(course_id))
        and len(course_id) <= MAX_ID_LEN
        and (courses_root / course_id / "_course.json").is_file()
    )
    if is_valid_course:
        course_name = course_id
        target_path = feedback_root / course_name / "pending.md"
        is_unrouted = False
    else:
        course_name = "_unrouted"
        target_path = feedback_root / "_unrouted" / "pending.md"
        is_unrouted = True

    resolved_feedback = feedback_root.resolve()
    resolved_target = target_path.resolve()
    try:
        rel_target = resolved_target.relative_to(resolved_feedback)
    except ValueError:
        sys.stderr.write(f"Refusing target outside feedback root: {target_path}\n")
        sys.exit(1)

    return course_name, target_path, str(rel_target), is_unrouted


def extract_headings_from_target(target_path: Path) -> dict[str, tuple[str, str]]:
    """Extract question_id and pack_id for filed issues from a target file.

    Args:
        target_path: Path to feedback markdown file.

    Returns:
        Mapping of issue_id to (pack_id, question_id).
    """
    result: dict[str, tuple[str, str]] = {}
    if not target_path.is_file():
        return result
    try:
        content = target_path.read_text(encoding="utf-8")
        for line in content.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                qid, pack_id, issue_id = m.group(1), m.group(2), m.group(3)
                result[issue_id] = (pack_id, qid)
    except Exception as e:
        _logger.warning("Could not read headings from %s: %s", target_path, e)
    return result


def generate_summary(
    ledger: dict[str, Any],
    feedback_root: Path,
    in_memory_issues: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Generate a grouped view of the ledger by course, pack, and question ID.

    Args:
        ledger: The ledger dictionary containing 'filed' mapping.
        feedback_root: Root directory for feedback files to look up headings.
        in_memory_issues: Optional map of issue_id to wrapper dictionary from current run.

    Returns:
        Formatted summary string (IDs only).
    """
    filed = ledger.get("filed", {})
    if not filed:
        return ""

    target_cache: dict[str, dict[str, tuple[str, str]]] = {}
    grouped: dict[str, dict[str, dict[str, list[str]]]] = {}

    for issue_id, entry in filed.items():
        course = entry.get("course", "_unrouted")
        pack = "unknown"
        qid = "unknown"

        if in_memory_issues and issue_id in in_memory_issues:
            issue_payload = in_memory_issues[issue_id]["issue"]
            pack = issue_payload["pack_id"]
            qid = issue_payload["question_id"]
        else:
            target_rel = entry.get("target")
            if target_rel:
                if target_rel not in target_cache:
                    target_file = feedback_root / target_rel
                    target_cache[target_rel] = extract_headings_from_target(target_file)
                if issue_id in target_cache[target_rel]:
                    pack, qid = target_cache[target_rel][issue_id]

        grouped.setdefault(course, {}).setdefault(pack, {}).setdefault(qid, []).append(issue_id)

    lines: list[str] = []
    for course in sorted(grouped):
        lines.append(course)
        for pack in sorted(grouped[course]):
            lines.append(f"  {pack}")
            for qid in sorted(grouped[course][pack]):
                ids = grouped[course][pack][qid]
                lines.append(f"    {qid} ({len(ids)}): {', '.join(ids)}")

    return "\n".join(lines) + "\n"


def ingest(
    sources: list[Path],
    courses_root: Path,
    feedback_root: Path,
    ledger_path: Path,
    log_dir: Path,
    dry_run: bool = False,
    summary: bool = False,
    debug: bool = False,
) -> int:
    """Execute the issue ingestion workflow.

    Args:
        sources: List of source paths to inspect.
        courses_root: Directory containing course folders.
        feedback_root: Root directory for feedback logs.
        ledger_path: Path to ingested issues ledger JSON.
        log_dir: Directory for log files.
        dry_run: Whether to simulate without modifying files.
        summary: Whether to output grouped summary to stdout.
        debug: Whether to enable debug logging.

    Returns:
        Process exit code (0 success, 1 error, 3 lock held).
    """
    _setup_logging(log_dir, debug)
    _logger.debug("Starting ingest run")

    feedback_root.mkdir(parents=True, exist_ok=True)
    lock_path = feedback_root / ".ingest.lock"
    try:
        lock_file = open(lock_path, "a")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as e:
        if isinstance(e, BlockingIOError) or e.errno in (errno.EAGAIN, errno.EACCES):
            sys.stderr.write("another ingest is running\n")
            return 3
        raise

    try:
        accepted_sources: list[tuple[Path, dict[str, Any]]] = []
        readable_sources_count = 0

        for p in sources:
            if not p.is_file():
                sys.stderr.write(f"Source not found: {p}\n")
                continue
            try:
                raw_bytes = p.read_bytes()
                readable_sources_count += 1
            except PermissionError as e:
                sys.stderr.write(
                    f"Cannot read source {p}: {e}. Check macOS file and privacy permissions.\n"
                )
                continue
            except OSError as e:
                sys.stderr.write(f"Cannot read source {p}: {e}.\n")
                continue

            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except Exception as e:
                sys.stderr.write(f"Source {p} rejected: invalid JSON: {e}\n")
                continue

            is_valid, reason = validate_top_level(data)
            if not is_valid:
                sys.stderr.write(f"Source {p} rejected: {reason}\n")
                continue

            accepted_sources.append((p, data["issues"]))

        if readable_sources_count == 0:
            sys.stderr.write("Error: no source could be read\n")
            return 1

        sources_read = len(accepted_sources)
        merged_issues: dict[str, dict[str, Any]] = {}
        invalid_count = 0

        for p, issues_map in accepted_sources:
            for pos, (key, wrapper) in enumerate(issues_map.items()):
                is_valid, reason = validate_issue(key, wrapper)
                if not is_valid:
                    invalid_count += 1
                    if is_safe_id(key):
                        sys.stderr.write(f"Warning: skipping invalid issue '{key}': {reason}\n")
                    else:
                        sys.stderr.write(
                            f"Warning: skipping invalid issue at position {pos}: {reason}\n"
                        )
                    continue

                issue_id = wrapper["issue"]["issue_id"]
                if issue_id in merged_issues:
                    existing_wrapper = merged_issues[issue_id]
                    if existing_wrapper != wrapper:
                        sys.stderr.write(
                            f"Warning: issue '{issue_id}' seen with different payload; keeping first\n"
                        )
                else:
                    merged_issues[issue_id] = wrapper

        valid_count = len(merged_issues)

        ledger: dict[str, Any] = {"version": 1, "filed": {}}
        if ledger_path.is_file():
            try:
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
                if (
                    isinstance(data, dict)
                    and data.get("version") == 1
                    and isinstance(data.get("filed"), dict)
                ):
                    ledger = data
            except Exception as e:
                _logger.warning("Could not read ledger at %s: %s", ledger_path, e)

        items_to_append: list[dict[str, Any]] = []
        new_count = 0
        already_filed_count = 0
        unrouted_count = 0
        ledger_modified = False

        cached_file_candidates: dict[Path, dict[str, list[str]]] = {}

        for issue_id, wrapper in merged_issues.items():
            issue = wrapper["issue"]
            course_name, target_path, target_rel, is_unrouted = resolve_target(
                issue["course_id"], courses_root, feedback_root
            )
            if is_unrouted:
                unrouted_count += 1

            if issue_id in ledger["filed"]:
                already_filed_count += 1
                continue

            if target_path not in cached_file_candidates:
                candidate_entries: dict[str, list[str]] = {}
                if target_path.is_file():
                    try:
                        content = target_path.read_text(encoding="utf-8")
                    except Exception as e:
                        _logger.warning("Could not read target file %s: %s", target_path, e)
                        content = ""
                    lines = content.split("\n")
                    i = 0
                    while i < len(lines):
                        line = lines[i]
                        m = _HEADING_RE.match(line)
                        if m:
                            cand_id = m.group(3)
                            j = i + 1
                            while j < len(lines) and not lines[j].startswith("### "):
                                j += 1
                            cand_text = "\n".join(lines[i:j])
                            candidate_entries.setdefault(cand_id, []).append(cand_text)
                            i = j
                        else:
                            i += 1
                cached_file_candidates[target_path] = candidate_entries

            file_candidates = cached_file_candidates[target_path]
            if issue_id in file_candidates:
                canonical_entry = format_entry(wrapper).rstrip("\r\n")
                matched = any(
                    cand.rstrip("\r\n") == canonical_entry
                    for cand in file_candidates[issue_id]
                )
                if matched:
                    ledger["filed"][issue_id] = {
                        "course": course_name,
                        "target": target_rel,
                        "filed_at_ms": int(time.time() * 1000),
                    }
                    ledger_modified = True
                    already_filed_count += 1
                    continue
                else:
                    _logger.warning(
                        "Candidate entry for issue %s in %s did not match canonical format; re-filing",
                        issue_id,
                        target_path,
                    )

            items_to_append.append({
                "issue_id": issue_id,
                "course_name": course_name,
                "target_path": target_path,
                "target_rel": target_rel,
                "wrapper": wrapper,
            })

        if dry_run:
            for item in items_to_append:
                sys.stderr.write(f"Would file {item['issue_id']} -> {item['target_rel']}\n")
            new_count = len(items_to_append)
        else:
            items_by_target: dict[Path, list[dict[str, Any]]] = {}
            for item in items_to_append:
                items_by_target.setdefault(item["target_path"], []).append(item)

            for target_path, items in items_by_target.items():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                existing_content = ""
                if target_path.is_file():
                    existing_content = target_path.read_text(encoding="utf-8")

                with open(target_path, "a", encoding="utf-8") as f:
                    if not existing_content:
                        header = (
                            f"# {items[0]['course_name']} pending feedback\n\n"
                            "Intake for in-app question reports and study feedback. Append one entry per raised\n"
                            "item with its date, question id, and source. Nothing here edits the pack.\n\n"
                            "## Entries\n"
                        )
                        f.write(header)
                        f.flush()
                        os.fsync(f.fileno())
                        existing_content = header
                    elif "## Entries" not in [line.strip() for line in existing_content.splitlines()]:
                        f.write("\n## Entries\n")
                        f.flush()
                        os.fsync(f.fileno())
                        existing_content += "\n## Entries\n"

                    for item in items:
                        entry_text = format_entry(item["wrapper"])
                        if existing_content.endswith("\n\n"):
                            prefix = ""
                        elif existing_content.endswith("\n"):
                            prefix = "\n"
                        else:
                            prefix = "\n\n"

                        f.write(prefix + entry_text)
                        f.flush()
                        os.fsync(f.fileno())
                        existing_content += prefix + entry_text

                        ledger["filed"][item["issue_id"]] = {
                            "course": item["course_name"],
                            "target": item["target_rel"],
                            "filed_at_ms": int(time.time() * 1000),
                        }
                        ledger_modified = True
                        new_count += 1

            if ledger_modified:
                ledger_dir = ledger_path.parent
                ledger_dir.mkdir(parents=True, exist_ok=True)
                tf = tempfile.NamedTemporaryFile("w", dir=ledger_dir, delete=False, encoding="utf-8")
                try:
                    json.dump(ledger, tf, indent=2)
                    tf.write("\n")
                    tf.flush()
                    os.fsync(tf.fileno())
                    tf.close()
                    os.replace(tf.name, ledger_path)
                except Exception:
                    if os.path.exists(tf.name):
                        os.unlink(tf.name)
                    raise

        sys.stderr.write(
            f"sources read: {sources_read}, valid: {valid_count}, new: {new_count}, "
            f"already filed: {already_filed_count}, unrouted: {unrouted_count}, invalid: {invalid_count}\n"
        )

        if summary:
            effective_ledger = ledger
            if dry_run and items_to_append:
                effective_ledger = {"version": 1, "filed": dict(ledger.get("filed", {}))}
                for item in items_to_append:
                    effective_ledger["filed"][item["issue_id"]] = {
                        "course": item["course_name"],
                        "target": item["target_rel"],
                        "filed_at_ms": 0,
                    }
            summary_text = generate_summary(effective_ledger, feedback_root, merged_issues)
            sys.stdout.write(summary_text)

        return 0

    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ingest_issue_reports.

    Args:
        argv: Command-line argument vector (defaults to sys.argv[1:]).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Ingest in-app question issue reports into feedback intake markdown files."
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Path to issue inbox JSON file (repeatable). Default: standard Mac app and container locations.",
    )
    parser.add_argument(
        "--courses-root",
        default=None,
        help="Root directory for courses (default: question-packs relative to repo root).",
    )
    parser.add_argument(
        "--feedback-root",
        default=None,
        help="Root directory for feedback logs (default: .logs/feedback relative to repo root).",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="Path to ingested issues ledger JSON (default: <feedback-root>/.ingested-issues.json).",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory for log file (default: .logs relative to repo root).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be filed without writing files or ledger.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print grouped summary of ledger to stdout.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging in log file.",
    )

    args = parser.parse_args(argv)

    if args.courses_root:
        courses_root = Path(args.courses_root).resolve()
    else:
        courses_root = (REPO_ROOT / "question-packs").resolve()

    if args.feedback_root:
        feedback_root = Path(args.feedback_root).resolve()
    else:
        feedback_root = (REPO_ROOT / ".logs" / "feedback").resolve()

    if args.ledger:
        ledger_path = Path(args.ledger).resolve()
    else:
        ledger_path = (feedback_root / ".ingested-issues.json").resolve()

    if args.log_dir:
        log_dir = Path(args.log_dir).resolve()
    else:
        log_dir = (REPO_ROOT / ".logs").resolve()

    if args.sources:
        sources = [Path(os.path.expanduser(s)) for s in args.sources]
    else:
        sources = [
            Path(os.path.expanduser("~/Library/Application Support/Quizzler/issue-inbox-v1.json")),
            Path(
                os.path.expanduser(
                    "~/Library/Containers/com.zerodelta.quizzler/Data/Library/Application Support/Quizzler/issue-inbox-v1.json"
                )
            ),
        ]

    return ingest(
        sources=sources,
        courses_root=courses_root,
        feedback_root=feedback_root,
        ledger_path=ledger_path,
        log_dir=log_dir,
        dry_run=args.dry_run,
        summary=args.summary,
        debug=args.debug,
    )


if __name__ == "__main__":
    sys.exit(main())
