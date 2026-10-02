"""Guard that npm test runs every Python unittest suite by discovery."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
PACKAGE_JSON = ROOT / "package.json"


def _on_disk_modules() -> set[str]:
    return {path.stem for path in TESTS_DIR.glob("test_*.py")}


def _discovered_modules() -> set[str]:
    suite = unittest.TestLoader().discover(
        start_dir=str(TESTS_DIR), pattern="test_*.py"
    )
    modules: set[str] = set()
    pending = [suite]
    while pending:
        item = pending.pop()
        if isinstance(item, unittest.TestSuite):
            pending.extend(item)
        else:
            modules.add(item.__class__.__module__)
    return modules


class SuiteWiringGuardTests(unittest.TestCase):
    def test_npm_test_uses_the_discovery_pattern(self) -> None:
        package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            package.get("scripts", {}).get("test"),
            "python3 -m unittest discover -s tests -p 'test_*.py' -v",
        )
        self.assertNotIn("@playwright/test", package.get("devDependencies", {}))

    def test_every_suite_file_is_discovered_without_phantoms(self) -> None:
        self.assertEqual(_discovered_modules(), _on_disk_modules())


if __name__ == "__main__":
    unittest.main()
