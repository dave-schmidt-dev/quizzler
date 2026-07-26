"""CR-5 / PM-5 guard: every ``tests/test_*.py`` module MUST be wired into the
``python-suites.spec.js`` array so it actually runs under ``npm test``.

The npm-test bridge (``tests/python-suites.spec.js``) runs Python suites from a
*hand-curated* hardcoded array of module names — nothing auto-discovers a new
``tests/test_*.py``. A test file that exists on disk but is missing from that
array is a silent gate bypass: it looks like coverage but never runs. This guard
turns that process rule into an enforced failure — if any on-disk test module is
absent from the array, ``npm test`` goes red here.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
SPEC = ROOT / "tests" / "python-suites.spec.js"

# Matches the array entries, e.g.  "tests.test_lint_packs",
_MODULE_RE = re.compile(r'"(tests\.test_[A-Za-z0-9_]+)"')


def _on_disk_modules() -> set[str]:
    """Every tests/test_*.py rendered as its dotted unittest module name."""
    return {f"tests.{p.stem}" for p in TESTS_DIR.glob("test_*.py")}


def _wired_modules() -> set[str]:
    """Module strings currently present in the python-suites.spec.js array."""
    text = SPEC.read_text(encoding="utf-8")
    return set(_MODULE_RE.findall(text))


class SuiteWiringGuardTests(unittest.TestCase):
    def test_every_test_module_is_wired_into_npm_test(self) -> None:
        on_disk = _on_disk_modules()
        wired = _wired_modules()
        missing = sorted(on_disk - wired)
        self.assertEqual(
            missing,
            [],
            "These tests/test_*.py modules exist on disk but are NOT wired into "
            f"the array in {SPEC.relative_to(ROOT)} — they never run under "
            "`npm test` (silent gate bypass). Append each to the array in the "
            f"same commit as the test file: {missing}",
        )

    def test_spec_array_has_no_phantom_modules(self) -> None:
        """A module wired in the array but with no file on disk would make the
        Playwright bridge error out; catch that drift here with a clear message."""
        on_disk = _on_disk_modules()
        wired = _wired_modules()
        phantom = sorted(wired - on_disk)
        self.assertEqual(
            phantom,
            [],
            "These modules are wired in python-suites.spec.js but have no "
            f"matching tests/test_*.py on disk: {phantom}",
        )


if __name__ == "__main__":
    unittest.main()
