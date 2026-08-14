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
DEFAULT_OUTPUT_LIMIT = 2_000
DEVICE_OUTPUT_LIMIT = 16_000


def _bounded_output(output: str, limit: int) -> str:
    if len(output) <= limit:
        return output
    marker = "\n... output truncated ...\n"
    if limit <= len(marker):
        return output[:limit]
    available = limit - len(marker)
    head = available // 2
    tail = available - head
    return f"{output[:head]}{marker}{output[-tail:]}"


def run(argv: list[str], timeout: float = 5.0, output_limit: int = DEFAULT_OUTPUT_LIMIT) -> dict[str, object]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "available": False, "status": None, "output": str(exc)}
    output = (result.stdout + result.stderr).strip()
    return {"argv": argv, "available": result.returncode == 0, "status": result.returncode, "output": _bounded_output(output, output_limit)}


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
    # Device output contains runtime headings needed to verify the pinned
    # device/runtime association. Keep this bounded, but large enough to
    # retain the complete current Xcode device listing.
    devices = run(["xcrun", "simctl", "list", "devices", "available"], timeout=8, output_limit=DEVICE_OUTPUT_LIMIT)
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


def _runtime_matches_pin(output: str, runtime: str) -> bool:
    """Return whether simctl lists the pinned runtime as usable."""
    for line in output.splitlines():
        if not re.match(rf"^\s*{re.escape(runtime)}(?:\s|$)", line):
            continue
        # Newer simctl output omits the old ``(available)`` suffix.  A
        # successful runtimes probe is sufficient in that format, while an
        # explicit unavailable marker remains fail-closed.
        return "(unavailable" not in line.lower()
    return False


def _device_type_matches_pin(output: str, device: str) -> bool:
    """Return whether simctl lists the exact pinned device type."""
    return any(re.match(rf"^\s*{re.escape(device)}\s+\(", line) for line in output.splitlines())


def _device_usable_for_runtime(output: str, runtime: str, device: str) -> bool:
    """Return whether the pinned device has a shutdown instance on its runtime."""
    runtime_heading = re.compile(rf"^\s*--\s*{re.escape(runtime)}\s*--\s*$")
    other_heading = re.compile(r"^\s*--\s*.+\s*--\s*$")
    device_line = re.compile(rf"^\s*{re.escape(device)}\s+\([^)]*\)\s+\(Shutdown\)(?:\s|$)")
    in_pinned_runtime = False
    for line in output.splitlines():
        if runtime_heading.match(line):
            in_pinned_runtime = True
        elif other_heading.match(line):
            in_pinned_runtime = False
        elif in_pinned_runtime and device_line.match(line):
            return True
    return False


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
    device = pins.get("simulator_device", "")
    runtime_available = _runtime_matches_pin(runtime_output, runtime)
    device_type_available = _device_type_matches_pin(device_type_output, device)
    device_usable = _device_usable_for_runtime(device_output, runtime, device)
    simulator["runtime_matches_pin"] = runtime_available
    simulator["device_type_matches_pin"] = device_type_available
    simulator["device_usable"] = device_usable
    simulator_available = bool(simulator.get("runtime_probe", {}).get("available"))
    if not simulator_available:
        # A dead CoreSimulator service makes every simulator sub-probe empty.
        # Do not misreport those empty results as three independent pin defects.
        failures.append("BLOCKER: Simulator service/runtime unavailable: repair CoreSimulator, then re-run the probe")
    elif not runtime_available:
        failures.append(f"Pinned simulator runtime {runtime!r} is unavailable: review release-config.toml and deliberately re-pin")
    if simulator_available:
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
