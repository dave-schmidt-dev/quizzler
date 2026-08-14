#!/usr/bin/env python3
"""Bind the explicitly opted-in Development probe environment to one target."""

import argparse
import os
import plistlib
from pathlib import Path

from resolve_xctestrun_app import _target_from_plist, resolve


TARGET_ENVIRONMENT_KEYS = (
    "QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE",
    "QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE_RECOVERY",
)
# Remove this retired value if a generated run specification predates the
# hash-free probe contract. It is never injected into the bound run.
RETIRED_ENVIRONMENT_KEYS = ("QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE_SIGNED_APP_SHA256",)


def _fail(message: str) -> None:
    raise ValueError(message)


def bind(
    xctestrun_path: Path,
    output_path: Path,
    signed_app_path: Path,
    *,
    live_probe: bool = False,
    recovery_probe: bool = False,
) -> Path:
    """Write a target-scoped disposable xctestrun with exact opt-ins."""
    xctestrun = Path(os.path.abspath(xctestrun_path))
    output = Path(os.path.abspath(output_path))
    if output == xctestrun:
        _fail("bound XCTest run output must be a separate file")
    if output.is_symlink():
        _fail("bound XCTest run output must not be a symlink")
    # Resolve before writing so the disposable plist retains the exact app
    # binding and cannot be used to redirect the probe to another bundle.
    resolve(xctestrun, signed_app_path)
    try:
        with xctestrun.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        _fail(f"XCTest run specification is not a valid plist: {exc}")
    if not isinstance(plist, dict):
        _fail("XCTest run specification root is not a dictionary")

    target = _target_from_plist(plist)
    existing = target.get("EnvironmentVariables", {})
    if not isinstance(existing, dict):
        _fail(f"{_target_name()}.EnvironmentVariables is not a dictionary")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in existing.items()):
        _fail(f"{_target_name()}.EnvironmentVariables must contain string pairs")

    # Never inherit stale live-probe values from a generated plist. The only
    # values restored below are exact flags supplied by this attended call.
    environment = {
        key: value for key, value in existing.items()
        if key not in TARGET_ENVIRONMENT_KEYS and key not in RETIRED_ENVIRONMENT_KEYS
    }
    if live_probe:
        environment["QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE"] = "enabled"
    if recovery_probe:
        environment["QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE_RECOVERY"] = "enabled"
    if environment:
        target["EnvironmentVariables"] = environment
    else:
        target.pop("EnvironmentVariables", None)

    output.parent.mkdir(parents=False, exist_ok=True)
    try:
        with output.open("wb") as stream:
            plistlib.dump(plist, stream, fmt=plistlib.FMT_XML, sort_keys=False)
    except OSError as exc:
        _fail(f"could not write bound XCTest run: {exc}")
    return output


def _target_name() -> str:
    return "QuizzleriOSUITests"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xctestrun", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signed-app", type=Path, required=True)
    parser.add_argument("--live-probe", action="store_true")
    parser.add_argument("--recovery-probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(bind(
            args.xctestrun,
            args.output,
            args.signed_app,
            live_probe=args.live_probe,
            recovery_probe=args.recovery_probe,
        ))
    except ValueError as exc:
        print(f"FAIL: {exc}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
