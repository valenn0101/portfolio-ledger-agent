import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP_DATA = tempfile.TemporaryDirectory()
os.environ["AGENT_DATA_DIR"] = TEMP_DATA.name
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


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
        self.assertIn("Buscar oportunidades con fuentes", body)
        self.assertIn("Incluir opinión y zonas de compra o venta", body)
        self.assertIn("Fuentes gratuitas que prioriza el agente", body)
        self.assertIn("research-run-status", body)
        self.assertIn("Tus inversiones hoy", body)
        self.assertIn("portfolio-quotes", body)

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


if __name__ == "__main__":
    unittest.main()
