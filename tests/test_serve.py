"""Unit tests for scripts/serve.py — the dual-stack loopback dev server.

The bind logic carries the real failure modes: a port already in use on EITHER
loopback family must fail (serving one family reintroduces the localhost
split-brain), while a genuinely unavailable IPv6 stack is a tolerable IPv4-only
fallback. ``serve_forever()`` is not exercised (it blocks); only the pure
``bind_loopback_servers`` binder is tested.

Also covers NoListingHTTPRequestHandler (F7 remediation): directory listings
must be disabled everywhere serve.py serves, since the app only ever fetches
manifest.json and named pack files by URL. The primary functional coverage of
this behavior against the real --lan process lives in
tests/test_start_sh.py::TestLanScopedServe; this file only checks the handler
exists and rejects listing() directly, cheaply, without spawning a server.

Run: python3 -m unittest tests.test_serve
"""
from __future__ import annotations

import importlib.util
import io
import socket
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "serve.py"

_spec = importlib.util.spec_from_file_location("serve", SCRIPT_PATH)
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


def _occupy(family, addr):
    """Bind+listen on (addr, 0); return (socket, assigned_port). No SO_REUSEADDR
    so the port is unambiguously held for the duration."""
    s = socket.socket(family, socket.SOCK_STREAM)
    s.bind((addr, 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class BindLoopbackTests(unittest.TestCase):
    def test_binds_free_port_and_returns_servers(self):
        port = _free_port()
        servers = serve.bind_loopback_servers(port, str(PROJECT_ROOT))
        try:
            self.assertGreaterEqual(len(servers), 1)  # at least IPv4
        finally:
            for s in servers:
                s.server_close()

    def test_ipv4_port_in_use_is_fatal(self):
        occupier, port = _occupy(socket.AF_INET, "127.0.0.1")
        try:
            with self.assertRaises(OSError):
                serve.bind_loopback_servers(port, str(PROJECT_ROOT))
        finally:
            occupier.close()

    def test_ipv6_port_in_use_is_fatal_not_silent_ipv4_fallback(self):
        # The P2 itself: IPv4 free but IPv6 already bound must FAIL — silently
        # serving IPv4 only would reintroduce the localhost split-brain.
        try:
            occupier, port = _occupy(socket.AF_INET6, "::1")
        except OSError:
            self.skipTest("IPv6 loopback not available in this environment")
        try:
            with self.assertRaises(OSError):
                serve.bind_loopback_servers(port, str(PROJECT_ROOT))
        finally:
            occupier.close()

    def test_fatal_bind_closes_partial_servers(self):
        # When IPv6 is contended, the IPv4 server bound first must be closed
        # (not leaked) before the error propagates — otherwise the port stays
        # held and a retry would spuriously fail on IPv4 too.
        try:
            occupier, port = _occupy(socket.AF_INET6, "::1")
        except OSError:
            self.skipTest("IPv6 loopback not available in this environment")
        try:
            with self.assertRaises(OSError):
                serve.bind_loopback_servers(port, str(PROJECT_ROOT))
            # IPv4 must be free again — a fresh bind proves it wasn't leaked.
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", port))
            finally:
                probe.close()
        finally:
            occupier.close()

    def test_main_usage_error_returns_2(self):
        self.assertEqual(serve.main(["serve.py"]), 2)

    def test_main_lan_flag_does_not_break_usage_parsing(self):
        # --lan is additive: usage arity (port, directory) is unaffected by
        # its presence or absence, in any argv position.
        self.assertEqual(serve.main(["serve.py", "--lan"]), 2)


class NoListingHTTPRequestHandlerTests(unittest.TestCase):
    """Cheap, direct check that listing responses are 403s, without spawning
    a real server socket (that end-to-end path is covered by
    tests/test_start_sh.py::TestLanScopedServe against the real --lan
    process)."""

    def test_handler_exists_and_subclasses_simple_http_handler(self):
        self.assertTrue(hasattr(serve, "NoListingHTTPRequestHandler"))
        self.assertTrue(
            issubclass(
                serve.NoListingHTTPRequestHandler,
                __import__("http.server", fromlist=["SimpleHTTPRequestHandler"])
                .SimpleHTTPRequestHandler,
            )
        )

    def test_list_directory_returns_403_and_none(self):
        handler = serve.NoListingHTTPRequestHandler.__new__(
            serve.NoListingHTTPRequestHandler
        )
        # send_error() writes to self.wfile and reads self.request_version /
        # self.close_connection; stub the minimal surface it touches instead
        # of standing up a real socket.
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"
        handler.close_connection = True
        handler.requestline = ""
        handler.command = "GET"
        handler.client_address = ("127.0.0.1", 0)
        handler.log_message = lambda *a, **k: None

        result = handler.list_directory("/irrelevant")

        self.assertIsNone(result)
        response = handler.wfile.getvalue()
        self.assertIn(b"403", response)


if __name__ == "__main__":
    unittest.main()
