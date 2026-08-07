#!/usr/bin/env python3
"""Build question-packs/manifest.json from the question-packs/ folder layout.

Walks each subdirectory of question-packs/, reads optional _course.json for
display metadata, and lists every JSON pack in the folder. The output drives
the home-screen course grid in app/index.html, replacing the old hand-maintained
COURSES array.

Before writing the manifest, every installed pack (non-archive course folder,
non-template) must pass Layer-A lint and the install gate: a top-level
``coverage_blueprint`` and a fresh ``certification`` block
(``pack_cert.certification_fresh``). In strict mode (the default) any lint
critical or install-gate failure aborts the whole build with exit 1 so a
non-compliant pack never reaches the app. Use ``--no-strict`` /
``QUIZZLER_LINT_STRICT=0`` only for local preview — uncertified packs still
emit a warning naming each offending file.

Conventions:
  - One subfolder per course under question-packs/ (e.g., question-packs/my-course/).
  - Optional _course.json in the folder with: id, name, description.
  - Any other *.json file is treated as a question pack.
  - A course is advisory above 200 questions and cannot install above 240
    questions by default. This prevents a single exam course from silently
    expanding into a token-heavy 400–500 question bank.
  - Pack module title comes from pack["title"]; description from pack["notes"].
  - Modules sort naturally by filename (mod1.json, mod2.json, ..., mod10.json).

Usage:
  python3 scripts/build_manifest.py
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `import lint_packs` when running build_manifest.py standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_packs  # noqa: E402
import pack_cert  # noqa: E402

PACKS_DIR = Path(__file__).resolve().parent.parent / "question-packs"
MANIFEST = PACKS_DIR / "manifest.json"

# Full per-finding lint detail is written here at build time. Startup stays quiet
# (summary line + criticals only) and points authors at this log instead of
# dumping every warning. `--verbose` restores inline enumeration. The authoring-
# time gate (scripts/lint_hook.py) and scripts/lint_packs.py surface specifics.
LINT_LOG = Path("/tmp/quizzler-lint.log")

# Pack `notes` (used as the module subtitle on the home screen) gets truncated
# in the UI past this length. Warn during build so authors notice before ship.
MAX_NOTES_LENGTH = 120

# Course-level budget guardrail. Pack-level lint/certification does not control
# total learner workload, so this gate is intentionally independent of Layer A.
# A future course may declare a lower ``question_budget.target`` in
# ``_course.json`` for planning visibility, but cannot raise the hard ceiling.
COURSE_QUESTION_SOFT_MAX = 200
COURSE_QUESTION_HARD_MAX = 240

# Question order is randomized at runtime, so prompts that reference previous
# questions will confuse the user when the follow-up is drawn before the setup.
# Warn on common sequential-coupling phrases so authors rewrite them as
# self-contained scenarios. See AUTHORING.md "Quality Rules" #11.
SEQUENTIAL_COUPLING_PATTERNS = [
    # "Same X scenario" / "Same X-Y scenario" / "Same X Y Z scenario" — allow
    # hyphenated and multi-word qualifiers (e.g. "Same fraud-detection scenario").
    re.compile(r"\bsame\s+[\w\s-]{1,40}?\s*scenario\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+previous\s+question\b", re.IGNORECASE),
    re.compile(r"\bas\s+(discussed|mentioned)\s+(earlier|above|previously)\b", re.IGNORECASE),
    re.compile(r"\breferring\s+to\s+the\s+(prior|previous|earlier)\b", re.IGNORECASE),
    re.compile(r"\bcontinuing\s+from\s+(above|the\s+previous)\b", re.IGNORECASE),
]


def natural_key(name: str) -> list:
    """Sort 'mod10.json' after 'mod9.json'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def read_course_meta(course_dir: Path) -> dict:
    """Read _course.json if present; otherwise derive from the folder name."""
    meta_file = course_dir / "_course.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
            meta = {
                "id": data.get("id", course_dir.name),
                "name": data.get("name", course_dir.name.upper()),
                "description": data.get("description", ""),
                "sort_order": data.get("sort_order", 100),
                "_question_budget": data.get("question_budget", {}),
            }
            # The exam-area taxonomy is RUNTIME data, not authoring-only: the app
            # needs it to report per-area accuracy (and, later, to weight
            # selection toward weak areas), and questions reference it by id.
            # Unlike _question_budget it is therefore carried through to
            # manifest.json rather than popped before the write.
            syllabus = data.get("syllabus")
            if isinstance(syllabus, dict):
                meta["syllabus"] = syllabus
            return meta
        except json.JSONDecodeError as e:
            print(f"warn: {meta_file} has invalid JSON ({e}); using defaults",
                  file=sys.stderr)
    return {
        "id": course_dir.name,
        "name": course_dir.name.upper(),
        "description": "",
        "sort_order": 100,
        "_question_budget": {},
    }


