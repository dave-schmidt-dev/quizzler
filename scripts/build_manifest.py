#!/usr/bin/env python3
"""Compute the question-packs install manifest from the folder layout.

Walks each subdirectory of question-packs/, reads optional _course.json for
display metadata, and lists every JSON pack in the folder for authoring and
quality checks. The manifest is returned to the caller, not written to disk;
native app bundling is handled separately by ``scripts/build_pack_assets.py``.

Before assembling the manifest, every installed pack (non-archive course folder,
non-template) must pass Layer-A lint and the install gate: a top-level
``coverage_blueprint`` and a fresh ``certification`` block
(``pack_cert.certification_fresh``). In strict mode (the default) any lint
critical or install-gate failure aborts the whole build with exit 1 so a
non-compliant pack never reaches the app. Use ``--no-strict`` /
``QUIZZLER_LINT_STRICT=0`` only for local preview — uncertified packs still
emit a warning naming each offending file.

Conventions:
  - One subfolder per course under question-packs/ (e.g., question-packs/my-course/).
  - Optional _course.json in the folder with: id, name, description. If present,
    it must be valid JSON with an object root or that course is refused.
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

# Allow `import install_gate` when running build_manifest.py standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_gate  # noqa: E402
import pack_discovery  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = PROJECT_ROOT / "question-packs"

# Full per-finding lint detail is written here at build time. Startup stays quiet
# (summary line + criticals only) and points authors at this log instead of
# dumping every warning. `--verbose` restores inline enumeration. The authoring-
# commit-time gate and scripts/lint_packs.py surface specifics. The path is
# derived from the repository root (not the process cwd) and lands in the
# gitignored .logs/ directory, which is created on demand.
LINT_LOG = PROJECT_ROOT / ".logs" / "quizzler-lint.log"

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


def read_course_meta(course_dir: Path) -> dict | None:
    """Read course metadata, returning ``None`` for malformed present metadata."""
    meta_file = course_dir / "_course.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
            if not isinstance(data, dict):
                print(
                    f"error: {meta_file} must contain a JSON object (got "
                    f"{type(data).__name__}); refusing to install this course",
                    file=sys.stderr,
                )
                return None
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
            # Unlike _question_budget it is therefore carried through to the
            # manifest rather than popped before it is returned.
            syllabus = data.get("syllabus")
            if isinstance(syllabus, dict):
                meta["syllabus"] = syllabus
            return meta
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"error: {meta_file} is not parseable ({e}); refusing to install "
                "this course",
                file=sys.stderr,
            )
            return None
    return {
        "id": course_dir.name,
        "name": course_dir.name.upper(),
        "description": "",
        "sort_order": 100,
        "_question_budget": {},
    }


def _read_pack(pack_file: Path) -> tuple[dict | None, dict | None]:
    """Parse one pack once and return its manifest entry plus source object."""
    try:
        data = json.loads(pack_file.read_text())
    except json.JSONDecodeError as e:
        print(f"warn: skipping {pack_file}: invalid JSON ({e})", file=sys.stderr)
        return None, None
    if not isinstance(data, dict):
        print(
            f"warn: skipping {pack_file}: pack root is not a JSON object "
            f"(got {type(data).__name__})",
            file=sys.stderr,
        )
        return None, None
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
    return ({
        "file": pack_file.name,
        "title": data.get("title", pack_file.stem),
        "description": notes,
        # Use the normalized list so malformed null/non-list values cannot
        # crash the manifest build or distort the course-size guardrail.
        "questionCount": len(questions),
    }, data)


def read_pack_meta(pack_file: Path) -> dict | None:
    """Extract the manifest entry for one question pack."""
    return _read_pack(pack_file)[0]


def _module_pack_data(course: dict, module: dict) -> dict | None:
    """Return carried source data, with a compatibility fallback for callers."""
    carried = module.get("_pack_data")
    if isinstance(carried, dict):
        return carried
    dir_name = course.get("_dir_name")
    filename = module.get("file")
    if not isinstance(dir_name, str) or not isinstance(filename, str):
        return None
    try:
        data = json.loads((PACKS_DIR / dir_name / filename).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


def course_area_distribution_findings(course: dict) -> list[tuple[str, str]]:
    """Return critical area-distribution findings for one surviving course.

    Build callers carry parsed pack objects in private module metadata so this
    post-prune aggregate describes what will be installed without disk re-read.
    Direct helper callers without that metadata retain the legacy read fallback.
    The aggregation itself lives in ``install_gate`` (extracted M0d) so the
    manifest builder and the native bundler evaluate the same check.
    """
    syllabus = course.get("syllabus")
    areas = syllabus.get("areas") if isinstance(syllabus, dict) else None
    if not isinstance(areas, list) or not areas:
        return []

    dir_name = course.get("_dir_name")
    if not isinstance(dir_name, str):
        return []
    pack_datas = [
        _module_pack_data(course, module)
        for module in course.get("modules", [])
        if isinstance(module, dict)
    ]
    return install_gate.area_distribution_findings(
        course.get("id", dir_name), syllabus, pack_datas)


def course_blueprint_distribution_findings(course: dict) -> list[tuple[str, str]]:
    """Return critical blueprint-minimum-distribution findings for one surviving course.

    Mirrors ``course_area_distribution_findings`` but aggregates each pack's
    declared ``coverage_blueprint`` ``min`` values by area (L27 finding 14,
    L27-BLUEPRINT-DISTRIBUTION) instead of actual question counts. Every pack
    is linted with ``include_distribution=False`` above, so without this
    course-level pass nothing on the install path evaluates finding 14 at all.
    The aggregation itself lives in ``install_gate`` (extracted M0d) so the
    manifest builder and the native bundler evaluate the same check.
    """
    syllabus = course.get("syllabus")
    areas = syllabus.get("areas") if isinstance(syllabus, dict) else None
    if not isinstance(areas, list) or not areas:
        return []

    dir_name = course.get("_dir_name")
    if not isinstance(dir_name, str):
        return []
    pack_datas = [
        _module_pack_data(course, module)
        for module in course.get("modules", [])
        if isinstance(module, dict)
    ]
    return install_gate.blueprint_distribution_findings(
        course.get("id", dir_name), syllabus, pack_datas)


def build(strict: bool = True, verbose: bool = False, lint: bool = True,
          allow_course_size_preview: bool = False) -> tuple[int, dict]:
    """Compute the install manifest and return it with the build's exit code.

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

    Returns ``(exit_code, manifest)`` — the manifest data is handed to the
    caller, never written to disk; a build that aborts before assembling one
    (bad packs dir, course-size hard ceiling) returns ``{}`` for it. Exit
    codes: 0 = clean; 2 = partial install (survivors installed, failures
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
        return 1, {}

    courses = []
    malformed_course_dirs: list[str] = []
    for course_dir in pack_discovery.iter_courses(PACKS_DIR):
        meta = read_course_meta(course_dir)
        if meta is None:
            malformed_course_dirs.append(course_dir.name)
            continue
        modules = []
        pack_files = sorted(
            pack_discovery.iter_course_packs(course_dir),
            key=lambda p: natural_key(p.name),
        )
        for pack_file in pack_files:
            entry, pack_data = _read_pack(pack_file)
            if entry is not None:
                entry["_pack_data"] = pack_data
                modules.append(entry)

        if not modules:
            print(f"warn: {course_dir.name} has no valid packs; skipping",
                  file=sys.stderr)
            continue

        meta["modules"] = modules
        # The lint/gate loop below keys failures by folder name, but a course's
        # declared id can differ from the folder it lives in. Carry the folder
        # through so pruning can map a failing pack back onto its course; popped
        # before the manifest is returned, like _question_budget.
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
    # Budget metadata is authoring-only and must not leak into the manifest.
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
        return 1, {}

    if malformed_course_dirs:
        malformed_summary = (
            f"{len(malformed_course_dirs)} malformed course metadata file(s)"
        )
        gate_failure_summary = malformed_summary
        print(
            "error: refusing to install course metadata for: "
            + ", ".join(malformed_course_dirs),
            file=sys.stderr,
        )

    # ── Layer A quality-gate: lint every pack before writing the manifest ──────
    lint_criticals = 0
    lint_warnings = 0
    install_gate_failures = 0
    distribution_failures = 0
    gate_failure_summary = (
        f"{len(malformed_course_dirs)} malformed course metadata file(s)"
        if malformed_course_dirs else ""
    )
    excluded_packs: list[str] = []
    findings = bool(course_size_log)
    log_lines: list[str] = list(course_size_log)
    if lint:
        # The pack gate and the course distribution checks live in
        # ``install_gate.evaluate`` (extracted M0d) so the manifest builder and
        # the native bundler enforce ONE gate over the same parse. Parsed packs
        # are handed through so the gate lints the parse ``build`` already
        # read — one read per pack. Discovery walks files on disk there, so
        # EVERY pack is gated regardless of what _course.json's `id` says;
        # keying off course id silently skipped a whole course's packs
        # whenever the declared id differed from the folder name (a strict-gate
        # bypass).
        parsed_packs = {
            (course.get("_dir_name"), module.get("file")): module.get("_pack_data")
            for course in courses
            for module in course.get("modules", [])
            if isinstance(module, dict)
        }
        gate = install_gate.evaluate(
            PACKS_DIR, strict=strict, report=lambda _message: None,
            parsed=parsed_packs)
        lint_criticals = gate.lint_criticals
        lint_warnings = gate.lint_warnings
        install_gate_failures = gate.install_gate_failures
        for pack_report in gate.pack_reports:
            crits = pack_report["criticals"]
            warns = pack_report["warnings"]
            rel = pack_report["rel"]
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

            if pack_report["gate_reasons"]:
                gate_detail = "; ".join(pack_report["gate_reasons"])
                gate_line = f"install gate: {rel}: {gate_detail}"
                log_lines.append(gate_line)
                if strict:
                    print(f"error: {gate_line}", file=sys.stderr)
                else:
                    print(f"warn: {gate_line}", file=sys.stderr)

        if strict:
            # Always prune first, including when no pack failed, so the
            # distribution check below is defined over installed packs.
            excluded_packs = prune_failed_packs(courses, set(gate.rejections))
        if strict and (lint_criticals or install_gate_failures):
            parts: list[str] = []
            if lint_criticals:
                parts.append(f"{lint_criticals} critical lint violation(s)")
            if install_gate_failures:
                parts.append(f"{install_gate_failures} install gate failure(s)")
            gate_failure_summary = "; ".join(parts)

        if strict:
            distribution_failures = gate.distribution_failures
            for exclusion in gate.excluded:
                for _, detail in exclusion["findings"]:
                    log_lines.append(f"distribution: {detail}")
                    print(f"error: distribution: {detail}", file=sys.stderr)
            if gate.excluded:
                for exclusion in gate.excluded:
                    for course in courses:
                        if course.get("_dir_name") != exclusion["course"]:
                            continue
                        dir_name = course.get("_dir_name")
                        for module in course.get("modules", []):
                            if isinstance(module, dict):
                                filename = module.get("file")
                                if isinstance(filename, str):
                                    excluded_packs.append(f"{dir_name}/{filename}")
                courses[:] = [
                    course for course in courses
                    if course.get("_dir_name")
                    not in {exclusion["course"] for exclusion in gate.excluded}
                ]
                area_exclusions = sum(
                    1 for exclusion in gate.excluded if exclusion["kind"] == "area")
                blueprint_exclusions = sum(
                    1 for exclusion in gate.excluded if exclusion["kind"] == "blueprint")
                summaries = []
                if area_exclusions:
                    summaries.append(
                        f"{area_exclusions} course area distribution failure(s)")
                if blueprint_exclusions:
                    summaries.append(
                        f"{blueprint_exclusions} course blueprint distribution "
                        "failure(s)")
                gate_failure_summary = "; ".join(
                    part for part in (gate_failure_summary, *summaries) if part
                )

        findings = bool(log_lines)
        if findings:
            try:
                LINT_LOG.parent.mkdir(parents=True, exist_ok=True)
                LINT_LOG.write_text("\n".join(log_lines) + "\n")
            except OSError:
                findings = False

        if strict and (lint_criticals or install_gate_failures):
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
        elif strict and distribution_failures:
            print(
                f"error: {gate_failure_summary} across courses; "
                f"{len(excluded_packs)} pack(s) EXCLUDED from the manifest "
                "(strict mode). Fix the distribution above",
                file=sys.stderr,
            )
            for name in excluded_packs:
                print(f"error:   not installed: {name}", file=sys.stderr)
    if not lint and findings:
        try:
            LINT_LOG.parent.mkdir(parents=True, exist_ok=True)
            LINT_LOG.write_text("\n".join(log_lines) + "\n")
        except OSError:
            findings = False
    # ── Assemble manifest ──────────────────────────────────────────────────────
    # `strict_gate` records WHICH gate produced this manifest. Without it the
    # artifact is ambiguous: a manifest listing an uncertified pack could mean
    # either "the gate is broken" or "someone deliberately built with
    # --no-strict". Recording the mode makes the difference checkable — notably
    # by tests; otherwise a local preview artifact could look identical to a
    # gate failure.
    for c in courses:
        c.pop("_dir_name", None)
        for module in c.get("modules", []):
            if isinstance(module, dict):
                module.pop("_pack_data", None)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strict_gate": bool(strict and lint),
        "courses": courses,
    }
    if gate_failure_summary or malformed_course_dirs:
        # Returned even when every pack was excluded: an empty manifest is a
        # valid "nothing installed" state the app can explain, rather than an
        # absent one that hides the failure the gate just recorded.
        out["revoked"] = {
            "reason": gate_failure_summary,
            "revoked_packs": excluded_packs,
            "detail": "Strict quality gate failed for these packs; they were "
                      "excluded from the manifest. Packs that passed are still "
                      "installed. Re-run scripts/build_manifest.py after fixing "
                      "the violations to reinstall.",
        }
    total_packs = sum(len(c["modules"]) for c in courses)
    summary = f"manifest: {len(courses)} courses, {total_packs} packs total"
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
        # callers need to tell a partial install (2) from one where every pack
        # was excluded (1).
        if gate_failure_summary:
            print(
                summary + f"; {len(excluded_packs)} pack(s) excluded by the strict gate",
                file=sys.stderr,
            )
        else:
            print(
                summary + "; malformed course metadata refused",
                file=sys.stderr,
            )
        return (2 if courses else 1), out
    print(summary)
    return 0, out


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
        help="Compute the manifest even if Layer-A lint or the install gate finds "
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
    exit_code, _manifest = build(
        strict=args.strict,
        verbose=args.verbose,
        allow_course_size_preview=args.allow_course_size_preview,
    )
    sys.exit(exit_code)
