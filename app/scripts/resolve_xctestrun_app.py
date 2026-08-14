#!/usr/bin/env python3
"""Resolve the signed application bound by a generated XCTest run plist."""

import argparse
import os
import plistlib
import sys
from pathlib import Path


TARGET_NAME = "QuizzleriOSUITests"
APP_PATH_KEY = "UITargetAppPath"
TESTROOT = "__TESTROOT__"


def _fail(message: str) -> None:
    raise ValueError(message)


def _target_from_plist(plist):
    """Find exactly one target in modern or legacy xctestrun structure."""
    if "TestConfigurations" in plist:
        configurations = plist["TestConfigurations"]
        if not isinstance(configurations, list):
            _fail("XCTest run TestConfigurations is not an array")
        matches = []
        for configuration in configurations:
            if not isinstance(configuration, dict) or not isinstance(configuration.get("TestTargets"), list):
                _fail("XCTest run TestConfigurations contains an invalid TestTargets array")
            for target in configuration["TestTargets"]:
                if isinstance(target, dict) and target.get("BlueprintName") == TARGET_NAME:
                    matches.append(target)
        if len(matches) == 0:
            _fail(f"XCTest run specification is missing the {TARGET_NAME} target")
        if len(matches) != 1:
            _fail(f"XCTest run specification contains multiple {TARGET_NAME} targets")
        return matches[0]

    # Older xctestrun files put the target dictionary at the root. Keep this
    # fallback strict and never recursively search unrelated plist metadata.
    target = plist.get(TARGET_NAME)
    if not isinstance(target, dict):
        _fail(f"XCTest run specification is missing the {TARGET_NAME} target")
    return target


def _parent_without_symlink(path: Path) -> Path:
    """Use the xctestrun parent as a canonical containment root."""
    return Path(os.path.realpath(path))


def resolve(xctestrun_path: Path, signed_app_path: Path) -> Path:
    xctestrun = Path(os.path.abspath(xctestrun_path))
    signed_app = Path(os.path.abspath(signed_app_path))
    if not xctestrun.is_file() or xctestrun.is_symlink():
        _fail("XCTest run specification is absent or symlinked")
    if not signed_app.is_dir() or signed_app.is_symlink():
        _fail("exact signed app bundle is absent or symlinked")

    try:
        with xctestrun.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        _fail(f"XCTest run specification is not a valid plist: {exc}")
    if not isinstance(plist, dict):
        _fail("XCTest run specification root is not a dictionary")

    target = _target_from_plist(plist)
    app_path = target.get(APP_PATH_KEY)
    if app_path is None:
        _fail(f"XCTest run specification is missing {TARGET_NAME}.{APP_PATH_KEY}")
    if not isinstance(app_path, str) or not app_path:
        _fail(f"{TARGET_NAME}.{APP_PATH_KEY} must be a non-empty string")
    if not app_path.startswith(TESTROOT + "/"):
        _fail(f"{TARGET_NAME}.{APP_PATH_KEY} uses an unsupported placeholder")
    relative = app_path[len(TESTROOT) + 1 :]
    if not relative or "__" in relative or "$" in relative or "\\" in relative:
        _fail(f"{TARGET_NAME}.{APP_PATH_KEY} uses an unsupported placeholder")
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        _fail(f"{TARGET_NAME}.{APP_PATH_KEY} escapes the xctestrun parent")

    parent = _parent_without_symlink(xctestrun.parent)
    resolved = Path(os.path.normpath(os.path.join(parent, *parts)))
    try:
        if os.path.commonpath((str(parent), str(resolved))) != str(parent):
            _fail(f"{TARGET_NAME}.{APP_PATH_KEY} escapes the xctestrun parent")
    except ValueError:
        _fail(f"{TARGET_NAME}.{APP_PATH_KEY} escapes the xctestrun parent")
    if resolved.is_symlink() or not resolved.is_dir():
        _fail("xctestrun app path is absent or symlinked")
    if Path(os.path.realpath(resolved)) != resolved:
        _fail("xctestrun app path contains a symlink")

    signed_app_real = Path(os.path.realpath(signed_app))
    if signed_app_real != Path(os.path.realpath(signed_app.parent)) / signed_app.name:
        _fail("exact signed app bundle is symlinked")
    if Path(os.path.realpath(resolved)) != signed_app_real:
        _fail("XCTest run specification is not bound to the exact signed app")
    return resolved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xctestrun", type=Path, required=True)
    parser.add_argument("--signed-app", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(resolve(args.xctestrun, args.signed_app))
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
