#!/usr/bin/env bash
set -euo pipefail

# Attended release-milestone step: run the Mac Catalyst UI tests (tab bar and
# session keyboard commands). These seize the screen, so no gate or git hook
# calls this script; SessionCommandsTests fails if test-gate.sh ever names them.
# Usage: scripts/mac_milestone_ui_tests.sh
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
mkdir -p .logs
log=".logs/mac-milestone-ui-$(date +%Y%m%d-%H%M%S).log"
echo "==> Mac Catalyst milestone UI tests (log: $log)"
echo "    This drives the real Mac UI; leave the keyboard and mouse alone until it finishes."
set +e
QUIZZLER_MAC_MILESTONE=1 TEST_RUNNER_QUIZZLER_MAC_MILESTONE=1 \
  "$ROOT/app/scripts/xcb" test \
    -project app/Quizzler.xcodeproj \
    -scheme Quizzler \
    -testPlan Quizzler \
    -destination "platform=macOS,variant=Mac Catalyst" \
    -only-testing:QuizzleriOSUITests/MacCatalystUITests \
    2>&1 | tee "$log" | grep --line-buffered -E 'Test Case|error:|Executed [0-9]+ tests|BUILD'
status=${PIPESTATUS[0]}
set -e
echo "==> exit $status"
exit "$status"
