"""Static guard for the generated QuizzlerKit Xcode target membership."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "app" / "QuizzlerKit" / "Sources" / "QuizzlerKit"
PROJECT = ROOT / "app" / "Quizzler.xcodeproj" / "project.pbxproj"
HEX_ID = r"[0-9A-F]{24}"


def section(source: str, name: str) -> str:
    match = re.search(
        rf"/\* Begin {re.escape(name)} section \*/(.*?)/\* End {re.escape(name)} section \*/",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing {name} section")
    return match.group(1)


def quizzler_kit_sources_phase(project: str) -> str:
    targets = section(project, "PBXNativeTarget")
    target = re.search(
        rf"^\s*{HEX_ID} /\* QuizzlerKit \*/ = \{{\n\s*isa = PBXNativeTarget;.*?^\s*\}};",
        targets,
        re.DOTALL | re.MULTILINE,
    )
    if target is None:
        raise AssertionError("missing QuizzlerKit native target")
    phase_id = re.search(
        rf"buildPhases = \(\s*({HEX_ID}) /\* Sources \*/",
        target.group(0),
        re.DOTALL,
    )
    if phase_id is None:
        raise AssertionError("QuizzlerKit target has no Sources build phase")
    phases = section(project, "PBXSourcesBuildPhase")
    phase = re.search(
        rf"^\s*{phase_id.group(1)} /\* Sources \*/ = \{{(.*?)^\s*\}};",
        phases,
        re.DOTALL | re.MULTILINE,
    )
    if phase is None:
        raise AssertionError("missing QuizzlerKit Sources build phase")
    return phase.group(1)


TARGET_BUILD_DEST = "${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}"
BUILT_PRODUCTS_DEST = "${BUILT_PRODUCTS_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}"
ESCAPED_TARGET_BUILD_DEST = '\\"${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}\\"'
ESCAPED_BUILT_PRODUCTS_DEST = '\\"${BUILT_PRODUCTS_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}\\"'


def assert_packer_destination_has_target_build(project: str, source: str) -> None:
    """Assert question-pack bundling uses TARGET_BUILD_DIR in both source and generated project."""
    source_destination = f'--destination "{TARGET_BUILD_DEST}"'
    project_destination = f"--destination {ESCAPED_TARGET_BUILD_DEST}"
    if source_destination not in source:
        raise AssertionError("source project still uses unsupported destination path")
    if project_destination not in project:
        raise AssertionError("generated project still uses unsupported destination path")


def source_files_in_phase(project: str) -> set[str]:
    build_files = section(project, "PBXBuildFile")
    phase = quizzler_kit_sources_phase(project)
    phase_build_ids = set(re.findall(rf"({HEX_ID}) /\* .*? in Sources \*/", phase))
    return {
        name
        for build_id, name, _ in re.findall(
            rf"({HEX_ID}) /\* ([^*]+) in Sources \*/ = \{{isa = PBXBuildFile; fileRef = ({HEX_ID})",
            build_files,
        )
        if build_id in phase_build_ids
    }


class XcodeTargetMembershipTests(unittest.TestCase):
    def test_pack_bundle_destination_uses_target_build_dir(self):
        source = (ROOT / "app" / "project.yml").read_text(encoding="utf-8")
        project = PROJECT.read_text(encoding="utf-8")
        assert_packer_destination_has_target_build(project, source)
        self.assertNotIn(f"--destination \"{BUILT_PRODUCTS_DEST}\"", source)
        self.assertNotIn(f"--destination {ESCAPED_BUILT_PRODUCTS_DEST}", project)
        self.assertIn(f"--destination {ESCAPED_TARGET_BUILD_DEST}", project)

    def test_every_quizzlerkit_swift_file_is_in_the_generated_sources_phase(self):
        project = PROJECT.read_text(encoding="utf-8")
        expected = {path.name for path in SOURCE_ROOT.rglob("*.swift")}
        actual = source_files_in_phase(project)
        self.assertEqual(sorted(expected - actual), [])

    def test_known_missing_files_are_explicitly_covered(self):
        project = PROJECT.read_text(encoding="utf-8")
        actual = source_files_in_phase(project)
        self.assertTrue(
            {"ProgressMerge.swift", "SyncRecovery.swift", "MigrationEnvelope.swift"}
            <= actual
        )


if __name__ == "__main__":
    unittest.main()
