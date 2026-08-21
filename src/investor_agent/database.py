from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import MovementDraft


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    movement_type TEXT NOT NULL,
                    account TEXT NOT NULL,
                    ticker TEXT,
                    quantity REAL,
                    unit_price REAL,
                    cash_amount REAL,
                    currency TEXT NOT NULL,
                    fee REAL,
                    notes TEXT,
                    source_text TEXT NOT NULL,
                    external_ref TEXT,
                    inferred_fields_json TEXT NOT NULL DEFAULT '[]',
                    excel_synced INTEGER NOT NULL DEFAULT 0,
                    excel_error TEXT,
                    voided_at TEXT,
                    void_reason TEXT,
                    superseded_by INTEGER,
                    cash_delta_applied REAL
                );

                CREATE TABLE IF NOT EXISTS pending_actions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    before_json TEXT,
                    after_json TEXT
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cash_balances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    account TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    amount REAL NOT NULL CHECK(amount >= 0),
                    source_text TEXT NOT NULL,
                    notes TEXT,
                    UNIQUE(as_of, account, currency)
                );

                CREATE TABLE IF NOT EXISTS research_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    research_type TEXT NOT NULL,
                    query TEXT NOT NULL,
                    model TEXT NOT NULL,
                    response_id TEXT,
                    answer TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    search_queries_json TEXT NOT NULL DEFAULT '[]',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    status TEXT NOT NULL DEFAULT 'completed',
                    incomplete_reason TEXT,
                    include_advice INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS market_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    market_timestamp TEXT NOT NULL,
                    price REAL NOT NULL CHECK(price > 0),
                    previous_close REAL,
                    change_amount REAL,
                    change_percent REAL,
                    currency TEXT,
                    exchange TEXT,
                    UNIQUE(symbol, provider, market_timestamp)
                );

                CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_latest
                ON market_quotes(symbol, retrieved_at DESC);

                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_type TEXT NOT NULL,
                    snapshot_key TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(snapshot_type, snapshot_key, provider, as_of)
                );

                CREATE INDEX IF NOT EXISTS idx_market_snapshots_latest
                ON market_snapshots(snapshot_type, snapshot_key, retrieved_at DESC);
                """
            )
            movement_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(movements)")
            }
            if "external_ref" not in movement_columns:
                connection.execute("ALTER TABLE movements ADD COLUMN external_ref TEXT")
            movement_migrations = {
                "voided_at": "TEXT",
                "void_reason": "TEXT",
                "superseded_by": "INTEGER",
                "cash_delta_applied": "REAL",
            }
            for column, definition in movement_migrations.items():
                if column not in movement_columns:
                    connection.execute(
                        f"ALTER TABLE movements ADD COLUMN {column} {definition}"
                    )
            research_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(research_reports)")
            }
            status_was_missing = "status" not in research_columns
            research_migrations = {
                "status": "TEXT NOT NULL DEFAULT 'completed'",
                "incomplete_reason": "TEXT",
                "include_advice": "INTEGER NOT NULL DEFAULT 0",
                "duration_ms": "INTEGER",
            }
            for column, definition in research_migrations.items():
                if column not in research_columns:
                    connection.execute(
                        f"ALTER TABLE research_reports ADD COLUMN {column} {definition}"
                    )
            # La primera versión permitía 3.500 tokens y no guardaba el estado de
            # OpenAI. Alcanzar exactamente ese tope identifica los informes legados
            # que pudieron quedar cortados a mitad de frase.
            if status_was_missing:
                connection.execute(
                    """
                    UPDATE research_reports
                    SET status = 'incomplete', incomplete_reason = 'max_output_tokens'
                    WHERE output_tokens >= 3500 AND status = 'completed'
                    """
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_movements_external_ref
                ON movements(external_ref)
                WHERE external_ref IS NOT NULL
                """
            )
            for key in ("default_account", "default_currency"):
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, '', ?)",
                    (key, utc_now()),
                )

    def get_settings(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def update_settings(self, settings: dict[str, str]) -> dict[str, str]:
        allowed = {"default_account", "default_currency"}
        with self.connect() as connection:
            for key, value in settings.items():
                if key not in allowed:
                    continue
                connection.execute(
                    """
                    INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, str(value).strip(), utc_now()),
                )
            self._audit(connection, "update", "settings", None, None, settings)
        return self.get_settings()

    def save_pending(self, session_id: str, draft: MovementDraft) -> None:
        now = utc_now()
        payload = json.dumps(draft.to_dict(), ensure_ascii=False)
        with self.connect() as connection:
            before = connection.execute(
                "SELECT payload_json FROM pending_actions WHERE session_id = ?", (session_id,)
            ).fetchone()
            connection.execute(
                """
                INSERT INTO pending_actions(session_id, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (session_id, now, now, payload),
            )
            self._audit(
                connection,
                "create" if before is None else "update",
                "pending_action",
                session_id,
                json.loads(before["payload_json"]) if before else None,
                draft.to_dict(),
            )

    def get_pending(self, session_id: str) -> MovementDraft | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pending_actions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return MovementDraft.from_dict(json.loads(row["payload_json"])) if row else None

    def clear_pending(self, session_id: str, reason: str = "cancel") -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pending_actions WHERE session_id = ?", (session_id,)
            ).fetchone()
            connection.execute("DELETE FROM pending_actions WHERE session_id = ?", (session_id,))
            if row:
                self._audit(
                    connection,
                    reason,
                    "pending_action",
                    session_id,
                    json.loads(row["payload_json"]),
                    None,
                )

    def confirm_pending(self, session_id: str, draft: MovementDraft) -> int:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO movements(
                    created_at, confirmed_at, trade_date, movement_type, account, ticker,
                    quantity, unit_price, cash_amount, currency, fee, notes, source_text,
                    inferred_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    draft.trade_date,
                    draft.movement_type,
                    draft.account,
                    draft.ticker,
                    draft.quantity,
                    draft.unit_price,
                    draft.cash_amount,
                    draft.currency,
                    draft.fee,
                    draft.notes,
                    draft.source_text,
                    json.dumps(draft.inferred_fields, ensure_ascii=False),
                ),
            )
            movement_id = int(cursor.lastrowid)
            movement = connection.execute(
                "SELECT * FROM movements WHERE id = ?", (movement_id,)
            ).fetchone()
            self._apply_cash_effect_for_row(connection, movement)
            connection.execute("DELETE FROM pending_actions WHERE session_id = ?", (session_id,))
            saved = dict(
                connection.execute(
                    "SELECT * FROM movements WHERE id = ?", (movement_id,)
                ).fetchone()
            )
            self._audit(connection, "confirm", "movement", str(movement_id), None, saved)
        return movement_id

    def confirm_amendment(
        self, session_id: str, draft: MovementDraft
    ) -> tuple[int, list[int]]:
        """Inserta el reemplazo y anula sus antecedentes sin borrar la auditoría."""

        movement_ids = sorted({int(value) for value in draft.amends_movement_ids})
        if not movement_ids:
            raise ValueError("La enmienda no identifica movimientos anteriores.")
        if draft.missing_fields():
            raise ValueError(f"Movimiento incompleto: {', '.join(draft.missing_fields())}")

        now = utc_now()
        placeholders = ",".join("?" for _ in movement_ids)
        with self.connect() as connection:
            previous_rows = connection.execute(
                f"SELECT * FROM movements WHERE id IN ({placeholders}) AND voided_at IS NULL",
                movement_ids,
            ).fetchall()
            if len(previous_rows) != len(movement_ids):
                raise ValueError("Algún movimiento a corregir no existe o ya fue anulado.")

            previous_labels = ", ".join(f"#{value}" for value in movement_ids)
            correction_note = f"Enmienda auditada de {previous_labels}."
            notes = " | ".join(filter(None, [draft.notes, correction_note]))
            cursor = connection.execute(
                """
                INSERT INTO movements(
                    created_at, confirmed_at, trade_date, movement_type, account, ticker,
                    quantity, unit_price, cash_amount, currency, fee, notes, source_text,
                    inferred_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    draft.trade_date,
                    draft.movement_type,
                    draft.account,
                    draft.ticker,
                    draft.quantity,
                    draft.unit_price,
                    draft.cash_amount,
                    draft.currency,
                    draft.fee,
                    notes,
                    draft.source_text,
                    json.dumps(draft.inferred_fields, ensure_ascii=False),
                ),
            )
            replacement_id = int(cursor.lastrowid)
            reason = f"Reemplazado por movimiento #{replacement_id}"
            for previous in previous_rows:
                before = dict(previous)
                connection.execute(
                    """
                    UPDATE movements
                    SET voided_at = ?, void_reason = ?, superseded_by = ?,
                        excel_synced = 0, excel_error = NULL
                    WHERE id = ?
                    """,
                    (now, reason, replacement_id, previous["id"]),
                )
                after = dict(
                    connection.execute(
                        "SELECT * FROM movements WHERE id = ?", (previous["id"],)
                    ).fetchone()
                )
                self._audit(
                    connection,
                    "void",
                    "movement",
                    str(previous["id"]),
                    before,
                    after,
                )

            self._apply_amendment_cash_effect(
                connection, replacement_id, previous_rows
            )
            connection.execute("DELETE FROM pending_actions WHERE session_id = ?", (session_id,))
            self._audit(
                connection,
                "amend",
                "movement",
                str(replacement_id),
                {"voided_movement_ids": movement_ids},
                {**draft.to_dict(), "notes": notes},
            )
        return replacement_id, movement_ids

    @staticmethod
    def _movement_cash_delta(movement: sqlite3.Row | dict[str, Any]) -> float | None:
        movement_type = movement["movement_type"]
        fee = abs(float(movement["fee"] or 0))
        amount = movement["cash_amount"]
        if amount is None and movement_type in {"Compra", "Venta"}:
            quantity = movement["quantity"]
            unit_price = movement["unit_price"]
            if quantity is not None and unit_price is not None:
                amount = abs(float(quantity) * float(unit_price))
        if amount is None:
            return None
        amount = abs(float(amount))
        if movement_type == "Compra":
            return -(amount + fee)
        if movement_type == "Venta":
            return amount - fee
        if movement_type in {"Depósito", "Dividendo", "Interés"}:
            return amount - fee
        if movement_type in {"Retiro", "Comisión", "Impuesto"}:
            return -(amount + fee)
        return None

    @staticmethod
    def _latest_cash_balance_for(
        connection: sqlite3.Connection, account: str, currency: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM cash_balances
            WHERE account = ? AND currency = ?
            ORDER BY as_of DESC, id DESC
            LIMIT 1
            """,
            (account, currency),
        ).fetchone()

    def _change_cash_balance(
        self,
        connection: sqlite3.Connection,
        *,
        account: str,
        currency: str,
        trade_date: str,
        delta: float,
        movement_id: int,
        force_current: bool = False,
    ) -> dict[str, Any] | None:
        latest = self._latest_cash_balance_for(connection, account, currency)
        if latest is None:
            return None
        if not force_current and trade_date < latest["as_of"]:
            return None

        effective_date = max(trade_date, latest["as_of"])
        new_amount = float(latest["amount"]) + float(delta)
        if new_amount < -1e-7:
            raise ValueError(
                f"El movimiento #{movement_id} dejaría el efectivo de {account} "
                f"en {currency} por debajo de cero. Actualizá el saldo disponible "
                "o corregí el monto antes de confirmar."
            )
        new_amount = max(0.0, new_amount)
        now = utc_now()
        source_label = f"Ajuste automático por movimiento #{movement_id}"

        if effective_date == latest["as_of"]:
            before = dict(latest)
            source_text = " | ".join(
                filter(None, [latest["source_text"], source_label])
            )
            connection.execute(
                """
                UPDATE cash_balances
                SET created_at = ?, amount = ?, source_text = ?, notes = ?
                WHERE id = ?
                """,
                (
                    now,
                    new_amount,
                    source_text,
                    f"Variación aplicada: {delta:+.2f} {currency}",
                    latest["id"],
                ),
            )
            balance_id = int(latest["id"])
        else:
            before = None
            cursor = connection.execute(
                """
                INSERT INTO cash_balances(
                    created_at, as_of, account, currency, amount, source_text, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    effective_date,
                    account,
                    currency,
                    new_amount,
                    source_label,
                    (
                        f"Saldo derivado del registro #{latest['id']}; "
                        f"variación aplicada: {delta:+.2f} {currency}"
                    ),
                ),
            )
            balance_id = int(cursor.lastrowid)

        after = dict(
            connection.execute(
                "SELECT * FROM cash_balances WHERE id = ?", (balance_id,)
            ).fetchone()
        )
        self._audit(
            connection,
            "movement_adjust",
            "cash_balance",
            str(balance_id),
            before,
            after,
        )
        return after

    def _apply_cash_effect_for_row(
        self, connection: sqlite3.Connection, movement: sqlite3.Row
    ) -> dict[str, Any] | None:
        if movement["cash_delta_applied"] is not None:
            return self._latest_cash_balance_for(
                connection, movement["account"], movement["currency"]
            )
        delta = self._movement_cash_delta(movement)
        if delta is None:
            return None
        balance = self._change_cash_balance(
            connection,
            account=movement["account"],
            currency=movement["currency"],
            trade_date=movement["trade_date"],
            delta=delta,
            movement_id=int(movement["id"]),
        )
        if balance is not None:
            connection.execute(
                "UPDATE movements SET cash_delta_applied = ? WHERE id = ?",
                (delta, movement["id"]),
            )
        return balance

    def _apply_amendment_cash_effect(
        self,
        connection: sqlite3.Connection,
        replacement_id: int,
        previous_rows: list[sqlite3.Row],
    ) -> None:
        replacement = connection.execute(
            "SELECT * FROM movements WHERE id = ?", (replacement_id,)
        ).fetchone()
        replacement_delta = self._movement_cash_delta(replacement)
        adjustments: dict[tuple[str, str], float] = {}
        for previous in previous_rows:
            applied = previous["cash_delta_applied"]
            if applied is None:
                continue
            key = (previous["account"], previous["currency"])
            adjustments[key] = adjustments.get(key, 0.0) - float(applied)

        replacement_key = (replacement["account"], replacement["currency"])
        if replacement_delta is not None:
            adjustments[replacement_key] = (
                adjustments.get(replacement_key, 0.0) + replacement_delta
            )

        replacement_applied = False
        for (account, currency), delta in adjustments.items():
            balance = self._change_cash_balance(
                connection,
                account=account,
                currency=currency,
                trade_date=replacement["trade_date"],
                delta=delta,
                movement_id=replacement_id,
                force_current=True,
            )
            if (account, currency) == replacement_key and balance is not None:
                replacement_applied = True

        if replacement_applied and replacement_delta is not None:
            connection.execute(
                "UPDATE movements SET cash_delta_applied = ? WHERE id = ?",
                (replacement_delta, replacement_id),
            )

    def apply_cash_effect_for_movement(self, movement_id: int) -> dict[str, Any] | None:
        """Aplica una sola vez el efecto de efectivo de un movimiento ya confirmado."""

        with self.connect() as connection:
            movement = connection.execute(
                "SELECT * FROM movements WHERE id = ? AND voided_at IS NULL",
                (movement_id,),
            ).fetchone()
            if movement is None:
                raise ValueError("El movimiento no existe o está anulado.")
            return self._apply_cash_effect_for_row(connection, movement)

    def import_movement(self, draft: MovementDraft, external_ref: str) -> tuple[int, bool]:
        """Importa una operación confirmada de forma idempotente."""
        if draft.missing_fields():
            raise ValueError(f"Movimiento incompleto: {', '.join(draft.missing_fields())}")
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM movements WHERE external_ref = ?", (external_ref,)
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            cursor = connection.execute(
                """
                INSERT INTO movements(
                    created_at, confirmed_at, trade_date, movement_type, account, ticker,
                    quantity, unit_price, cash_amount, currency, fee, notes, source_text,
                    external_ref, inferred_fields_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    draft.trade_date,
                    draft.movement_type,
                    draft.account,
                    draft.ticker,
                    draft.quantity,
                    draft.unit_price,
                    draft.cash_amount,
                    draft.currency,
                    draft.fee,
                    draft.notes,
                    draft.source_text,
                    external_ref,
                    json.dumps(draft.inferred_fields, ensure_ascii=False),
                ),
            )
            movement_id = int(cursor.lastrowid)
            self._audit(
                connection,
                "import",
                "movement",
                str(movement_id),
                None,
                {**draft.to_dict(), "external_ref": external_ref},
            )
        return movement_id, True

    def upsert_cash_balance(
        self,
        *,
        as_of: str,
        account: str,
        currency: str,
        amount: float,
        source_text: str,
        notes: str | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as connection:
            before = connection.execute(
                """
                SELECT * FROM cash_balances
                WHERE as_of = ? AND account = ? AND currency = ?
                """,
                (as_of, account, currency),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO cash_balances(
                    created_at, as_of, account, currency, amount, source_text, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(as_of, account, currency) DO UPDATE SET
                    created_at=excluded.created_at,
                    amount=excluded.amount,
                    source_text=excluded.source_text,
                    notes=excluded.notes
                """,
                (now, as_of, account, currency, amount, source_text, notes),
            )
            row = connection.execute(
                """
                SELECT * FROM cash_balances
                WHERE as_of = ? AND account = ? AND currency = ?
                """,
                (as_of, account, currency),
            ).fetchone()
            after = dict(row)
            self._audit(
                connection,
                "create" if before is None else "update",
                "cash_balance",
                str(row["id"]),
                dict(before) if before else None,
                after,
            )
        return int(row["id"])

    def latest_cash_balances(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT current.*
                FROM cash_balances AS current
                JOIN (
                    SELECT account, currency, MAX(as_of) AS latest_date
                    FROM cash_balances
                    GROUP BY account, currency
                ) AS latest
                  ON current.account = latest.account
                 AND current.currency = latest.currency
                 AND current.as_of = latest.latest_date
                ORDER BY current.account, current.currency
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_excel_sync(self, movement_id: int, success: bool, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE movements SET excel_synced = ?, excel_error = ? WHERE id = ?",
                (1 if success else 0, error, movement_id),
            )

    def mark_excel_sync_many(
        self, movement_ids: list[int], success: bool, error: str | None = None
    ) -> None:
        if not movement_ids:
            return
        placeholders = ",".join("?" for _ in movement_ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE movements SET excel_synced = ?, excel_error = ? WHERE id IN ({placeholders})",
                [1 if success else 0, error, *movement_ids],
            )

    def get_movement(self, movement_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM movements WHERE id = ?", (movement_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def recent_movements(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM movements WHERE voided_at IS NULL ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def active_movements_for_ticker(
        self, ticker: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM movements
                WHERE voided_at IS NULL AND UPPER(ticker) = ?
                ORDER BY id DESC LIMIT ?
                """,
                (ticker.upper().strip(), safe_limit),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def positions(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ticker,
                       SUM(CASE movement_type
                             WHEN 'Compra' THEN quantity
                             WHEN 'Venta' THEN -quantity
                             ELSE 0 END) AS quantity
                FROM movements
                WHERE voided_at IS NULL
                  AND ticker IS NOT NULL AND movement_type IN ('Compra', 'Venta')
                GROUP BY ticker
                HAVING ABS(quantity) > 0.0000001
                ORDER BY ticker
                """
            ).fetchall()
        return [{"ticker": row["ticker"], "quantity": row["quantity"]} for row in rows]

    def position_costs(self) -> list[dict[str, Any]]:
        """Calcula costo promedio ponderado para las posiciones abiertas."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, trade_date, movement_type, account, ticker, quantity,
                       unit_price, currency, COALESCE(fee, 0) AS fee
                FROM movements
                WHERE voided_at IS NULL
                  AND ticker IS NOT NULL AND movement_type IN ('Compra', 'Venta')
                ORDER BY trade_date, id
                """
            ).fetchall()

        states: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            ticker = str(row["ticker"]).upper().strip()
            currency = str(row["currency"]).upper().strip()
            key = (ticker, currency)
            state = states.setdefault(
                key,
                {
                    "ticker": ticker,
                    "currency": currency,
                    "quantity": 0.0,
                    "cost_basis": 0.0,
                    "accounts": set(),
                    "first_trade_date": row["trade_date"],
                    "last_trade_date": row["trade_date"],
                    "cost_warning": None,
                },
            )
            quantity = float(row["quantity"] or 0)
            unit_price = float(row["unit_price"] or 0)
            fee = float(row["fee"] or 0)
            state["accounts"].add(row["account"])
            state["last_trade_date"] = row["trade_date"]

            if row["movement_type"] == "Compra":
                state["quantity"] += quantity
                state["cost_basis"] += quantity * unit_price + fee
                continue

            open_quantity = float(state["quantity"])
            if open_quantity <= 0 or quantity > open_quantity + 0.0000001:
                state["quantity"] = open_quantity - quantity
                state["cost_basis"] = 0.0
                state["cost_warning"] = (
                    "Hay una venta mayor que la posición registrada; el costo no es calculable."
                )
                continue
            average_cost = state["cost_basis"] / open_quantity
            state["quantity"] = open_quantity - quantity
            state["cost_basis"] = max(0.0, state["cost_basis"] - average_cost * quantity)

        result = []
        for state in states.values():
            quantity = float(state["quantity"])
            if abs(quantity) <= 0.0000001:
                continue
            cost_basis = float(state["cost_basis"])
            average_cost = cost_basis / quantity if quantity > 0 and not state["cost_warning"] else None
            result.append(
                {
                    "ticker": state["ticker"],
                    "currency": state["currency"],
                    "quantity": quantity,
                    "cost_basis": cost_basis if average_cost is not None else None,
                    "average_cost": average_cost,
                    "accounts": sorted(state["accounts"]),
                    "first_trade_date": state["first_trade_date"],
                    "last_trade_date": state["last_trade_date"],
                    "cost_warning": state["cost_warning"],
                }
            )
        return sorted(result, key=lambda item: (item["ticker"], item["currency"]))

    def save_market_quote(self, quote: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_quotes(
                    symbol, provider, retrieved_at, market_timestamp, price,
                    previous_close, change_amount, change_percent, currency, exchange
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, provider, market_timestamp) DO UPDATE SET
                    retrieved_at=excluded.retrieved_at,
                    price=excluded.price,
                    previous_close=excluded.previous_close,
                    change_amount=excluded.change_amount,
                    change_percent=excluded.change_percent,
                    currency=excluded.currency,
                    exchange=excluded.exchange
                """,
                (
                    quote["symbol"],
                    quote["provider"],
                    quote["retrieved_at"],
                    quote["market_timestamp"],
                    quote["price"],
                    quote.get("previous_close"),
                    quote.get("change_amount"),
                    quote.get("change_percent"),
                    quote.get("currency"),
                    quote.get("exchange"),
                ),
            )
        return self.latest_market_quote(str(quote["symbol"])) or dict(quote)

    def latest_market_quote(self, symbol: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_quotes
                WHERE symbol = ?
                ORDER BY retrieved_at DESC, id DESC
                LIMIT 1
                """,
                (symbol.upper().strip(),),
            ).fetchone()
        return dict(row) if row else None

    def save_market_snapshot(
        self,
        *,
        snapshot_type: str,
        snapshot_key: str,
        provider: str,
        as_of: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        retrieved_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_snapshots(
                    snapshot_type, snapshot_key, provider, as_of, retrieved_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_type, snapshot_key, provider, as_of) DO UPDATE SET
                    retrieved_at=excluded.retrieved_at,
                    payload_json=excluded.payload_json
                """,
                (
                    snapshot_type,
                    snapshot_key,
                    provider,
                    as_of,
                    retrieved_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return {
            "snapshot_type": snapshot_type,
            "snapshot_key": snapshot_key,
            "provider": provider,
            "as_of": as_of,
            "retrieved_at": retrieved_at,
            "payload": payload,
        }

    def latest_market_snapshot(
        self, snapshot_type: str, snapshot_key: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_snapshots
                WHERE snapshot_type = ? AND snapshot_key = ?
                ORDER BY retrieved_at DESC, id DESC
                LIMIT 1
                """,
                (snapshot_type, snapshot_key),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result

    def latest_market_snapshot_for_provider(
        self, snapshot_type: str, snapshot_key: str, provider: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM market_snapshots
                WHERE snapshot_type = ? AND snapshot_key = ? AND provider = ?
                ORDER BY retrieved_at DESC, id DESC
                LIMIT 1
                """,
                (snapshot_type, snapshot_key, provider),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json") or "{}")
        return result

    def save_research_report(
        self,
        *,
        session_id: str,
        research_type: str,
        query: str,
        context: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO research_reports(
                    created_at, session_id, research_type, query, model, response_id,
                    answer, citations_json, sources_json, search_queries_json,
                    context_json, input_tokens, output_tokens, status,
                    incomplete_reason, include_advice, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    session_id,
                    research_type,
                    query,
                    result["model"],
                    result.get("response_id"),
                    result["answer"],
                    json.dumps(result.get("citations", []), ensure_ascii=False),
                    json.dumps(result.get("sources", []), ensure_ascii=False),
                    json.dumps(result.get("search_queries", []), ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False),
                    result.get("input_tokens"),
                    result.get("output_tokens"),
                    result.get("status", "completed"),
                    result.get("incomplete_reason"),
                    1 if result.get("include_advice") else 0,
                    result.get("duration_ms"),
                ),
            )
            report_id = int(cursor.lastrowid)
            self._audit(
                connection,
                "create",
                "research_report",
                str(report_id),
                None,
                {
                    "research_type": research_type,
                    "query": query,
                    "model": result["model"],
                    "source_count": len(result.get("sources", [])),
                    "status": result.get("status", "completed"),
                    "include_advice": bool(result.get("include_advice")),
                },
            )
        return self.get_research_report(report_id) or {}

    def get_research_report(self, report_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_reports WHERE id = ?", (report_id,)
            ).fetchone()
        return self._research_row(row) if row else None

    def recent_research_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_reports ORDER BY id DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self._research_row(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            movements = connection.execute(
                "SELECT COUNT(*) FROM movements WHERE voided_at IS NULL"
            ).fetchone()[0]
            voided_movements = connection.execute(
                "SELECT COUNT(*) FROM movements WHERE voided_at IS NOT NULL"
            ).fetchone()[0]
            pending = connection.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0]
            sync_errors = connection.execute(
                "SELECT COUNT(*) FROM movements WHERE voided_at IS NULL AND excel_synced = 0"
            ).fetchone()[0]
            cash_balances = connection.execute("SELECT COUNT(*) FROM cash_balances").fetchone()[0]
            research_reports = connection.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0]
            market_quotes = connection.execute("SELECT COUNT(*) FROM market_quotes").fetchone()[0]
        return {
            "movements": movements,
            "voided_movements": voided_movements,
            "pending": pending,
            "sync_errors": sync_errors,
            "cash_balances": cash_balances,
            "research_reports": research_reports,
            "market_quotes": market_quotes,
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["excel_synced"] = bool(result["excel_synced"])
        result["inferred_fields"] = json.loads(result.pop("inferred_fields_json") or "[]")
        return result

    @staticmethod
    def _research_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["citations"] = json.loads(result.pop("citations_json") or "[]")
        result["sources"] = json.loads(result.pop("sources_json") or "[]")
        result["search_queries"] = json.loads(result.pop("search_queries_json") or "[]")
        result["context"] = json.loads(result.pop("context_json") or "{}")
        result["include_advice"] = bool(result.get("include_advice"))
        return result

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        action: str,
        entity_type: str,
        entity_id: str | None,
        before: Any,
        after: Any,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(created_at, action, entity_type, entity_id, before_json, after_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                action,
                entity_type,
                entity_id,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
            ),
        )
