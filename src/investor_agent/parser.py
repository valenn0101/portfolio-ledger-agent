from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

from .models import MOVEMENT_TYPES, MovementDraft


COMPANY_ALIASES = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "mercado libre": "MELI",
    "mercadolibre": "MELI",
    "coca cola": "KO",
    "coca-cola": "KO",
    "berkshire": "BRK.B",
    "amd": "AMD",
    "visa": "V",
    "ebay": "EBAY",
    "ovintiv": "OVV",
    "ascent solar": "ASTI",
}

KNOWN_ACCOUNTS = {
    "wallbit": "Wallbit",
    "iol": "IOL",
    "invertir online": "IOL",
    "balanz": "Balanz",
    "bull market": "Bull Market",
    "ppi": "PPI",
    "portfolio personal": "PPI",
    "interactive brokers": "Interactive Brokers",
    "ibkr": "Interactive Brokers",
    "binance": "Binance",
}

CURRENCY_ALIASES = {
    "usdc": "USDC",
    "usd": "USD",
    "us$": "USD",
    "dolar": "USD",
    "dolares": "USD",
    "ars": "ARS",
    "peso": "ARS",
    "pesos": "ARS",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "brl": "BRL",
    "reales": "BRL",
}

TYPE_PATTERNS = (
    ("Dividendo", r"\bdividend(?:o|os)\b"),
    (
        "Compra",
        r"\b(?:compre|compramos|compra|comprad[oa]s?|adquiri|"
        r"agregar\s+(?:a\s+)?(?:mi\s+)?cartera)\b",
    ),
    ("Venta", r"\b(?:vendi|vendimos|venta|vendid[oa]s?)\b"),
    ("Depósito", r"\b(?:deposite|deposito|ingrese|aporte|transferi)\b"),
    ("Retiro", r"\b(?:retire|retiro|extraje)\b"),
    ("Interés", r"\bintere(?:s|ses)\b"),
    ("Comisión", r"\bcomision(?:es)?\b"),
    ("Impuesto", r"\bimpuesto(?:s)?\b"),
)

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

STOP_TICKERS = {
    "USDC",
    "USD",
    "ARS",
    "EUR",
    "BRL",
    "HOY",
    "AYER",
    "EN",
    "DE",
    "POR",
    "CON",
    "SIN",
}


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalized_text(value: str) -> str:
    return strip_accents(value).lower().strip()


def parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip().replace(" ", "").strip(".,")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class LocalMovementParser:
    """Intérprete conservador para las frases habituales de compra y venta."""

    def __init__(self, today_provider=date.today):
        self.today_provider = today_provider

    def looks_like_movement(self, text: str) -> bool:
        """Detecta intención transaccional sin clasificar consultas como “qué comprar”."""
        return self._movement_type(normalized_text(text)) is not None

    def ticker_from_text(self, text: str) -> str | None:
        """Extrae un ticker para enrutar correcciones de movimientos confirmados."""
        return self._ticker(text, normalized_text(text))

    def parse(
        self,
        text: str,
        base: MovementDraft | None = None,
        default_account: str | None = None,
        default_currency: str | None = None,
    ) -> MovementDraft:
        draft = MovementDraft.from_dict(base.to_dict() if base else None)
        original = text.strip()
        normalized = normalized_text(original)
        draft.source_text = " | ".join(filter(None, [draft.source_text, original]))

        movement_type = self._movement_type(normalized)
        if movement_type and (
            not draft.movement_type or self._has_explicit_type_correction(normalized)
        ):
            draft.movement_type = movement_type

        parsed_date, inferred = self._date(original, normalized)
        if parsed_date:
            draft.trade_date = parsed_date
            if inferred and "trade_date" not in draft.inferred_fields:
                draft.inferred_fields.append("trade_date")
            elif not inferred and "trade_date" in draft.inferred_fields:
                draft.inferred_fields.remove("trade_date")

        account = self._account(original, normalized)
        if account:
            draft.account = account
            if "account" in draft.inferred_fields:
                draft.inferred_fields.remove("account")
        elif not draft.account and default_account:
            draft.account = default_account
            draft.inferred_fields.append("account")

        currency = self._currency(original, normalized)
        if currency:
            draft.currency = currency
            if "currency" in draft.inferred_fields:
                draft.inferred_fields.remove("currency")
        elif not draft.currency and default_currency:
            draft.currency = default_currency
            draft.inferred_fields.append("currency")

        ticker = self._ticker(original, normalized)
        if ticker:
            draft.ticker = ticker

        quantity = self._quantity(normalized, draft.movement_type)
        if quantity is not None:
            draft.quantity = quantity

        price = self._unit_price(normalized, draft.movement_type)
        if price is not None:
            draft.unit_price = price

        fee = self._fee(normalized)
        if fee is not None:
            draft.fee = fee

        amount = self._cash_amount(normalized, draft.movement_type)
        if amount is not None:
            draft.cash_amount = amount

        if not draft.trade_date and draft.movement_type:
            draft.trade_date = self.today_provider().isoformat()
            if "trade_date" not in draft.inferred_fields:
                draft.inferred_fields.append("trade_date")

        return draft

    @staticmethod
    def _movement_type(normalized: str) -> str | None:
        for movement_type, pattern in TYPE_PATTERNS:
            if re.search(pattern, normalized):
                return movement_type
        for movement_type in MOVEMENT_TYPES:
            if normalized == normalized_text(movement_type):
                return movement_type
        return None

    @staticmethod
    def _has_explicit_type_correction(normalized: str) -> bool:
        if re.search(r"\b(?:tipo(?:\s+de\s+movimiento)?|movimiento)\b", normalized):
            return True
        return bool(
            re.search(
                r"\ben\s+realidad\s+(?:fue|era|es)?\s*(?:un|una)?\s*"
                r"(?:compra|venta|deposito|retiro|dividendo|interes|comision|impuesto)\b",
                normalized,
            )
        )

    def _date(self, original: str, normalized: str) -> tuple[str | None, bool]:
        today = self.today_provider()
        if re.search(r"\bhoy\b", normalized):
            return today.isoformat(), False
        if re.search(r"\bayer\b", normalized):
            return (today - timedelta(days=1)).isoformat(), False

        iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", original)
        if iso:
            return self._safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), False

        latin = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", original)
        if latin:
            return self._safe_date(int(latin.group(3)), int(latin.group(2)), int(latin.group(1))), False

        month_names = "|".join(MONTHS)
        written = re.search(
            rf"\b(\d{{1,2}})\s+de\s+({month_names})(?:\s+(?:de|del)\s+(20\d{{2}}))?\b",
            normalized,
        )
        if written:
            explicit_year = written.group(3)
            year = int(explicit_year) if explicit_year else today.year
            parsed = self._safe_date(year, MONTHS[written.group(2)], int(written.group(1)))
            if parsed and not explicit_year and datetime.fromisoformat(parsed).date() > today:
                parsed = self._safe_date(year - 1, MONTHS[written.group(2)], int(written.group(1)))
            return parsed, explicit_year is None
        return None, False

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> str | None:
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    @staticmethod
    def _account(original: str, normalized: str) -> str | None:
        for alias, label in KNOWN_ACCOUNTS.items():
            if re.search(rf"\b{re.escape(alias)}\b", normalized):
                return label
        match = re.search(
            r"\b(?:cuenta|broker)(?:\s+correct[oa])?\s*(?:es|era|fue|a|por|:)?\s+([\w .-]{2,35})",
            original,
            flags=re.IGNORECASE,
        )
        if match:
            value = re.split(r"\b(?:a|por|con|sin|el|la)\b", match.group(1), maxsplit=1)[0]
            return value.strip(" .,-") or None
        return None

    @staticmethod
    def _currency(original: str, normalized: str) -> str | None:
        if "$" in original and not re.search(r"\b(?:ars|pesos?)\b", normalized):
            return "USD"
        for alias, code in CURRENCY_ALIASES.items():
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                return code
        return None

    @staticmethod
    def _ticker(original: str, normalized: str) -> str | None:
        for alias in sorted(COMPANY_ALIASES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", normalized):
                return COMPANY_ALIASES[alias]

        explicit = re.search(
            r"\b(?:ticker|simbolo|activo)(?:\s+correct[oa])?\s*(?:es|era|fue|a|por|:)?\s+"
            r"([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b",
            original,
            re.IGNORECASE,
        )
        if explicit:
            return explicit.group(1).upper()

        uppercase = re.findall(r"(?<![A-Za-z])([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Za-z])", original)
        candidates = [value for value in uppercase if value not in STOP_TICKERS]
        return candidates[0] if candidates else None

    @staticmethod
    def _quantity(normalized: str, movement_type: str | None) -> float | None:
        if movement_type not in {"Compra", "Venta"}:
            return None
        match = re.search(
            r"\b(?:mis\s+)?acciones?(?:\s+(?:de|en)\s+[a-z]{1,6})?\s*,?\s*"
            r"(?:son|eran|fueron)\s+(\d[\d.,]*)",
            normalized,
        )
        if not match:
            match = re.search(r"\b(\d[\d.,]*)\s+(?:unidades?|acciones?)\b", normalized)
        if not match:
            match = re.search(
                r"\b(?:compre|compramos|compra|vendi|vendimos|venta)\s+(\d[\d.,]*)\b",
                normalized,
            )
        if not match:
            match = re.search(
                r"\b(?:cantidad|unidades?|acciones?)(?:\s+correct[oa]s?)?\s*"
                r"(?:es|era|fue|a|de|:)?\s*(\d[\d.,]*)",
                normalized,
            )
        return parse_number(match.group(1)) if match else None

    @staticmethod
    def _unit_price(normalized: str, movement_type: str | None) -> float | None:
        if movement_type not in {"Compra", "Venta"}:
            return None
        patterns = (
            r"\bprecio(?:\s+unitario)?(?:\s+correcto)?\s*(?:es|era|fue|a|de|:)?\s*"
            r"(?:usdc|usd|us\$|ars|eur|\$)?\s*(\d[\d.,]*)",
            r"(?:\ba|@|precio(?:\s+unitario)?(?:\s+de)?|cada\s+una?\s*(?:a)?)\s*(?:usd|us\$|ars|eur|\$)?\s*(\d[\d.,]*)",
            r"\b(?:por)\s+(?:usdc|usd|us\$|ars|eur|\$)\s*(\d[\d.,]*)\s*(?:cada|c/u)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return parse_number(match.group(1))
        return None

    @staticmethod
    def _fee(normalized: str) -> float | None:
        if re.search(r"\bsin\s+comision", normalized):
            return 0.0
        patterns = (
            r"\bcomision(?:es)?(?:\s+correcta)?\s*(?:es|era|fue|a|de|:)?\s*"
            r"(?:usdc|usd|ars|eur|\$)?\s*(\d[\d.,]*)",
            r"\bcon\s+(?:usdc|usd|ars|eur|\$)?\s*(\d[\d.,]*)\s+de\s+comision",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return parse_number(match.group(1))
        return None

    @staticmethod
    def _cash_amount(normalized: str, movement_type: str | None) -> float | None:
        if movement_type is None:
            return None
        if movement_type in {"Compra", "Venta"}:
            trade_patterns = (
                r"\b(?:monto(?:\s+efectivo)?|importe|total|desembolso)\s*"
                r"(?:correcto\s*)?(?:es|era|fue|de|:)?\s*"
                r"(?:(?:usdc|usd|ars|eur|\$)\s*)?(\d[\d.,]*)",
                r"\b(?:compre|vendi)\s+(?:por\s+)?"
                r"(?:(?:usdc|usd|ars|eur|\$)\s*)?(\d[\d.,]*)\s*"
                r"(?:usdc|usd|ars|eur|\$)\s+en\b",
                r"\b(?:inverti|invirti|pague|desembolse|cobre|recibi)\s+"
                r"(?:(?:usdc|usd|ars|eur|\$)\s*)?(\d[\d.,]*)",
            )
            for pattern in trade_patterns:
                match = re.search(pattern, normalized)
                if match:
                    return parse_number(match.group(1))
            return None
        patterns = (
            r"\b(?:monto|importe|total)\s*(?:de|:)?\s*(?:usd|ars|eur|\$)?\s*(\d[\d.,]*)",
            r"\b(?:deposite|deposito|ingrese|aporte|transferi|retire|retiro|cobre|recibi)\s+(?:usd|ars|eur|\$)?\s*(\d[\d.,]*)",
            r"\b(?:de|por)\s+(?:usd|ars|eur|\$)?\s*(\d[\d.,]*)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                return parse_number(match.group(1))
        return None
