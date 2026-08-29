#!/bin/bash
# Launch Quizzler — starts a local server, opens the browser, stops on Enter
#
# Default: LAN-accessible on all interfaces. Use --no-lan for loopback-only.
#
# Modes:
#   ./start.sh                              LAN, app opens directly
#   ./start.sh --shared-progress            LAN, opens pairing page
#   ./start.sh --no-lan                     loopback-only, app opens directly
#   ./start.sh --no-lan --shared-progress   loopback-only, pairing page
#   ./start.sh --shared-progress --tailscale Tailscale + pairing page
#   ./start.sh --no-open                    suppress browser open (any mode)
#   ./start.sh --allow-course-size-preview  local WIP/test preview only

PORT=4123
DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse flags — LAN is the default; --no-lan restricts to loopback.
LAN=1
LAN_EXPLICIT=0
NO_LAN=0
NO_OPEN=0
SHARED=0
TAILSCALE=0
TAILSCALE_IP=""
ALLOW_COURSE_SIZE_PREVIEW=0
[ -n "$QUIZZLER_NO_OPEN" ] && NO_OPEN=1
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=1; LAN_EXPLICIT=1 ;;
    --no-lan) NO_LAN=1; LAN=0 ;;
    --no-open) NO_OPEN=1 ;;
    --shared-progress) SHARED=1 ;;
    --tailscale) TAILSCALE=1 ;;
    --allow-course-size-preview) ALLOW_COURSE_SIZE_PREVIEW=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# Tailscale is loopback plus the Tailscale address unless LAN was explicitly
# requested. Resolve this after parsing so --tailscale --lan and --lan
# --tailscale have the same result.
if [ "$TAILSCALE" -eq 1 ] && [ "$LAN_EXPLICIT" -eq 0 ]; then
  LAN=0
fi

# Rebuild the question-pack manifest so the home screen reflects whatever packs
# are on disk. See scripts/build_manifest.py for conventions. The explicit
# course-size preview override is for local WIP/test fixtures only; production
# launches remain strict by default.
BUILD_ARGS=()
if [ "$ALLOW_COURSE_SIZE_PREVIEW" -eq 1 ]; then
  BUILD_ARGS+=(--allow-course-size-preview)
  echo "warning: oversized-course preview enabled; do not use this path to install or ship." >&2
fi
# Exit 2 means "partial install": some packs failed the strict quality gate and
# were excluded, but the ones that passed are installed and worth serving. Only a
# hard failure (exit 1 — nothing installed) aborts the launch.
python3 "$DIR/scripts/build_manifest.py" "${BUILD_ARGS[@]}"
BUILD_STATUS=$?
if [ "$BUILD_STATUS" -eq 2 ]; then
  echo "warning: some question packs failed the quality gate and were NOT installed (see above). Launching with the packs that passed." >&2
elif [ "$BUILD_STATUS" -ne 0 ]; then
  echo "Manifest build failed; aborting." >&2
  exit 1
fi

# Pin the port — localStorage is partitioned per origin, so a silent port swap
# strands prior progress on the previous origin. Fail loudly instead. macOS
# normally provides lsof, but keep port safety explicit on minimal systems too.
if command -v lsof >/dev/null 2>&1; then
  if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "error: port $PORT is in use. Kill the squatter:  lsof -ti:$PORT | xargs kill" >&2
    exit 1
  fi
else
  echo "warning: lsof is unavailable; using the Python socket port check." >&2
  python3 - "$PORT" <<'PY'
import errno
import socket
import sys

port = int(sys.argv[1])

# Probe wildcards and every address Python can resolve for the local host. A
# listener may be restricted to a LAN interface, so loopback-only connect
# probes would miss the exact mode start.sh is about to launch. Binding with
# SO_REUSEADDR avoids treating a recently-closed connection in TIME_WAIT as an
# occupied service port.
checks = []
seen = set()

def add_check(family, address):
    key = (family, address)
    if key not in seen:
        seen.add(key)
        checks.append(key)

add_check(socket.AF_INET, "0.0.0.0")
add_check(socket.AF_INET, "127.0.0.1")
if hasattr(socket, "AF_INET6"):
    add_check(socket.AF_INET6, "::")
    add_check(socket.AF_INET6, "::1")

try:
    for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
        socket.gethostname(), None, socket.AF_UNSPEC, socket.SOCK_STREAM
    ):
        if family in (socket.AF_INET, getattr(socket, "AF_INET6", -1)):
            add_check(family, sockaddr[0])
except socket.gaierror:
    # Explicit wildcard/loopback probes above remain useful when the host
    # name is not resolvable (common in minimal or offline environments).
    pass

probe_errors = []
benign_errors = {
    errno.EAFNOSUPPORT,
    errno.EADDRNOTAVAIL,
    errno.ENOPROTOOPT,
    errno.EPROTONOSUPPORT,
}

for family, address in checks:
    probe = None
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == getattr(socket, "AF_INET6", None):
            v6_only = getattr(socket, "IPV6_V6ONLY", None)
            if v6_only is not None:
                probe.setsockopt(socket.IPPROTO_IPV6, v6_only, 1)
        probe.bind((address, port) if family == socket.AF_INET else (address, port, 0, 0))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"active listener found on {address}:{port}", file=sys.stderr)
            raise SystemExit(10)
        if exc.errno in benign_errors:
            continue
        probe_errors.append(f"{address}: {exc}")
    finally:
        if probe is not None:
            probe.close()

if probe_errors:
    print("; ".join(probe_errors), file=sys.stderr)
    raise SystemExit(11)
PY
  PYTHON_PORT_CHECK_STATUS=$?
  if [ "$PYTHON_PORT_CHECK_STATUS" -eq 10 ]; then
    echo "error: port $PORT is in use (lsof is unavailable; Python socket check found an active listener)." >&2
    exit 1
  elif [ "$PYTHON_PORT_CHECK_STATUS" -ne 0 ]; then
    echo "error: could not verify port $PORT (lsof is unavailable; Python socket probe failed)." >&2
    exit 1
  fi
fi

# Build serve.py arguments — always pass scoped routing roots.
SERVE_ARGS=("$DIR/scripts/serve.py" "$PORT" "$DIR"
  --app-root "$DIR/app"
  --packs-root "$DIR/question-packs")

if [ "$LAN" -eq 1 ]; then
  SERVE_ARGS+=(--lan)
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
if [ "$TAILSCALE" -eq 1 ]; then
  if [ "$SHARED" -eq 1 ]; then
    echo "Pairing page: http://localhost:${PORT}/pair"
    echo "On your phone, open: http://${TAILSCALE_IP}:${PORT}/app/ and enter the pairing code."
  fi
else
  LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<your-lan-ip>")
  if [ "$LAN" -eq 1 ]; then
    echo "LAN URL:  http://${LAN_IP}:${PORT}/app/"
    if [ "$SHARED" -eq 1 ]; then
      echo "Pairing page: http://localhost:${PORT}/pair"
      echo "On your phone, open: http://${LAN_IP}:${PORT}/app/ and enter the pairing code."
    fi
  fi
fi
echo "Press Enter to stop the server."
read -r

# Cleanup
kill "$SERVER_PID" 2>/dev/null
echo "Server stopped."
