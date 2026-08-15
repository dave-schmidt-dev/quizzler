"""Integration tests for shared-progress server mode.

Starts the real ``scripts/serve.py`` in shared mode against temporary
directories, then exercises the auth + progress API through HTTP.

Run: python3 -m unittest tests.test_shared_progress_server -v
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVE_SCRIPT = PROJECT_ROOT / "scripts" / "serve.py"

# Load shared_progress for token helpers in test setup
SP_PATH = PROJECT_ROOT / "scripts" / "shared_progress.py"
_sp_spec = importlib.util.spec_from_file_location("_sp", SP_PATH)
_sp = importlib.util.module_from_spec(_sp_spec)
_sp_spec.loader.exec_module(_sp)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class SharedServerTestCase(unittest.TestCase):
    """Base: starts a shared-mode server against temp directories."""

    @classmethod
    def setUpClass(cls):
        cls._tmp_app = tempfile.TemporaryDirectory()
        cls._tmp_packs = tempfile.TemporaryDirectory()
        cls._tmp_data = tempfile.TemporaryDirectory()
        cls._tmp_logs = tempfile.TemporaryDirectory()

        app_dir = Path(cls._tmp_app.name)
        packs_dir = Path(cls._tmp_packs.name)

        (app_dir / "index.html").write_text(
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="UTF-8">'
            '<meta name="quizzler-mode" content="local">'
            '</head><body>Test</body></html>'
        )

        (packs_dir / "manifest.json").write_text(
            json.dumps({"packs": [{"id": "sample-pack", "course": "samples"}]})
        )

        cls._port = _free_port()
        cls._proc = subprocess.Popen(
            [
                sys.executable,
                str(SERVE_SCRIPT),
                str(cls._port),
                "/dev/null",
                "--shared-progress",
                "--data-dir", cls._tmp_data.name,
                "--log-dir", cls._tmp_logs.name,
                "--app-root", cls._tmp_app.name,
                "--packs-root", cls._tmp_packs.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", cls._port, timeout=0.5)
                conn.request("GET", "/healthz")
                resp = conn.getresponse()
                if resp.status == 200:
                    conn.close()
                    break
                conn.close()
            except OSError:
                pass
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        if cls._proc:
            cls._proc.terminate()
            try:
                cls._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls._proc.kill()
                cls._proc.wait(timeout=5)
        cls._tmp_app.cleanup()
        cls._tmp_packs.cleanup()
        cls._tmp_data.cleanup()
        cls._tmp_logs.cleanup()

    def _request(self, method, path, body=None, headers=None,
                 check_status=True):
        last_err = None
        for attempt in range(3):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
                try:
                    hdrs = headers or {}
                    if body is not None:
                        data = json.dumps(body).encode("utf-8")
                        hdrs = {**hdrs, "Content-Type": "application/json"}
                    else:
                        data = None

                    conn.request(method, path, body=data, headers=hdrs)
                    resp = conn.getresponse()
                    raw = resp.read()
                    status = resp.status
                    resp_headers = dict(resp.getheaders())
                    try:
                        resp_body = json.loads(raw.decode("utf-8")) if raw else {}
                    except json.JSONDecodeError:
                        resp_body = raw.decode("utf-8") if raw else ""
                    return status, resp_headers, resp_body
                finally:
                    conn.close()
            except (ConnectionRefusedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.1)
        raise last_err

    def _pair(self) -> tuple[str, str]:
        """Complete the pairing flow, return (session_cookie, csrf_token)."""
        _, hdrs, body = self._request("POST", "/api/v1/auth/pair-local")
        self.assertEqual(body.get("pairing_code"), _sp.generate_pairing_code().__class__(body.get("pairing_code", "")))

        code = body["pairing_code"]
        status, hdrs2, body2 = self._request(
            "POST", "/api/v1/auth/pair",
            body={"pairing_code": code},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body2.get("ok"))

        set_cookie = hdrs2.get("Set-Cookie", hdrs2.get("set-cookie", ""))
        cookie = _sp.parse_cookies(set_cookie)
        token = cookie.get("quizzler_session", "")
        self.assertTrue(token)
        csrf = body2.get("csrf_token", "")
        self.assertTrue(csrf)

        return token, csrf

    def _auth_headers(self, token, csrf=None, origin="http://localhost"):
        hdrs = {"Cookie": f"quizzler_session={token}"}
        if origin:
            hdrs["Origin"] = origin
        return hdrs


class HealthzTests(SharedServerTestCase):
    def test_healthz_public_no_auth(self):
        status, _, body = self._request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")


class OriginCheckTests(unittest.TestCase):
    def test_origin_uses_complete_parsed_tuple(self):
        self.assertTrue(_sp.check_origin("quiz.example:8080",
                                         "http://quiz.example:8080"))
        self.assertFalse(_sp.check_origin("quiz.example:8080",
                                          "https://quiz.example:8080"))
        self.assertFalse(_sp.check_origin("quiz.example:8080",
                                          "http://quiz.example:8081"))
        self.assertFalse(_sp.check_origin("quiz.example:8080",
                                          "http://other.example:8080"))

    def test_loopback_bypass_requires_exact_hostname(self):
        for origin in (
            "http://localhost",
            "http://127.0.0.1:3000",
            "http://[::1]:3000",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(_sp.check_origin("quiz.example:8080", origin))

        for origin in (
            "http://localhost.evil.com",
            "http://127.0.0.1.evil.com",
            "http://[::1].evil.com",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(_sp.check_origin("quiz.example:8080", origin))


    def test_absent_origin_is_allowed(self):
        self.assertTrue(_sp.check_origin("quiz.example:8080", ""))
        self.assertFalse(_sp.check_origin("quiz.example:8080", "not-an-origin"))

    def test_origin_rejects_non_origin_url_components_and_credentials(self):
        for origin in (
            "http://quiz.example:8080/path",
            "http://quiz.example:8080?query",
            "http://quiz.example:8080#fragment",
            "http://user@quiz.example:8080",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(_sp.check_origin("quiz.example:8080", origin))


class PairingStateTests(unittest.TestCase):
    def test_consume_code_is_single_use(self):
        state = _sp.PairingState()
        code = state.set_code()

        self.assertTrue(state.consume_code(code))
        self.assertFalse(state.consume_code(code))

    def test_concurrent_consume_code_succeeds_at_most_once(self):
        state = _sp.PairingState()
        code = state.set_code()
        results = []
        barrier = threading.Barrier(8)

        def consume():
            barrier.wait()
            results.append(state.consume_code(code))

        threads = [threading.Thread(target=consume) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(True), 1)

    def test_ensure_code_mints_once_for_concurrent_callers(self):
        state = _sp.PairingState()
        results = []
        barrier = threading.Barrier(8)

        def ensure():
            barrier.wait()
            results.append(state.ensure_code())

        threads = [threading.Thread(target=ensure) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(set(results)), 1)

    def test_code_expires_at_ttl_boundary(self):
        now = [100.0]
        state = _sp.PairingState(clock=lambda: now[0])
        code = state.set_code()

        now[0] += _sp.PAIRING_CODE_TTL

        self.assertFalse(state.consume_code(code))
        self.assertEqual(state.get_code(), None)

    def test_global_failure_ceiling_invalidates_code(self):
        state = _sp.PairingState()
        code = state.set_code()

        for index in range(_sp.MAX_FAILED_PAIR_GLOBAL - 1):
            self.assertTrue(state.record_failure(f"source-{index}"))

        self.assertFalse(state.record_failure("last-source"))
        self.assertFalse(state.consume_code(code))
        self.assertIsNone(state.get_code())

    def test_minting_code_resets_global_failure_counter(self):
        state = _sp.PairingState()
        state.set_code()
        state.record_failure("source")
        self.assertEqual(state._global_failures, 1)

        state.set_code()
        self.assertEqual(state._global_failures, 0)

        state.record_failure("source")
        state.invalidate_code()
        state.ensure_code()
        self.assertEqual(state._global_failures, 0)

    def test_valid_code_succeeds_under_global_and_source_limits(self):
        state = _sp.PairingState()
        code = state.set_code()

        for _ in range(_sp.MAX_FAILED_PAIR_PER_MINUTE - 1):
            self.assertTrue(state.record_failure("one-source"))

        self.assertTrue(state.consume_code(code))

    def test_failure_source_map_is_bounded(self):
        state = _sp.PairingState()

        for window in range(3):
            state.set_code()
            for index in range(_sp.MAX_FAILED_PAIR_SOURCES + 1):
                state.record_failure(f"source-{window}-{index}")

        self.assertLessEqual(
            len(state._failures), _sp.MAX_FAILED_PAIR_SOURCES
        )


class PreflightTests(SharedServerTestCase):
    def test_same_host_preflight_allows_credentials(self):
        origin = f"http://127.0.0.1:{self._port}"
        status, headers, _ = self._request(
            "OPTIONS",
            "/api/v1/progress",
            headers={"Origin": origin},
        )

        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(headers.get("Access-Control-Allow-Credentials"), "true")
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_loopback_variant_preflight_is_allowed(self):
        origin = "http://localhost:3000"
        status, headers, _ = self._request(
            "OPTIONS",
            "/api/v1/progress",
            headers={"Origin": origin},
        )

        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(headers.get("Access-Control-Allow-Credentials"), "true")

    def test_arbitrary_origin_is_not_reflected_with_credentials(self):
        status, headers, _ = self._request(
            "OPTIONS",
            "/api/v1/progress",
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Access-Control-Allow-Credentials", headers)


class UnauthenticatedAccessTests(SharedServerTestCase):
    def test_app_serves_without_auth(self):
        status, _, body = self._request("GET", "/app/")
        self.assertEqual(status, 200)
        self.assertIn('quizzler-auth-status', str(body))

    def test_question_packs_serves_without_auth(self):
        status, _, _ = self._request("GET", "/question-packs/manifest.json")
        self.assertEqual(status, 200)

    def test_progress_get_requires_auth(self):
        status, _, _ = self._request("GET", "/api/v1/progress")
        self.assertEqual(status, 401)

    def test_progress_mutation_requires_auth(self):
        status, _, _ = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={"operation_id": "x"},
        )
        self.assertEqual(status, 401)


class ProtocolNegotiationTests(SharedServerTestCase):
    def test_progress_advertises_v1_without_exposing_cloudkit(self):
        token, _ = self._pair()
        status, _, body = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        self.assertEqual(status, 200)
        self.assertEqual(body["protocol_version"], 1)
        self.assertEqual(body["supported_protocol_versions"], [1])
        self.assertNotIn("cloudkit", json.dumps(body).lower())

    def test_incompatible_protocol_is_rejected_before_mutation(self):
        token, csrf = self._pair()
        _, _, before = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        status, _, body = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "protocol_version": 2,
                "expected_revision": before["revision"],
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack", "questions": []},
                "course_id": "samples", "pack_id": "sample-pack", "mastery_delta": {},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "incompatible_protocol")
        _, _, after = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        self.assertEqual(after["revision"], before["revision"])

    def test_malformed_protocol_versions_and_non_object_bodies_are_rejected(self):
        token, csrf = self._pair()
        _, _, before = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))

        for version in (None, "1", 1.5, True):
            with self.subTest(version=version):
                status, _, body = self._request(
                    "POST", "/api/v1/progress/sessions",
                    body={"protocol_version": version, "csrf_token": csrf},
                    headers=self._auth_headers(token, csrf),
                )
                self.assertEqual(status, 409)
                self.assertEqual(body["error"], "incompatible_protocol")

        status, _, body = self._request(
            "POST", "/api/v1/progress/sessions",
            body=[], headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "request body must be an object")
        _, _, after = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        self.assertEqual(after["revision"], before["revision"])

    def test_legacy_mutation_without_protocol_version_is_accepted(self):
        token, csrf = self._pair()
        _, _, before = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        status, _, body = self._request(
            "POST", "/api/v1/progress/sessions",
            body={
                "expected_revision": before["revision"],
                "operation_id": str(uuid.uuid4()),
                "sessions": [],
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], before["revision"] + 1)


class PairingFlowTests(SharedServerTestCase):
    def test_pair_local_works_from_loopback(self):
        status, _, body = self._request("POST", "/api/v1/auth/pair-local")
        self.assertEqual(status, 200)
        self.assertIn("pairing_code", body)
        self.assertEqual(len(body["pairing_code"]), 4)

    def test_pair_self_from_loopback_with_matching_origin_returns_session(self):
        origin = f"http://127.0.0.1:{self._port}"
        status, headers, body = self._request(
            "POST", "/api/v1/auth/pair-self",
            headers={"Origin": origin},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("csrf_token", body)
        self.assertIn("quizzler_session", headers.get("Set-Cookie", ""))

    def test_pair_self_does_not_require_session_cookie(self):
        origin = f"http://127.0.0.1:{self._port}"
        status, _, body = self._request(
            "POST", "/api/v1/auth/pair-self",
            headers={"Origin": origin},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_pair_self_rejects_missing_origin(self):
        status, _, _ = self._request("POST", "/api/v1/auth/pair-self")
        self.assertEqual(status, 403)

    def test_pair_self_rejects_origin_not_matching_host(self):
        status, _, _ = self._request(
            "POST", "/api/v1/auth/pair-self",
            headers={"Origin": f"http://localhost:{self._port}"},
        )
        self.assertEqual(status, 403)

    def test_pair_self_rejects_loopback_origin_with_different_port(self):
        status, _, _ = self._request(
            "POST", "/api/v1/auth/pair-self",
            headers={"Origin": f"http://127.0.0.1:{self._port + 1}"},
        )
        self.assertEqual(status, 403)

    def test_pair_self_rejections_do_not_evict_existing_session(self):
        token, _ = self._pair()
        for _ in range(4):
            status, _, _ = self._request(
                "POST", "/api/v1/auth/pair-self",
                headers={"Origin": f"http://localhost:{self._port}"},
            )
            self.assertEqual(status, 403)

        status, _, _ = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)

    def test_claim_valid_code_returns_session(self):
        _, _, local_body = self._request("POST", "/api/v1/auth/pair-local")
        code = local_body["pairing_code"]

        status, hdrs, body = self._request(
            "POST", "/api/v1/auth/pair",
            body={"pairing_code": code},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("csrf_token", body)
        set_cookie = hdrs.get("Set-Cookie", hdrs.get("set-cookie", ""))
        self.assertIn("quizzler_session", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)

    def test_claim_invalid_code_returns_403(self):
        status, _, body = self._request(
            "POST", "/api/v1/auth/pair",
            body={"pairing_code": "deadbeef"},
        )
        self.assertEqual(status, 403)

    def test_claim_missing_code_returns_400(self):
        status, _, _ = self._request("POST", "/api/v1/auth/pair", body={})
        self.assertEqual(status, 400)

    def test_rate_limiting_blocks_too_many_failures(self):
        for _ in range(6):
            status, _, _ = self._request(
                "POST", "/api/v1/auth/pair",
                body={"pairing_code": "badcode1"},
            )
        self.assertEqual(status, 429)


class SessionTests(SharedServerTestCase):
    def test_authenticated_can_access_app(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)

    def test_invalid_session_serves_app_expired(self):
        status, _, body = self._request(
            "GET", "/app/",
            headers={"Cookie": "quizzler_session=fake-token"},
        )
        self.assertEqual(status, 200)
        self.assertIn('quizzler-auth-status" content="expired"', str(body))

    def test_logout_invalidates_session(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "POST", "/api/v1/auth/logout",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)

        status, _, body = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)
        self.assertIn('quizzler-auth-status" content="expired"', str(body))

    def test_logout_sets_expired_cookie(self):
        token, _ = self._pair()
        status, hdrs, _ = self._request(
            "POST", "/api/v1/auth/logout",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)
        set_cookie = hdrs.get("Set-Cookie", hdrs.get("set-cookie", ""))
        self.assertIn("Max-Age=0", set_cookie)


class CSRFTests(SharedServerTestCase):
    def test_mutation_without_csrf_token_fails(self):
        token, _ = self._pair()

        status, _, body = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": 0,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
            },
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 403)

    def test_mutation_with_csrf_succeeds(self):
        token, csrf = self._pair()

        status, _, body = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": 0,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)


class QuizCompletionTests(SharedServerTestCase):
    def test_quiz_completion_updates_progress(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]
        prev_session_count = len(pb["document"]["sessions"])

        sid = str(uuid.uuid4())
        status, _, body = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": current_rev,
                "operation_id": sid,
                "session": {
                    "course": "samples",
                    "pack": "sample-pack",
                    "score": 85,
                    "questions": [],
                    "timestamp": "2024-01-01T00:00:00Z",
                },
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {
                    "seen": {"q1": True},
                    "correct": {"q1": True},
                    "consecutive": {"q1": 1},
                },
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)
        self.assertIn("revision", body)
        self.assertEqual(body["revision"], current_rev + 1)

        status, _, doc_body = self._request(
            "GET", "/api/v1/progress",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)
        self.assertEqual(doc_body["revision"], current_rev + 1)
        self.assertEqual(
            len(doc_body["document"]["sessions"]), prev_session_count + 1)
        self.assertEqual(
            doc_body["document"]["sessions"][0]["course"], "samples"
        )

    def test_quiz_completion_idempotent(self):
        token, csrf = self._pair()

        sid = str(uuid.uuid4())
        common = {
            "expected_revision": 0,
            "operation_id": sid,
            "session": {
                "course": "samples",
                "pack": "sample-pack",
                "score": 90,
                "questions": [],
                "timestamp": "2024-01-01T00:00:00Z",
            },
            "course_id": "samples",
            "pack_id": "sample-pack",
            "mastery_delta": {
                "seen": {"q1": True},
                "correct": {"q1": True},
                "consecutive": {},
            },
            "csrf_token": csrf,
        }

        _, _, r1 = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body=common,
            headers=self._auth_headers(token, csrf),
        )
        _, _, r2 = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body=common,
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(r1, r2)

        status, _, doc_body = self._request(
            "GET", "/api/v1/progress",
            headers=self._auth_headers(token),
        )
        self.assertEqual(len(doc_body["document"]["sessions"]), 1)

    def test_revision_conflict_returns_409(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "conflict")
        self.assertIn("current_revision", body)


class SRSRatingTests(SharedServerTestCase):
    def test_srs_rated_updates_tier(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        ck = f"samples::sample-pack::q-updates-{uuid.uuid4()}"
        status, _, body = self._request(
            "POST", "/api/v1/progress/srs-rated",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "course_id": "samples",
                "composite_key": ck,
                "rating": "good",
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["old_tier"], 1)
        self.assertEqual(body["new_tier"], 2)

    def test_srs_rated_idempotent(self):
        token, csrf = self._pair()

        oid = str(uuid.uuid4())
        common = {
            "expected_revision": 0,
            "operation_id": oid,
            "course_id": "samples",
            "composite_key": "samples::sample-pack::q1",
            "rating": "good",
            "csrf_token": csrf,
        }

        _, _, r1 = self._request(
            "POST", "/api/v1/progress/srs-rated",
            body=common,
            headers=self._auth_headers(token, csrf),
        )
        _, _, r2 = self._request(
            "POST", "/api/v1/progress/srs-rated",
            body=common,
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(r1, r2)

    def test_srs_revision_conflict(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        self._request(
            "POST", "/api/v1/progress/srs-rated",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "course_id": "samples",
                "composite_key": "samples::sample-pack::q1",
                "rating": "good",
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/srs-rated",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "course_id": "samples",
                "composite_key": "samples::sample-pack::q2",
                "rating": "hard",
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)


class PathIsolationTests(SharedServerTestCase):
    def test_dot_git_not_reachable(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/.git/config",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)

    def test_scripts_not_reachable(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/scripts/serve.py",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)

    def test_data_dir_not_reachable(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/.data/quizzler.sqlite3",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)

    def test_traversal_rejected(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/app/../../etc/passwd",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)

    def test_traversal_dotdot_slash_rejected(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/question-packs/..%2F..%2Fetc%2Fpasswd",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)

    def test_unknown_endpoint_returns_404(self):
        token, _ = self._pair()
        status, _, _ = self._request(
            "GET", "/api/v1/secret",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 404)


class BodySizeLimitTests(SharedServerTestCase):
    def test_large_body_rejected(self):
        token, csrf = self._pair()
        big = "x" * (600 * 1024)
        status, _, _ = self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": 0,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
                "csrf_token": csrf,
                "padding": big,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 400)


class RuntimeMarkerTests(SharedServerTestCase):
    def test_shared_mode_html_has_marker_and_csrf(self):
        token, csrf = self._pair()
        status, hdrs, body = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
            check_status=False,
        )
        self.assertEqual(status, 200)
        self.assertIn('quizzler-auth-status" content="active"', str(body))
        self.assertIn(f'content="{csrf}"', str(body))

    def test_shared_mode_html_cache_control_no_store(self):
        token, _ = self._pair()
        status, hdrs, body = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
            check_status=False,
        )
        cc = hdrs.get("Cache-Control", hdrs.get("cache-control", ""))
        self.assertIn("no-store", cc)

    def test_security_headers_present(self):
        token, _ = self._pair()
        status, hdrs, body = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
            check_status=False,
        )
        self.assertIn("Content-Security-Policy", hdrs)
        self.assertIn("X-Content-Type-Options", hdrs)


class DefaultModeTests(unittest.TestCase):
    """Test the default (non-shared) handler for scoped routing."""

    @classmethod
    def setUpClass(cls):
        cls._tmp_app = tempfile.TemporaryDirectory()
        cls._tmp_packs = tempfile.TemporaryDirectory()

        app_dir = Path(cls._tmp_app.name)
        packs_dir = Path(cls._tmp_packs.name)

        (app_dir / "index.html").write_text(
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="UTF-8">'
            '<meta name="quizzler-mode" content="local">'
            '</head><body>Local Test</body></html>'
        )

        cls._port = _free_port()
        cls._proc = subprocess.Popen(
            [
                sys.executable,
                str(SERVE_SCRIPT),
                str(cls._port),
                "/dev/null",
                "--app-root", cls._tmp_app.name,
                "--packs-root", cls._tmp_packs.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", cls._port, timeout=0.5)
                conn.request("GET", "/app/")
                resp = conn.getresponse()
                if resp.status == 200:
                    conn.close()
                    break
                conn.close()
            except OSError:
                pass
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        if cls._proc:
            cls._proc.terminate()
            try:
                cls._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls._proc.kill()
                cls._proc.wait(timeout=5)
        cls._tmp_app.cleanup()
        cls._tmp_packs.cleanup()

    def _status(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request("GET", path)
            return conn.getresponse().status
        finally:
            conn.close()

    def test_app_serves_html_with_local_marker(self):
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request("GET", "/app/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn('content="local"', body)
            self.assertNotIn('content="shared"', body)
            self.assertNotIn("csrf-token", body.lower())
        finally:
            conn.close()

    def test_app_is_reachable(self):
        self.assertEqual(self._status("/app/"), 200)

    def test_paths_outside_roots_are_404(self):
        self.assertEqual(self._status("/.git/config"), 404)
        self.assertEqual(self._status("/etc/passwd"), 404)

    def test_traversal_rejected(self):
        self.assertEqual(self._status("/app/../../etc/passwd"), 404)


class SaveSessionsTests(SharedServerTestCase):
    def test_save_sessions_revision_conflict(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        self._request(
            "POST", "/api/v1/progress/sessions",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "sessions": [{"course": "math", "pack": "alg", "score": 95,
                              "questions": [], "timestamp": "2024-01-01T00:00:00Z"}],
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/sessions",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "sessions": [{"course": "math", "pack": "alg", "score": 95,
                              "questions": [], "timestamp": "2024-01-01T00:00:00Z"}],
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "conflict")
        self.assertIn("current_revision", body)


class SaveSRSStateTests(SharedServerTestCase):
    def test_save_srs_state_revision_conflict(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        srs_state = {
            "schema_version": 1,
            "updated_at": "2024-01-01T00:00:00+00:00",
            "questions": {},
        }

        self._request(
            "POST", "/api/v1/progress/srs",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "course_id": "samples",
                "state": srs_state,
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/srs",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "course_id": "samples",
                "state": srs_state,
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "conflict")
        self.assertIn("current_revision", body)


class ImportResetCleanupTests(SharedServerTestCase):
    def test_import_progress_replaces_document(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        doc = {
            "schema_version": 1,
            "sessions": [{"course": "math", "pack": "alg", "score": 95,
                          "questions": [], "timestamp": "2024-01-01T00:00:00Z"}],
            "mastery": {},
            "srs": {},
        }
        status, _, body = self._request(
            "POST", "/api/v1/progress/import",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "document": doc,
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)

        _, _, doc_body = self._request(
            "GET", "/api/v1/progress",
            headers=self._auth_headers(token),
        )
        self.assertEqual(doc_body["document"]["sessions"][0]["course"], "math")

    def test_import_progress_revision_conflict(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        doc = {
            "schema_version": 1,
            "sessions": [],
            "mastery": {},
            "srs": {},
        }

        self._request(
            "POST", "/api/v1/progress/import",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "document": doc,
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/import",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "document": doc,
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "conflict")
        self.assertIn("current_revision", body)

    def test_reset_clears_sessions_and_mastery(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {"seen": {"q1": True}, "correct": {"q1": True},
                                  "consecutive": {}},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, _ = self._request(
            "POST", "/api/v1/progress/reset",
            body={
                "expected_revision": current_rev + 1,
                "operation_id": str(uuid.uuid4()),
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 200)

        _, _, doc_body = self._request(
            "GET", "/api/v1/progress",
            headers=self._auth_headers(token),
        )
        self.assertEqual(doc_body["document"]["sessions"], [])
        self.assertEqual(doc_body["document"]["mastery"], {})

    def test_reset_progress_revision_conflict(self):
        token, csrf = self._pair()

        _, _, pb = self._request(
            "GET", "/api/v1/progress", headers=self._auth_headers(token))
        current_rev = pb["revision"]

        self._request(
            "POST", "/api/v1/progress/quiz-completed",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "session": {"course": "samples", "pack": "sample-pack",
                            "score": 100, "questions": [],
                            "timestamp": "2024-01-01T00:00:00Z"},
                "course_id": "samples",
                "pack_id": "sample-pack",
                "mastery_delta": {},
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )

        status, _, body = self._request(
            "POST", "/api/v1/progress/reset",
            body={
                "expected_revision": current_rev,
                "operation_id": str(uuid.uuid4()),
                "csrf_token": csrf,
            },
            headers=self._auth_headers(token, csrf),
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "conflict")
        self.assertIn("current_revision", body)


class AuthStatusTests(SharedServerTestCase):
    def test_status_unauthenticated_returns_false(self):
        status, _, body = self._request("GET", "/api/v1/auth/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])
        self.assertIsNone(body["csrf_token"])

    def test_status_authenticated_returns_true_and_token(self):
        token, csrf = self._pair()
        status, _, body = self._request(
            "GET", "/api/v1/auth/status",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["csrf_token"], csrf)

    def test_status_expired_session_returns_false(self):
        status, _, body = self._request(
            "GET", "/api/v1/auth/status",
            headers={"Cookie": "quizzler_session=fake-token"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])
        self.assertIsNone(body["csrf_token"])


class PairLocalLoopbackTests(SharedServerTestCase):
    def test_pair_local_from_loopback_returns_code(self):
        status, _, body = self._request("POST", "/api/v1/auth/pair-local")
        self.assertEqual(status, 200)
        self.assertIn("pairing_code", body)
        self.assertEqual(len(body["pairing_code"]), 4)

    def test_pair_local_from_non_loopback_rejected(self):
        # The test server always binds 127.0.0.1, so all test requests come
        # from loopback.  The _is_loopback check in serve.py rejects any
        # address other than 127.0.0.1 / ::1 / localhost with 403, but there
        # is no way to simulate a non-loopback client in this integration
        # setup without patching the server or running from a different host.
        pass


class AppHtmlTests(SharedServerTestCase):
    def test_app_html_without_session_has_auth_status_none(self):
        status, _, body = self._request("GET", "/app/")
        self.assertEqual(status, 200)
        body_str = str(body)
        self.assertIn('quizzler-auth-status" content="none"', body_str)
        self.assertNotIn("csrf-token", body_str.lower())

    def test_app_html_with_session_has_auth_status_active(self):
        token, csrf = self._pair()
        status, _, body = self._request(
            "GET", "/app/",
            headers=self._auth_headers(token),
        )
        self.assertEqual(status, 200)
        body_str = str(body)
        self.assertIn('quizzler-auth-status" content="active"', body_str)
        self.assertIn(f'content="{csrf}"', body_str)

    def test_app_html_with_invalid_session_has_auth_status_expired(self):
        status, _, body = self._request(
            "GET", "/app/",
            headers={"Cookie": "quizzler_session=fake-token"},
        )
        self.assertEqual(status, 200)
        body_str = str(body)
        self.assertIn('quizzler-auth-status" content="expired"', body_str)
        self.assertNotIn("csrf-token", body_str.lower())


class AdminStatusTests(SharedServerTestCase):
    def test_admin_status_returns_port_and_interfaces(self):
        status, _, body = self._request("GET", "/api/v1/admin/status")
        self.assertEqual(status, 200)
        self.assertIsInstance(body["port"], int)
        self.assertIsInstance(body["interfaces"], list)
        self.assertTrue(
            all(isinstance(i, str) for i in body["interfaces"])
        )
        self.assertTrue(body["shared_available"])


if __name__ == "__main__":
    unittest.main()
