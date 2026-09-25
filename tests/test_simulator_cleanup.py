"""Hermetic simulator lifecycle tests using a fake ``xcrun`` binary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "app" / "scripts" / "simulator_lifecycle.sh"
WRAPPER = ROOT / "scripts" / "with-ui-simulator.sh"

UDID = "AAAAAAAA-1111-2222-3333-444444444444"


def inventory(*devices: dict[str, str]) -> str:
    """Return the minimal simctl inventory shape consumed by the helper."""
    return json.dumps({"devices": {"iOS 26.5": list(devices)}})


class SimulatorCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = self.root / "xcrun.log"
        self.first = self.root / "first.json"
        self.after = self.root / "after.json"
        self.count = self.root / "list-count"
        self.lock = self.root / "apple-ui-test-lock"
        self.xcrun = self.root / "xcrun"
        self._write_fake_tools()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fake_tools(self) -> None:
        self.xcrun.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_XCRUN_LOG"
if [[ "$*" == *"list devices -j"* ]]; then
  count=0
  [[ -f "$FAKE_XCRUN_COUNT" ]] && count=$(cat "$FAKE_XCRUN_COUNT")
  count=$((count + 1))
  printf '%s' "$count" > "$FAKE_XCRUN_COUNT"
  if [[ "$count" -eq 1 ]]; then cat "$FAKE_XCRUN_FIRST"; else cat "$FAKE_XCRUN_AFTER"; fi
elif [[ "$*" == "simctl create "* ]]; then
  printf '%s\\n' "AAAAAAAA-1111-2222-3333-444444444444"
fi
""",
            encoding="utf-8",
        )
        self.lock.write_text(
            """#!/usr/bin/env bash
set -eu
while [[ "$#" -gt 0 && "$1" != "--" ]]; do shift; done
[[ "${1:-}" == "--" ]] && shift
exec "$@"
""",
            encoding="utf-8",
        )
        for tool in (self.xcrun, self.lock):
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.root}:{env['PATH']}",
                "FAKE_XCRUN_LOG": str(self.log),
                "FAKE_XCRUN_FIRST": str(self.first),
                "FAKE_XCRUN_AFTER": str(self.after),
                "FAKE_XCRUN_COUNT": str(self.count),
                "APPLE_UI_TEST_LOCK": str(self.lock),
                "GATE_XCTEST_DEVICE_SET": str(self.root / "absent-xctest-devices"),
            }
        )
        return env

    def _run_lifecycle(self, first: str, after: str, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
        self.first.write_text(first, encoding="utf-8")
        self.after.write_text(after, encoding="utf-8")
        script = (
            'source "$1"; quizzler_simulator_lifecycle_init; '
            'quizzler_simulator_track_destination "platform=iOS Simulator,id=' + UDID + '" >/dev/null; '
            f"exit {exit_code}"
        )
        return subprocess.run(
            ["bash", "-c", script, "bash", str(LIFECYCLE)],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
        )

    def test_pass_and_failure_restore_a_shutdown_destination(self) -> None:
        shutdown = inventory({"udid": UDID, "name": "iPhone 17", "state": "Shutdown"})
        booted = inventory({"udid": UDID, "name": "iPhone 17", "state": "Booted"})
        for code in (0, 7):
            with self.subTest(exit_code=code):
                self.log.unlink(missing_ok=True)
                self.count.unlink(missing_ok=True)
                result = self._run_lifecycle(shutdown, booted, code)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(f"simctl shutdown {UDID}", self.log.read_text(encoding="utf-8"))

    def test_preserves_a_destination_that_was_booted_at_start(self) -> None:
        booted = inventory({"udid": UDID, "name": "iPhone 17", "state": "Booted"})
        result = self._run_lifecycle(booted, booted)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("simctl shutdown", self.log.read_text(encoding="utf-8"))

    def test_orphan_sweep_is_contained_to_dead_quizzler_gate_devices(self) -> None:
        dead = "BBBBBBBB-1111-2222-3333-444444444444"
        live = "CCCCCCCC-1111-2222-3333-444444444444"
        manual = "DDDDDDDD-1111-2222-3333-444444444444"
        self.first.write_text(
            inventory(
                {"udid": dead, "name": "quizzler-gate-999999-stale", "state": "Booted"},
                {"udid": live, "name": f"quizzler-gate-{os.getpid()}-active", "state": "Shutdown"},
                {"udid": manual, "name": "quizzler-ui-study-controls-20260923", "state": "Booted"},
                {"udid": UDID, "name": "quizzler-gate-manual", "state": "Shutdown"},
            ),
            encoding="utf-8",
        )
        self.after.write_text("{}", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; quizzler_simulator_sweep_orphans', "bash", str(LIFECYCLE)],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")
        log = self.log.read_text(encoding="utf-8")
        self.assertIn(f"simctl shutdown {dead}", log)
        self.assertIn(f"simctl delete {dead}", log)
        for untouched in (live, manual, UDID):
            self.assertNotIn(untouched, "\n".join(line for line in log.splitlines() if "list devices" not in line))

    def test_ad_hoc_wrapper_cleans_up_its_created_simulator_on_pass_and_failure(self) -> None:
        empty = inventory()
        for exit_code in (0, 6):
            with self.subTest(exit_code=exit_code):
                self.log.unlink(missing_ok=True)
                self.count.unlink(missing_ok=True)
                self.first.write_text(empty, encoding="utf-8")
                self.after.write_text(empty, encoding="utf-8")
                command = (
                    'test "$QUIZZLER_UI_SIMULATOR_UDID" = "' + UDID + '"; '
                    f"exit {exit_code}"
                )
                result = subprocess.run(
                    ["bash", str(WRAPPER), "capture", "--", "bash", "-c", command],
                    text=True,
                    capture_output=True,
                    env=self._env(),
                    check=False,
                )
                self.assertEqual(result.returncode, exit_code, result.stderr)
                log = self.log.read_text(encoding="utf-8")
                self.assertIn("simctl create quizzler-gate-", log)
                self.assertIn(f"simctl shutdown {UDID}", log)
                self.assertIn(f"simctl delete {UDID}", log)


if __name__ == "__main__":
    unittest.main()
