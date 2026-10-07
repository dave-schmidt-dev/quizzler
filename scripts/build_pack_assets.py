#!/usr/bin/env python3
"""Bundle locally installed question packs into a native app build.

The iOS app ships no question content of its own. It reads
`question-assets.json` from its resource bundle, and that file is produced
here — during the Xcode build, over whatever packs are present on the machine
doing the building.

That indirection is deliberate. `question-packs/*/` is gitignored (see
`.gitignore`), so course material is local-only and a clean checkout contains
just `samples`. A build therefore cannot reference pack files by a committed
path: it has to discover them. The trade-off worth stating plainly is that
**two machines with different installed packs produce different apps**. The
manifest records a content digest per pack so the resulting build is at least
self-describing about which content it carries.

Every emitted pack must satisfy the native metadata contract (lint rule L29,
which mirrors QuizzlerKit's `PackManifest.validate()`). A pack that fails is
refused rather than copied: shipping a file the decoder rejects produces an
app that silently has no questions, which is exactly the failure this whole
path exists to prevent.

The bundler also runs the FULL install gate (`scripts/install_gate.py`, the
same gate `scripts/build_manifest.py` enforces): lint criticals, the
coverage/certification pack gate, and the course-level area and blueprint
distribution checks. Admission is decided over one parse per pack, and the
exact bytes that passed the gate are the bytes written into the bundle, so a
source file mutated after gating cannot change what ships.

A pack reduced by `scripts/pack_quarantine.py` carries a `partial_install`
marker and is refused here by default. `--allow-partial` — passed by
`app/project.yml` only when CONFIGURATION is Debug — admits a valid partial as
its retained subset, marker included so the app can label it, and
nothing else is relaxed: the subset must still clear lint, the course
distribution aggregates, and a fresh certification.

Usage (from an Xcode build phase):

    build_pack_assets.py --destination "$BUILT_PRODUCTS_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lint_packs import check_l29_native_metadata_contract  # noqa: E402
import install_gate  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKS_ROOT = PROJECT_ROOT / "question-packs"

# Mirrors `NativePackAssetManifest.contractVersion` in PackLoader.swift.
CONTRACT_VERSION = 1
MANIFEST_NAME = "question-assets.json"
PACKS_SUBDIRECTORY = "Packs"


def canonical_bytes(value) -> bytes:
    """Serialize `value` the way `PackLoader.contentDigest` does in Swift.

    Foundation reaches the same bytes with `.sortedKeys` plus
    `.withoutEscapingSlashes`; without the latter it writes `/` as `\\/` and
    any pack containing a URL hashes differently in the two languages.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_digest(value) -> str:
    """The `sha256:<hex>` digest QuizzlerKit checks a bundled pack against."""
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def collect_packs(packs_root: Path, report, *,
                  allow_partial: bool = False) -> tuple[list[dict], list[str]]:
    """Return `(assets, rejections)` for every discoverable pack.

    The full install gate (``install_gate.evaluate``) decides admission over
    one parse per pack; this function layers the bundler's own checks — the
    native contract (L29) and `pack_id` uniqueness — on top and shapes the
    survivors as `NativePackAsset` entries. Each asset carries the exact
    ``_raw_bytes`` the gate admitted, so `write_bundle` ships the gated bytes
    even if the source file changes afterwards.

    Partial installs (M3): by default a pack carrying a ``partial_install``
    marker is refused. With ``allow_partial`` the gate admits a VALID partial
    as its retained subset, marker included — the Debug-build posture — and
    every other quality bar still applies to that subset. ``app/project.yml``
    passes ``--allow-partial`` only when CONFIGURATION is Debug.
    """
    assets: list[dict] = []
    rejections: list[str] = []
    seen_pack_ids: dict[str, str] = {}

    gate = install_gate.evaluate(
        packs_root, report=report, allow_partial=allow_partial)

    for (course, filename), entry in gate.admitted.items():
        relative = f"{course}/{filename}"
        data = entry["data"]
        if not isinstance(data, dict) or "questions" not in data:
            # Course metadata and templates live alongside packs; skipping
            # them quietly is correct, they were never pack candidates.
            continue

        findings = check_l29_native_metadata_contract(data)
        if findings:
            detail = "; ".join(finding["detail"] for finding in findings)
            rejections.append(f"{relative}: fails the native contract (L29) — {detail}")
            continue

        pack_id = data["pack_id"]
        if pack_id in seen_pack_ids:
            rejections.append(
                f"{relative}: pack_id {pack_id!r} already provided by {seen_pack_ids[pack_id]}"
            )
            continue
        seen_pack_ids[pack_id] = relative

        assets.append(
            {
                "course_id": course,
                "pack_id": pack_id,
                "path": relative,
                "content_digest": content_digest(data),
                "_raw_bytes": entry["raw_bytes"],
                "_questions": len(data["questions"]),
            }
        )

    for (course, filename), rejection in gate.rejections.items():
        relative = f"{course}/{filename}"
        if rejection["parse_error"] is not None:
            rejections.append(f"{relative}: unreadable JSON ({rejection['parse_error']})")
            continue
        data = rejection["data"]
        if data is not None and "questions" not in data:
            # Course metadata and templates live alongside packs; skipping
            # them quietly is correct, they were never pack candidates.
            continue
        # Name the certification alone only when it is the sole reason, so a
        # stale cert never hides a lint or contract failure on the same pack.
        if rejection["reasons"] == ["certification missing or stale"]:
            rejections.append(f"{relative}: fails INV-8 certification (missing or stale)")
            continue
        detail = "; ".join(rejection["reasons"])
        rejections.append(f"{relative}: fails the install gate — {detail}")

    for exclusion in gate.excluded:
        detail = "; ".join(description for _, description in exclusion["findings"])
        for course, filename in exclusion["packs"]:
            rejections.append(
                f"{course}/{filename}: course failed the install gate — {detail}"
            )

    return assets, rejections


