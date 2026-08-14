import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_market_providers import quote_result, safe_error, search_rows


class ProviderDiagnosticTests(unittest.TestCase):
    def test_safe_error_redacts_credentials(self):
        message = safe_error(ValueError("token=super-secret"), ["super-secret"])

        self.assertNotIn("super-secret", message)
        self.assertIn("<redacted>", message)

    def test_quote_result_rejects_missing_or_non_positive_price(self):
        self.assertFalse(quote_result("demo", "V", price=None)["ok"])
        self.assertFalse(quote_result("demo", "V", price=0)["ok"])
        self.assertTrue(quote_result("demo", "V", price="365.45")["ok"])

    def test_search_rows_normalizes_provider_shapes(self):
        rows = search_rows(
            {
                "result": [
                    {
                        "symbol": "000660.KS",
                        "description": "SK Hynix Inc",
                        "type": "Common Stock",
                    }
                ]
            }
        )

        self.assertEqual(rows[0]["symbol"], "000660.KS")
        self.assertEqual(rows[0]["name"], "SK Hynix Inc")
        self.assertEqual(rows[0]["type"], "Common Stock")


if __name__ == "__main__":
    unittest.main()
