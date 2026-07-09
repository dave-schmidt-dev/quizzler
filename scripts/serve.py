#!/usr/bin/env python3
"""Serve a directory over HTTP on loopback ONLY, answering on both IPv4
(127.0.0.1) and IPv6 (::1).

Why not `python3 -m http.server --bind 127.0.0.1`: that binds IPv4 loopback
only. Browsers disagree on how `localhost` resolves — Chromium/curl prefer
127.0.0.1, Safari prefers ::1 — so an IPv4-only bind loads in Chrome but fails
in Safari with "Load failed". Binding both loopback addresses makes `localhost`
work everywhere while still never exposing the server to the LAN (unlike a
0.0.0.0 / :: bind).

Directory listings are always disabled (see NoListingHTTPRequestHandler
below): the app only ever fetches manifest.json and named pack files, so a
listing is never legitimate traffic — only ever reconnaissance. This matters
most in --lan mode, where anyone on the Wi-Fi can otherwise browse
question-packs/ wholesale instead of one file at a time.

Usage: serve.py <port> <directory> [--lan]

By default this binds loopback only, as above. Pass --lan to instead bind a
single all-interfaces IPv4 server (0.0.0.0) so other devices on the same
network (e.g. a phone) can reach it — this is an explicit opt-in; the caller
(start.sh) is expected to have already scoped <directory> down to a public-safe
subset before passing --lan.
"""
import errno
import functools
import http.server
import socket
import socketserver
import sys
import threading


class NoListingHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with directory listings disabled.

    SimpleHTTPRequestHandler only calls list_directory() when a requested
    directory has no index.html to fall back to. For this app that means
    /app/ (has index.html) still serves normally, but /question-packs/ (no
    index.html) would otherwise return a full directory listing to anyone who
    requests it — over the LAN, unauthenticated. The app never needs a
    listing; it always fetches manifest.json and named pack files directly.
    """

    def list_directory(self, path):
        self.send_error(403, "Directory listing is disabled")
        return None


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
        NoListingHTTPRequestHandler, directory=directory
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


def bind_lan_server(port, directory):
    """Bind a single all-interfaces IPv4 server (0.0.0.0) for --lan mode.

    Explicit opt-in only: unlike bind_loopback_servers, this exposes the
    directory to every device on the local network. Matches the previous
    stock `python3 -m http.server --bind 0.0.0.0` LAN behavior — IPv4 only,
    no IPv6-LAN bind — so the printed LAN URL (an IPv4 address) is the only
    address that is actually served.
    """
    return [_make_server(socket.AF_INET, "0.0.0.0", port, directory)]


def main(argv):
    args = [a for a in argv[1:] if a != "--lan"]
    lan = "--lan" in argv[1:]
    if len(args) != 2:
        print("usage: serve.py <port> <directory> [--lan]", file=sys.stderr)
        return 2
    port = int(args[0])
    directory = args[1]

    try:
        servers = (
            bind_lan_server(port, directory)
            if lan
            else bind_loopback_servers(port, directory)
        )
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
