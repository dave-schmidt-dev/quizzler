#!/usr/bin/env bash
#
# Profile-free test build for the switchyard macOS VM lane.
#
# The VM guest holds no Apple Development identity, no team membership, and no
# provisioning profile, so anything that needs one cannot build or run there.
#
# This project needs no signing overrides to satisfy that: every target is
# platform: iOS, so every test destination is a simulator, and Xcode already
# signs simulator products ad-hoc and strips their entitlements. What the guest
# cannot do is honor those entitlements at run time -- see the README section
# "VM profile-free test configuration" for what that costs in coverage.
#
# The script therefore builds bare and then asserts the profile-free property
# instead of assuming it. That assertion is the point: it catches the day a
# target regains a DEVELOPMENT_TEAM on the host, rather than in the guest.
#
# Usage:
#   scripts/vm-test-build.sh [scheme] [extra xcodebuild args...]
#
# Environment:
#   VM_TEST_DERIVED_DATA  derived data path (default: build/vm-test)
#   VM_TEST_SIM_UDID      iOS simulator UDID (default: newest available)

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly PROJECT="${REPO_ROOT}/app/Quizzler.xcodeproj"

scheme="${1:-Quizzler}"
[[ $# -gt 0 ]] && shift

derived_data="${VM_TEST_DERIVED_DATA:-${REPO_ROOT}/build/vm-test}"

resolve_simulator() {
  if [[ -n "${VM_TEST_SIM_UDID:-}" ]]; then
    printf '%s' "${VM_TEST_SIM_UDID}"
    return
  fi
  # Resolve to a UDID, never a name: duplicate device names across runtimes make
  # `name=` ambiguous and xcodebuild picks one of them without saying which.
  local udid
  udid="$(xcrun simctl list devices available --json |
    /usr/bin/python3 -c '
import json, sys
devices = json.load(sys.stdin)["devices"]
runtimes = sorted(k for k in devices if "iOS" in k)
for runtime in reversed(runtimes):
    for device in devices[runtime]:
        if device.get("isAvailable"):
            print(device["udid"])
            sys.exit(0)
sys.exit(1)
')"
  if [[ -z "${udid}" ]]; then
    echo "error: no available iOS simulator; set VM_TEST_SIM_UDID" >&2
    exit 1
  fi
  printf '%s' "${udid}"
}

destination="platform=iOS Simulator,id=$(resolve_simulator)"

echo "==> building ${scheme} for testing" >&2
echo "    destination:  ${destination}" >&2
echo "    derived data: ${derived_data}" >&2
echo "    overrides:    none (simulator products are ad-hoc signed by default)" >&2

xcodebuild \
  -project "${PROJECT}" \
  -scheme "${scheme}" \
  -destination "${destination}" \
  -derivedDataPath "${derived_data}" \
  build-for-testing \
  "$@"

products_dir="${derived_data}/Build/Products/Debug-iphonesimulator"
echo "==> verifying products are profile-free" >&2

failures=0
checked=0
while IFS= read -r bundle; do
  checked=$((checked + 1))
  info="$(codesign -dvvv "${bundle}" 2>&1 || true)"
  name="$(basename "${bundle}")"
  if ! grep -q 'Signature=adhoc' <<<"${info}"; then
    echo "    FAIL ${name}: not ad-hoc signed" >&2
    failures=$((failures + 1))
  elif ! grep -q 'TeamIdentifier=not set' <<<"${info}"; then
    echo "    FAIL ${name}: carries a TeamIdentifier" >&2
    failures=$((failures + 1))
  else
    echo "    ok   ${name}" >&2
  fi
done < <(find "${products_dir}" \( -name '*.app' -o -name '*.xctest' \) | sort)

if ((checked == 0)); then
  echo "error: no bundles found under ${products_dir}" >&2
  exit 1
fi
if ((failures > 0)); then
  echo "error: ${failures} of ${checked} bundles are not profile-free" >&2
  exit 1
fi

echo "==> ${checked} bundles verified profile-free" >&2
echo "    run with: xcodebuild -project ${PROJECT} -scheme ${scheme} \\" >&2
echo "                -destination '${destination}' -derivedDataPath ${derived_data} \\" >&2
echo "                test-without-building" >&2
