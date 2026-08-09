#!/usr/bin/env python3
"""Credential-free local Apple toolchain probe and reviewed-pin check."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(argv: list[str], timeout: float = 5.0) -> dict[str, object]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "available": False, "status": None, "output": str(exc)}
    output = (result.stdout + result.stderr).strip()
    return {"argv": argv, "available": result.returncode == 0, "status": result.returncode, "output": output[-2000:]}


def probe() -> dict[str, object]:
    try:
        import cryptography

        crypto = {"available": True, "version": cryptography.__version__}
    except (ImportError, AttributeError) as exc:
        crypto = {"available": False, "version": None, "error": str(exc)}
    xcodebuild = run(["xcodebuild", "-version"])
    xcode_match = re.search(r"^Xcode\s+(\S+)", str(xcodebuild["output"]), re.M)
    build_match = re.search(r"^Build version\s+(\S+)", str(xcodebuild["output"]), re.M)
    xcodegen = run(["xcodegen", "--version"])
    swift = run(["swift", "--version"])
    sim = run(["xcrun", "simctl", "list", "runtimes"], timeout=8)
    devices = run(["xcrun", "simctl", "list", "devices", "available"], timeout=8)
    device_types = run(["xcrun", "simctl", "list", "devicetypes"], timeout=8)
    tools = {}
    for name in ("altool", "cktool"):
        path = shutil.which(name) or f"/Applications/Xcode.app/Contents/Developer/usr/bin/{name}"
        tools[name] = {"path": path, "exists": Path(path).exists(), "help": run([path, "--help"], timeout=3)}
    return {
        "host": {"os": platform.system(), "os_version": platform.mac_ver()[0], "arch": platform.machine()},
        "cryptography": crypto,
        "xcode": {"version": xcode_match.group(1) if xcode_match else None, "build": build_match.group(1) if build_match else None, "probe": xcodebuild},
        "xcodegen": xcodegen,
        "swift": swift,
        "simulator": {
            "runtime_probe": sim,
            "devices_probe": devices,
            "device_types_probe": device_types,
        },
        "release_tools": tools,
    }


def load_pins() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "release-config.toml").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z_]+)\s*=\s*\"([^\"]*)\"", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def check(p: dict[str, object]) -> tuple[bool, list[str]]:
    pins = load_pins()
    failures: list[str] = []
    cryptography = p.get("cryptography", {})
    expected_crypto = pins.get("cryptography")
    if not cryptography.get("available") or not cryptography.get("version"):
        failures.append("cryptography unavailable: install the reviewed version and re-pin only after a credential-free capability probe")
    elif cryptography.get("version") != expected_crypto:
        failures.append("cryptography drift: review the installed version and deliberately re-pin release-config.toml")
    xcode = p["xcode"]
    if xcode.get("version") != pins.get("xcode") or xcode.get("build") != pins.get("xcode_build"):
        failures.append("Xcode drift: review release-config.toml and deliberately re-pin")
    xcodegen_output = str(p["xcodegen"].get("output", ""))
    if pins.get("xcodegen") not in xcodegen_output:
        failures.append("XcodeGen drift: review release-config.toml and deliberately re-pin")
    simulator = p["simulator"]
    runtime_output = str(simulator.get("runtime_probe", {}).get("output", ""))
    device_output = str(simulator.get("devices_probe", {}).get("output", ""))
    device_type_output = str(simulator.get("device_types_probe", {}).get("output", ""))
    runtime = pins.get("simulator_runtime", "")
    runtime_label = runtime.removeprefix("iOS ")
    device = pins.get("simulator_device", "")
    runtime_available = bool(re.search(rf"\biOS\s+{re.escape(runtime_label)}\b.*\(available\)", runtime_output))
    device_type_available = bool(re.search(rf"\b{re.escape(device)}\b", device_type_output))
    device_usable = bool(re.search(rf"\b{re.escape(device)}\b.*\(.*Shutdown\)", device_output))
    simulator["runtime_matches_pin"] = runtime_available
    simulator["device_type_matches_pin"] = device_type_available
    simulator["device_usable"] = device_usable
    if not simulator.get("runtime_probe", {}).get("available"):
        failures.append("BLOCKER: Simulator service/runtime unavailable: repair CoreSimulator, then re-run the probe")
    elif not runtime_available:
        failures.append(f"Pinned simulator runtime {runtime!r} is unavailable: review release-config.toml and deliberately re-pin")
    if not device_type_available:
        failures.append(f"Pinned simulator device {device!r} is unavailable: review release-config.toml and deliberately re-pin")
    elif not device_usable:
        failures.append(f"Pinned simulator device {device!r} has no usable available instance: create/repair it before re-running the probe")
    for name, item in p["release_tools"].items():
        if not item.get("exists") or not item.get("help", {}).get("available"):
            failures.append(f"{name} missing at reviewed path; re-pin only after capability review")
    return not failures, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare the probe with reviewed pins")
    parser.add_argument("--json", action="store_true", help="emit the probe JSON")
    args = parser.parse_args(argv)
    result = probe()
    if args.json or not args.check:
        print(json.dumps(result, indent=2, sort_keys=True))
    if args.check:
        ok, failures = check(result)
        if not ok:
            for failure in failures:
                print(f"FAIL: {failure}", flush=True)
            return 1
        print("toolchain capabilities match reviewed pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
