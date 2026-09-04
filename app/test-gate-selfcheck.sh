#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source app/test-gate.sh
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

# A changed test-plan snapshot self-heals the local pin rather than hard-failing
# -- a reviewed plan edit must not stop the gate -- but it must rewrite only the
# pin and must record the change. Driven against copies of the gate script,
# project manifest and HISTORY.md: this case used to assert the older
# hard-fail contract and pointed the refresh at the real app/test-gate.sh, so
# every run left the gate repinned to a throwaway fixture and appended a bogus
# HISTORY.md entry.
(
  pin_root=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-pin-fixture.XXXXXX")
  trap 'rm -rf "$pin_root"' EXIT
  mkdir -p "$pin_root/app"
  cp app/test-gate.sh "$pin_root/app/test-gate.sh"
  cp app/project.yml "$pin_root/app/project.yml"
  cp HISTORY.md "$pin_root/HISTORY.md"
  cp app/Quizzler.xctestplan "$pin_root/app/Quizzler.xctestplan"
  printf '\n' >>"$pin_root/app/Quizzler.xctestplan"
  real_gate_before=$(shasum -a 256 app/test-gate.sh | awk '{print $1}')
  drifted=$(shasum -a 256 "$pin_root/app/Quizzler.xctestplan" | awk '{print $1}')

  GATE_ROOT="$pin_root"
  GATE_SELF="$pin_root/app/test-gate.sh"
  XCTESTPLAN_FILE="$pin_root/app/Quizzler.xctestplan"
  validate_pinned_inputs >/dev/null 2>&1 || {
    echo "FAIL: a reviewed test-plan edit did not self-heal the local pin" >&2
    exit 1
  }
  grep -qF "XCTESTPLAN_BASELINE_SHA256=\"$drifted\"" "$pin_root/app/test-gate.sh" || {
    echo "FAIL: self-healed pin did not record the new test-plan digest" >&2
    exit 1
  }
  grep -qF "pin-refresh: xctestplan-pin" "$pin_root/HISTORY.md" || {
    echo "FAIL: self-healed pin was not recorded in HISTORY.md" >&2
    exit 1
  }
  [[ "$(shasum -a 256 app/test-gate.sh | awk '{print $1}')" == "$real_gate_before" ]] || {
    echo "FAIL: the pin self-check rewrote the real gate script" >&2
    exit 1
  }

  # Self-healing covers the digest only. A plan whose declared target set
  # drifted is a contract change and must still be refused.
  jq 'del(.testTargets[0])' "$pin_root/app/Quizzler.xctestplan" >"$pin_root/app/dropped.xctestplan"
  XCTESTPLAN_FILE="$pin_root/app/dropped.xctestplan"
  if validate_pinned_inputs >/dev/null 2>&1; then
    echo "FAIL: XCTest plan with a dropped test target accepted" >&2
    exit 1
  fi
) || exit 1
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
# Simulator clone sweep. xcodebuild's UI-test clones land in
# ~/Library/Developer/XCTestDevices, a device set `simctl list devices` does
# not enumerate, so nothing in this gate could see them: 26 orphans totalling
# 105 GB accumulated over one night in August 2026 before anyone looked at the
# disk. The shared library now sweeps that set; this drives the real sweep
# against a fabricated one. It never invokes xcodebuild and never touches the
# host's own set -- xcrun is stubbed and the set path is overridden, so a
# regression in either guard fails here rather than deleting live devices.
(
  clone_root=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-xctest-devices.XXXXXX")
  trap 'rm -rf "$clone_root"' EXIT
  clone_log="$clone_root/xcrun.log"
  stale_udid=AAAAAAAA-1111-2222-3333-444444444444
  fresh_udid=BBBBBBBB-1111-2222-3333-444444444444
  mkdir -p "$clone_root/$stale_udid/data" "$clone_root/$fresh_udid/data"
  # SetFile is the only way to move a directory's APFS birth time, which is
  # what the sweep reads; without it the age cutoff cannot be exercised.
  /usr/bin/SetFile -d "$(date -v-26H '+%m/%d/%Y %H:%M:%S')" "$clone_root/$stale_udid"
  clone_json=$(jq -cn --arg root "$clone_root" --arg stale "$stale_udid" --arg fresh "$fresh_udid" \
    '{devices:{"com.apple.CoreSimulator.SimRuntime.iOS-26-5":[
       {udid:$stale,name:"Clone 2 of iPhone 17",dataPath:($root+"/"+$stale+"/data"),state:"Shutdown"},
       {udid:$fresh,name:"Clone 2 of iPhone 17",dataPath:($root+"/"+$fresh+"/data"),state:"Shutdown"}]}}')
  xcrun() {
    printf '%s\n' "$*" >>"$clone_log"
    case "$*" in
      *"list devices -j"*) printf '%s' "$clone_json" ;;
    esac
    return 0
  }
  export GATE_XCTEST_DEVICE_SET="$clone_root"
  # shellcheck source=/dev/null
  source "/Users/dave/Documents/Projects/apple_developer/release_tools/templates/simctl_gate_lib.sh"

  swept=$(gate_sweep_xctest_clones)
  [[ "$swept" == "1" ]] || {
    echo "FAIL: clone sweep reported $swept deletions, expected 1" >&2
    exit 1
  }
  # Ordering matters: simctl delete refuses a booted device, and a clone left
  # by a killed run can still be booted.
  stale_calls=$(grep -F "$stale_udid" "$clone_log" || true)
  [[ "$stale_calls" == "simctl --set $clone_root shutdown $stale_udid
simctl --set $clone_root delete $stale_udid" ]] || {
    echo "FAIL: stale clone was not shut down and then deleted (got: $stale_calls)" >&2
    exit 1
  }
  # A concurrently running gate's clone is minutes old and must survive.
  ! grep -qF "$fresh_udid" "$clone_log" || {
    echo "FAIL: clone sweep touched a device inside the age cutoff" >&2
    exit 1
  }

  # The sweep must be automatic. Nothing swept this set for months precisely
  # because it needed a call site no consumer had.
  : >"$clone_log"
  rm -f "${_GATE_LIB_SIM_REGISTRY}".swept.*
  gate_sweep quizzler >/dev/null
  grep -qF -- "--set $clone_root list devices -j" "$clone_log" || {
    echo "FAIL: gate_sweep did not sweep the XCTestDevices set" >&2
    exit 1
  }

  # Per-leg reap. The 24h sweep above bounds how much can accumulate but not
  # how much can exist at once -- the August leak put 26 clones on disk in
  # about four hours, none of them old enough for that cutoff until the next
  # day. gate_ui_test_lock now deletes what a leg created as soon as it ends,
  # which is what actually stops the accumulation; the sweep is the backstop
  # for a run killed before anything after the lock helper can run.
  : >"$clone_log"
  rm -f "${_GATE_LIB_SIM_REGISTRY}".swept.*
  export GATE_XCTEST_DEVICE_SET="$clone_root"
  clone_state="$clone_root/state.json"
  # The listing has to be able to change mid-leg: the reap compares a snapshot
  # taken before the command against one taken after, so a fixed fixture could
  # only ever express "the leg created nothing".
  xcrun() {
    printf '%s\n' "$*" >>"$clone_log"
    case "$*" in
      *"list devices -j"*) cat "$clone_state" ;;
    esac
    return 0
  }
  lock_stub="$clone_root/fake-ui-test-lock"
  cat >"$lock_stub" <<'LOCKSTUB'
