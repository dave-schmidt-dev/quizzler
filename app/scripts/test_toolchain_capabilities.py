#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

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

    def test_pin_and_tool_usability_are_checked(self):
        result = {
            "xcode": {"version": "26.6", "build": "17F113"},
            "xcodegen": {"output": "Version: 2.46.0"},
            "cryptography": {"available": True, "version": "50.0.0"},
            "simulator": {
                "runtime_probe": {"available": True, "output": "iOS 26.5 (26.5 - 23G93) - com.apple.CoreSimulator.SimRuntime.iOS-26-5 (available)"},
                "devices_probe": {"available": True, "output": "iPhone 16 (ABC) (Shutdown)"},
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
                "devices_probe": {"available": True, "output": "iPhone 16 (Shutdown)"},
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


if __name__ == "__main__":
    unittest.main()
