#!/bin/bash
# Launch Quizzler — starts a local server, opens the browser, stops on Enter

PORT=4123
DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse flags
LAN=0
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# Rebuild the question-pack manifest so the home screen reflects whatever packs
# are on disk. See scripts/build_manifest.py for conventions.
python3 "$DIR/scripts/build_manifest.py" || { echo "Manifest build failed; aborting." >&2; exit 1; }

# Pin the port — localStorage is partitioned per origin, so a silent port swap
# strands prior progress on the previous origin. Fail loudly instead.
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "error: port $PORT is in use. Kill the squatter:  lsof -ti:$PORT | xargs kill" >&2
  exit 1
fi

# Build a scoped public directory for --lan so only app/ and question-packs/
# are exposed over the network. Recreate symlinks idempotently each launch.
if [ "$LAN" -eq 1 ]; then
  mkdir -p "$DIR/.public"
  rm -f "$DIR/.public/app" "$DIR/.public/question-packs"
  ln -s "../app" "$DIR/.public/app"
  ln -s "../question-packs" "$DIR/.public/question-packs"
  SERVE_DIR="$DIR/.public"
else
  SERVE_DIR="$DIR"
fi

# Start server in background (loopback-only by default; all interfaces with --lan)
if [ "$LAN" -eq 1 ]; then
  python3 -m http.server "$PORT" -d "$SERVE_DIR" &>/dev/null &
else
  python3 -m http.server "$PORT" -d "$SERVE_DIR" --bind 127.0.0.1 &>/dev/null &
fi
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
if [ "$LAN" -eq 1 ]; then
  LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<your-lan-ip>")
  echo "LAN URL:  http://${LAN_IP}:${PORT}/app/"
fi
echo "Press Enter to stop the server."
read -r

# Cleanup
kill "$SERVER_PID" 2>/dev/null
echo "Server stopped."