#!/bin/bash
[[ "${1:-}" == "--label" ]] && shift 2
[[ "${1:-}" == "--" ]] && shift
exec "$@"
LOCKSTUB
  chmod +x "$lock_stub"
  export APPLE_UI_TEST_LOCK="$lock_stub"
  leg_udid=CCCCCCCC-1111-2222-3333-444444444444
  mkdir -p "$clone_root/$leg_udid/data"
  # Five seconds, not zero: birth times and the reap's end-of-leg stamp are
  # whole seconds and the comparison is strict, so a clone created and reaped
  # inside one second would be spared and this case would flake.
  /usr/bin/SetFile -d "$(date -v-5S '+%m/%d/%Y %H:%M:%S')" "$clone_root/$leg_udid"
  leg_json=$(jq -cn --arg root "$clone_root" --arg udid "$leg_udid" \
    '{devices:{"com.apple.CoreSimulator.SimRuntime.iOS-26-5":[
       {udid:$udid,name:"Clone 2 of iPhone 17",dataPath:($root+"/"+$udid+"/data"),state:"Shutdown"}]}}')
  printf '%s' '{"devices":{}}' >"$clone_state"
  gate_ui_test_lock --label "fixture leg" bash -c "printf '%s' '$leg_json' > '$clone_state'; exit 7"
  leg_rc=$?
  [[ "$leg_rc" == "7" ]] || {
    echo "FAIL: the reap rewrote the leg's exit status ($leg_rc, expected 7)" >&2
    exit 1
  }
  grep -qF -- "--set $clone_root delete $leg_udid" "$clone_log" || {
    echo "FAIL: a clone the leg created survived the leg" >&2
    exit 1
  }
  # A device already present when the leg started is not the leg's to delete.
  : >"$clone_log"
  printf '%s' "$leg_json" >"$clone_state"
  gate_ui_test_lock --label "fixture leg" true
  ! grep -qF -- "delete $leg_udid" "$clone_log" || {
    echo "FAIL: the reap deleted a clone that predated the leg" >&2
    exit 1
  }
  # A Booted device may be in use by a run that does not hold this lock.
  : >"$clone_log"
  booted_json=${leg_json/\"Shutdown\"/\"Booted\"}
  printf '%s' '{"devices":{}}' >"$clone_state"
  gate_ui_test_lock --label "fixture leg" bash -c "printf '%s' '$booted_json' > '$clone_state'"
  ! grep -qF -- "delete $leg_udid" "$clone_log" || {
    echo "FAIL: the reap deleted a booted clone" >&2
    exit 1
  }
  unset -f xcrun
  xcrun() {
    printf '%s\n' "$*" >>"$clone_log"
    case "$*" in
      *"list devices -j"*) printf '%s' "$clone_json" ;;
    esac
    return 0
  }
  unset APPLE_UI_TEST_LOCK

  # An absent set is a no-op, not an error: a machine that has never run an
  # XCUITest has no such directory.
  : >"$clone_log"
  rm -rf "${clone_root:?}/$fresh_udid"
  export GATE_XCTEST_DEVICE_SET="$clone_root/absent"
  swept=$(gate_sweep_xctest_clones)
  [[ "$swept" == "0" ]] || { echo "FAIL: absent clone set reported $swept deletions" >&2; exit 1; }
  [[ ! -s "$clone_log" ]] || { echo "FAIL: absent clone set still called simctl" >&2; exit 1; }

  # Every XCUITest leg must reach the lock through gate_ui_test_lock, never
  # apple-ui-test-lock directly. Both serialize identically; only the wrapper
  # reaps the leg's clones, so the direct form is a silent per-run leak and
  # nothing else here would catch the regression. (It was the live shape in
  # two sibling repositories while this reap was already shipping.)
  grep -qF -- 'gate_ui_test_lock --label' app/test-gate.sh || {
    echo "FAIL: no UI-test leg takes the lock through gate_ui_test_lock" >&2
    exit 1
  }
  if grep -qF -- '"$APPLE_UI_TEST_LOCK" --label' app/test-gate.sh; then
    echo "FAIL: a leg calls apple-ui-test-lock directly and leaks its clones" >&2
    exit 1
  fi
) || exit 1

failures=0
for ((i=0; i<EXPECTED_COUNTING_LEG_COUNT; i++)); do
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" emit 0 >/dev/null 2>&1; then failures=$((failures+1)); fi
  if assert_counting_leg "${COUNTING_LEG_NAMES[i]}" absent >/dev/null 2>&1; then failures=$((failures+1)); fi
done
if [[ $failures -ne 0 ]]; then echo "FAIL: zero-test self-check accepted a fixture" >&2; exit 1; fi
echo "test-gate self-check passed"
