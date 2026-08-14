import tempfile
import unittest
from pathlib import Path


import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.database import Database
from investor_agent.market_data import MarketDataService
from investor_agent.models import MovementDraft


TREASURY_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry><content><m:properties>
    <d:NEW_DATE>2026-08-13T00:00:00</d:NEW_DATE>
    <d:BC_10YEAR>4.63</d:BC_10YEAR>
  </m:properties></content></entry>
</feed>"""


class FakeTransport:
    def __init__(self):
        self.calls = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append(("json", url, params or {}))
        if "twelvedata.com/quote" in url:
            symbol = params["symbol"]
            if symbol == "FALLBACK":
                return {"status": "error", "message": "not included"}
            return {
                "symbol": symbol,
                "close": "110",
                "previous_close": "100",
                "change": "10",
                "percent_change": "10",
                "currency": "USD",
                "exchange": "NASDAQ",
                "datetime": "2026-08-13",
            }
        if "finnhub.io/api/v1/quote" in url:
            return {"c": 210, "pc": 200, "d": 10, "dp": 5, "t": 1786651200}
        if "fred/series/observations" in url:
            values = {
                "DGS10": "4.68",
                "CPIAUCSL": "332.813",
                "CPIAUCNS": "333.918",
                "UNRATE": "4.1",
            }
            return {
                "observations": [
                    {"date": "2026-07-01", "value": values[params["series_id"]]}
                ]
            }
        if "api.bls.gov" in url:
            value = "333.918" if "CUUR0000SA0" in url else "4.1"
            return {
                "Results": {
                    "series": [{"data": [{"year": "2026", "period": "M07", "value": value}]}]
                }
            }
        if url.endswith("company_tickers.json"):
            return {"0": {"cik_str": 1403161, "ticker": "V", "title": "Visa Inc."}}
        if "submissions/CIK" in url:
            return {
                "name": "Visa Inc.",
                "filings": {
                    "recent": {
                        "form": ["10-Q"],
                        "filingDate": ["2026-07-24"],
                        "accessionNumber": ["0001403161-26-000001"],
                        "primaryDocument": ["v-20260630.htm"],
                    }
                },
            }
        if "companyfacts/CIK" in url:
            return {
                "facts": {
                    "us-gaap": {
                        "NetIncomeLoss": {
                            "units": {
                                "USD": [
                                    {
                                        "val": 5600000000,
                                        "end": "2026-06-30",
                                        "filed": "2026-07-24",
                                        "form": "10-Q",
                                        "fy": 2026,
                                        "fp": "Q3",
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        raise AssertionError(f"Unexpected JSON URL: {url}")

    def get_text(self, url, *, params=None, headers=None, accept=None):
        self.calls.append(("text", url, params or {}))
        if "home.treasury.gov" in url:
            return TREASURY_XML
        raise AssertionError(f"Unexpected text URL: {url}")


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.transport = FakeTransport()

    def tearDown(self):
        self.temp.cleanup()

    def service(self, **keys):
        configured = {
            "twelve_data": "twelve-key" if keys.get("twelve", True) else "",
            "finnhub": "finnhub-key" if keys.get("finnhub", True) else "",
            "tiingo": "",
            "fmp": "",
            "fred": "fred-key" if keys.get("fred", True) else "",
        }
        return MarketDataService(
            self.db,
            keys=configured,
            transport=self.transport,
            quote_ttl_seconds=900,
            sec_user_agent="PortfolioLedgerAgent tests@example.com",
        )

    def add_buy(self, *, ref, quantity, price, ticker="TEST", currency="USDC", fee=0):
        draft = MovementDraft(
            movement_type="Compra",
            trade_date="2026-08-01",
            account="Wallbit",
            ticker=ticker,
            quantity=quantity,
            unit_price=price,
            currency=currency,
            fee=fee,
            source_text="test",
        )
        self.db.import_movement(draft, ref)

    def test_quote_falls_back_and_then_uses_persistent_cache(self):
        service = self.service()

        first = service.get_quote("FALLBACK")
        call_count = len(self.transport.calls)
        second = service.get_quote("FALLBACK")

        self.assertEqual("finnhub", first["provider"])
        self.assertEqual(210, first["price"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(call_count, len(self.transport.calls))
        self.assertIsNotNone(self.db.latest_market_quote("FALLBACK"))

    def test_portfolio_valuation_reconciles_cost_day_change_and_cash(self):
        self.add_buy(ref="buy-1", quantity=2, price=90)
        self.add_buy(ref="buy-2", quantity=1, price=100)
        self.db.upsert_cash_balance(
            as_of="2026-08-13",
            account="Wallbit",
            currency="USD",
            amount=50,
            source_text="test",
        )

        valuation = self.service().portfolio_valuation()
        position = valuation["positions"][0]

        self.assertAlmostEqual(3, position["quantity"])
        self.assertAlmostEqual(280, position["cost_basis"])
        self.assertAlmostEqual(330, position["market_value"])
        self.assertAlmostEqual(30, position["daily_change"])
        self.assertAlmostEqual(50, position["unrealized_change"])
        self.assertAlmostEqual(380, valuation["summary"]["portfolio_value"])
        self.assertIn("paridad 1 USDC = 1 USD", valuation["currency_note"])

    def test_weighted_cost_basis_survives_partial_sale(self):
        self.add_buy(ref="buy", quantity=10, price=10, fee=1)
        sale = MovementDraft(
            movement_type="Venta",
            trade_date="2026-08-02",
            account="Wallbit",
            ticker="TEST",
            quantity=4,
            unit_price=20,
            currency="USDC",
            source_text="test",
        )
        self.db.import_movement(sale, "sale")

        position = self.db.position_costs()[0]

        self.assertAlmostEqual(6, position["quantity"])
        self.assertAlmostEqual(10.1, position["average_cost"])
        self.assertAlmostEqual(60.6, position["cost_basis"])

    def test_official_macro_sources_are_normalized_and_cached(self):
        service = self.service()

        first = service.macro_snapshot()
        call_count = len(self.transport.calls)
        second = service.macro_snapshot()

        self.assertEqual("ready", first["status"])
        self.assertEqual(4.63, first["series"]["treasury_10y"]["value"])
        self.assertEqual(333.918, first["series"]["fred_cpiaucns"]["value"])
        self.assertEqual(333.918, first["series"]["bls_cpi_nsa"]["value"])
        self.assertTrue(second["cached"])
        self.assertEqual(call_count, len(self.transport.calls))

    def test_sec_snapshot_resolves_cik_filings_and_facts(self):
        service = self.service()

        result = service.sec_company_snapshot("V")

        self.assertEqual("0001403161", result["cik"])
        self.assertEqual("10-Q", result["recent_filings"][0]["form"])
        self.assertEqual(5600000000, result["facts"]["net_income"]["value"])
        self.assertIn("Archives/edgar/data/1403161/", result["recent_filings"][0]["url"])


if __name__ == "__main__":
    unittest.main()
