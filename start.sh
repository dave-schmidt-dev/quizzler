#!/bin/bash
# Launch Quizzler — starts a local server, opens the browser, stops on Enter

PORT=8000
DIR="$(cd "$(dirname "$0")" && pwd)"

# Find an open port if 8000 is taken
while lsof -ti:"$PORT" >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

# Start server in background
python3 -m http.server "$PORT" -d "$DIR" &>/dev/null &
SERVER_PID=$!

# Give server a moment to start
sleep 0.3

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
