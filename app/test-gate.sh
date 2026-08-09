#!/usr/bin/env bash
set -euo pipefail

EXPECTED_COUNTING_LEG_COUNT=5
COUNTING_LEG_NAMES=("swift-contract" "fixture-isolation" "artifact-metadata" "toolchain-capabilities" "signing-bootstrap")
COUNTING_LEG_REPORTERS=("swift-testing" "pytest" "pytest" "pytest" "pytest")
# These floors are the committed tests each command actually runs. Keep them
# explicit: a zero-test or truncated command must not satisfy a leg.
COUNTING_LEG_MINIMUMS=(6 2 6 3 3)
COUNTING_LEG_RUN_COUNT=0

validate_counting_leg_declarations() {
  local n=${#COUNTING_LEG_NAMES[@]}
  [[ "$n" -eq "$EXPECTED_COUNTING_LEG_COUNT" ]] || { echo "FAIL: counting leg names mismatch" >&2; return 1; }
  [[ ${#COUNTING_LEG_REPORTERS[@]} -eq "$n" && ${#COUNTING_LEG_MINIMUMS[@]} -eq "$n" ]] || { echo "FAIL: counting leg metadata mismatch" >&2; return 1; }
  local floor
  for floor in "${COUNTING_LEG_MINIMUMS[@]}"; do
    [[ "$floor" =~ ^[1-9][0-9]*$ ]] || { echo "FAIL: invalid test floor '$floor'" >&2; return 1; }
  done
}

assert_counting_leg() {
  local name=$1; shift
  local index=-1 i
  for ((i=0; i<${#COUNTING_LEG_NAMES[@]}; i++)); do [[ ${COUNTING_LEG_NAMES[i]} == "$name" ]] && index=$i; done
  [[ $index -ge 0 ]] || { echo "FAIL: undeclared counting leg '$name'" >&2; return 1; }
  local out status count
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-gate.XXXXXX")
  # Keep the transcript for count validation while teeing it live. This is
  # required for Xcode/toolchain legs, which can otherwise appear stalled.
  if "$@" 2>&1 | tee "$out"; then status=${PIPESTATUS[0]}; else status=${PIPESTATUS[0]}; fi
  if [[ $status -ne 0 ]]; then rm -f "$out"; echo "FAIL: $name exited $status" >&2; return $status; fi
  case "${COUNTING_LEG_REPORTERS[index]}" in
    swift-testing)
      count=$(grep -Eo '[0-9]+ tests?' "$out" | awk '{print $1}' | sort -n | tail -1 || true)
      ;;
    pytest)
      count=$(grep -Eo 'Ran [0-9]+ tests?' "$out" | awk '{print $2}' | sort -n | tail -1 || true)
      ;;
  esac
  rm -f "$out"
  [[ -n "$count" ]] || { echo "FAIL: $name emitted no count" >&2; return 1; }
  [[ $count -ge ${COUNTING_LEG_MINIMUMS[index]} ]] || { echo "FAIL: $name count $count below ${COUNTING_LEG_MINIMUMS[index]}" >&2; return 1; }
  COUNTING_LEG_RUN_COUNT=$((COUNTING_LEG_RUN_COUNT + 1))
}

assert_counting_legs_complete() { [[ $COUNTING_LEG_RUN_COUNT -eq $EXPECTED_COUNTING_LEG_COUNT ]] || { echo "FAIL: incomplete counting legs" >&2; return 1; }; }

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  cd "$(dirname "$0")/.."
  validate_counting_leg_declarations
  echo "==> Swift contract package"
  assert_counting_leg swift-contract swift test --disable-sandbox --scratch-path "${TMPDIR:-/tmp}/quizzler-swiftpm" --package-path app/QuizzlerKit
  echo "==> fixture isolation"
  assert_counting_leg fixture-isolation python3 app/scripts/test-release-fixture-isolation.py
  echo "==> artifact metadata"
  assert_counting_leg artifact-metadata python3 app/scripts/test_artifact_metadata.py
  echo "==> toolchain capabilities"
  assert_counting_leg toolchain-capabilities python3 app/scripts/test_toolchain_capabilities.py
  echo "==> signing bootstrap"
  assert_counting_leg signing-bootstrap python3 app/scripts/test_provision_signing.py
  assert_counting_legs_complete
  echo "test-gate passed ($COUNTING_LEG_RUN_COUNT counted legs)"
fi
