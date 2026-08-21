import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP_DATA = tempfile.TemporaryDirectory()
os.environ["AGENT_DATA_DIR"] = TEMP_DATA.name
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from investor_agent.auth import AuthSettings, LoginThrottle, SessionAuth  # noqa: E402


AUTH_SETTINGS = AuthSettings(
    enabled=True,
    username="valentin",
    password="a-test-password-123",
    session_secret="test-session-secret-with-more-than-32-characters",
    session_hours=12,
    secure_cookies="auto",
)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AuthUnitTests(unittest.TestCase):
    def test_signed_cookie_authenticates_and_tampering_fails(self):
        auth = SessionAuth(AUTH_SETTINGS)
        cookie = auth.issue_cookie("valentin", secure=True)
        cookie_pair = cookie.split(";", 1)[0]

        self.assertTrue(auth.is_authenticated(cookie_pair))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertFalse(auth.is_authenticated(cookie_pair + "changed"))
        self.assertTrue(auth.should_secure_cookie("https"))

    def test_enabled_auth_rejects_weak_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "al menos 12"):
            SessionAuth(
                AuthSettings(
                    enabled=True,
                    username="valentin",
                    password="short",
                    session_secret="test-session-secret-with-more-than-32-characters",
                )
            )

    def test_login_throttle_limits_failures_and_can_reset(self):
        throttle = LoginThrottle(max_attempts=2, window_seconds=300)

        self.assertTrue(throttle.is_allowed("local"))
        throttle.record_failure("local")
        self.assertTrue(throttle.is_allowed("local"))
        throttle.record_failure("local")
        self.assertFalse(throttle.is_allowed("local"))
        throttle.reset("local")
        self.assertTrue(throttle.is_allowed("local"))


class HttpAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_auth = app.AUTH
        app.AUTH = SessionAuth(AUTH_SETTINGS)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.AgentHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        app.AUTH = cls.original_auth

    def post_json(self, path, payload, cookie=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if cookie:
            request.add_header("Cookie", cookie)
        return urllib.request.urlopen(request, timeout=5)

    def test_private_routes_require_login_but_health_is_public(self):
        with urllib.request.urlopen(self.base_url + "/api/health", timeout=5) as response:
            self.assertEqual({"status": "ok"}, json.load(response))
            self.assertEqual("DENY", response.headers["X-Frame-Options"])

        with urllib.request.urlopen(self.base_url + "/app.css", timeout=5) as response:
            self.assertEqual("text/css", response.headers.get_content_type())
            self.assertIn(b"--teal", response.read())

        opener = urllib.request.build_opener(NoRedirectHandler())
        with self.assertRaises(urllib.error.HTTPError) as context:
            opener.open(self.base_url + "/", timeout=5)
        self.assertEqual(303, context.exception.code)
        self.assertEqual("/login", context.exception.headers["Location"])
        context.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.base_url + "/api/status", timeout=5)
        self.assertEqual(401, context.exception.code)
        context.exception.close()

    def test_login_session_and_logout(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.post_json("/api/login", {"username": "valentin", "password": "incorrect"})
        self.assertEqual(401, context.exception.code)
        context.exception.close()

        with self.post_json(
            "/api/login",
            {"username": "valentin", "password": AUTH_SETTINGS.password},
        ) as response:
            payload = json.load(response)
            set_cookie = response.headers["Set-Cookie"]
        self.assertTrue(payload["authenticated"])
        self.assertIn("HttpOnly", set_cookie)
        cookie = set_cookie.split(";", 1)[0]

        request = urllib.request.Request(
            self.base_url + "/api/session", headers={"Cookie": cookie}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            session = json.load(response)
        self.assertEqual("valentin", session["username"])
        self.assertTrue(session["auth_enabled"])

        with self.post_json("/api/logout", {}, cookie=cookie) as response:
            self.assertIn("Max-Age=0", response.headers["Set-Cookie"])


class HttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.AgentHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        TEMP_DATA.cleanup()

    def get_json(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return json.load(response)

    def test_health_and_status(self):
        self.assertEqual({"status": "ok"}, self.get_json("/api/health"))
        status = self.get_json("/api/status?session_id=smoke")
        self.assertTrue(status["excel_ready"])
        self.assertEqual(0, status["stats"]["movements"])

    def test_home_page(self):
        with urllib.request.urlopen(self.base_url + "/", timeout=5) as response:
            body = response.read().decode("utf-8")
        self.assertIn("Portfolio Ledger Agent", body)
        self.assertIn("ASISTENTE PERSONAL DE INVERSIONES", body)
        self.assertIn("<h2 id=\"research-title\">Investigación</h2>", body)
        self.assertEqual(3, body.count("section-number"))
        self.assertIn("Incluir opinión y zonas de compra o venta", body)
        self.assertIn("Fuentes gratuitas que prioriza el agente", body)
        self.assertIn("research-run-status", body)
        self.assertIn("Tus inversiones hoy", body)
        self.assertIn("portfolio-quotes", body)
        self.assertIn("<th scope=\"col\">Consenso</th>", body)
        self.assertIn("consensus-modal", body)

    def test_research_history_endpoint(self):
        payload = self.get_json("/api/research?limit=5")
        self.assertEqual([], payload["reports"])

    def test_research_source_catalog_endpoint(self):
        payload = self.get_json("/api/research/sources")
        self.assertGreaterEqual(len(payload["sources"]), 8)
        self.assertEqual("SEC EDGAR", payload["sources"][0]["name"])

    def test_empty_portfolio_valuation_endpoint(self):
        payload = self.get_json("/api/portfolio/valuation")

        self.assertEqual("ready", payload["status"])
        self.assertEqual([], payload["positions"])
        self.assertEqual({"quoted": 0, "total": 0}, payload["coverage"])

    def test_empty_portfolio_consensus_endpoint(self):
        payload = self.get_json("/api/portfolio/consensus")

        self.assertEqual([], payload["positions"])
        self.assertEqual({"available": 0, "total": 0}, payload["coverage"])


if __name__ == "__main__":
    unittest.main()
