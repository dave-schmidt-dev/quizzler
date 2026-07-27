"""CR-5 / PM-5 guard: every ``tests/test_*.py`` module MUST be wired into
``npm test`` — either through the Playwright bridge array in
``python-suites.spec.js`` OR through the direct ``python3 -m unittest``
call in ``package.json``'s ``test`` script.

The npm-test bridge (``tests/python-suites.spec.js``) runs Python suites
from a *hand-curated* hardcoded array — nothing auto-discovers a new
``tests/test_*.py``. A test file that exists on disk but is missing from
both the array AND the direct call is a silent gate bypass: it looks like
coverage but never runs. This guard turns that process rule into an
enforced failure.

(Some suites that start subprocess servers are intentionally kept only
in the direct call to avoid contention under Playwright's fullyParallel
runner.)
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
SPEC = ROOT / "tests" / "python-suites.spec.js"
PACKAGE_JSON = ROOT / "package.json"

# Matches the array entries, e.g.  "tests.test_lint_packs",
_MODULE_RE = re.compile(r'"(tests\.test_[A-Za-z0-9_]+)"')
# Matches module names in the direct python3 -m unittest call
_UNITTEST_CALL_RE = re.compile(r'(tests\.test_[A-Za-z0-9_]+)')


def _on_disk_modules() -> set[str]:
    """Every tests/test_*.py rendered as its dotted unittest module name."""
    return {f"tests.{p.stem}" for p in TESTS_DIR.glob("test_*.py")}


def _wired_modules() -> set[str]:
    """Module strings currently present in the python-suites.spec.js array."""
    text = SPEC.read_text(encoding="utf-8")
    return set(_MODULE_RE.findall(text))


def _direct_call_modules() -> set[str]:
    """Module strings in the direct ``python3 -m unittest`` call of
    ``package.json``'s ``test`` script."""
    try:
        pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    test_script = pkg.get("scripts", {}).get("test", "")
    return set(_UNITTEST_CALL_RE.findall(test_script))


class SuiteWiringGuardTests(unittest.TestCase):
    def test_every_test_module_is_wired_into_npm_test(self) -> None:
        on_disk = _on_disk_modules()
        wired = _wired_modules()
        direct = _direct_call_modules()
        covered = wired | direct
        missing = sorted(on_disk - covered)
        self.assertEqual(
            missing,
            [],
            "These tests/test_*.py modules exist on disk but are NOT wired "
            "into `npm test` — neither in the python-suites.spec.js array "
            "nor in the direct python3 -m unittest call in package.json. "
            "They never run under `npm test` (silent gate bypass). "
            f"Append each to the array or direct call: {missing}",
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

    def test_direct_call_has_no_phantom_modules(self) -> None:
        """A module in the direct call but with no file on disk would error
        out at the end of ``npm test``."""
        on_disk = _on_disk_modules()
        direct = _direct_call_modules()
        phantom = sorted(direct - on_disk)
        self.assertEqual(
            phantom,
            [],
            "These modules are in the direct python3 -m unittest call in "
            f"package.json but have no matching tests/test_*.py on disk: "
            f"{phantom}",
        )


if __name__ == "__main__":
    unittest.main()
