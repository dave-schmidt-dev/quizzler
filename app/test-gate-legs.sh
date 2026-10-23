# shellcheck shell=bash
# Verify-once gate legs for app/test-gate.sh (source it; it defines functions only).
#
# Every gate step is a leg declared under `gateLegs` in .release/release-adapter.json.
# A leg that passes records a receipt keyed by a digest of its inputs; the same digest
# later, from any worktree, is reused instead of rerun (apple_developer
# release_tools/templates/gate_receipts.sh). Every leg runs even after one fails, and the
# gate then exits non-zero naming each failed leg. Each gate attempt keeps its own
# transcript under .logs/test-gate/ (apple_developer templates/attempt_log.sh).
#
# QUIZZLER_GATE_MODE selects the legs that run:
#   release (default)  headless legs run or are reused; ui legs are listed, never run
#   headless           same legs; the pre-push hook's mode
#   ui-verify          ui legs only (app/verify-release-ui); screen-seizing
#   full               everything
#
# Nothing here may put an absolute or per-worktree path in a leg's argv: it is part of
# the receipt digest, so two worktrees would never share a receipt.

GATE_ADAPTER=.release/release-adapter.json
GATE_CENTRAL="$GATE_ROOT/../apple_developer"
GATE_FAILED_LEGS=()

gate_legs_load_templates() {
  local template
  for template in gate_receipts.sh attempt_log.sh; do
    [[ -f "$GATE_CENTRAL/release_tools/templates/$template" ]] || {
      echo "FAIL: apple_developer template $template is unavailable at $GATE_CENTRAL (a sibling checkout with the verify-once release path is required)" >&2
      return 1
    }
    # shellcheck source=/dev/null
    source "$GATE_CENTRAL/release_tools/templates/$template"
  done
}

# gate_legs_start [mode]: load the templates and open the receipt session.
gate_legs_start() {
  gate_legs_load_templates || return 1
  gate_legs_init "$GATE_CENTRAL" "$GATE_ADAPTER" "${1:-${QUIZZLER_GATE_MODE:-release}}" || return 1
  GATE_FAILED_LEGS=()
}

# run_gate_leg <id> [--context k=v ...] -- <command...>: run or reuse one leg and
# remember a failure instead of stopping the gate.
run_gate_leg() {
  local id=$1
  gate_leg "$@" || {
    GATE_FAILED_LEGS+=("$id")
    echo "FAIL: gate leg $id failed" >&2
    return 0
  }
}

# run_counting_leg <name> <command...>: a leg whose transcript must report a test
# count at or above its declared floor. A reused leg ran no command, so it counts as
# complete here without a transcript.
run_counting_leg() {
  local name=$1 before=$COUNTING_LEG_RUN_COUNT failures=${#GATE_FAILED_LEGS[@]}
  shift
  run_gate_leg "$name" -- assert_counting_leg "$name" "$@"
  if [[ ${#GATE_FAILED_LEGS[@]} -eq $failures && $COUNTING_LEG_RUN_COUNT -eq $before ]]; then
    COUNTING_LEG_RUN_COUNT=$((COUNTING_LEG_RUN_COUNT + 1))
  fi
}

# The simulator a leg ran on is part of what its receipt vouches for.
gate_sim_context() {
  GATE_SIM_CONTEXT=(--context "runtime=$(_quizzler_iphone_runtime_id)" --context "device=$QUIZZLER_IPHONE_DEVICETYPE_ID")
}

# gate_legs_conclude: list deferred ui legs, then fail naming every failed leg.
gate_legs_conclude() {
  gate_legs_finish || return 1
  if [[ ${#GATE_FAILED_LEGS[@]} -gt 0 ]]; then
    echo "FAIL: ${#GATE_FAILED_LEGS[@]} gate leg(s) failed: ${GATE_FAILED_LEGS[*]}" >&2
    return 1
  fi
}

# run_native_leg: `--phase native` as the same leg the aggregate gate runs, so the
# pre-push hook's second invocation reuses the first's receipt.
run_native_leg() {
  gate_legs_start headless || return 1
  gate_sim_context
  run_gate_leg native-phase "${GATE_SIM_CONTEXT[@]}" -- run_native_phase
  gate_legs_conclude
}

# run_gate_legs_logged: run run_gate_legs with its transcript kept in a fresh
# per-attempt log, and return its status (not tee's).
run_gate_legs_logged() {
  gate_legs_load_templates || return 1
  local log status tee_status
  log=$(attempt_log_open "$GATE_ROOT/.logs/test-gate" test-gate) || return 1
  echo "==> gate attempt log: ${log#"$GATE_ROOT"/}"
  set +e
  run_gate_legs 2>&1 | tee -a "$log"
  local -a pipeline_status=("${PIPESTATUS[@]}")
  set -e
  status=${pipeline_status[0]}
  tee_status=${pipeline_status[1]}
  if [[ $tee_status -ne 0 ]]; then
    echo "FAIL: gate transcript failed (tee exited $tee_status)" >&2
    return "$tee_status"
  fi
  return "$status"
}
