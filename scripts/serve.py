#!/usr/bin/env python3
"""Serve Quizzler over HTTP — loopback (default) or LAN (--lan).

Two modes:
  Default (browser-local): dual-stack loopback (127.0.0.1 + ::1) so
  `localhost` works in every browser. Unauthenticated, scoped static routing.

  Shared (--shared-progress): dual-stack loopback, auth + CSRF gated,
  progress API endpoints backed by SQLite via scripts/progress_store.py.
  Intended for multiple browsers on the same Mac sharing a single
  server-authoritative progress store.

Usage: serve.py <port> <directory> [--lan] [--shared-progress]
       [--data-dir PATH] [--log-dir PATH]
       [--app-root PATH] [--packs-root PATH]
"""

from __future__ import annotations

import argparse
import errno
import functools
import html
import http.server
import json
import logging
import logging.handlers
import os
import socket
import socketserver
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Logging (WARNING+ to RotatingFileHandler, never log secrets)
# ---------------------------------------------------------------------------


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_logger = logging.getLogger("quizzler.server")


def _setup_logging(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "quizzler.log")
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_048_576, backupCount=3
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _logger.addHandler(handler)
    _logger.setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Scoped static routing
# ---------------------------------------------------------------------------


def resolve_static_path(request_path: str, route_roots: dict[str, str]) -> str | None:
    """Resolve *request_path* under *route_roots*. Returns a realpath or None.

    Path traversal (``..``) and symlink escapes are rejected. The resolved
    path must stay beneath the canonical root for its prefix.
    """
    for prefix, root in route_roots.items():
        if request_path == prefix.rstrip("/") or request_path.startswith(prefix):
            rel = request_path[len(prefix):]
            safe = rel.lstrip("/") if rel else ""

            if not safe:
                safe = "index.html" if prefix == "/app/" else ""
                if not safe and not os.path.isfile(root):
                    return None
                candidate = root
                if safe:
                    candidate = os.path.join(root, safe)
            else:
                if ".." in safe.split("/"):
                    return None
                candidate = os.path.join(root, safe)

            try:
                real_candidate = os.path.realpath(candidate)
                real_root = os.path.realpath(root)
            except OSError:
                return None

            if real_candidate == real_root:
                return real_candidate
            if not real_candidate.startswith(real_root + os.sep):
                return None
            if not os.path.isfile(real_candidate):
                return None
            return real_candidate
    return None


# ---------------------------------------------------------------------------
# Handler base — directory listings disabled
# ---------------------------------------------------------------------------


class NoListingHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, "Directory listing is disabled")
        return None


# ---------------------------------------------------------------------------
# Shared-mode handler factory
# ---------------------------------------------------------------------------