def read_pack_meta(pack_file: Path) -> dict | None:
    """Extract the manifest entry for one question pack."""
    try:
        data = json.loads(pack_file.read_text())
    except json.JSONDecodeError as e:
        print(f"warn: skipping {pack_file}: invalid JSON ({e})", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(
            f"warn: skipping {pack_file}: pack root is not a JSON object "
            f"(got {type(data).__name__})",
            file=sys.stderr,
        )
        return None
    notes = data.get("notes", "")
    rel = pack_file.relative_to(PACKS_DIR.parent)
    if len(notes) > MAX_NOTES_LENGTH:
        print(
            f"warn: {rel} 'notes' is {len(notes)} chars (>{MAX_NOTES_LENGTH}); "
            f"will be truncated in the UI",
            file=sys.stderr,
        )
    questions = data.get("questions", [])
    if not isinstance(questions, list):
        questions = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or "")
        for pattern in SEQUENTIAL_COUPLING_PATTERNS:
            match = pattern.search(prompt)
            if match:
                print(
                    f"warn: {rel} {q.get('id', '?')} prompt contains "
                    f"sequential-coupling phrase '{match.group(0)}'; "
                    f"questions are randomized — rewrite as standalone",
                    file=sys.stderr,
                )
                break
    return {
        "file": pack_file.name,
        "title": data.get("title", pack_file.stem),
        "description": notes,
        # Use the normalized list so malformed null/non-list values cannot
        # crash the manifest build or distort the course-size guardrail.
        "questionCount": len(questions),
    }


def prune_failed_packs(courses: list[dict], failed: set[tuple[str, str]]) -> list[str]:
    """Drop every gate-failing pack from ``courses``; return what was excluded.

    "Refuse to install any pack that has not passed the gates" is a per-pack
    statement. Aborting the whole build instead is both too strict and too
    weak: too strict because a pack that PASSED cannot install while some
    unrelated pack is broken, and too weak because the only way out is
    ``--no-strict``, which reinstalls the broken pack alongside the good ones.
    That is exactly backwards during authoring, which is when the gate matters
    most.

    So: exclude the failures, install the survivors, and still exit non-zero.
    Courses left with no modules are dropped entirely. ``failed`` is keyed by
    ``(course folder name, pack filename)`` because a course's declared ``id``
    in ``_course.json`` can differ from its folder name.

    Mutates ``courses`` in place. Returns ``"folder/pack.json"`` strings for the
    excluded packs, in stable order.
    """
    excluded: list[str] = []
    for course in courses:
        dir_name = course.get("_dir_name")
        kept = []
        for module in course.get("modules", []):
            if (dir_name, module.get("file")) in failed:
                excluded.append(f"{dir_name}/{module.get('file')}")
            else:
                kept.append(module)
        course["modules"] = kept
    courses[:] = [c for c in courses if c["modules"]]
    return excluded


