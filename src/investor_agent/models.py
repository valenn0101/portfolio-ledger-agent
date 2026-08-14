from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


MOVEMENT_TYPES = (
    "Compra",
    "Venta",
    "Depósito",
    "Retiro",
    "Dividendo",
    "Interés",
    "Comisión",
    "Impuesto",
    "Otro",
)


FIELD_LABELS = {
    "trade_date": "fecha",
    "movement_type": "tipo de movimiento",
    "account": "cuenta o broker",
    "ticker": "ticker o activo",
    "quantity": "cantidad",
    "unit_price": "precio unitario",
    "cash_amount": "monto efectivo",
    "currency": "moneda",
}


@dataclass
class MovementDraft:
    movement_type: str | None = None
    trade_date: str | None = None
    account: str | None = None
    ticker: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    cash_amount: float | None = None
    currency: str | None = None
    fee: float | None = None
    notes: str | None = None
    source_text: str = ""
    inferred_fields: list[str] = field(default_factory=list)
    amends_movement_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "MovementDraft":
        if not raw:
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    def required_fields(self) -> list[str]:
        required = ["trade_date", "movement_type", "account", "currency"]
        if self.movement_type in {"Compra", "Venta"}:
            required.extend(["ticker", "quantity", "unit_price"])
        elif self.movement_type == "Dividendo":
            required.extend(["ticker", "cash_amount"])
        elif self.movement_type in {
            "Depósito",
            "Retiro",
            "Interés",
            "Comisión",
            "Impuesto",
            "Otro",
        }:
            required.append("cash_amount")
        return required

    def missing_fields(self) -> list[str]:
        return [field for field in self.required_fields() if getattr(self, field) in (None, "")]

    def is_transaction(self) -> bool:
        return self.movement_type is not None


@dataclass
class ServiceReply:
    message: str
    kind: str = "info"
    draft: dict[str, Any] | None = None
    missing_fields: list[str] = field(default_factory=list)
    movement_id: int | None = None
    excel_synced: bool | None = None
    parser_mode: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
