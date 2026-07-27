"""Auth primitives, sessions, CSRF, and rate limiting for shared-progress mode.

Injectable clock for testability: set ``_clock`` to a no-argument callable
that returns a Unix timestamp (default ``time.time``). Test suites override
it to freeze or advance time deterministically.

stdlib only — no external packages.
"""

from __future__ import annotations

import http.cookies
import secrets
import threading
import time
import uuid
from typing import Any, Callable

_clock: Callable[[], float] = time.time

# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

PAIRING_CODE_TTL = 600  # 10 minutes
MAX_FAILED_PAIR_PER_MINUTE = 5


def generate_pairing_code() -> str:
    return secrets.token_hex(4)


def generate_session_token() -> str:
    return secrets.token_hex(32)


class PairingState:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._lock = threading.Lock()
        self._code: str | None = None
        self._code_created_at: float = 0.0
        self._max_age = PAIRING_CODE_TTL
        self._failures: dict[str, list[float]] = {}
        self._clock = clock or _clock

    def set_code(self) -> str:
        code = generate_pairing_code()
        with self._lock:
            self._code = code
            self._code_created_at = self._clock()
        return code

    def get_code(self) -> str | None:
        with self._lock:
            if self._code is None:
                return None
            if self._clock() - self._code_created_at > self._max_age:
                self._code = None
                return None
            return self._code

    def validate_code(self, candidate: str) -> bool:
        with self._lock:
            code = self._code
            if code is None:
                return False
            if self._clock() - self._code_created_at > self._max_age:
                self._code = None
                return False
            return secrets.compare_digest(candidate, code)

    def record_failure(self, source: str) -> bool:
        """Record a failed pair attempt from *source*. Returns False if rate-limited."""
        now = self._clock()
        with self._lock:
            if source not in self._failures:
                self._failures[source] = []
            timestamps = self._failures[source]
            timestamps = [t for t in timestamps if now - t < 60]
            if len(timestamps) >= MAX_FAILED_PAIR_PER_MINUTE:
                self._failures[source] = timestamps
                return False
            timestamps.append(now)
            self._failures[source] = timestamps
            return True

    def invalidate_code(self) -> None:
        with self._lock:
            self._code = None


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

MAX_ACTIVE_SESSIONS = 4
SESSION_IDLE_EXPIRY = 12 * 3600  # 12 hours


class SessionManager:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._clock = clock or _clock

    def create_session(self) -> dict[str, Any]:
        token = generate_session_token()
        csrf_token = secrets.token_hex(32)
        now = self._clock()
        session = {
            "token": token,
            "csrf_token": csrf_token,
            "created_at": now,
            "last_activity": now,
        }
        with self._lock:
            self._prune_expired()
            if len(self._sessions) >= MAX_ACTIVE_SESSIONS:
                oldest = min(
                    self._sessions, key=lambda k: self._sessions[k]["created_at"]
                )
                del self._sessions[oldest]
            self._sessions[token] = session
        return session

    def validate_session(self, cookie_value: str) -> dict[str, Any] | None:
        with self._lock:
            self._prune_expired()
            session = self._sessions.get(cookie_value)
            if session is None:
                return None
            now = self._clock()
            if now - session["last_activity"] > SESSION_IDLE_EXPIRY:
                del self._sessions[cookie_value]
                return None
            session["last_activity"] = now
            return session

    def invalidate_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def session_count(self) -> int:
        with self._lock:
            self._prune_expired()
            return len(self._sessions)

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [
            k
            for k, s in self._sessions.items()
            if now - s["last_activity"] > SESSION_IDLE_EXPIRY
        ]
        for k in expired:
            del self._sessions[k]


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def make_session_cookie(token: str) -> str:
    c = http.cookies.SimpleCookie()
    c["quizzler_session"] = token
    c["quizzler_session"]["httponly"] = True
    c["quizzler_session"]["samesite"] = "Strict"
    c["quizzler_session"]["path"] = "/"
    return c.output(header="").strip()


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def validate_csrf(
    request_headers: dict[str, str],
    request_body: dict[str, Any] | None,
    session: dict[str, Any],
) -> bool:
    origin = request_headers.get("origin", "")
    if origin:
        if not (origin.startswith("http://localhost") or
                origin.startswith("http://127.0.0.1") or
                origin.startswith("http://[::1]")):
            return False

    body_csrf = (request_body or {}).get("csrf_token", "")
    return secrets.compare_digest(body_csrf, session["csrf_token"])


# ---------------------------------------------------------------------------
# Request parsing helpers
# ---------------------------------------------------------------------------

MAX_BODY_BYTES = 512 * 1024


def parse_cookies(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    cookie = http.cookies.SimpleCookie(header)
    return {k: v.value for k, v in cookie.items()}


def read_body_with_limit(rfile, content_length: int, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    if content_length <= 0:
        return b""
    if content_length > max_bytes:
        raise ValueError(f"Body size {content_length} exceeds limit {max_bytes}")
    return rfile.read(content_length)


def constant_time_compare(a: str | bytes, b: str | bytes) -> bool:
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return secrets.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Security headers (shared mode)
# ---------------------------------------------------------------------------


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
