#!/usr/bin/env python3
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("toolchain_capabilities", HERE / "toolchain_capabilities.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ToolchainCapabilityTests(unittest.TestCase):
    def test_probe_is_credential_free_and_structured(self):
        source = (HERE / "toolchain_capabilities.py").read_text(encoding="utf-8")
        self.assertNotIn("bws-get", source)
        self.assertNotIn("Authorization", source)
        result = module.probe()
        self.assertIn("xcode", result)
        self.assertIn("cryptography", result)
        self.assertIn("simulator", result)
        self.assertIn("release_tools", result)
        self.assertIn("runtime_probe", result["simulator"])
        self.assertIn("devices_probe", result["simulator"])
        self.assertIn("device_types_probe", result["simulator"])

    def test_drift_has_one_actionable_repin_decision(self):
        result = {"xcode": {"version": "old", "build": "old"}, "xcodegen": {"output": "0.0"}, "simulator": {"available": False}, "release_tools": {"altool": {"exists": False}, "cktool": {"exists": False}}}
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(failures)
        self.assertTrue(any("re-pin" in item for item in failures))
        self.assertTrue(any("CoreSimulator" in item for item in failures))
        self.assertTrue(any("BLOCKER" in item for item in failures))

    def test_dead_simulator_service_does_not_misreport_an_empty_device_probe_as_pin_drift(self):
        result = {
            "xcode": {"version": "26.6", "build": "17F113"},
            "xcodegen": {"output": "Version: 2.46.0"},
            "cryptography": {"available": True, "version": "50.0.0"},
            "simulator": {
                "runtime_probe": {"available": False, "output": "CoreSimulator unavailable"},
                "devices_probe": {"available": False, "output": ""},
                "device_types_probe": {"available": False, "output": ""},
            },
            "release_tools": {
                "altool": {"exists": True, "help": {"available": True}},
                "cktool": {"exists": True, "help": {"available": True}},
            },
        }
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertEqual(failures, ["BLOCKER: Simulator service/runtime unavailable: repair CoreSimulator, then re-run the probe"])

    def test_pin_and_tool_usability_are_checked(self):
        result = {
            "xcode": {"version": "26.6", "build": "17F113"},
            "xcodegen": {"output": "Version: 2.46.0"},
            "cryptography": {"available": True, "version": "50.0.0"},
            "simulator": {
                "runtime_probe": {"available": True, "output": "iOS 26.5 (26.5 - 23G93) - com.apple.CoreSimulator.SimRuntime.iOS-26-5"},
                "devices_probe": {"available": True, "output": "== Devices ==\n-- iOS 26.5 --\n    iPhone 16 (ABC) (Shutdown)"},
                "device_types_probe": {"available": True, "output": "iPhone 16 (com.apple.CoreSimulator.SimDeviceType.iPhone-16)"},
            },
            "release_tools": {
                "altool": {"exists": True, "help": {"available": True}},
                "cktool": {"exists": True, "help": {"available": True}},
            },
        }
        ok, failures = module.check(result)
        self.assertTrue(ok, failures)
        self.assertTrue(result["simulator"]["device_usable"])

    def test_cryptography_drift_or_import_failure_is_actionable(self):
        result = {
            "xcode": {"version": "26.6", "build": "17F113"},
            "xcodegen": {"output": "Version: 2.46.0"},
            "cryptography": {"available": True, "version": "0.0.0"},
            "simulator": {
                "runtime_probe": {"available": True, "output": "iOS 26.5 (available)"},
                "devices_probe": {"available": True, "output": "-- iOS 26.5 --\n    iPhone 16 (ABC) (Shutdown)"},
                "device_types_probe": {"available": True, "output": "iPhone 16"},
            },
            "release_tools": {
                "altool": {"exists": True, "help": {"available": True}},
                "cktool": {"exists": True, "help": {"available": True}},
            },
        }
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertEqual(sum("cryptography" in item for item in failures), 1)
        self.assertIn("re-pin", next(item for item in failures if "cryptography" in item))
        result["cryptography"] = {"available": False, "version": None, "error": "missing"}
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertEqual(sum("cryptography" in item for item in failures), 1)
        self.assertIn("install", next(item for item in failures if "cryptography" in item))

    def test_runtime_must_match_pin_and_explicitly_unavailable_runtime_fails(self):
        result = self._complete_result(
            runtime_output="iOS 26.4 (26.4 - 23E214) - com.apple.CoreSimulator.SimRuntime.iOS-26-4",
        )
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(any("runtime" in item for item in failures))

        result = self._complete_result(
            runtime_output="iOS 26.5 (unavailable, runtime profile not found)",
        )
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(any("runtime" in item for item in failures))

    def test_device_must_be_exact_type_and_associated_with_pinned_runtime(self):
        result = self._complete_result(
            devices_output="== Devices ==\n-- iOS 26.4 --\n    iPhone 16 (ABC) (Shutdown)",
        )
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(any("device" in item for item in failures))

        result = self._complete_result(
            device_types_output="iPhone 16 Pro (com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro)",
        )
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(any("device" in item for item in failures))

    def test_missing_or_timed_out_release_tool_fails_closed(self):
        result = self._complete_result()
        result["release_tools"]["altool"] = {"exists": False, "help": {"available": False}}
        result["release_tools"]["cktool"] = {"exists": True, "help": {"available": False, "output": "timed out"}}
        ok, failures = module.check(result)
        self.assertFalse(ok)
        self.assertTrue(any("altool missing" in item for item in failures))
        self.assertTrue(any("cktool missing" in item for item in failures))

    def test_device_probe_bound_preserves_runtime_heading_and_tail_device(self):
        full_output = "-- iOS 26.5 --\n" + ("    iPhone 15 (OLD) (Shutdown)\n" * 250) + "    iPhone 16 (ABC) (Shutdown)\n"
        self.assertGreater(len(full_output), module.DEFAULT_OUTPUT_LIMIT)
        completed = subprocess.CompletedProcess(["simctl"], 0, stdout=full_output, stderr="")
        with patch.object(module.subprocess, "run", return_value=completed):
            probe = module.run(["xcrun", "simctl", "list", "devices", "available"], output_limit=module.DEVICE_OUTPUT_LIMIT)
        self.assertEqual(probe["output"], full_output.strip())
        result = self._complete_result(devices_output=probe["output"])
        ok, failures = module.check(result)
        self.assertTrue(ok, failures)

    @staticmethod
    def _complete_result(**overrides):
        result = {
            "xcode": {"version": "26.6", "build": "17F113"},
            "xcodegen": {"output": "Version: 2.46.0"},
            "cryptography": {"available": True, "version": "50.0.0"},
            "simulator": {
                "runtime_probe": {"available": True, "output": "iOS 26.5 (26.5 - 23G93) - com.apple.CoreSimulator.SimRuntime.iOS-26-5"},
                "devices_probe": {"available": True, "output": "== Devices ==\n-- iOS 26.5 --\n    iPhone 16 (ABC) (Shutdown)"},
                "device_types_probe": {"available": True, "output": "iPhone 16 (com.apple.CoreSimulator.SimDeviceType.iPhone-16)"},
            },
            "release_tools": {
                "altool": {"exists": True, "help": {"available": True}},
                "cktool": {"exists": True, "help": {"available": True}},
            },
        }
        for key, value in overrides.items():
            if key == "runtime_output":
                result["simulator"]["runtime_probe"]["output"] = value
            elif key == "devices_output":
                result["simulator"]["devices_probe"]["output"] = value
            elif key == "device_types_output":
                result["simulator"]["device_types_probe"]["output"] = value
        return result


if __name__ == "__main__":
    unittest.main()
