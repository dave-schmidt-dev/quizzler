#!/usr/bin/env python3
"""The install gate every question pack must pass before it ships (INV-7).

Extracted from ``build_manifest.build`` (certification hardening M0d) so the
manifest builder and the native bundler enforce ONE gate instead of two
approximations of one. The manifest path was the only caller that ran the full
quality bar -- lint criticals (including L23 coverage and L27 exam-area
alignment), the pack gate (``coverage_blueprint`` presence, the PM-5 pack-wide
L23 waiver, and ``pack_cert.certification_fresh``), and the course-level area
and blueprint distribution checks -- while ``build_pack_assets.collect_packs``
checked only the native contract (L29) and certification freshness. A pack
could therefore bundle while failing lint or the coverage gate, and the
bundler re-read each pack after deciding, so the bytes it copied were never
provably the bytes it gated (finding 4).

``evaluate`` walks the packs tree once, parses each pack once, and returns a
``GateResult``. ``admitted`` maps ``(course folder, pack file)`` to the exact
``raw_bytes`` and parsed ``data`` that passed every check; callers bundle
those bytes rather than re-reading the source, so what ships is what was
gated.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_packs  # noqa: E402
import pack_cert  # noqa: E402
import pack_discovery  # noqa: E402


def area_distribution_findings(
    course_id: object,
    syllabus: dict | None,
    pack_datas: list[dict | None],
) -> list[tuple[str, str]]:
    """Return critical L27-DISTRIBUTION findings for one course's packs.

    Aggregates the ``exam_area`` counts of the packs that will install and
    checks each weighted syllabus area against its published-weight range.
    A module pack may cover only part of a syllabus, which is why the per-pack
    lint pass skips this check; this aggregate over the surviving packs is the
    installation invariant.

    Args:
        course_id: The course's display id (``_course.json`` ``id``, or the
            folder name when absent) used in finding messages.
        syllabus: The course's ``syllabus`` block, or ``None`` when the course
            declares none.
        pack_datas: The parsed pack objects whose questions will install;
            ``None`` entries are skipped.

    Returns:
        A list of ``(area_id, detail)`` tuples, empty when the syllabus
        carries no usable weighted areas or the course is below the
        distribution floor.
    """
    areas = syllabus.get("areas") if isinstance(syllabus, dict) else None
    if not isinstance(areas, list) or not areas:
        return []

    weighted_areas: list[tuple[str, int | float]] = []
    for area in areas:
        if not isinstance(area, dict):
            return []
        area_id = str(area.get("id") or "").strip()
        weight = area.get("weight")
        if (
            not area_id
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
        ):
            return []
        weighted_areas.append((area_id, weight))

    area_counts = {area_id: 0 for area_id, _ in weighted_areas}
    question_count = 0
    for data in pack_datas:
        questions = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(questions, list):
            continue
        for question in questions:
            if not isinstance(question, dict):
                continue
            question_count += 1
            area_id = question.get("exam_area")
            if isinstance(area_id, str) and area_id.strip() in area_counts:
                area_counts[area_id.strip()] += 1

    findings: list[tuple[str, str]] = []
    for area_id, weight in weighted_areas:
        count_range = lint_packs.area_weight_count_range(weight, question_count)
        if count_range is None:
            return []
        expected, minimum, maximum = count_range
        actual = area_counts[area_id]
        if actual < minimum or actual > maximum:
            share = actual / question_count * 100 if question_count else 0
            findings.append((
                area_id,
                f"course {course_id!r} area {area_id!r} has "
                f"{actual}/{question_count} questions ({share:.1f}%), expected "
                f"about {expected} within inclusive range {minimum}-{maximum} "
                f"at published weight {weight:g}% (critical L27-DISTRIBUTION)",
            ))
    return findings


def blueprint_distribution_findings(
    course_id: object,
    syllabus: dict | None,
    pack_datas: list[dict | None],
) -> list[tuple[str, str]]:
    """Return critical L27-BLUEPRINT-DISTRIBUTION findings for one course.

    Mirrors :func:`area_distribution_findings` but aggregates each pack's
    declared ``coverage_blueprint`` ``min`` values by area (L27 finding 14)
    instead of actual question counts. Every pack is linted with
    ``include_distribution=False``, so without this course-level pass nothing
    on the install path evaluates finding 14 at all.

    Args:
        course_id: The course's display id used in finding messages.
        syllabus: The course's ``syllabus`` block, or ``None``.
        pack_datas: The parsed pack objects that will install; ``None``
            entries are skipped.

    Returns:
        A list of ``(area_id, detail)`` tuples, empty when the syllabus
        carries no usable weighted areas or the aggregate is below the
        distribution floor.
    """
    areas = syllabus.get("areas") if isinstance(syllabus, dict) else None
    if not isinstance(areas, list) or not areas:
        return []

    weighted_areas: list[tuple[str, int | float]] = []
    for area in areas:
        if not isinstance(area, dict):
            return []
        area_id = str(area.get("id") or "").strip()
        weight = area.get("weight")
        if (
            not area_id
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
        ):
            return []
        weighted_areas.append((area_id, weight))

    area_min_totals: dict[str, int] = {}
    for data in pack_datas:
        if not isinstance(data, dict):
            continue
        for _, area, minimum in lint_packs._parse_blueprint(data.get("coverage_blueprint")):
            if area is not None:
                norm = lint_packs._norm_topic(area)
                area_min_totals[norm] = area_min_totals.get(norm, 0) + minimum
    total_blueprint_min = sum(area_min_totals.values())

    findings: list[tuple[str, str]] = []
    for area_id, weight in weighted_areas:
        count_range = lint_packs.area_weight_count_range(weight, total_blueprint_min)
        if count_range is None:
            return []
        expected, minimum, maximum = count_range
        actual = area_min_totals.get(lint_packs._norm_topic(area_id), 0)
        if actual < minimum or actual > maximum:
            share = actual / total_blueprint_min * 100 if total_blueprint_min else 0
            findings.append((
                area_id,
                f"course {course_id!r} area {area_id!r} declares "
                f"{actual}/{total_blueprint_min} coverage_blueprint minimum unit(s) "
                f"({share:.1f}%), expected about {expected} within inclusive range "
                f"{minimum}-{maximum} at published weight {weight:g}% "
                "(critical L27-BLUEPRINT-DISTRIBUTION)",
            ))
    return findings


def _course_identity(course_dir: Path) -> tuple[object, dict | None] | None:
    """Return ``(course_id, syllabus)`` for a course dir, or ``None``.

    ``None`` means the course's ``_course.json`` is present but malformed;
    such a course is refused by the manifest builder for metadata reasons and
    carries no syllabus to distribution-check here. An absent ``_course.json``
    yields ``(folder name, None)``.
    """
    meta_file = course_dir / "_course.json"
    if not meta_file.exists():
        return course_dir.name, None
    try:
        data = json.loads(meta_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    syllabus = data.get("syllabus")
    return data.get("id", course_dir.name), syllabus if isinstance(syllabus, dict) else None


class GateResult:
    """The outcome of running the install gate over one packs tree.

    Attributes:
        admitted: Maps ``(course folder, pack file)`` to ``{"raw_bytes",
            "data"}`` for every pack that passed the full gate. ``raw_bytes``
            are the exact source bytes read at gate time; bundling them instead
            of re-reading the source is what makes the shipped bytes the
            gated bytes.
        rejections: Maps ``(course folder, pack file)`` to a record with
            ``reasons`` (human-readable failure causes), ``parse_error`` (set
            when the file is not readable JSON) and ``data`` (the parsed pack,
            when available).
        excluded: Course-level distribution failures, in check order. Each
            record carries ``kind`` (``"area"`` or ``"blueprint"``), the course
            folder, its display id, the ``packs`` dropped with it, and the
            ``findings`` that excluded it.
        pack_reports: Per-pack lint and gate detail for every discovered pack,
            in discovery order, so callers can reproduce their own diagnostics.
        lint_criticals: Total critical lint violations across all packs.
        lint_warnings: Total lint warnings across all packs.
        install_gate_failures: Number of packs that failed the pack gate.
        distribution_failures: Total course distribution findings.
    """

    def __init__(self) -> None:
        self.admitted: dict[tuple[str, str], dict] = {}
        self.rejections: dict[tuple[str, str], dict] = {}
        self.excluded: list[dict] = []
        self.pack_reports: list[dict] = []
        self.lint_criticals = 0
        self.lint_warnings = 0
        self.install_gate_failures = 0
        self.distribution_failures = 0


def _evaluate_course_distribution(packs_root: Path, result: GateResult) -> None:
    """Exclude courses whose admitted packs fail a distribution check.

    The area check runs first over every course with admitted packs; the
    blueprint check then runs over the courses it left behind, so a course
    excluded by one aggregate is not re-judged by the other. Excluded
    courses' packs are removed from ``admitted``.
    """
    admitted_by_course: dict[str, list[tuple[str, dict]]] = {}
    for (course, filename), entry in result.admitted.items():
        admitted_by_course.setdefault(course, []).append((filename, entry["data"]))

    identities: dict[str, tuple[object, dict | None]] = {}
    for course_dir in pack_discovery.iter_courses(packs_root):
        identity = _course_identity(course_dir)
        if identity is not None:
            identities[course_dir.name] = identity

    surviving = list(admitted_by_course)
    for kind, finder in (
        ("area", area_distribution_findings),
        ("blueprint", blueprint_distribution_findings),
    ):
        still_surviving: list[str] = []
        for course in surviving:
            if course not in identities:
                still_surviving.append(course)
                continue
            course_id, syllabus = identities[course]
            pack_datas = [data for _, data in admitted_by_course[course]]
            findings = finder(course_id, syllabus, pack_datas)
            if not findings:
                still_surviving.append(course)
                continue
            result.distribution_failures += len(findings)
            result.excluded.append({
                "kind": kind,
                "course": course,
                "course_id": course_id,
                "packs": [(course, filename) for filename, _ in admitted_by_course[course]],
                "findings": findings,
            })
        surviving = still_surviving

    for exclusion in result.excluded:
        for key in exclusion["packs"]:
            result.admitted.pop(key, None)


def evaluate(
    packs_root: Path,
    *,
    strict: bool = True,
    report: Callable[[str], None],
    parsed: dict | None = None,
) -> GateResult:
    """Run the full install gate over every installable pack under packs_root.

    Packs are discovered by walking files on disk — not by matching
    ``_course.json``'s declared ``id`` — so every installable pack is gated
    regardless of what the course metadata says. Each pack is linted with
    ``include_distribution=False`` (the course-level aggregates are evaluated
    separately, below), then checked against the pack gate: a top-level
    ``coverage_blueprint``, no pack-wide L23 waiver (PM-5), and a fresh
    ``certification`` block. In strict mode the area and blueprint
    distribution checks then run over the packs that survived, and a course
    that fails either is excluded with all its packs.

    Args:
        packs_root: The question-packs root to walk.
        strict: When True, courses failing a distribution check are excluded
            and their packs are removed from ``admitted``. The per-pack gate
            always runs.
        report: Progress callback invoked once per discovered pack.
        parsed: Optional mapping of ``(course folder, pack file)`` to an
            already-parsed pack object, so a caller that has read the file
            (e.g. ``build_manifest``) does not pay a second read per pack.

    Returns:
        The GateResult summarizing admission, rejection, and exclusion.
    """
    result = GateResult()
    carried = parsed or {}
    for pack_path in pack_discovery.iter_installable_packs(packs_root):
        course = pack_path.parent.name
        filename = pack_path.name
        report(f"inspecting {course}/{filename}")

        data = carried.get((course, filename))
        parse_error = None
        if not isinstance(data, dict):
            try:
                data = json.loads(pack_path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                parse_error = str(error)
                data = None

        if isinstance(data, dict):
            lint_result = lint_packs.lint_pack(
                pack_path, include_distribution=False, parsed_data=data)
        else:
            lint_result = lint_packs.lint_pack(pack_path, include_distribution=False)
        criticals = [
            v for v in lint_result["violations"] if v.get("severity") == "critical"
        ]
        warnings = [
            v for v in lint_result["violations"] if v.get("severity") == "warning"
        ]

        # Install gate (INV-7): every installed pack needs blueprint + fresh
        # cert, and no pack may waive coverage enforcement pack-wide.
        gate_reasons: list[str] = []
        if isinstance(data, dict):
            if not data.get("coverage_blueprint"):
                gate_reasons.append("missing coverage_blueprint")
            if pack_cert.has_pack_wide_l23_waiver(data):
                gate_reasons.append("pack-wide L23 waiver (PM-5)")
            if not pack_cert.certification_fresh(data):
                gate_reasons.append("certification missing or stale")

        result.lint_criticals += len(criticals)
        result.lint_warnings += len(warnings)
        if gate_reasons:
            result.install_gate_failures += 1
        try:
            rel = str(pack_path.relative_to(packs_root.parent))
        except ValueError:
            rel = str(pack_path)
        result.pack_reports.append({
            "course": course,
            "file": filename,
            "rel": rel,
            "criticals": criticals,
            "warnings": warnings,
            "gate_reasons": gate_reasons,
        })

        if criticals or gate_reasons:
            reasons: list[str] = []
            if criticals:
                details = "; ".join(
                    f"{v.get('rule')}: {v.get('detail')}" for v in criticals
                )
                reasons.append(f"lint critical(s): {details}")
            reasons.extend(gate_reasons)
            result.rejections[(course, filename)] = {
                "reasons": reasons,
                "parse_error": parse_error,
                "data": data if isinstance(data, dict) else None,
            }
            continue

        try:
            raw_bytes = pack_path.read_bytes()
        except OSError as error:
            # Fail closed: a pack whose bytes can no longer be read cannot be
            # proven to be the bytes that passed, so it must not ship.
            result.rejections[(course, filename)] = {
                "reasons": [f"unreadable source bytes ({error})"],
                "parse_error": None,
                "data": data if isinstance(data, dict) else None,
            }
            continue
        result.admitted[(course, filename)] = {"raw_bytes": raw_bytes, "data": data}

    if strict:
        _evaluate_course_distribution(packs_root, result)
    return result