def _make_shared_handler(sp_mod, ps_mod, pairing_state, session_manager,
                         route_roots, app_root, packs_root, data_dir):

    class _Handler(NoListingHTTPRequestHandler):
        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------

        def _send_json(self, status, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for hdr, val in sp_mod.SECURITY_HEADERS.items():
                self.send_header(hdr, val)
            self.end_headers()
            self.wfile.write(data)

        def _send_json_error(self, status, error, **extra):
            body = {"error": error, **extra}
            self._send_json(status, body)

        def _read_json_body(self):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                return None
            try:
                raw = sp_mod.read_body_with_limit(
                    self.rfile, content_length
                )
                return json.loads(raw.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                self._send_json_error(400, "invalid request body")
                return None

        def _require_auth(self):
            cookie_header = self.headers.get("Cookie", "")
            cookies = sp_mod.parse_cookies(cookie_header)
            token = cookies.get("quizzler_session")
            if not token:
                self._send_json_error(401, "authentication required")
                return None
            session = session_manager.validate_session(token)
            if session is None:
                self._send_json_error(401, "session expired or invalid")
                return None
            return session

        def _require_csrf(self, session, body):
            headers = {k.lower(): v for k, v in self.headers.items()}
            request_headers = {"origin": headers.get("origin", "")}
            if not sp_mod.validate_csrf(request_headers, body, session):
                self._send_json_error(403, "csrf validation failed")
                return False
            return True

        def _is_loopback(self):
            client = self.client_address[0]
            return client in ("127.0.0.1", "::1", "localhost")

        # ------------------------------------------------------------------
        # Route dispatch
        # ------------------------------------------------------------------

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/healthz":
                body = {"status": "ok"}
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if path == "/app/" or path == "/app/index.html":
                session = self._require_auth()
                if session is None:
                    return
                return self._serve_app_html(session)

            if path.startswith("/app/"):
                session = self._require_auth()
                if session is None:
                    return
                return self._serve_static_file(path)

            if path.startswith("/question-packs/"):
                session = self._require_auth()
                if session is None:
                    return
                return self._serve_static_file(path)

            if path == "/api/v1/progress":
                session = self._require_auth()
                if session is None:
                    return
                return self._handle_get_progress()

            self.send_error(404, "Not found")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/v1/auth/pair":
                return self._handle_pair()

            if path == "/api/v1/auth/pair-local":
                return self._handle_pair_local()

            if path == "/api/v1/auth/logout":
                return self._handle_logout()

            body = self._read_json_body()
            if body is None and path not in ("/api/v1/auth/pair-local", "/api/v1/auth/logout"):
                return

            session = self._require_auth()
            if session is None:
                return

            handler_map = {
                "/api/v1/progress/import": self._handle_import_progress,
                "/api/v1/progress/quiz-completed": self._handle_quiz_completed,
                "/api/v1/progress/srs-rated": self._handle_srs_rated,
                "/api/v1/progress/reset": self._handle_reset_progress,
                "/api/v1/progress/cleanup-orphans": self._handle_cleanup_orphans,
            }

            handler = handler_map.get(path)
            if handler is None:
                self.send_error(404, "Not found")
                return

            if not self._require_csrf(session, body):
                return

            handler(session, body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin",
                             self.headers.get("Origin", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.end_headers()

        # ------------------------------------------------------------------
        # Static serving
        # ------------------------------------------------------------------

        def _serve_app_html(self, session):
            html_path = os.path.join(app_root, "index.html")
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                self.send_error(404, "Not found")
                return

            content = content.replace(
                '<meta name="quizzler-mode" content="local">',
                '<meta name="quizzler-mode" content="shared">',
            )
            csrf_tag = (
                f'<meta name="csrf-token" content="{html.escape(session["csrf_token"])}">'
            )
            content = content.replace("<head>", f"<head>\n  {csrf_tag}", 1)

            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for hdr, val in sp_mod.SECURITY_HEADERS.items():
                self.send_header(hdr, val)
            self.end_headers()
            self.wfile.write(data)

        def _serve_static_file(self, path):
            resolved = resolve_static_path(path, route_roots)
            if resolved is None or not os.path.isfile(resolved):
                self.send_error(404, "Not found")
                return

            ct_map = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript",
                ".json": "application/json",
                ".css": "text/css",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }
            ext = os.path.splitext(resolved)[1].lower()
            content_type = ct_map.get(ext, "application/octet-stream")

            try:
                with open(resolved, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(404, "Not found")
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for hdr, val in sp_mod.SECURITY_HEADERS.items():
                self.send_header(hdr, val)
            self.end_headers()
            self.wfile.write(data)

        # ------------------------------------------------------------------
        # Auth handlers
        # ------------------------------------------------------------------

        def _handle_pair(self):
            body = self._read_json_body()
            if body is None:
                return

            code = body.get("pairing_code", "")
            if not code:
                self._send_json_error(400, "missing pairing_code")
                return

            if not pairing_state.validate_code(code):
                client = self.client_address[0]
                if not pairing_state.record_failure(client):
                    _logger.warning("Rate-limited pair attempt from %s", client)
                    self._send_json_error(429, "too many attempts")
                    return
                self._send_json_error(403, "invalid pairing code")
                return

            session = session_manager.create_session()
            cookie = sp_mod.make_session_cookie(session["token"])

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            for hdr, val in sp_mod.SECURITY_HEADERS.items():
                self.send_header(hdr, val)
            body_out = json.dumps({
                "ok": True,
                "csrf_token": session["csrf_token"]
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)

        def _handle_pair_local(self):
            if not self._is_loopback():
                self._send_json_error(403, "loopback only")
                return

            code = pairing_state.get_code()
            if code is None:
                pairing_state.set_code()
                code = pairing_state.get_code()

            self._send_json(200, {"pairing_code": code})

        def _handle_logout(self):
            cookie_header = self.headers.get("Cookie", "")
            cookies = sp_mod.parse_cookies(cookie_header)
            token = cookies.get("quizzler_session")
            if token:
                session_manager.invalidate_session(token)

            expired = "quizzler_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", expired)
            self.send_header("Cache-Control", "no-store")
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------------------------
        # Progress handlers
        # ------------------------------------------------------------------

        def _handle_get_progress(self):
            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                revision, doc = ps_mod.get_progress(db_path)
            except Exception:
                _logger.exception("get_progress failed")
                self._send_json_error(500, "internal error")
                return
            self._send_json(200, {"revision": revision, "document": doc})

        def _handle_import_progress(self, session, body):
            er = body.get("expected_revision")
            if er is None:
                self._send_json_error(400, "missing expected_revision")
                return
            oid = body.get("operation_id")
            if not oid:
                self._send_json_error(400, "missing operation_id")
                return
            document = body.get("document")
            if document is None:
                self._send_json_error(400, "missing document")
                return

            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                result = ps_mod.import_progress(
                    db_path, document, oid, expected_revision=er)
                self._send_json(200, result)
            except ps_mod.RevisionConflictError as e:
                self._send_json_error(409, "conflict",
                                      current_revision=e.current_revision)
            except ValueError as e:
                self._send_json_error(400, str(e))
            except Exception:
                _logger.exception("import_progress failed")
                self._send_json_error(500, "internal error")

        def _handle_quiz_completed(self, session, body):
            er = body.get("expected_revision")
            if er is None:
                self._send_json_error(400, "missing expected_revision")
                return
            oid = body.get("operation_id")
            if not oid:
                self._send_json_error(400, "missing operation_id")
                return
            qs = body.get("session")
            md = body.get("mastery_delta")
            cid = body.get("course_id")
            pid = body.get("pack_id")
            if qs is None or md is None or not cid or not pid:
                self._send_json_error(400,
                    "missing required fields (session, mastery_delta, course_id, pack_id)")
                return

            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                result = ps_mod.quiz_completed(
                    db_path, qs, md, cid, pid, oid, expected_revision=er)
                self._send_json(200, result)
            except ps_mod.RevisionConflictError as e:
                self._send_json_error(409, "conflict",
                                      current_revision=e.current_revision)
            except ValueError as e:
                self._send_json_error(400, str(e))
            except Exception:
                _logger.exception("quiz_completed failed")
                self._send_json_error(500, "internal error")

        def _handle_srs_rated(self, session, body):
            er = body.get("expected_revision")
            if er is None:
                self._send_json_error(400, "missing expected_revision")
                return
            oid = body.get("operation_id")
            if not oid:
                self._send_json_error(400, "missing operation_id")
                return
            cid = body.get("course_id")
            ck = body.get("composite_key")
            rating = body.get("rating")
            if not all([cid, ck, rating]):
                self._send_json_error(400,
                    "missing required fields (course_id, composite_key, rating)")
                return
            if rating not in ("again", "hard", "good", "easy"):
                self._send_json_error(400, "invalid rating")
                return

            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                result = ps_mod.srs_rated(
                    db_path, cid, ck, rating, oid, expected_revision=er)
                self._send_json(200, result)
            except ps_mod.RevisionConflictError as e:
                self._send_json_error(409, "conflict",
                                      current_revision=e.current_revision)
            except ValueError as e:
                self._send_json_error(400, str(e))
            except Exception:
                _logger.exception("srs_rated failed")
                self._send_json_error(500, "internal error")

        def _handle_reset_progress(self, session, body):
            er = body.get("expected_revision")
            if er is None:
                self._send_json_error(400, "missing expected_revision")
                return
            oid = body.get("operation_id")
            if not oid:
                self._send_json_error(400, "missing operation_id")
                return
            clear_srs = body.get("clear_srs_course_id")

            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                result = ps_mod.reset_progress(
                    db_path, oid, clear_srs_course_id=clear_srs,
                    expected_revision=er)
                self._send_json(200, result)
            except ps_mod.RevisionConflictError as e:
                self._send_json_error(409, "conflict",
                                      current_revision=e.current_revision)
            except ValueError as e:
                self._send_json_error(400, str(e))
            except Exception:
                _logger.exception("reset_progress failed")
                self._send_json_error(500, "internal error")

        def _handle_cleanup_orphans(self, session, body):
            er = body.get("expected_revision")
            if er is None:
                self._send_json_error(400, "missing expected_revision")
                return
            oid = body.get("operation_id")
            if not oid:
                self._send_json_error(400, "missing operation_id")
                return
            active = body.get("active_course_ids", [])

            db_path = os.path.join(data_dir, "quizzler.sqlite3")
            try:
                result = ps_mod.cleanup_orphans(
                    db_path, active, oid, expected_revision=er)
                self._send_json(200, result)
            except ps_mod.RevisionConflictError as e:
                self._send_json_error(409, "conflict",
                                      current_revision=e.current_revision)
            except ValueError as e:
                self._send_json_error(400, str(e))
            except Exception:
                _logger.exception("cleanup_orphans failed")
                self._send_json_error(500, "internal error")

        def log_message(self, format, *args):
            return

    return _Handler


# ---------------------------------------------------------------------------
# Default-mode handler factory
# ---------------------------------------------------------------------------


def _make_default_handler(route_roots):

    class _Handler(NoListingHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            resolved = resolve_static_path(path, route_roots)
            if resolved is None:
                self.send_error(404, "Not found")
                return

            ct_map = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript",
                ".json": "application/json",
                ".css": "text/css",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
            }
            ext = os.path.splitext(resolved)[1].lower()
            content_type = ct_map.get(ext, "application/octet-stream")

            try:
                with open(resolved, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(404, "Not found")
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return _Handler


# ---------------------------------------------------------------------------
# Server binding
# ---------------------------------------------------------------------------


_IPV6_UNAVAILABLE_ERRNOS = frozenset({
    errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT,
})


def _make_server(family, addr, port, handler_class):
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        address_family = family
        daemon_threads = True

    return Server((addr, port), handler_class)


def bind_loopback_servers(port, handler_class):
    servers = []
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            servers.append(_make_server(family, addr, port, handler_class))
        except OSError as e:
            if family == socket.AF_INET6 and e.errno in _IPV6_UNAVAILABLE_ERRNOS:
                print(f"serve: IPv6 loopback unavailable, serving IPv4 only ({e})",
                      file=sys.stderr)
                continue
            for s in servers:
                s.server_close()
            raise
    return servers


def bind_lan_server(port, handler_class):
    return [_make_server(socket.AF_INET, "0.0.0.0", port, handler_class)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _import_script_module(name, rel_path):
    import importlib.util
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(script_dir, rel_path)
    spec = importlib.util.spec_from_file_location(name, full_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="Quizzler HTTP server")
    parser.add_argument("port", type=int, help="TCP port")
    parser.add_argument("directory", type=str, help="Serve directory (default mode)")
    parser.add_argument("--lan", action="store_true",
                        help="Bind all interfaces")
    parser.add_argument("--shared-progress", action="store_true",
                        help="Enable shared-progress mode with auth + API")
    parser.add_argument("--data-dir", type=str, default="./.data",
                        help="Data directory for SQLite (shared mode)")
    parser.add_argument("--log-dir", type=str, default="./.logs",
                        help="Log directory")
    parser.add_argument("--app-root", type=str, default=None,
                        help="Path to app/ directory")
    parser.add_argument("--packs-root", type=str, default=None,
                        help="Path to question-packs/ directory")

    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as e:
        return e.code if e.code is not None else 2

    port = args.port
    directory = args.directory
    lan = args.lan
    shared = args.shared_progress

    app_root = args.app_root or os.path.join(directory, "app")
    packs_root = args.packs_root or os.path.join(directory, "question-packs")

    try:
        app_root = os.path.realpath(app_root)
        packs_root = os.path.realpath(packs_root)
    except OSError as e:
        print(f"serve: cannot resolve root paths ({e})", file=sys.stderr)
        return 1

    route_roots = {
        "/app/": app_root,
        "/question-packs/": packs_root,
    }

    _setup_logging(args.log_dir)

    if shared:
        sp_mod = _import_script_module("shared_progress", "shared_progress.py")
        ps_mod = _import_script_module("progress_store", "progress_store.py")

        os.makedirs(args.data_dir, exist_ok=True)
        db_path = os.path.join(args.data_dir, "quizzler.sqlite3")
        ps_mod.init_db(db_path)

        pairing_state = sp_mod.PairingState()
        pairing_state.set_code()

        session_manager = sp_mod.SessionManager()

        handler_class = _make_shared_handler(
            sp_mod, ps_mod, pairing_state, session_manager,
            route_roots, app_root, packs_root, args.data_dir,
        )

        _logger.warning("Shared-progress mode enabled on port %d", port)
    else:
        handler_class = _make_default_handler(route_roots)

    try:
        servers = (
            bind_lan_server(port, handler_class)
            if lan
            else bind_loopback_servers(port, handler_class)
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
    sys.exit(main())
