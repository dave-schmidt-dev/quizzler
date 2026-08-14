#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/test-gate.sh"
validate_pinned_inputs
validate_counting_leg_declarations
validate_sync_phase_declarations
emit() { printf 'Ran %s tests\n' "$1"; }
absent() { printf 'command completed successfully\n'; }
COUNTING_LEG_RUN_COUNT=0
for ((i=0; i<EXPECTED_COUNTING_LEG_COUNT; i++)); do
  assert_counting_leg "${COUNTING_LEG_NAMES[i]}" emit "${COUNTING_LEG_MINIMUMS[i]}"
done
assert_counting_legs_complete

# Metadata mutations must fail before any command is trusted.
saved_leg_names=("${COUNTING_LEG_NAMES[@]}")
COUNTING_LEG_NAMES=("${COUNTING_LEG_NAMES[@]:0:${#COUNTING_LEG_NAMES[@]}-1}")
if validate_counting_leg_declarations >/dev/null 2>&1; then
  echo "FAIL: omitted counting-leg declaration accepted" >&2
  exit 1
fi
COUNTING_LEG_NAMES=("${saved_leg_names[@]}")
saved_reporters=("${COUNTING_LEG_REPORTERS[@]}")
COUNTING_LEG_REPORTERS[0]=unsupported
if validate_counting_leg_declarations >/dev/null 2>&1; then
  echo "FAIL: unsupported counting-leg reporter accepted" >&2
  exit 1
fi
COUNTING_LEG_REPORTERS=("${saved_reporters[@]}")

saved_sync_suites=("${SYNC_TEST_SUITES[@]}")
SYNC_TEST_SUITES=("${SYNC_TEST_SUITES[@]:0:${#SYNC_TEST_SUITES[@]}-1}")
if validate_sync_phase_declarations >/dev/null 2>&1; then
  echo "FAIL: omitted sync-phase suite accepted" >&2
  exit 1
fi
SYNC_TEST_SUITES=("${saved_sync_suites[@]}")

# A producer that emits a plausible count and then fails must remain red.
producer_fails() { printf 'Ran 99 tests\n'; return 9; }
if assert_counting_leg swift-contract producer_fails >/dev/null 2>&1; then
  echo "FAIL: masked producer exit accepted" >&2
  exit 1
fi

# A changed test-plan snapshot is a stale baseline, not a new green contract.
stale_plan=$(mktemp "${TMPDIR:-/tmp}/quizzler-stale-plan.XXXXXX")
cp app/Quizzler.xctestplan "$stale_plan"
printf '\n' >>"$stale_plan"
XCTESTPLAN_FILE="$stale_plan"
if validate_pinned_inputs >/dev/null 2>&1; then
  rm -f "$stale_plan"
  echo "FAIL: stale XCTest plan baseline accepted" >&2
  exit 1
fi
rm -f "$stale_plan"
XCTESTPLAN_FILE=app/Quizzler.xctestplan
expected_accessibility_count=$(accessibility_expected_test_count)
[[ "$expected_accessibility_count" -eq "$ACCESSIBILITY_TEST_CASE_COUNT" ]] || {
  echo "FAIL: accessibility expected-count self-check mismatch" >&2
  exit 1
}
simctl_fixture='{"devices":{"iOS 18.0":[{"name":"iPhone 17","udid":"FCEE0000-0000-0000-0000-000000000001"},{"name":"iPad Pro 13-inch","udid":"FCEE0000-0000-0000-0000-000000000002"}]}}'
[[ "$(QUIZZLER_ACCESSIBILITY_SIMCTL_DEVICES_JSON="$simctl_fixture" accessibility_destination_class 'platform=iOS Simulator,id=FCEE0000-0000-0000-0000-000000000001')" == iphone ]] || {
  echo "FAIL: iPhone simulator ID classification self-check failed" >&2
  exit 1
}
[[ "$(QUIZZLER_ACCESSIBILITY_SIMCTL_DEVICES_JSON="$simctl_fixture" accessibility_destination_class 'platform=iOS Simulator,id=FCEE0000-0000-0000-0000-000000000002')" == ipad ]] || {
  echo "FAIL: iPad simulator ID classification self-check failed" >&2
  exit 1
}
if QUIZZLER_ACCESSIBILITY_SIMCTL_DEVICES_JSON="$simctl_fixture" accessibility_destination_class 'platform=iOS Simulator,id=FCEE0000-0000-0000-0000-000000000099' >/dev/null 2>&1; then
  echo "FAIL: unknown simulator ID classification self-check accepted a fixture" >&2
  exit 1
fi
receipt_fixture_root=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-accessibility-receipt.XXXXXX")
contract_fixture_root=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-contract-probe.XXXXXX")
trap 'rm -rf "$receipt_fixture_root" "$contract_fixture_root"' EXIT