def build(strict: bool = True, verbose: bool = False, lint: bool = True,
          allow_course_size_preview: bool = False) -> int:
    """Build manifest.json.

    QA is meant to happen at AUTHORING time (the lint hook + scripts/lint_packs.py),
    but this build also enforces the install gate on every pack that would ship:
      • lint criticals — one line per affected pack (real defects you want to see),
      • install gate — missing ``coverage_blueprint`` or stale/missing certification
        (``pack_cert.certification_fresh``); one error line per pack in strict mode,
        or an always-on warning per pack under ``--no-strict``,
      • lint warnings — counted in the one-line summary, not enumerated,
      • full lint detail — always written to LINT_LOG.
    `verbose=True` (CLI `--verbose` / env `QUIZZLER_LINT_VERBOSE=1`) restores the
    full inline enumeration. `strict` is the DEFAULT: a pack with lint criticals
    or an install-gate failure is EXCLUDED from the manifest so it never reaches
    the app. Exclusion is per pack, not per build — a pack that passes still
    installs while a sibling is broken, which matters because otherwise the only
    way to run during authoring is `--no-strict`, and that reinstalls the broken
    pack too. Pass `strict=False` (CLI `--no-strict` / env
    `QUIZZLER_LINT_STRICT=0`) only to deliberately install past those failures.

    Exit codes: 0 = clean; 2 = partial install (survivors written, failures
    excluded); 1 = nothing installed (bad packs dir, course-size hard ceiling,
    or every pack excluded).
  Advisory lint warnings never block. `lint=False` skips lint and the install gate
  entirely (used by manifest-structure unit tests). The hard course-size ceiling
  remains active unless the caller explicitly sets
  ``allow_course_size_preview=True``; that escape hatch is reserved for
  local WIP/test preview servers.
    """
    if not PACKS_DIR.is_dir():
        print(f"error: {PACKS_DIR} does not exist", file=sys.stderr)
        return 1

    courses = []
    for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name):
        if not course_dir.is_dir():
            continue
        # Skip hidden folders (.foo) and archive folders (_foo, e.g. _archive).
        # Also skip zz-hooktest-* — ephemeral fixtures from tests.test_lint_hook.
        if course_dir.name.startswith((".", "_")):
            continue
        if course_dir.name.startswith("zz-hooktest-"):
            continue

        meta = read_course_meta(course_dir)
        modules = []
        pack_files = sorted(
            (p for p in course_dir.glob("*.json") if p.name != "_course.json"),
            key=lambda p: natural_key(p.name),
        )
        for pack_file in pack_files:
            entry = read_pack_meta(pack_file)
            if entry is not None:
                modules.append(entry)

        if not modules:
            print(f"warn: {course_dir.name} has no valid packs; skipping",
                  file=sys.stderr)
            continue

        meta["modules"] = modules
        # The lint/gate loop below keys failures by folder name, but a course's
        # declared id can differ from the folder it lives in. Carry the folder
        # through so pruning can map a failing pack back onto its course; popped
        # before the manifest is written, like _question_budget.
        meta["_dir_name"] = course_dir.name
        courses.append(meta)

    # Sort by explicit sort_order (lower = earlier), then by name.
    courses.sort(key=lambda c: (c.get("sort_order", 100), c["name"].lower()))
    # Drop sort_order from runtime output after the internal budget check below.
    for c in courses:
        c.pop("sort_order", None)

    # ── Course-level workload guardrail ─────────────────────────────────────
    # The pack gates below can all pass while an exam course still becomes an
    # unnecessarily large study bank. Keep this check separate from lint so a
    # caller cannot bypass it with ``lint=False`` or a lenient lint preview.
    course_size_failures = 0
    course_size_warnings = 0
    course_size_log: list[str] = []
    for course in courses:
        question_count = sum(int(module.get("questionCount", 0)) for module in course["modules"])
        budget = course.get("_question_budget")
        target = budget.get("target") if isinstance(budget, dict) else None
        if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
            target = None
        if question_count > COURSE_QUESTION_HARD_MAX:
            course_size_failures += 1
            course_size_log.append(
                f"course size: {course['id']}: {question_count} questions exceeds "
                f"the hard ceiling of {COURSE_QUESTION_HARD_MAX}"
            )
        elif question_count > COURSE_QUESTION_SOFT_MAX:
            course_size_warnings += 1
            course_size_log.append(
                f"course size: {course['id']}: {question_count} questions exceeds "
                f"the advisory planning threshold of {COURSE_QUESTION_SOFT_MAX}"
            )
        elif target is not None and question_count > target:
            course_size_warnings += 1
            course_size_log.append(
                f"course size: {course['id']}: {question_count} questions exceeds "
                f"the declared planning target of {target}"
            )
    # Budget metadata is authoring-only and must not leak into manifest.json.
    for c in courses:
        c.pop("_question_budget", None)
    for line in course_size_log:
        if line.startswith("course size:") and "hard ceiling" in line:
            print(("warn: " if allow_course_size_preview else "error: ") + line,
                  file=sys.stderr)
        else:
            print("warn: " + line, file=sys.stderr)
    if course_size_failures and not allow_course_size_preview:
        print(
            f"error: {course_size_failures} course size violation(s); "
            "manifest not written (the hard workload ceiling requires an "
            "explicit preview override)",
            file=sys.stderr,
        )
        return 1

    # ── Layer A quality-gate: lint every pack before writing the manifest ──────
    lint_criticals = 0
    lint_warnings = 0
    install_gate_failures = 0
    # (course folder, pack filename) for every pack that failed Layer A or the
    # install gate — the exclusion list strict mode prunes with.
    failed_packs: set[tuple[str, str]] = set()
    gate_failure_summary = ""
    excluded_packs: list[str] = []
    findings = bool(course_size_log)
    log_lines: list[str] = list(course_size_log)
    if lint:
        # Discover packs by walking files on disk — the same glob `build` uses
        # above — so EVERY pack is linted regardless of what _course.json's `id`
        # says. Keying off course id silently skipped a whole course's packs
        # whenever the declared id differed from the folder name (a strict-gate
        # bypass).
        all_pack_paths = [
            pack_path
            for course_dir in sorted(PACKS_DIR.iterdir(), key=lambda p: p.name)
            if course_dir.is_dir()
            and not course_dir.name.startswith((".", "_"))
            and not course_dir.name.startswith("zz-hooktest-")
            for pack_path in sorted(
                p for p in course_dir.glob("*.json") if p.name != "_course.json"
            )
        ]
        for pack_path in all_pack_paths:
            result = lint_packs.lint_pack(pack_path)
            crits = [v for v in result["violations"] if v.get("severity") == "critical"]
            warns = [v for v in result["violations"] if v.get("severity") == "warning"]
            lint_criticals += len(crits)
            lint_warnings += len(warns)
            rel = pack_path.relative_to(PACKS_DIR.parent)
            if crits:
                failed_packs.add((pack_path.parent.name, pack_path.name))
            if crits or warns:
                log_lines.append(f"lint: {rel}: {len(crits)} critical, {len(warns)} warning")
                for v in crits + warns:
                    qid = v.get("qid") or "(pack)"
                    log_lines.append(f"  [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}")
                if verbose:
                    print(f"lint: {rel}: {len(crits)} critical, {len(warns)} warning", file=sys.stderr)
                    for v in crits + warns:
                        qid = v.get("qid") or "(pack)"
                        print(f"  [{v['severity']:8s}] {v['rule']} @ {qid}: {v['detail']}", file=sys.stderr)
                elif crits:
                    # Criticals are real defects — one line per affected pack even in
                    # quiet mode; warnings live in the log only. The log pointer rides
                    # on the summary line (written once, after we know the log exists).
                    print(f"lint: {rel}: {len(crits)} critical, {len(warns)} warning",
                          file=sys.stderr)

            # Install gate (INV-7): every installed pack needs blueprint + fresh cert.
            try:
                data = json.loads(pack_path.read_text())
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                gate_reasons: list[str] = []
                if not data.get("coverage_blueprint"):
                    gate_reasons.append("missing coverage_blueprint")
                if pack_cert.has_pack_wide_l23_waiver(data):
                    gate_reasons.append("pack-wide L23 waiver (PM-5)")
                if not pack_cert.certification_fresh(data):
                    gate_reasons.append("certification missing or stale")
                if gate_reasons:
                    install_gate_failures += 1
                    failed_packs.add((pack_path.parent.name, pack_path.name))
                    gate_detail = "; ".join(gate_reasons)
                    gate_line = f"install gate: {rel}: {gate_detail}"
                    log_lines.append(gate_line)
                    if strict:
                        print(f"error: {gate_line}", file=sys.stderr)
                    else:
                        print(f"warn: {gate_line}", file=sys.stderr)

        findings = bool(log_lines)
        if findings:
            try:
                LINT_LOG.write_text("\n".join(log_lines) + "\n")
            except OSError:
                findings = False  # don't point at a log we couldn't write
        if strict and (lint_criticals or install_gate_failures):
            parts: list[str] = []
            if lint_criticals:
                parts.append(f"{lint_criticals} critical lint violation(s)")
            if install_gate_failures:
                parts.append(f"{install_gate_failures} install gate failure(s)")
            gate_failure_summary = "; ".join(parts)
            excluded_packs = prune_failed_packs(courses, failed_packs)
            print(
                f"error: {gate_failure_summary} across packs/courses; "
                f"{len(excluded_packs)} pack(s) EXCLUDED from the manifest "
                f"(strict mode). Fix the issues above"
                + (f" (see {LINT_LOG})" if findings else "")
                + ", or re-run with --no-strict to install them anyway.",
                file=sys.stderr,
            )
            for name in excluded_packs:
                print(f"error:   not installed: {name}", file=sys.stderr)
    if not lint and findings:
        try:
            LINT_LOG.write_text("\n".join(log_lines) + "\n")
        except OSError:
            findings = False
    # ── Write manifest ─────────────────────────────────────────────────────────
    # `strict_gate` records WHICH gate produced this manifest. Without it the
    # artifact is ambiguous: a manifest listing an uncertified pack could mean
    # either "the gate is broken" or "someone deliberately built with
    # --no-strict". Recording the mode makes the difference checkable — notably
    # by tests, since the Playwright webServer builds --no-strict on purpose and
    # would otherwise look identical to a gate failure.
    for c in courses:
        c.pop("_dir_name", None)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strict_gate": bool(strict and lint),
        "courses": courses,
    }
    if gate_failure_summary:
        # Written even when every pack was excluded: an empty manifest is a
        # valid "nothing installed" state the app can explain, and it revokes
        # whatever the previous build left on disk. Declining to overwrite would
        # keep serving the packs that just failed.
        out["revoked"] = {
            "reason": gate_failure_summary,
            "revoked_packs": excluded_packs,
            "detail": "Strict quality gate failed for these packs; they were "
                      "excluded from the manifest. Packs that passed are still "
                      "installed. Re-run scripts/build_manifest.py after fixing "
                      "the violations to reinstall.",
        }
    tmp = MANIFEST.with_name(MANIFEST.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    os.replace(tmp, MANIFEST)
    total_packs = sum(len(c["modules"]) for c in courses)
    summary = f"wrote {MANIFEST.relative_to(PACKS_DIR.parent)}: {len(courses)} courses, {total_packs} packs total"
    if lint_criticals or lint_warnings or course_size_warnings:
        summary += f" (lint: {lint_criticals} critical, {lint_warnings} warning"
        if course_size_warnings:
            summary += f"; {course_size_warnings} course-size advisory"
        if findings and not verbose:
            summary += f"; see {LINT_LOG}"
        summary += ")"
    if gate_failure_summary:
        # Survivors installed, failures excluded — the build still didn't fully
        # succeed, so the exit code stays non-zero. Two distinct codes because
        # callers need to tell the cases apart: start.sh can serve a partial
        # install (2) but has nothing to serve when everything was excluded (1).
        print(summary + f"; {len(excluded_packs)} pack(s) excluded by the strict gate",
              file=sys.stderr)
        return 2 if courses else 1
    print(summary)
    return 0


def _strict_default(env: dict | None = None) -> bool:
    """Return the default for strict mode, read from the environment.

    Strict is ON unless ``QUIZZLER_LINT_STRICT`` is set to a common falsey
    spelling — ``0``, ``false``, ``no``, ``off``, or empty/whitespace
    (case-insensitive). Anything else, including unset, is ON. This avoids the
    footgun where only the literal ``"0"`` disabled strict and
    ``QUIZZLER_LINT_STRICT=false`` silently stayed strict. Pass ``env`` to test
    without touching the real environment; ``None`` reads ``os.environ``.
    """
    if env is None:
        env = os.environ
    value = env.get("QUIZZLER_LINT_STRICT", "1")
    return value.strip().lower() not in ("0", "false", "no", "off", "")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        default=_strict_default(),
        help="Write the manifest even if Layer-A lint or the install gate finds "
        "failures (default: strict — lint criticals or missing/stale certification "
        "abort the build with exit 1 so a non-compliant pack never reaches the "
        "app; set QUIZZLER_LINT_STRICT to any of 0, false, no, off, or empty to "
        "default off). Uncertified packs still emit a warning per pack.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=os.environ.get("QUIZZLER_LINT_VERBOSE") == "1",
        help="Enumerate every lint finding inline (default: quiet — criticals "
        f"only, full detail in {LINT_LOG}).",
    )
    parser.add_argument(
        "--allow-course-size-preview",
        action="store_true",
        help="Explicitly allow an oversized course for the local WIP preview "
        "or test server; never use this for installation or shipping.",
    )
    args = parser.parse_args()
    sys.exit(build(
        strict=args.strict,
        verbose=args.verbose,
        allow_course_size_preview=args.allow_course_size_preview,
    ))
