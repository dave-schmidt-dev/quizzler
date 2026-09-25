#!/usr/bin/env bash
# Quizzler-specific simulator ownership on top of simctl_gate_lib.sh.
#
# Source this only after the shared library.  The shared registry owns devices
# created by gate_sim_create; this layer owns neither those devices nor any
# pre-existing device.  It records a destination's initial state so a gate can
# return a simulator it booted to Shutdown without changing one that was
# already Booted.

QUIZZLER_SIMULATOR_STATE_REGISTRY=""

_quizzler_simulator_append_exit_trap() {
  local new_command="$1" previous
  previous="$(trap -p EXIT)"
  if [[ -n "$previous" ]]; then
    previous="${previous#trap -- }"
    previous="${previous% EXIT}"
    # trap -p returns a shell-quoted command. Preserve it so this cleanup is
    # appended after, rather than replacing, simctl_gate_lib's cleanup trap.
    # shellcheck disable=SC2064
    trap "eval ${previous}; ${new_command}" EXIT
  else
    # shellcheck disable=SC2064
    trap "${new_command}" EXIT
  fi
}

quizzler_simulator_lifecycle_init() {
  [[ -n "$QUIZZLER_SIMULATOR_STATE_REGISTRY" ]] && return 0
  QUIZZLER_SIMULATOR_STATE_REGISTRY="$(mktemp "${TMPDIR:-/tmp}/quizzler-simulator-state.XXXXXX")" || {
    echo "FAIL: could not create simulator state registry" >&2
    return 1
  }
  _quizzler_simulator_append_exit_trap quizzler_simulator_restore_destinations
}

_quizzler_simulator_list() {
  xcrun simctl list devices -j | python3 -c '
import json
import sys

try:
    inventory = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError):
    sys.exit(1)
for devices in inventory.get("devices", {}).values():
    for device in devices:
        udid = device.get("udid", "")
        name = device.get("name", "")
        state = device.get("state", "")
        if udid and name:
            print(f"{udid}\t{name}\t{state}")
'
}

_quizzler_simulator_destination_state() {
  local destination="$1" inventory
  inventory="$(_quizzler_simulator_list)" || return 1
  python3 -c '
import sys

destination = sys.argv[1]
parts = dict(part.split("=", 1) for part in destination.split(",") if "=" in part)
if parts.get("platform") != "iOS Simulator":
    raise SystemExit(0)
device_id = parts.get("id")
name = parts.get("name")
rows = [line.split("\t", 2) for line in sys.stdin.read().splitlines()]
if device_id:
    matches = [row for row in rows if row[0] == device_id]
elif name:
    matches = [row for row in rows if row[1] == name]
else:
    raise SystemExit(2)
if len(matches) != 1:
    raise SystemExit(2)
print(f"{matches[0][0]}\t{matches[0][2]}")
' "$destination" <<<"$inventory"
}

# Record a simulator destination once and print its UDID. Non-simulator
# destinations print nothing, allowing the signed-device contract leg through.
quizzler_simulator_track_destination() {
  local destination="$1" resolved udid state
  [[ -n "$QUIZZLER_SIMULATOR_STATE_REGISTRY" ]] || {
    echo "FAIL: simulator lifecycle was not initialized" >&2
    return 1
  }
  resolved="$(_quizzler_simulator_destination_state "$destination")" || {
    case "$destination" in
      *"platform=iOS Simulator"*)
        echo "FAIL: could not resolve simulator destination: $destination" >&2
        return 1
        ;;
      *) printf '\n'; return 0 ;;
    esac
  }
  [[ -n "$resolved" ]] || { printf '\n'; return 0; }
  IFS=$'\t' read -r udid state <<<"$resolved"
  [[ -n "$udid" && -n "$state" ]] || return 1
  if ! grep -q "^${udid}"$'\t' "$QUIZZLER_SIMULATOR_STATE_REGISTRY"; then
    printf '%s\t%s\n' "$udid" "$state" >>"$QUIZZLER_SIMULATOR_STATE_REGISTRY"
  fi
  printf '%s\n' "$udid"
}

_quizzler_simulator_state_for_udid() {
  local udid="$1" row
  while IFS=$'\t' read -r listed_udid _name state; do
    [[ "$listed_udid" == "$udid" ]] && { printf '%s\n' "$state"; return 0; }
  done < <(_quizzler_simulator_list)
  return 1
}

# EXIT cleanup never deletes a destination. It only returns a device the gate
# found Shutdown to Shutdown if it is Booted/Booting at gate exit.
quizzler_simulator_restore_destinations() {
  [[ -n "$QUIZZLER_SIMULATOR_STATE_REGISTRY" && -f "$QUIZZLER_SIMULATOR_STATE_REGISTRY" ]] || return 0
  local udid initial_state current_state
  while IFS=$'\t' read -r udid initial_state; do
    # Shutdown is the only state this gate may restore: a pre-existing device
    # that was Booting/Creating is just as much someone else's as Booted.
    [[ "$initial_state" == "Shutdown" ]] || continue
    current_state="$(_quizzler_simulator_state_for_udid "$udid" 2>/dev/null || true)"
    case "$current_state" in
      Booted|Booting)
        xcrun simctl shutdown "$udid" >/dev/null 2>&1 || true
        echo "simulator lifecycle: shut down gate-booted destination $udid" >&2
        ;;
    esac
  done <"$QUIZZLER_SIMULATOR_STATE_REGISTRY"
  rm -f "$QUIZZLER_SIMULATOR_STATE_REGISTRY"
  QUIZZLER_SIMULATOR_STATE_REGISTRY=""
}

# Delete only inactive devices with the exact gate-created name shape. A live
# creator PID wins even if the simulator is temporarily Shutdown between legs.
quizzler_simulator_sweep_orphans() {
  local udid name state creator_pid swept=0 inventory
  inventory="$(_quizzler_simulator_list)" || return 1
  while IFS=$'\t' read -r udid name state; do
    [[ "$name" =~ ^quizzler-gate-([1-9][0-9]*)-[A-Za-z0-9._-]+$ ]] || continue
    creator_pid="${BASH_REMATCH[1]}"
    if kill -0 "$creator_pid" 2>/dev/null; then
      echo "simulator lifecycle: preserving active $name ($udid)" >&2
      continue
    fi
    if [[ "$state" != "Shutdown" ]]; then
      xcrun simctl shutdown "$udid" >/dev/null 2>&1 || {
        echo "simulator lifecycle: could not shut down $name ($udid)" >&2
        continue
      }
    fi
    if xcrun simctl delete "$udid" >/dev/null 2>&1; then
      swept=$((swept + 1))
      echo "simulator lifecycle: deleted inactive $name ($udid)" >&2
    fi
  done <<<"$inventory"
  printf '%s\n' "$swept"
}

# Route every xcodebuild test leg through the shared clone-reaping lock while
# recording any simulator destination it is about to boot.
quizzler_simulator_ui_test() {
  local destination="$1" label="$2" tracked_udid
  shift 2
  tracked_udid="$(quizzler_simulator_track_destination "$destination")" || return 1
  local -a lock_args=(--label "$label")
  [[ -z "$tracked_udid" ]] || lock_args+=(--simulator-udid "$tracked_udid")
  gate_ui_test_lock "${lock_args[@]}" "$@"
}
