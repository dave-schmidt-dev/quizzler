"""Unit tests for ``scripts/verifier_profiles.py`` — the registry of approved
certifying verifier profiles used by hybrid certification.

Run from the project root::

    python3 -m unittest tests.test_verifier_profiles -v
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "verifier_profiles.py"

_spec = importlib.util.spec_from_file_location("verifier_profiles", SCRIPT_PATH)
vprof = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verifier_profiles", vprof)
_spec.loader.exec_module(vprof)


class VerifierProfileRegistryTests(unittest.TestCase):
    def assertProfile(self, name, provider, model, effort):
        profile = vprof.get_profile(name)
        self.assertEqual(profile.name, name)
        self.assertEqual(profile.provider, provider)
        self.assertEqual(profile.model, model)
        self.assertEqual(profile.reasoning_effort, effort)

    def test_claude_sonnet_high_is_registered(self):
        self.assertProfile("claude-sonnet-high", "claude", "sonnet", None)

    def test_codex_terra_high_unchanged(self):
        self.assertProfile("codex-terra-high", "codex", "gpt-5.6-terra", "high")

    def test_claude_opus_high_unchanged(self):
        self.assertProfile("claude-opus-high", "claude", "opus", None)

    def test_default_profile_unchanged(self):
        self.assertEqual(vprof.DEFAULT_PROFILE, "codex-terra-high")
        self.assertIn(vprof.DEFAULT_PROFILE, vprof.PROFILES)

    def test_unknown_profile_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            vprof.get_profile("no-such-profile")
        self.assertIn("no-such-profile", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