def manifest_for_assets(assets: list[dict]) -> dict:
    """Return the exact in-memory manifest later written into the app bundle."""

    return {
        "contract_version": CONTRACT_VERSION,
        "packs": [
            {key: asset[key] for key in ("course_id", "pack_id", "path", "content_digest")}
            for asset in assets
        ],
    }


def manifest_digest(manifest: dict) -> str:
    """Return the canonical identity of a question-assets manifest."""

    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def snapshot_manifest(packs_root: Path, report, *,
                      allow_partial: bool = False) -> tuple[dict, list[str]]:
    """Compute the build manifest without copying packs or writing files.

    Refuses partial packs by default: the snapshot describes a release
    candidate, so it keeps the release posture even though the Debug bundler
    may pass ``allow_partial``.
    """

    assets, rejections = collect_packs(
        packs_root, report, allow_partial=allow_partial)
    return manifest_for_assets(assets), rejections


def write_bundle(assets: list[dict], destination: Path, report) -> Path:
    """Write each gated pack under `destination/Packs/` and write the manifest."""
    packs_directory = destination / PACKS_SUBDIRECTORY
    # A stale pack left from a previous build would still be listed in the old
    # manifest's absence, so clear the tree rather than merging into it.
    if packs_directory.exists():
        shutil.rmtree(packs_directory)

    for asset in assets:
        target = packs_directory / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write the exact bytes the install gate admitted: the digest was taken
        # over the same parse, so re-reading the source here would be a second
        # chance to diverge from what was actually checked.
        target.write_bytes(asset["_raw_bytes"])
        report(f"bundled {asset['path']} ({asset['_questions']} questions)")

    manifest = manifest_for_assets(assets)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def build(packs_root: Path, destination: Path, report, *,
          allow_partial: bool = False) -> dict:
    assets, rejections = collect_packs(
        packs_root, report, allow_partial=allow_partial)
    manifest_path = write_bundle(assets, destination, report)
    return {"assets": assets, "rejections": rejections, "manifest_path": manifest_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packs-root", type=Path, default=DEFAULT_PACKS_ROOT)
    parser.add_argument("--destination", type=Path, required=True, help="resource directory inside the built app")
    parser.add_argument(
        "--require-pack",
        action="store_true",
        help="exit non-zero when no pack survives validation (use for release builds)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "admit packs carrying a valid partial_install marker as their "
            "retained subset (Debug builds only; every other quality bar, "
            "including blueprint coverage, course distribution, and a fresh "
            "certification, still applies)"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress per-pack progress lines")
    args = parser.parse_args(argv)

    # Progress goes to stderr so stdout stays free for machine-readable use and
    # the Xcode build log still shows what was bundled while it happens (INV-1).
    def report(message: str) -> None:
        if not args.quiet:
            print(f"build_pack_assets: {message}", file=sys.stderr, flush=True)

    result = build(
        args.packs_root, args.destination, report,
        allow_partial=args.allow_partial)

    for rejection in result["rejections"]:
        print(f"build_pack_assets: REFUSED {rejection}", file=sys.stderr, flush=True)

    count = len(result["assets"])
    questions = sum(asset["_questions"] for asset in result["assets"])
    report(f"wrote {result['manifest_path'].name}: {count} pack(s), {questions} question(s)")

    if result["rejections"]:
        # A refused pack is a build-visible error even when others succeeded:
        # the alternative is a course quietly missing from the app.
        return 1
    if args.require_pack and count == 0:
        print("build_pack_assets: no installed pack passed validation", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