# Contract safety checks are executable, not just source-string assertions.
# Missing attended inputs must fail before codesign/xcodebuild can be reached.
if (unset QUIZZLER_DEVELOPMENT_PROBE_RUN QUIZZLER_DEVELOPMENT_PROBE_DESTINATION \
    QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN QUIZZLER_DEVELOPMENT_PROBE_XCRESULT \
    QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP; run_signed_contract_probe >/dev/null 2>&1); then
  echo "FAIL: contract probe accepted missing attended opt-in" >&2
  exit 1
fi
if (export QUIZZLER_DEVELOPMENT_PROBE_RUN=1; unset QUIZZLER_DEVELOPMENT_PROBE_DESTINATION; \
    run_signed_contract_probe >/dev/null 2>&1); then
  echo "FAIL: contract probe accepted missing destination" >&2
  exit 1
fi
mkdir -p "$contract_fixture_root/Signed.app" "$contract_fixture_root/existing.xcresult"
printf '%s' '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>QuizzleriOSUITests</key><dict><key>UITargetAppPath</key><string>__TESTROOT__/Signed.app</string></dict></dict></plist>' >"$contract_fixture_root/Quizzler.xctestrun"
probe_call_marker="$contract_fixture_root/called"
if (
  xcodebuild() { : >"$probe_call_marker"; return 91; }
  codesign() { : >"$probe_call_marker"; return 92; }
  export QUIZZLER_DEVELOPMENT_PROBE_RUN=1
  export QUIZZLER_DEVELOPMENT_PROBE_DESTINATION='platform=iOS Simulator,id=SELF-CHECK'
  export QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN="$contract_fixture_root/Quizzler.xctestrun"
  export QUIZZLER_DEVELOPMENT_PROBE_XCRESULT="$contract_fixture_root/existing.xcresult"
  export QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP="$contract_fixture_root/Signed.app"
  run_signed_contract_probe >/dev/null 2>&1
); then
  echo "FAIL: contract probe accepted stale result bundle" >&2
  exit 1
fi
[[ ! -e "$probe_call_marker" ]] || {
  echo "FAIL: stale-result refusal reached codesign/xcodebuild" >&2
  exit 1
}

mkdir -p "$receipt_fixture_root/iphone-1.xcresult" "$receipt_fixture_root/iphone-2.xcresult" "$receipt_fixture_root/iphone-3.xcresult"
valid_receipt_entry=$(jq -cn --arg root "$receipt_fixture_root" \
  '[{destination_class:"iphone",sample_count:3,expected_test_count:10,observed_test_counts:[10,10,10],outcome:"passed",xcresult_paths:[($root+"/iphone-1.xcresult"),($root+"/iphone-2.xcresult"),($root+"/iphone-3.xcresult")]}]')
write_accessibility_receipt "$receipt_fixture_root/valid.json" "$valid_receipt_entry" >/dev/null
jq -e 'length == 1 and .[0].outcome == "passed" and (.[0].timestamp | strings | length > 0)' "$receipt_fixture_root/valid.json" >/dev/null || {
  echo "FAIL: valid accessibility receipt self-check failed" >&2
  exit 1
}
for invalid_receipt_entry in \
  ' [{"destination_class":"iphone","sample_count":3,"expected_test_count":10,"observed_test_counts":[10,10,10],"outcome":"failed","xcresult_paths":[]}]' \
  ' [{"destination_class":"iphone","sample_count":0,"expected_test_count":10,"observed_test_counts":[],"outcome":"passed","xcresult_paths":[]}]' \
  ' [{"destination_class":"iphone","sample_count":3,"expected_test_count":10,"observed_test_counts":[10,10],"outcome":"passed","xcresult_paths":[]}]'; do
  invalid_receipt_path=$(mktemp "$receipt_fixture_root/invalid.XXXXXX")
  rm -f "$invalid_receipt_path"
  if write_accessibility_receipt "$invalid_receipt_path" "$invalid_receipt_entry" >/dev/null 2>&1; then
    echo "FAIL: invalid accessibility receipt self-check accepted a fixture" >&2
    exit 1
  fi
  [[ ! -e "$invalid_receipt_path" ]] || {
    echo "FAIL: rejected accessibility receipt left an output file" >&2
    exit 1
  }
done
failures=0
for ((i=0; i<EXPECTED_COUNTING_LEG_COUNT; i++)); do
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" emit 0 >/dev/null 2>&1; then failures=$((failures+1)); fi
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" absent >/dev/null 2>&1; then failures=$((failures+1)); fi
done
if [[ $failures -ne 0 ]]; then echo "FAIL: zero-test self-check accepted a fixture" >&2; exit 1; fi
echo "test-gate self-check passed"
