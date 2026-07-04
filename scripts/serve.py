#!/usr/bin/env python3
"""Serve a directory over HTTP on loopback ONLY, answering on both IPv4
(127.0.0.1) and IPv6 (::1).

Why not `python3 -m http.server --bind 127.0.0.1`: that binds IPv4 loopback
only. Browsers disagree on how `localhost` resolves — Chromium/curl prefer
127.0.0.1, Safari prefers ::1 — so an IPv4-only bind loads in Chrome but fails
in Safari with "Load failed". Binding both loopback addresses makes `localhost`
work everywhere while still never exposing the server to the LAN (unlike a
0.0.0.0 / :: bind).

Usage: serve.py <port> <directory>
"""
import errno
import functools
import http.server
import socket
import socketserver
import sys
import threading

# A genuinely absent IPv6 stack (kernel support disabled, no ::1 configured) is
# a tolerable IPv4-only fallback. A port that is already IN USE is NOT: serving
# on only one loopback family reintroduces the exact `localhost` split-brain
# this script exists to prevent (Chromium→127.0.0.1, Safari→::1), so EADDRINUSE
# on either family must fail loudly instead of silently degrading.
_IPV6_UNAVAILABLE_ERRNOS = frozenset({
    errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT,
})


def _make_server(family, addr, port, directory):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=directory
    )

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        address_family = family
        daemon_threads = True

    return Server((addr, port), handler)


def bind_loopback_servers(port, directory):
    """Bind loopback HTTP servers on IPv4 (127.0.0.1) and IPv6 (::1).

    Returns the list of bound servers — normally two, or one when IPv6 is
    genuinely unavailable. Raises ``OSError`` (after closing anything already
    bound, so the port is left free for a retry) when a family's address is
    already in use or IPv4 loopback cannot bind: serving on only one family
    would reintroduce the localhost split-brain across browsers, so a contended
    port is a hard failure, not a silent IPv4-only degrade.
    """
    servers = []
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            servers.append(_make_server(family, addr, port, directory))
        except OSError as e:
            if family == socket.AF_INET6 and e.errno in _IPV6_UNAVAILABLE_ERRNOS:
                print(f"serve: IPv6 loopback unavailable, serving IPv4 only ({e})",
                      file=sys.stderr)
                continue
            for s in servers:
                s.server_close()
            raise
    return servers


def main(argv):
    if len(argv) != 3:
        print("usage: serve.py <port> <directory>", file=sys.stderr)
        return 2
    port = int(argv[1])
    directory = argv[2]

    try:
        servers = bind_loopback_servers(port, directory)
    except OSError as e:
        print(f"serve: could not bind port {port} ({e})", file=sys.stderr)
        return 1

    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        for s in servers:
            s.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
