#!/usr/bin/env python3
"""Pure artifact metadata assertions used by the counted gate and CI."""
from __future__ import annotations

import functools
import json
import plistlib
import subprocess
import struct
import sys
import tempfile
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_ENTITLEMENTS = ROOT / "QuizzleriOS/QuizzleriOS.Release.entitlements"
REQUIRED_APP_ICON_SLOTS = (
    ("iphone", "20x20", "2x"),
    ("iphone", "20x20", "3x"),
    ("iphone", "29x29", "2x"),
    ("iphone", "29x29", "3x"),
    ("iphone", "40x40", "2x"),
    ("iphone", "40x40", "3x"),
    ("iphone", "60x60", "2x"),
    ("iphone", "60x60", "3x"),
    ("ipad", "20x20", "1x"),
    ("ipad", "20x20", "2x"),
    ("ipad", "29x29", "1x"),
    ("ipad", "29x29", "2x"),
    ("ipad", "40x40", "1x"),
    ("ipad", "40x40", "2x"),
    ("ipad", "76x76", "1x"),
    ("ipad", "76x76", "2x"),
    ("ipad", "83.5x83.5", "2x"),
    ("ios-marketing", "1024x1024", "1x"),
)


def inspect_entitlements(path: Path = RELEASE_ENTITLEMENTS) -> dict[str, object]:
    return plistlib.loads(path.read_bytes())


def assert_release_entitlements(entitlements: dict[str, object]) -> None:
    containers = entitlements.get("com.apple.developer.icloud-container-identifiers", [])
    if containers != ["iCloud.com.zerodelta.quizzler.dev"]:
        raise AssertionError("Release must contain the configured CloudKit container")
    if entitlements.get("aps-environment") != "production":
        raise AssertionError("Release must contain literal production push entitlement")
    if entitlements.get("com.apple.developer.icloud-container-environment") != "Production":
        raise AssertionError("Release must contain literal Production CloudKit environment")


def project_settings() -> dict[str, object]:
    spec = yaml.safe_load((ROOT / "project.yml").read_text(encoding="utf-8"))
    return spec["targets"]["QuizzleriOS"]["settings"]


@functools.lru_cache(maxsize=1)
def build_release_artifact() -> Path:
    """Build the unsigned generic-device Release product for metadata checks.

    Cached: the Release build is the slowest step in this leg and every test
    that needs it wants the same bytes.
    """
    workspace = Path(tempfile.mkdtemp(prefix="quizzler-release-") )
    project = workspace / "project"
    project.mkdir()
    print("artifact metadata: generating Xcode project", flush=True)
    subprocess.run([
        "xcodegen", "generate", "--spec", str(ROOT / "project.yml"),
        "--project", str(project), "--project-root", str(ROOT), "--quiet",
    ], check=True, text=True)
    derived = workspace / "derived"
    print("artifact metadata: building unsigned Release artifact", flush=True)
    subprocess.run([
        "xcodebuild", "-project", str(project / "Quizzler.xcodeproj"),
        "-scheme", "Quizzler", "-configuration", "Release",
        "-sdk", "iphoneos", "-destination", "generic/platform=iOS",
        "-derivedDataPath", str(derived), "CODE_SIGNING_ALLOWED=NO", "build",
    ], check=True, text=True)
    print("artifact metadata: Release artifact built", flush=True)
    return derived / "Build/Products/Release-iphoneos"


