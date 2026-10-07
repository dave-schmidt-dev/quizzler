#!/usr/bin/env bash
set -euo pipefail

# On-demand review captures: run ReviewCaptureUITests on a disposable simulator
# and export its kept screenshots to .logs/captures/<timestamp>/. Not part of
# any gate. Usage: scripts/review_captures.sh
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/quizzler-captures.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
bundle="$scratch/ReviewCapture.xcresult"
out=".logs/captures/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
echo "==> Running ReviewCaptureUITests (result bundle in $scratch)"
set +e
bash scripts/with-ui-simulator.sh review-captures -- bash -c '
  "$1/app/scripts/xcb" test -project app/Quizzler.xcodeproj -scheme Quizzler -testPlan Quizzler \
    -destination "$QUIZZLER_UI_SIMULATOR_DESTINATION" -resultBundlePath "$2" \
    -only-testing:QuizzleriOSUITests/ReviewCaptureUITests CODE_SIGNING_ALLOWED=NO' _ "$ROOT" "$bundle" \
  2>&1 | tee "$out/xcodebuild.log" | grep --line-buffered -E 'Test Case|error:|Executed [0-9]+ tests'
status=${PIPESTATUS[0]}
set -e
if [[ ! -d "$bundle" ]]; then
  echo "==> no result bundle was produced (exit $status)" >&2
  exit "${status:-1}"
fi
echo "==> Exporting attachments"
xcrun xcresulttool export attachments --path "$bundle" --output-path "$scratch/export" >/dev/null
# Keep only the tour's named captures ("NN-screen"), renamed to that name;
# XCTest's own snapshots, synthesized events and recordings stay behind.
python3 - "$scratch/export" "$out" <<'PY'
import json, pathlib, re, shutil, sys
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
for test in json.loads((src / "manifest.json").read_text()):
    for item in test.get("attachments", []):
        match = re.match(r"(\d\d-[a-z0-9-]+)_", item.get("suggestedHumanReadableName", ""))
        if match:
            shutil.copy2(src / item["exportedFileName"], dst / f"{match.group(1)}.png")
PY
count=$(find "$out" -name '*.png' | wc -l | tr -d ' ')
echo "==> $count capture(s) in $out (test exit $status; log: $out/xcodebuild.log)"
exit "$status"
