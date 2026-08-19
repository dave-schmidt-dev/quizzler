#!/usr/bin/env bash
set -euo pipefail

GATE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GATE_VERSION=1
EXPECTED_COUNTING_LEG_COUNT=8
COUNTING_LEG_NAMES=("swift-contract" "fixture-isolation" "artifact-metadata" "toolchain-capabilities" "signing-bootstrap" "development-probe-evidence" "release-workflow" "runner-manifest")
COUNTING_LEG_REPORTERS=("swift-testing" "pytest" "pytest" "pytest" "pytest" "pytest" "pytest" "pytest")
# These floors are the committed tests each command actually runs. Keep them
# explicit: a zero-test or truncated command must not satisfy a leg.
COUNTING_LEG_MINIMUMS=(6 2 10 3 3 4 127 6)
COUNTING_LEG_RUN_COUNT=0

# Phase 3 has its own bounded evidence surface.  Keep the suite names explicit
# so a later generic SwiftPM run cannot silently replace the convergence gate.
SYNC_TEST_SUITES=("CloudProgressRepositoryTests" "CloudKitMappingTests" "ProgressMergeTests" "SyncRecoveryTests" "MigrationReconciliationTests")
SYNC_TEST_MINIMUM=73

# These inputs are intentionally pinned. A changed test plan or floating
# toolchain pin must produce a reviewed gate change instead of silently
# changing what the gate certifies.
XCODE_VERSION_FILE=${QUIZZLER_XCODE_VERSION_FILE:-$GATE_ROOT/app/.xcode-version}
SIMULATOR_VERSION_FILE=${QUIZZLER_SIMULATOR_VERSION_FILE:-$GATE_ROOT/app/.simulator-version}
XCTESTPLAN_FILE=${QUIZZLER_XCTESTPLAN_FILE:-$GATE_ROOT/app/Quizzler.xctestplan}
XCODE_VERSION_BASELINE_SHA256="ca8b4a056d015faa6b485aaa40c2b6fa70d88acb60245595c2e36d9115b61dde"
SIMULATOR_VERSION_BASELINE_SHA256="25205f2e2f02dc71036ee827e19c49b893b231a3d1af240e35a3ac55aa8cdcb6"
XCTESTPLAN_BASELINE_SHA256="67016f85b940efef4870339e2deb95db1646c20db5cbe909c47ccd7f46c03d67"

validate_pinned_inputs() {
  local xcode_version simulator_version plan_hash project_xcode_version
  [[ -f "$XCODE_VERSION_FILE" && -f "$SIMULATOR_VERSION_FILE" && -f "$XCTESTPLAN_FILE" ]] || {
    echo "FAIL: pinned Xcode/runtime/test-plan input is absent" >&2
    return 1
  }
  xcode_version=$(tr -d '[:space:]' <"$XCODE_VERSION_FILE")
  simulator_version=$(tr -d '[:space:]' <"$SIMULATOR_VERSION_FILE")
  [[ "$xcode_version" =~ ^[0-9]+\.[0-9]+$ && "$simulator_version" =~ ^[0-9]+\.[0-9]+$ ]] || {
    echo "FAIL: Xcode and simulator pins must be concrete major.minor versions" >&2
    return 1
  }
  project_xcode_version=$(sed -n 's/^  xcodeVersion: *"\([^"]*\)".*$/\1/p' "$GATE_ROOT/app/project.yml" | head -1)
  [[ "$project_xcode_version" == "$xcode_version" ]] || {
    echo "FAIL: project.yml Xcode pin ($project_xcode_version) differs from $XCODE_VERSION_FILE ($xcode_version)" >&2
    return 1
  }
  [[ "$(shasum -a 256 "$XCODE_VERSION_FILE" | awk '{print $1}')" == "$XCODE_VERSION_BASELINE_SHA256" ]] || {
    echo "FAIL: Xcode version pin baseline is stale" >&2
    return 1
  }
  [[ "$(shasum -a 256 "$SIMULATOR_VERSION_FILE" | awk '{print $1}')" == "$SIMULATOR_VERSION_BASELINE_SHA256" ]] || {
    echo "FAIL: simulator runtime pin baseline is stale" >&2
    return 1
  }
  plan_hash=$(shasum -a 256 "$XCTESTPLAN_FILE" | awk '{print $1}')
  [[ "$plan_hash" == "$XCTESTPLAN_BASELINE_SHA256" ]] || {
    echo "FAIL: XCTest plan baseline is stale; review target/configuration changes" >&2
    return 1
  }
  jq -e '
    (.configurations | type == "array" and length == 1) and
    (.testTargets | type == "array" and
      (map(.target.name) | sort) == ["QuizzlerKitTests", "QuizzlerSnapshotTests", "QuizzleriOSTests", "QuizzleriOSUITests"])
  ' "$XCTESTPLAN_FILE" >/dev/null || {
    echo "FAIL: XCTest plan target/configuration contract drifted" >&2
    return 1
  }
}

