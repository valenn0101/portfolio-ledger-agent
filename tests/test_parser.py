import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.models import MovementDraft  # noqa: E402
from investor_agent.parser import LocalMovementParser, parse_number  # noqa: E402


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = LocalMovementParser(today_provider=lambda: date(2026, 8, 13))

    def test_complete_purchase(self):
        draft = self.parser.parse(
            "Compré 3 AAPL a 180 USD en Wallbit con 1 de comisión"
        )
        self.assertEqual("Compra", draft.movement_type)
        self.assertEqual("2026-08-13", draft.trade_date)
        self.assertEqual("Wallbit", draft.account)
        self.assertEqual("AAPL", draft.ticker)
        self.assertEqual(3, draft.quantity)
        self.assertEqual(180, draft.unit_price)
        self.assertEqual("USD", draft.currency)
        self.assertEqual(1, draft.fee)
        self.assertEqual([], draft.missing_fields())
        self.assertIn("trade_date", draft.inferred_fields)

    def test_share_quantity_wins_over_invested_cash_amount(self):
        draft = self.parser.parse(
            "Compre 1200 USD en NVDA, 5,32 acciones a 224,81 por accion",
            default_account="Wallbit",
            default_currency="USD",
        )

        self.assertEqual("NVDA", draft.ticker)
        self.assertEqual(5.32, draft.quantity)
        self.assertEqual(224.81, draft.unit_price)
        self.assertEqual(1200, draft.cash_amount)

    def test_add_existing_shares_to_portfolio_phrase(self):
        draft = self.parser.parse(
            "Agregar a mi cartera 0,902 acciones de GOOGL compradas el 29 de julio a 332,98 por accion",
            default_account="Wallbit",
            default_currency="USD",
        )

        self.assertEqual("Compra", draft.movement_type)
        self.assertEqual("2026-07-29", draft.trade_date)
        self.assertEqual("Wallbit", draft.account)
        self.assertEqual("GOOGL", draft.ticker)
        self.assertEqual(0.902, draft.quantity)
        self.assertEqual(332.98, draft.unit_price)
        self.assertEqual("USD", draft.currency)
        self.assertEqual([], draft.missing_fields())
        self.assertIn("trade_date", draft.inferred_fields)

    def test_question_about_what_to_buy_is_not_a_movement(self):
        self.assertFalse(self.parser.looks_like_movement("¿Qué comprar para mi cartera?"))

    def test_corrects_multiple_fields_in_an_existing_draft(self):
        base = MovementDraft(
            movement_type="Compra",
            trade_date="2026-07-29",
            account="Wallbit",
            ticker="GOOGL",
            quantity=0.902,
            unit_price=332.98,
            currency="USD",
            inferred_fields=["trade_date", "account", "currency"],
        )

        corrected = self.parser.parse(
            "El ticker correcto es GOOG, la cantidad correcta es 0,92, "
            "el precio correcto es 330,50, la moneda es USDC, "
            "la cuenta es Interactive Brokers, la fecha fue 2026-07-30 "
            "y la comision correcta es 1,25",
            base=base,
        )

        self.assertEqual("Compra", corrected.movement_type)
        self.assertEqual("2026-07-30", corrected.trade_date)
        self.assertEqual("Interactive Brokers", corrected.account)
        self.assertEqual("GOOG", corrected.ticker)
        self.assertEqual(0.92, corrected.quantity)
        self.assertEqual(330.50, corrected.unit_price)
        self.assertEqual("USDC", corrected.currency)
        self.assertEqual(1.25, corrected.fee)
        self.assertEqual([], corrected.inferred_fields)

    def test_changes_movement_type_only_when_explicitly_corrected(self):
        base = MovementDraft(
            movement_type="Compra",
            trade_date="2026-07-29",
            account="Wallbit",
            ticker="GOOGL",
            quantity=0.902,
            unit_price=332.98,
            currency="USD",
        )

        fee_only = self.parser.parse("La comision correcta es 1,25", base=base)
        self.assertEqual("Compra", fee_only.movement_type)

        corrected_type = self.parser.parse("En realidad fue una venta", base=fee_only)
        self.assertEqual("Venta", corrected_type.movement_type)
        self.assertEqual("GOOGL", corrected_type.ticker)
        self.assertEqual(0.902, corrected_type.quantity)

    def test_incomplete_sale_and_followup(self):
        draft = self.parser.parse("Vendí 2 Microsoft")
        self.assertEqual("MSFT", draft.ticker)
        self.assertCountEqual(
            ["account", "unit_price", "currency"], draft.missing_fields()
        )

        completed = self.parser.parse("Fue a 425 USD en Wallbit", base=draft)
        self.assertEqual("Venta", completed.movement_type)
        self.assertEqual(2, completed.quantity)
        self.assertEqual(425, completed.unit_price)
        self.assertEqual("Wallbit", completed.account)
        self.assertEqual("USD", completed.currency)
        self.assertEqual([], completed.missing_fields())

    def test_deposit(self):
        draft = self.parser.parse("Deposité 1.000,50 USD en Wallbit ayer")
        self.assertEqual("Depósito", draft.movement_type)
        self.assertEqual(1000.50, draft.cash_amount)
        self.assertEqual("2026-08-12", draft.trade_date)
        self.assertEqual([], draft.missing_fields())

    def test_defaults_are_marked_as_inferred(self):
        draft = self.parser.parse(
            "Compré 1 TSLA a 200",
            default_account="Wallbit",
            default_currency="USD",
        )
        self.assertEqual([], draft.missing_fields())
        self.assertCountEqual(
            ["trade_date", "account", "currency"], draft.inferred_fields
        )

    def test_number_formats(self):
        self.assertEqual(1234.56, parse_number("1.234,56"))
        self.assertEqual(1234.56, parse_number("1,234.56"))

    def test_fragment_without_transaction_is_not_a_new_transaction(self):
        draft = self.parser.parse("Fue a 200 USD en Wallbit")
        self.assertFalse(draft.is_transaction())
        self.assertIsNone(draft.movement_type)


if __name__ == "__main__":
    unittest.main()
