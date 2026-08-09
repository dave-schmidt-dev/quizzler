#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/test-gate.sh"
validate_counting_leg_declarations
emit() { printf 'Ran %s tests\n' "$1"; }
absent() { printf 'command completed successfully\n'; }
COUNTING_LEG_RUN_COUNT=0
for ((i=0; i<EXPECTED_COUNTING_LEG_COUNT; i++)); do
  assert_counting_leg "${COUNTING_LEG_NAMES[i]}" emit "${COUNTING_LEG_MINIMUMS[i]}"
done
assert_counting_legs_complete
failures=0
for ((i=0; i<EXPECTED_COUNTING_LEG_COUNT; i++)); do
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" emit 0 >/dev/null 2>&1; then failures=$((failures+1)); fi
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" absent >/dev/null 2>&1; then failures=$((failures+1)); fi
done
if [[ $failures -ne 0 ]]; then echo "FAIL: zero-test self-check accepted a fixture" >&2; exit 1; fi
echo "test-gate self-check passed"