verify_contract_evidence() {
  local evidence_path=${QUIZZLER_DEVELOPMENT_PROBE_EVIDENCE_PATH:-app/releases/evidence/development-cloudkit-probe.json}
  : "${QUIZZLER_DEVELOPMENT_PROBE_XCRESULT:?QUIZZLER_DEVELOPMENT_PROBE_XCRESULT must identify the attended XCTest result bundle}"
  : "${QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP:?QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP must identify the signed Debug app bundle}"
  python3 app/scripts/development_probe_evidence.py --verify --evidence-path "$evidence_path" \
    --xcresult "$QUIZZLER_DEVELOPMENT_PROBE_XCRESULT" --signed-app "$QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP"
}

run_signed_contract_probe() {
  : "${QUIZZLER_DEVELOPMENT_PROBE_RUN:?set QUIZZLER_DEVELOPMENT_PROBE_RUN=1 for the attended signed Development probe}"
  [[ "$QUIZZLER_DEVELOPMENT_PROBE_RUN" == 1 ]] || {
    echo "FAIL: signed Development probe requires QUIZZLER_DEVELOPMENT_PROBE_RUN=1" >&2
    return 1
  }
  : "${QUIZZLER_DEVELOPMENT_PROBE_DESTINATION:?QUIZZLER_DEVELOPMENT_PROBE_DESTINATION must name the attended device/simulator explicitly}"
  : "${QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN:?QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN must be the reviewed XCTest run specification}"
  : "${QUIZZLER_DEVELOPMENT_PROBE_XCRESULT:?QUIZZLER_DEVELOPMENT_PROBE_XCRESULT must be a new result-bundle path}"
  : "${QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP:?QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP must identify the exact signed Debug app bundle}"

  local xctestrun=$QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN
  local result_bundle=$QUIZZLER_DEVELOPMENT_PROBE_XCRESULT
  local signed_app=$QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP
  [[ -f "$xctestrun" && ! -L "$xctestrun" ]] || { echo "FAIL: XCTest run specification is absent or symlinked" >&2; return 1; }
  [[ -d "$signed_app" && ! -L "$signed_app" ]] || { echo "FAIL: exact signed app bundle is absent or symlinked" >&2; return 1; }
  [[ ! -e "$result_bundle" && ! -L "$result_bundle" ]] || { echo "FAIL: refusing to reuse an existing probe result bundle" >&2; return 1; }
  local result_parent=${result_bundle%/*}
  [[ "$result_parent" != "$result_bundle" ]] || result_parent=.
  [[ -d "$result_parent" ]] || { echo "FAIL: probe result parent directory is absent" >&2; return 1; }
  python3 app/scripts/resolve_xctestrun_app.py --xctestrun "$xctestrun" --signed-app "$signed_app" >/dev/null || {
    echo "FAIL: XCTest run specification is not safely bound to the exact signed app" >&2
    return 1
  }
  codesign --verify --deep --strict "$signed_app" >/dev/null 2>&1 || {
    echo "FAIL: exact signed app failed codesign verification" >&2
    return 1
  }

  # xcodebuild does not forward the caller's environment into the XCTest
  # target. Bind only exact attended opt-ins to a disposable plist beside the
  # reviewed run file so __TESTROOT__ remains bound to the signed app.
  local xctestrun_parent=${xctestrun%/*}
  [[ "$xctestrun_parent" != "$xctestrun" ]] || xctestrun_parent=.
  local bound_placeholder bound_xctestrun
  bound_placeholder=$(mktemp "$xctestrun_parent/.quizzler-contract-xctestrun.XXXXXX") || {
    echo "FAIL: could not create disposable bound XCTest run" >&2
    return 1
  }
  bound_xctestrun="${bound_placeholder}.xctestrun"
  if ! mv "$bound_placeholder" "$bound_xctestrun"; then
    rm -f "$bound_placeholder" "$bound_xctestrun"
    echo "FAIL: could not name disposable bound XCTest run" >&2
    return 1
  fi
  local -a bind_args=(
    --xctestrun "$xctestrun"
    --output "$bound_xctestrun"
    --signed-app "$signed_app"
  )
  if [[ "${QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE:-}" == enabled ]]; then
    bind_args+=(--live-probe)
  fi
  [[ "${QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE_RECOVERY:-}" == enabled ]] && bind_args+=(--recovery-probe)
  if ! python3 app/scripts/bind_development_probe_xctestrun.py "${bind_args[@]}" >/dev/null; then
    rm -f "$bound_xctestrun"
    echo "FAIL: XCTest run environment could not be safely bound to the signed Development probe" >&2
    return 1
  fi
  xctestrun=$bound_xctestrun

  local out status count
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-contract-probe.XXXXXX")
  echo "==> Signed Development probe (target: QuizzleriOSUITests/CloudKitDevelopmentProbeTests; destination: $QUIZZLER_DEVELOPMENT_PROBE_DESTINATION)"
  set +e
  xcodebuild test-without-building \
    -xctestrun "$xctestrun" \
    -destination "$QUIZZLER_DEVELOPMENT_PROBE_DESTINATION" \
    -resultBundlePath "$result_bundle" \
    -only-testing:QuizzleriOSUITests/CloudKitDevelopmentProbeTests 2>&1 | tee "$out"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  local tee_status=${pipeline_status[1]}
  if [[ $status -ne 0 ]]; then rm -f "$out" "$bound_xctestrun"; echo "FAIL: signed Development probe exited $status" >&2; return "$status"; fi
  if [[ $tee_status -ne 0 ]]; then rm -f "$out" "$bound_xctestrun"; echo "FAIL: signed Development probe transcript failed (tee exited $tee_status)" >&2; return "$tee_status"; fi
  grep -F "CloudKitDevelopmentProbeTests" "$out" >/dev/null || { rm -f "$out" "$bound_xctestrun"; echo "FAIL: probe output omitted the required XCTest target" >&2; return 1; }
  count=$(grep -Eo 'Executed [0-9]+ tests?' "$out" | awk '{print $2}' | sort -n | tail -1 || true)
  rm -f "$out"
  [[ -n "$count" && "$count" -gt 0 ]] || { rm -f "$bound_xctestrun"; echo "FAIL: signed Development probe emitted no positive XCTest count" >&2; return 1; }
  [[ -d "$result_bundle" && ! -L "$result_bundle" ]] || { rm -f "$bound_xctestrun"; echo "FAIL: signed Development probe did not produce its result bundle" >&2; return 1; }
  echo "signed Development probe launched ($count tests); verifying bound evidence"
  if ! verify_contract_evidence; then
    rm -f "$bound_xctestrun"
    return 1
  fi
  rm -f "$bound_xctestrun"
}

validate_counting_leg_declarations() {
  local n=${#COUNTING_LEG_NAMES[@]}
  [[ "$n" -eq "$EXPECTED_COUNTING_LEG_COUNT" ]] || { echo "FAIL: counting leg names mismatch" >&2; return 1; }
  [[ ${#COUNTING_LEG_REPORTERS[@]} -eq "$n" && ${#COUNTING_LEG_MINIMUMS[@]} -eq "$n" ]] || { echo "FAIL: counting leg metadata mismatch" >&2; return 1; }
  local floor
  for floor in "${COUNTING_LEG_MINIMUMS[@]}"; do
    [[ "$floor" =~ ^[1-9][0-9]*$ ]] || { echo "FAIL: invalid test floor '$floor'" >&2; return 1; }
  done
  local reporter
  for reporter in "${COUNTING_LEG_REPORTERS[@]}"; do
    [[ "$reporter" == "swift-testing" || "$reporter" == "pytest" ]] || {
      echo "FAIL: unsupported counting-leg reporter '$reporter'" >&2
      return 1
    }
  done
}

assert_counting_leg() {
  local name=$1; shift
  local index=-1 i
  for ((i=0; i<${#COUNTING_LEG_NAMES[@]}; i++)); do [[ ${COUNTING_LEG_NAMES[i]} == "$name" ]] && index=$i; done
  [[ $index -ge 0 ]] || { echo "FAIL: undeclared counting leg '$name'" >&2; return 1; }
  local out status count
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-gate.XXXXXX")
  # Keep the transcript for count validation while teeing it live. Capture
  # both sides explicitly: a pipe must never turn a failed producer green.
  set +e
  "$@" 2>&1 | tee "$out"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  local tee_status=${pipeline_status[1]}
  if [[ $status -ne 0 ]]; then rm -f "$out"; echo "FAIL: $name exited $status" >&2; return $status; fi
  if [[ $tee_status -ne 0 ]]; then rm -f "$out"; echo "FAIL: $name transcript failed (tee exited $tee_status)" >&2; return "$tee_status"; fi
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

validate_sync_phase_declarations() {
  local expected=("CloudProgressRepositoryTests" "CloudKitMappingTests" "ProgressMergeTests" "SyncRecoveryTests" "MigrationReconciliationTests")
  [[ ${#SYNC_TEST_SUITES[@]} -eq ${#expected[@]} && "$SYNC_TEST_MINIMUM" =~ ^[1-9][0-9]*$ ]] || {
    echo "FAIL: sync phase declaration is incomplete" >&2
    return 1
  }
  local i
  for ((i=0; i<${#expected[@]}; i++)); do
    [[ ${SYNC_TEST_SUITES[i]} == "${expected[i]}" ]] || {
      echo "FAIL: sync phase suite declaration drifted" >&2
      return 1
    }
  done
}

run_sync_phase() {
  validate_sync_phase_declarations
  local filter out status count
  filter=$(IFS='|'; printf '%s' "${SYNC_TEST_SUITES[*]}")
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-sync-phase.XXXXXX")
  echo "==> Sync phase (Swift convergence suites)"
  set +e
  swift test --disable-sandbox --scratch-path "${TMPDIR:-/tmp}/quizzler-sync-phase" \
    --package-path app/QuizzlerKit --filter "$filter" 2>&1 | tee "$out"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  local tee_status=${pipeline_status[1]}
  if [[ $status -ne 0 ]]; then rm -f "$out"; echo "FAIL: sync phase exited $status" >&2; return "$status"; fi
  if [[ $tee_status -ne 0 ]]; then rm -f "$out"; echo "FAIL: sync phase transcript failed (tee exited $tee_status)" >&2; return "$tee_status"; fi
  count=$(grep -Eo 'Executed [0-9]+ tests?' "$out" | awk '{print $2}' | sort -n | tail -1 || true)
  rm -f "$out"
  [[ -n "$count" && "$count" -ge "$SYNC_TEST_MINIMUM" ]] || {
    echo "FAIL: sync phase count ${count:-0} below $SYNC_TEST_MINIMUM" >&2
    return 1
  }
  echo "sync phase passed ($count tests)"
}

ACCESSIBILITY_TEST_CASE_COUNT=11
ACCESSIBILITY_RECEIPT_ENTRIES=()

accessibility_destination_class() {
  local destination=$1 normalized component device_id simulator_json device_name
  normalized=$(printf '%s' "$destination" | tr '[:upper:]' '[:lower:]')
  case "$normalized" in
    *ipad*) printf 'ipad\n' ;;
    *iphone*) printf 'iphone\n' ;;
    *)
      device_id=
      IFS=',' read -r -a destination_components <<<"$destination"
      for component in "${destination_components[@]}"; do
        if [[ "$component" == id=* ]]; then
          device_id=${component#id=}
          break
        fi
      done
      [[ "$device_id" =~ ^[[:alnum:]-]+$ ]] || {
        echo "FAIL: accessibility destination must identify an iPhone or iPad" >&2
        return 1
      }
      if [[ -n "${QUIZZLER_ACCESSIBILITY_SIMCTL_DEVICES_JSON:-}" ]]; then
        simulator_json=$QUIZZLER_ACCESSIBILITY_SIMCTL_DEVICES_JSON
      elif ! simulator_json=$(xcrun simctl list devices -j); then
        echo "FAIL: could not resolve accessibility simulator destination ID: $device_id" >&2
        return 1
      fi
      if ! device_name=$(jq -er --arg id "$device_id" '
        [.devices[]?[]? | select(.udid == $id) | .name | strings] | unique |
        if length == 1 then .[0] else error("simulator ID is unknown or ambiguous") end
      ' <<<"$simulator_json"); then
        echo "FAIL: accessibility simulator destination ID is unknown: $device_id" >&2
        return 1
      fi
      case "$(printf '%s' "$device_name" | tr '[:upper:]' '[:lower:]')" in
        *ipad*) printf 'ipad\n' ;;
        *iphone*) printf 'iphone\n' ;;
        *)
          echo "FAIL: accessibility simulator destination is not an iPhone or iPad: $device_name" >&2
          return 1
          ;;
      esac
      ;;
  esac
}

write_accessibility_receipt() {
  local receipt_path=$1 entries_json=$2 receipt_dir tmp timestamp
  [[ -n "$receipt_path" ]] || { echo "FAIL: accessibility receipt path is empty" >&2; return 1; }
  [[ ! -L "$receipt_path" ]] || { echo "FAIL: accessibility receipt path is symlinked: $receipt_path" >&2; return 1; }
  receipt_dir=${receipt_path%/*}
  [[ "$receipt_dir" != "$receipt_path" ]] || receipt_dir=.
  [[ -d "$receipt_dir" ]] || { echo "FAIL: accessibility receipt directory is absent: $receipt_dir" >&2; return 1; }
  timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  tmp=$(mktemp "$receipt_path.tmp.XXXXXX")
  if ! jq -e -c --arg timestamp "$timestamp" '
    . as $entries |
    if ($entries | type == "array" and length > 0 and
    all(.[];
      . as $item |
      (type == "object") and
      (($item | keys | sort) == ["destination_class", "expected_test_count", "observed_test_counts", "outcome", "sample_count", "xcresult_paths"]) and
      ($item.destination_class | type == "string" and (. == "iphone" or . == "ipad")) and
      ($item.sample_count | type == "number" and floor == . and . >= 1) and
      ($item.expected_test_count | type == "number" and floor == . and . >= 1) and
      ($item.observed_test_counts | type == "array") and
      ($item.xcresult_paths | type == "array") and
      ($item.outcome == "passed") and
      ($item.observed_test_counts | length == $item.sample_count) and
      ($item.xcresult_paths | length == $item.sample_count) and
      (all($item.observed_test_counts[]; type == "number" and floor == . and . == $item.expected_test_count))
    ) and
    ([$entries[].destination_class] | length == (unique | length)))
    then $entries | map(. + {timestamp: $timestamp})
    else error("invalid accessibility receipt")
    end
  ' <<<"$entries_json" >"$tmp"; then
    rm -f "$tmp"
    echo "FAIL: accessibility receipt would not prove a complete passing run" >&2
    return 1
  fi
  # The jq validation above is intentionally strict; check the result paths in
  # the shell because jq cannot prove that an xcresult directory is retained.
  local result_path
  while IFS= read -r result_path; do
    [[ -d "$result_path" && ! -L "$result_path" ]] || {
      rm -f "$tmp"
      echo "FAIL: accessibility receipt references an absent or symlinked xcresult: $result_path" >&2
      return 1
    }
  done < <(jq -er '.[] | .xcresult_paths[] | strings' <<<"$entries_json")
  mv "$tmp" "$receipt_path"
  echo "accessibility receipt written: $receipt_path"
}

accessibility_expected_test_count() {
  local configuration_count
  if ! configuration_count=$(jq -er '.configurations | if type == "array" then length else error("configurations must be an array") end' "$XCTESTPLAN_FILE"); then
    echo "FAIL: could not read accessibility test-plan configurations" >&2
    return 1
  fi
  [[ "$configuration_count" -eq 1 ]] || {
    echo "FAIL: accessibility test plan must have exactly one configuration (found $configuration_count)" >&2
    return 1
  }
  printf '%s\n' "$ACCESSIBILITY_TEST_CASE_COUNT"
}

validate_accessibility_result_bundle() {
  local result_bundle=$1 expected_count=$2 summary
  [[ -d "$result_bundle" && ! -L "$result_bundle" ]] || {
    echo "FAIL: accessibility result bundle is absent or symlinked: $result_bundle" >&2
    return 1
  }
  [[ "$expected_count" =~ ^[1-9][0-9]*$ ]] || {
    echo "FAIL: invalid accessibility expected test count: $expected_count" >&2
    return 1
  }
  if ! summary=$(xcrun xcresulttool get test-results summary --path "$result_bundle" --format json); then
    echo "FAIL: xcresulttool could not read accessibility result bundle: $result_bundle" >&2
    return 1
  fi

  local result total passed failed skipped expected_failures finish_time
  if ! result=$(jq -er '.result | strings' <<<"$summary"); then
    echo "FAIL: accessibility result summary omitted result" >&2
    return 1
  fi
  if ! total=$(jq -er '.totalTestCount | numbers | select(. >= 0 and . == floor)' <<<"$summary") \
    || ! passed=$(jq -er '.passedTests | numbers | select(. >= 0 and . == floor)' <<<"$summary") \
    || ! failed=$(jq -er '.failedTests | numbers | select(. >= 0 and . == floor)' <<<"$summary") \
    || ! skipped=$(jq -er '.skippedTests | numbers | select(. >= 0 and . == floor)' <<<"$summary") \
    || ! expected_failures=$(jq -er '.expectedFailures | numbers | select(. >= 0 and . == floor)' <<<"$summary") \
    || ! finish_time=$(jq -er '.finishTime | numbers | select(. > 0)' <<<"$summary"); then
    echo "FAIL: accessibility result summary is incomplete" >&2
    return 1
  fi

  if [[ "$result" != "Passed" || "$total" -ne "$expected_count" || "$passed" -ne "$expected_count" || "$failed" -ne 0 || "$skipped" -ne 0 || "$expected_failures" -ne 0 ]]; then
    echo "FAIL: accessibility result summary did not prove a complete run (result=$result total=$total passed=$passed failed=$failed skipped=$skipped expectedFailures=$expected_failures expected=$expected_count)" >&2
    return 1
  fi
  ACCESSIBILITY_OBSERVED_TEST_COUNT=$passed
  echo "accessibility result bundle validated (total=$total, passed=$passed, failed=$failed, skipped=$skipped)"
}

run_question_shell_quick() {
  local destination=${QUIZZLER_QUICK_TEST_DESTINATION:-"platform=iOS Simulator,name=iPhone 17,OS=$(tr -d '[:space:]' <"$SIMULATOR_VERSION_FILE")"}
  local out status shell_count snapshot_count
  validate_pinned_inputs
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-question-shell.XXXXXX")
  echo "==> Question shell quick tests ($destination)"
  set +e
  xcodebuild test \
      -project app/Quizzler.xcodeproj \
      -scheme Quizzler \
      -testPlan Quizzler \
      -destination "$destination" \
      -derivedDataPath "${TMPDIR:-/tmp}/quizzler-question-shell-derived" \
      -only-testing:QuizzleriOSTests/QuestionShellTests \
      -only-testing:QuizzlerSnapshotTests/QuestionRendererSnapshotTests \
      CODE_SIGNING_ALLOWED=NO 2>&1 | tee "$out"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  local tee_status=${pipeline_status[1]}
  if [[ $status -ne 0 ]]; then
    rm -f "$out"
    echo "FAIL: question-shell quick tests exited $status" >&2
    return "$status"
  fi
  if [[ $tee_status -ne 0 ]]; then
    rm -f "$out"
    echo "FAIL: question-shell transcript failed (tee exited $tee_status)" >&2
    return "$tee_status"
  fi

  shell_count=$(grep -Ec "Test Case .*QuestionShellTests.*' passed" "$out" || true)
  snapshot_count=$(grep -Ec "Test Case .*QuestionRendererSnapshotTests.*' passed" "$out" || true)
  rm -f "$out"
  [[ "$shell_count" -gt 0 ]] || { echo "FAIL: QuestionShellTests ran no passing test cases" >&2; return 1; }
  [[ "$snapshot_count" -gt 0 ]] || { echo "FAIL: QuestionRendererSnapshotTests ran no passing test cases" >&2; return 1; }
  echo "question-shell quick gate passed (QuestionShellTests=$shell_count, QuestionRendererSnapshotTests=$snapshot_count)"
}

run_accessibility_quick() {
  local default_destination="platform=iOS Simulator,name=iPhone 17,OS=$(tr -d '[:space:]' <"$SIMULATOR_VERSION_FILE")"
  local destinations=("${QUIZZLER_ACCESSIBILITY_DESTINATION:-$default_destination}")
  if [[ -n "${QUIZZLER_ACCESSIBILITY_IPAD_DESTINATION:-}" ]]; then
    destinations+=("$QUIZZLER_ACCESSIBILITY_IPAD_DESTINATION")
  fi
  local expected_count receipt_path=${QUIZZLER_ACCESSIBILITY_RECEIPT_PATH:-}
  validate_pinned_inputs
  expected_count=$(accessibility_expected_test_count)
  local receipt_result_root=
  if [[ -n "$receipt_path" ]]; then
    [[ ! -L "$receipt_path" ]] || { echo "FAIL: accessibility receipt path is symlinked: $receipt_path" >&2; return 1; }
    # Remove a stale receipt before starting. An interrupted run must not leave
    # an older successful receipt looking like evidence for this run.
    rm -f "$receipt_path"
    local receipt_dir=${receipt_path%/*}
    [[ "$receipt_dir" != "$receipt_path" ]] || receipt_dir=.
    [[ -d "$receipt_dir" ]] || { echo "FAIL: accessibility receipt directory is absent: $receipt_dir" >&2; return 1; }
    receipt_result_root=$(mktemp -d "$receipt_dir/accessibility-results.XXXXXX")
  fi
  local destination attempt out status result_root result_bundle destination_platform destination_class
  for destination in "${destinations[@]}"; do
    if [[ -n "$receipt_path" ]]; then
      destination_class=$(accessibility_destination_class "$destination")
    fi
    local observed_counts=() result_paths=()
    for attempt in 1 2 3; do
      out=$(mktemp "${TMPDIR:-/tmp}/quizzler-accessibility.XXXXXX")
      if [[ -n "$receipt_path" ]]; then
        result_root=$receipt_result_root
        result_bundle="$result_root/$destination_class-sample-$attempt.xcresult"
      else
        result_root=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-accessibility-result.XXXXXX")
        result_bundle="$result_root/accessibility.xcresult"
      fi
      echo "==> Accessibility sample $attempt/3 ($destination)"
      local -a xcodebuild_args
      xcodebuild_args=(
          test
          -project app/Quizzler.xcodeproj
          -scheme Quizzler
          -testPlan Quizzler
          -destination "$destination"
          -derivedDataPath "${TMPDIR:-/tmp}/quizzler-accessibility-derived-$attempt"
          -resultBundlePath "$result_bundle"
          -only-testing:QuizzleriOSUITests/QuizWorkflowUITests
          -only-testing:QuizzleriOSUITests/AccessibilityUITests
      )
      destination_platform=${destination%%,*}
      if [[ "$destination_platform" == "platform=iOS Simulator" ]]; then
        xcodebuild_args+=(CODE_SIGNING_ALLOWED=NO)
      fi
      set +e
      xcodebuild "${xcodebuild_args[@]}" 2>&1 | tee "$out"
      local -a pipeline_status=("${PIPESTATUS[@]}")
      set -e
      status=${pipeline_status[0]}
      local tee_status=${pipeline_status[1]}
      if [[ $status -ne 0 ]]; then
        rm -f "$out"
        rm -rf "$result_root"
        echo "FAIL: accessibility sample $attempt/3 exited $status" >&2
        return "$status"
      fi
      if [[ $tee_status -ne 0 ]]; then
        rm -f "$out"
        rm -rf "$result_root"
        echo "FAIL: accessibility sample transcript failed (tee exited $tee_status)" >&2
        return "$tee_status"
      fi
      if validate_accessibility_result_bundle "$result_bundle" "$expected_count"; then
        :
      else
        status=$?
        rm -f "$out"
        rm -rf "$result_root"
        echo "FAIL: accessibility sample $attempt/3 did not produce complete structured evidence" >&2
        return "$status"
      fi
      if [[ -n "$receipt_path" ]]; then
        observed_counts+=("$ACCESSIBILITY_OBSERVED_TEST_COUNT")
        result_paths+=("$result_bundle")
      fi
      rm -f "$out"
      if [[ -z "$receipt_path" ]]; then rm -rf "$result_root"; fi
    done
    if [[ -n "$receipt_path" ]]; then
      local observed_json paths_json entry
      observed_json=$(printf '%s\n' "${observed_counts[@]}" | jq -Rsc 'split("\n") | map(select(length > 0) | tonumber)')
      paths_json=$(printf '%s\n' "${result_paths[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')
      entry=$(jq -cn --arg destination_class "$destination_class" --argjson expected "$expected_count" --argjson observed "$observed_json" --argjson paths "$paths_json" \
        '{destination_class: $destination_class, sample_count: ($observed | length), expected_test_count: $expected, observed_test_counts: $observed, outcome: "passed", xcresult_paths: $paths}')
      ACCESSIBILITY_RECEIPT_ENTRIES+=("$entry")
    fi
  done
  if [[ -n "$receipt_path" ]]; then
    local entries_json
    entries_json=$(printf '%s\n' "${ACCESSIBILITY_RECEIPT_ENTRIES[@]}" | jq -Rsc 'split("\n") | map(select(length > 0) | fromjson)')
    write_accessibility_receipt "$receipt_path" "$entries_json"
  fi
  echo "accessibility quick gate passed (three consecutive complete result bundles per destination; expected=$expected_count)"
}

run_native_phase() {
  local destination=${QUIZZLER_NATIVE_DESTINATION:-"platform=iOS Simulator,name=iPhone 17,OS=$(tr -d '[:space:]' <"$SIMULATOR_VERSION_FILE")"}
  local out status count
  validate_pinned_inputs
  out=$(mktemp "${TMPDIR:-/tmp}/quizzler-native-phase.XXXXXX")
  echo "==> Native phase (pinned destination: $destination)"
  echo "    targets: QuizzlerKitTests, QuizzleriOSTests, QuizzlerSnapshotTests, QuizzleriOSUITests/{QuizWorkflowUITests,AccessibilityUITests}"
  set +e
  xcodebuild test \
    -project app/Quizzler.xcodeproj \
    -scheme Quizzler \
    -testPlan Quizzler \
    -destination "$destination" \
    -derivedDataPath "${TMPDIR:-/tmp}/quizzler-native-phase-derived" \
    -only-testing:QuizzlerKitTests \
    -only-testing:QuizzleriOSTests \
    -only-testing:QuizzlerSnapshotTests \
    -only-testing:QuizzleriOSUITests/QuizWorkflowUITests \
    -only-testing:QuizzleriOSUITests/AccessibilityUITests \
    CODE_SIGNING_ALLOWED=NO 2>&1 | tee "$out"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  local tee_status=${pipeline_status[1]}
  if [[ $status -ne 0 ]]; then rm -f "$out"; echo "FAIL: native phase xcodebuild exited $status" >&2; return "$status"; fi
  if [[ $tee_status -ne 0 ]]; then rm -f "$out"; echo "FAIL: native phase transcript failed (tee exited $tee_status)" >&2; return "$tee_status"; fi
  count=$(grep -Eo 'Executed [0-9]+ tests?' "$out" | awk '{print $2}' | sort -n | tail -1 || true)
  rm -f "$out"
  [[ -n "$count" && "$count" -gt 0 ]] || {
    echo "FAIL: native phase emitted no positive XCTest count" >&2
    return 1
  }
  echo "native phase passed ($count tests)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  cd "$(dirname "$0")/.."
  if [[ $# -eq 2 && "$1" == "--quick" && "$2" == "question-shell" ]]; then
    run_question_shell_quick
    exit $?
  elif [[ $# -eq 2 && "$1" == "--quick" && "$2" == "accessibility" ]]; then
    run_accessibility_quick
    exit $?
  elif [[ $# -eq 2 && "$1" == "--phase" && "$2" == "native" ]]; then
    run_native_phase
    exit $?
  elif [[ $# -eq 2 && "$1" == "--phase" && "$2" == "contract" ]]; then
    validate_pinned_inputs
    run_signed_contract_probe
    echo "test-gate contract phase passed"
    exit 0
  elif [[ $# -eq 2 && "$1" == "--phase" && "$2" == "sync" ]]; then
    run_sync_phase
    exit $?
  elif [[ $# -ne 0 ]]; then
    echo "FAIL: unsupported gate arguments (expected --quick question-shell|accessibility, --phase contract|native|sync, or no arguments)" >&2
    exit 2
  fi
  validate_pinned_inputs
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
  echo "==> Development probe evidence"
  assert_counting_leg development-probe-evidence python3 app/scripts/test_development_probe_evidence.py
  echo "==> TestFlight release workflow"
  assert_counting_leg release-workflow bash -c 'cd app/scripts && python3 -m unittest -v test_release_adapter test_release_readiness test_prepare_testflight_candidate test_deploy_testflight test_cloudkit_schema_compatibility test_device_acceptance test_reconcile_production test_prepare_testflight_receipt test_release_isolation test_release_restart test_release_security test_sync_release_tool test_testflight_workflow'
  echo "==> runner manifest"
  assert_counting_leg runner-manifest python3 tests/test_runner_manifest.py
  assert_counting_legs_complete
  echo "test-gate passed ($COUNTING_LEG_RUN_COUNT counted legs)"
fi
