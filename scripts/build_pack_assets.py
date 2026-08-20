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
import pack_cert  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKS_ROOT = PROJECT_ROOT / "question-packs"

# Mirrors `NativePackAssetManifest.contractVersion` in PackLoader.swift.
CONTRACT_VERSION = 1
MANIFEST_NAME = "question-assets.json"
PACKS_SUBDIRECTORY = "Packs"

# Directories and files whose names start with `_` are archive, staging, or
# course metadata rather than installable packs; `.` covers `.DS_Store` and
# friends. Discovery is a rule rather than an allowlist so a newly installed
# course is picked up without editing this file.
IGNORED_PREFIXES = ("_", ".")
# `tests/test_lint_hook.py` writes a throwaway course into the real packs root
# while it runs. A build that happens to overlap it must not bundle -- or fail
# on -- a fixture. The lint suite and the pre-push hook skip the same prefix.
IGNORED_COURSE_PREFIXES = IGNORED_PREFIXES + ("zz-hooktest-",)


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


def is_pack_candidate(path: Path) -> bool:
    return path.suffix == ".json" and not path.name.startswith(IGNORED_PREFIXES)


def discover_courses(packs_root: Path) -> list[Path]:
    if not packs_root.is_dir():
        return []
    return sorted(
        entry
        for entry in packs_root.iterdir()
        if entry.is_dir() and not entry.name.startswith(IGNORED_COURSE_PREFIXES)
    )


def collect_packs(packs_root: Path, report) -> tuple[list[dict], list[str]]:
    """Return `(assets, rejections)` for every discoverable pack.

    `assets` entries are already in the shape `NativePackAsset` decodes.
    """
    assets: list[dict] = []
    rejections: list[str] = []
    seen_pack_ids: dict[str, str] = {}

    for course in discover_courses(packs_root):
        for pack_path in sorted(p for p in course.iterdir() if p.is_file() and is_pack_candidate(p)):
            relative = f"{course.name}/{pack_path.name}"
            report(f"inspecting {relative}")
            try:
                data = json.loads(pack_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                rejections.append(f"{relative}: unreadable JSON ({error})")
                continue
            if not isinstance(data, dict) or "questions" not in data:
                # Course metadata and templates live alongside packs; skipping
                # them quietly is correct, they were never pack candidates.
                continue

            findings = check_l29_native_metadata_contract(data)
            if findings:
                detail = "; ".join(finding["detail"] for finding in findings)
                rejections.append(f"{relative}: fails the native contract (L29) — {detail}")
                continue

            if not pack_cert.certification_fresh(data):
                rejections.append(f"{relative}: fails INV-8 certification (missing or stale)")
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
                    "course_id": course.name,
                    "pack_id": pack_id,
                    "path": relative,
                    "content_digest": content_digest(data),
                    "_source": pack_path,
                    "_questions": len(data["questions"]),
                }
            )

    return assets, rejections


def write_bundle(assets: list[dict], destination: Path, report) -> Path:
    """Copy each pack under `destination/Packs/` and write the manifest."""
    packs_directory = destination / PACKS_SUBDIRECTORY
    # A stale pack left from a previous build would still be listed in the old
    # manifest's absence, so clear the tree rather than merging into it.
    if packs_directory.exists():
        shutil.rmtree(packs_directory)

    for asset in assets:
        target = packs_directory / asset["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copy the bytes verbatim: the digest was taken over the parsed value,
        # and re-serializing here would be a second chance to diverge.
        shutil.copyfile(asset["_source"], target)
        report(f"bundled {asset['path']} ({asset['_questions']} questions)")

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "packs": [
            {key: asset[key] for key in ("course_id", "pack_id", "path", "content_digest")}
            for asset in assets
        ],
    }
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def build(packs_root: Path, destination: Path, report) -> dict:
    assets, rejections = collect_packs(packs_root, report)
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
    parser.add_argument("--quiet", action="store_true", help="suppress per-pack progress lines")
    args = parser.parse_args(argv)

    # Progress goes to stderr so stdout stays free for machine-readable use and
    # the Xcode build log still shows what was bundled while it happens (INV-1).
    def report(message: str) -> None:
        if not args.quiet:
            print(f"build_pack_assets: {message}", file=sys.stderr, flush=True)

    result = build(args.packs_root, args.destination, report)

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
