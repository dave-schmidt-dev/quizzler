#!/bin/bash
# Launch Quizzler — starts a local server, opens the browser, stops on Enter
#
# Modes:
#   ./start.sh                              browser-local, loopback only
#   ./start.sh --lan                        browser-local, unauthenticated LAN
#   ./start.sh --shared-progress            shared, loopback only
#   ./start.sh --shared-progress --lan      shared, authenticated on all IPv4
#   ./start.sh --shared-progress --tailscale shared, loopback + Tailscale IP
#   ./start.sh --no-open                    suppress browser open (any mode)

PORT=4123
DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse flags
LAN=0
NO_OPEN=0
SHARED=0
TAILSCALE=0
TAILSCALE_IP=""
[ -n "$QUIZZLER_NO_OPEN" ] && NO_OPEN=1
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=1 ;;
    --no-open) NO_OPEN=1 ;;
    --shared-progress) SHARED=1 ;;
    --tailscale) TAILSCALE=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# Mutually exclusive flags
if [ "$LAN" -eq 1 ] && [ "$TAILSCALE" -eq 1 ]; then
  echo "error: --lan and --tailscale are mutually exclusive" >&2
  exit 1
fi

# Rebuild the question-pack manifest so the home screen reflects whatever packs
# are on disk. See scripts/build_manifest.py for conventions.
python3 "$DIR/scripts/build_manifest.py" || { echo "Manifest build failed; aborting." >&2; exit 1; }

# Pin the port — localStorage is partitioned per origin, so a silent port swap
# strands prior progress on the previous origin. Fail loudly instead.
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "error: port $PORT is in use. Kill the squatter:  lsof -ti:$PORT | xargs kill" >&2
  exit 1
fi

# Build serve.py arguments — always pass scoped routing roots.
SERVE_ARGS=("$DIR/scripts/serve.py" "$PORT" "$DIR"
  --app-root "$DIR/app"
  --packs-root "$DIR/question-packs")

if [ "$SHARED" -eq 1 ]; then
  SERVE_ARGS+=(--shared-progress)
fi

if [ "$TAILSCALE" -eq 1 ]; then
  # Discover Tailscale IPv4 with a 5-second timeout.
  TS_OUT="$(mktemp "/tmp/quizzler-ts-ip-$$.XXXXXX")"
  tailscale ip -4 >"$TS_OUT" 2>/dev/null &
  TS_PID=$!
  (sleep 5; kill "$TS_PID" 2>/dev/null) &
  wait "$TS_PID" 2>/dev/null
  TS_EXIT=$?
  TAILSCALE_IP=$(head -1 "$TS_OUT" 2>/dev/null)
  rm -f "$TS_OUT"

  if [ -z "$TAILSCALE_IP" ] || [ "$TS_EXIT" -ne 0 ]; then
    echo "error: Tailscale is not available. Install it or use --lan." >&2
    exit 1
  fi

  # Validate exactly one IPv4 address.
  IP_COUNT=$(echo "$TAILSCALE_IP" | wc -l | tr -d ' ')
  if [ "$IP_COUNT" -ne 1 ]; then
    echo "error: tailscale ip -4 returned multiple addresses:" >&2
    echo "$TAILSCALE_IP" >&2
    echo "Select one and configure manually, or use --lan instead." >&2
    exit 1
  fi

  SERVE_ARGS+=(--bind "$TAILSCALE_IP")

  # Resolve MagicDNS hostname for display.
  TS_DNS=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','unknown'))" 2>/dev/null)
  echo "Tailscale IP: $TAILSCALE_IP  (${TS_DNS:-unknown})"
elif [ "$LAN" -eq 1 ]; then
  SERVE_ARGS+=(--lan)
fi

# Start server in background.
python3 "${SERVE_ARGS[@]}" >/dev/null 2>/tmp/quizzler-server.log &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT INT TERM

# Poll /healthz until the server is ready (up to ~3s).
for _ in $(seq 1 60); do
  if curl -sS -o /dev/null "http://localhost:${PORT}/healthz" 2>/dev/null; then
    break
  fi
  sleep 0.05
done
if ! curl -sS -o /dev/null "http://localhost:${PORT}/healthz" 2>/dev/null; then
  echo "error: server on port $PORT did not become ready within 3s." >&2
  kill "$SERVER_PID" 2>/dev/null
  exit 1
fi

# Determine which URL to open.
LAUNCH_URL="http://localhost:${PORT}/app/"
if [ "$SHARED" -eq 1 ]; then
  LAUNCH_URL="http://localhost:${PORT}/pair"
fi

# Open browser unless suppressed.
if [ "$NO_OPEN" -eq 0 ]; then
  if command -v open &>/dev/null; then
    open "$LAUNCH_URL"
  elif command -v xdg-open &>/dev/null; then
    xdg-open "$LAUNCH_URL"
  else
    echo "Open ${LAUNCH_URL} in your browser."
  fi
fi

echo "Quizzler running at http://localhost:${PORT}/app/"
if [ "$SHARED" -eq 1 ]; then
  echo "Shared progress mode — pairing required."
  if [ "$TAILSCALE" -eq 1 ]; then
    echo "1. Open the pairing page on this Mac: http://localhost:${PORT}/pair"
    echo "2. On your phone, open: http://${TAILSCALE_IP}:${PORT}/app/"
    echo "3. Enter the code from the Mac into the phone's login page."
  elif [ "$LAN" -eq 1 ]; then
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<your-lan-ip>")
    echo "1. Open the pairing page on this Mac: http://localhost:${PORT}/pair"
    echo "2. On your phone, open: http://${LAN_IP}:${PORT}/app/"
    echo "3. Enter the code from the Mac into the phone's login page."
  fi
elif [ "$LAN" -eq 1 ]; then
  LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<your-lan-ip>")
  echo "LAN URL:  http://${LAN_IP}:${PORT}/app/"
  echo "warning: --lan serves your question packs to everyone on this Wi-Fi with NO authentication."
fi
echo "Press Enter to stop the server."
read -r

# Cleanup
kill "$SERVER_PID" 2>/dev/null
echo "Server stopped."
