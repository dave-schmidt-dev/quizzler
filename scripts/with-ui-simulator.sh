#!/usr/bin/env bash
set -euo pipefail

# Run an ad hoc UI/capture command against a disposable Quizzler simulator.
# Usage: scripts/with-ui-simulator.sh [purpose] -- command [args...]
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
purpose=ui
if [[ "${1:-}" != "--" ]]; then
  purpose="${1:-}"
  shift
fi
[[ "${1:-}" == "--" && "$#" -gt 1 ]] || {
  echo "usage: $0 [purpose] -- command [args...]" >&2
  exit 2
}
shift
[[ "$purpose" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "purpose must contain only letters, numbers, dot, underscore, or hyphen" >&2
  exit 2
}

# shellcheck source=/dev/null
source "/Users/dave/Documents/Projects/apple_developer/release_tools/templates/simctl_gate_lib.sh"
# shellcheck source=/dev/null
source "$ROOT/app/scripts/simulator_lifecycle.sh"
quizzler_simulator_lifecycle_init
quizzler_simulator_sweep_orphans >/dev/null
runtime_version="$(tr -d '[:space:]' <"$ROOT/app/.simulator-version" | tr '.' '-')"
runtime="com.apple.CoreSimulator.SimRuntime.iOS-${runtime_version}"
udid="$(gate_sim_create quizzler "adhoc-${purpose}" "com.apple.CoreSimulator.SimDeviceType.iPhone-17" "$runtime")"
QUIZZLER_UI_SIMULATOR_UDID="$udid" \
QUIZZLER_UI_SIMULATOR_DESTINATION="platform=iOS Simulator,id=$udid" \
  "$@"