class ArtifactMetadataTests(unittest.TestCase):
    def test_release_entitlements_are_literal_production_values(self):
        assert_release_entitlements(inspect_entitlements())

    def test_release_configuration_uses_one_container_across_environments(self):
        config = tomllib.loads((ROOT / "release-config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["development_container"], "iCloud.com.zerodelta.quizzler.dev")
        self.assertEqual(config["production_container"], "iCloud.com.zerodelta.quizzler.dev")
        self.assertEqual(config["schema_disposition"], "same-container")

    def test_debug_and_release_share_container_but_not_environment(self):
        debug = plistlib.loads((ROOT / "QuizzleriOS/QuizzleriOS.Debug.entitlements").read_bytes())
        release = inspect_entitlements()
        self.assertNotEqual(debug["aps-environment"], release["aps-environment"])
        self.assertEqual(debug["com.apple.developer.icloud-container-identifiers"], release["com.apple.developer.icloud-container-identifiers"])

    def test_release_build_selects_production_entitlements_and_generates_metadata(self):
        settings = project_settings()
        self.assertEqual(settings["base"]["CODE_SIGN_ENTITLEMENTS"], "QuizzleriOS/QuizzleriOS.Debug.entitlements")
        release = settings["configs"]["Release"]
        self.assertEqual(release["CODE_SIGN_ENTITLEMENTS"], "QuizzleriOS/QuizzleriOS.Release.entitlements")
        self.assertEqual(release["CODE_SIGN_IDENTITY"], "Apple Distribution")
        self.assertEqual(release["PROVISIONING_PROFILE_SPECIFIER"], "Quizzler iOS App Store (API-created)-H2C5D2K55S")
        assert_release_entitlements(inspect_entitlements())
        self.assertTrue(settings["base"]["GENERATE_INFOPLIST_FILE"])

    def test_release_settings_pin_launch_orientation_encryption_and_fixture_exclusion(self):
        settings = project_settings()
        base = settings["base"]
        self.assertTrue(base["INFOPLIST_KEY_UIApplicationSceneManifest_Generation"])
        self.assertTrue(base["INFOPLIST_KEY_UILaunchScreen_Generation"])
        self.assertFalse(base["INFOPLIST_KEY_UIRequiresFullScreen"])
        self.assertEqual(base["INFOPLIST_KEY_UISupportedInterfaceOrientations"], "UIInterfaceOrientationPortrait")
        self.assertIn("UIInterfaceOrientationLandscapeLeft", base["INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad"])
        self.assertFalse(base["INFOPLIST_KEY_ITSAppUsesNonExemptEncryption"])
        release_exclusions = settings["configs"]["Release"]["EXCLUDED_SOURCE_FILE_NAMES"]
        for marker in ("*Fixture*", "*FailureInjection*", "*TestOnly*"):
            self.assertIn(marker, release_exclusions)

    def test_framework_target_generates_its_info_plist(self):
        spec = yaml.safe_load((ROOT / "project.yml").read_text(encoding="utf-8"))
        settings = spec["targets"]["QuizzlerKit"]["settings"]["base"]
        self.assertTrue(settings["GENERATE_INFOPLIST_FILE"])
        self.assertEqual(settings["INFOPLIST_KEY_CFBundlePackageType"], "FMWK")

    def test_app_icon_set_declares_all_required_device_and_marketing_slots(self):
        icon_set = ROOT / "QuizzleriOS/Assets.xcassets/AppIcon.appiconset"
        contents = json.loads((icon_set / "Contents.json").read_text(encoding="utf-8"))
        images = contents.get("images", [])
        slots = {(item.get("idiom"), item.get("size"), item.get("scale")): item for item in images}
        self.assertEqual(set(slots), set(REQUIRED_APP_ICON_SLOTS))
        for slot in REQUIRED_APP_ICON_SLOTS:
            item = slots[slot]
            source = icon_set / item["filename"]
            self.assertTrue(source.is_file(), item["filename"])
            png = source.read_bytes()
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"), item["filename"])
            self.assertEqual(png[12:16], b"IHDR", item["filename"])
            width, height = struct.unpack(">II", png[16:24])
            logical_width = Decimal(item["size"].split("x", 1)[0])
            pixels = int(logical_width * int(item["scale"].rstrip("x")))
            self.assertEqual((width, height), (pixels, pixels), item["filename"])

    def test_unsigned_release_artifact_contains_app_and_framework_metadata(self):
        products = build_release_artifact()
        app_info = plistlib.loads((products / "QuizzleriOS.app/Info.plist").read_bytes())
        framework_info = plistlib.loads((products / "QuizzlerKit.framework/Info.plist").read_bytes())
        self.assertEqual(app_info["CFBundleIdentifier"], "com.zerodelta.quizzler")
        self.assertEqual(app_info["CFBundleExecutable"], "QuizzleriOS")
        self.assertTrue((products / "QuizzleriOS.app/QuizzleriOS").is_file())
        self.assertEqual(framework_info["CFBundlePackageType"], "FMWK")

    def test_release_artifact_carries_no_fixture_or_failure_injection_symbols(self):
        """Task 2.4's Release-archive scan: prove absence in the built bytes.

        The static checks in test-release-fixture-isolation.py read project.yml
        and prove the sources are excluded from the Release source set. They
        cannot see a fixture that reaches the binary another way -- a stray
        import, a resource, or a build setting that stops matching the
        exclusion pattern. This reads the shipped Mach-O and bundle contents
        instead, which catches the symbol regardless of how it arrived. A raw
        byte scan covers both string literals and Swift's mangled names, which
        embed the type name in readable form.
        """
        products = build_release_artifact()
        forbidden = (
            b"UITestFixture",
            b"QUIZZLER_UI_TEST_FIXTURE",
            b"DevelopmentProbeFailureInjection",
            b"QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE_INJECT_FAILURE",
        )
        scanned = 0
        for bundle in ("QuizzleriOS.app", "QuizzlerKit.framework"):
            for path in sorted((products / bundle).rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                blob = path.read_bytes()
                scanned += 1
                for symbol in forbidden:
                    self.assertNotIn(symbol, blob, f"{symbol!r} present in {path.relative_to(products)}")
        # A scan that reached nothing would pass vacuously.
        self.assertGreater(scanned, 0, "Release product contained no files to scan")


if __name__ == "__main__":
    unittest.main()
