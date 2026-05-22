#!/bin/bash
# Launch Quizzler — starts a local server, opens the browser, stops on Enter

PORT=4123
DIR="$(cd "$(dirname "$0")" && pwd)"

# Rebuild the question-pack manifest so the home screen reflects whatever packs
# are on disk. See scripts/build_manifest.py for conventions.
python3 "$DIR/scripts/build_manifest.py" || { echo "Manifest build failed; aborting." >&2; exit 1; }

# Pin the port — localStorage is partitioned per origin, so a silent port swap
# strands prior progress on the previous origin. Fail loudly instead.
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "error: port $PORT is in use. Kill the squatter:  lsof -ti:$PORT | xargs kill" >&2
  exit 1
fi

# Start server in background
python3 -m http.server "$PORT" -d "$DIR" &>/dev/null &
SERVER_PID=$!

# Wait for the manifest endpoint to actually answer before opening the browser.
# A fixed `sleep` raced Safari's first fetch on slower starts and surfaced as
# "Load failed" in the app — Safari then negative-cached the failure, so even
# after the server came up the page kept showing "Could not load courses"
# until a hard reload. Poll for up to ~3s instead.
for _ in $(seq 1 60); do
  if curl -sf "http://localhost:${PORT}/question-packs/manifest.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
if ! curl -sf "http://localhost:${PORT}/question-packs/manifest.json" >/dev/null 2>&1; then
  echo "error: server on port $PORT did not become ready within 3s." >&2
  kill "$SERVER_PID" 2>/dev/null
  exit 1
fi

# Open browser (macOS: open, Linux: xdg-open, WSL: explorer.exe)
if command -v open &>/dev/null; then
  open "http://localhost:${PORT}/app/"
elif command -v xdg-open &>/dev/null; then
  xdg-open "http://localhost:${PORT}/app/"
else
  echo "Open http://localhost:${PORT}/app/ in your browser."
fi

echo "Quizzler running at http://localhost:${PORT}/app/"
echo "Press Enter to stop the server."
read -r

# Cleanup
kill "$SERVER_PID" 2>/dev/null
echo "Server stopped."
