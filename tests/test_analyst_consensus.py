import tempfile
import unittest
from pathlib import Path


import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.analyst_consensus import (  # noqa: E402
    AnalystConsensusService,
    normalize_rating,
)
from investor_agent.database import Database  # noqa: E402


class AnalystTransport:
    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append((url, params or {}))
        if self.empty:
            return []
        if "price-target-consensus" in url:
            return [
                {
                    "symbol": "TEST",
                    "targetHigh": 210,
                    "targetLow": 130,
                    "targetMedian": 180,
                    "targetConsensus": 176,
                }
            ]
        if "grades-consensus" in url:
            return [
                {
                    "symbol": "TEST",
                    "strongBuy": 4,
                    "buy": 8,
                    "hold": 3,
                    "sell": 1,
                    "strongSell": 0,
                    "consensus": "Buy",
                }
            ]
        if url.endswith("/grades"):
            return [
                {
                    "symbol": "TEST",
                    "date": "2026-08-15",
                    "gradingCompany": "Example Research",
                    "previousGrade": "Neutral",
                    "newGrade": "Outperform",
                    "action": "upgrade",
                },
                {
                    "symbol": "TEST",
                    "date": "2026-07-01",
                    "gradingCompany": "Second Research",
                    "previousGrade": "Buy",
                    "newGrade": "Buy",
                    "action": "maintain",
                },
            ]
        if "price-target-news" in url:
            raise RuntimeError("HTTP 402: plan required")
        raise AssertionError(f"Unexpected URL: {url}")


class AnalystConsensusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "analysts.db")

    def tearDown(self):
        self.temp.cleanup()

    def service(self, transport):
        return AnalystConsensusService(
            self.db,
            keys={"fmp": "fmp-key", "finnhub": "", "twelve_data": ""},
            transport=transport,
            ttl_seconds=86_400,
        )

    def test_fmp_consensus_is_normalized_and_cached(self):
        transport = AnalystTransport()
        service = self.service(transport)

        first = service.consensus("TEST", current_price=150)
        call_count = len(transport.calls)
        second = service.consensus("TEST", current_price=150)

        self.assertEqual("ready", first["status"])
        self.assertEqual(180, first["selected_target"])
        self.assertAlmostEqual(20, first["upside_percent"])
        self.assertEqual("buy", first["ratings"]["label_code"])
        self.assertEqual(16, first["ratings"]["total"])
        self.assertEqual(call_count, len(transport.calls))
        self.assertTrue(second["providers"][0]["cached"])
        self.assertIsNotNone(
            self.db.latest_market_snapshot_for_provider(
                "analyst_consensus", "TEST", "fmp"
            )
        )

    def test_detail_preserves_raw_grades_and_plan_limitation(self):
        transport = AnalystTransport()
        result = self.service(transport).consensus("TEST", details=True)

        self.assertEqual(2, result["action_count"])
        self.assertEqual("Example Research", result["actions"][0]["firm"])
        self.assertEqual("Outperform", result["actions"][0]["rating_to"])
        self.assertEqual("buy", result["actions"][0]["normalized_rating"])
        self.assertIn("plan actual", result["providers"][0]["limitations"][0])

    def test_empty_provider_is_not_mislabeled_as_hold(self):
        result = self.service(AnalystTransport(empty=True)).consensus("TEST")

        self.assertEqual("no_coverage", result["status"])
        self.assertIsNone(result["ratings"])
        self.assertIsNone(result["selected_target"])

    def test_rating_vocabulary_is_normalized_without_losing_raw_value(self):
        self.assertEqual("strong_buy", normalize_rating("Conviction Buy"))
        self.assertEqual("buy", normalize_rating("Overweight"))
        self.assertEqual("hold", normalize_rating("Market Perform"))
        self.assertEqual("reduce", normalize_rating("Underperform"))
        self.assertEqual("sell", normalize_rating("Strong Sell"))


if __name__ == "__main__":
    unittest.main()
