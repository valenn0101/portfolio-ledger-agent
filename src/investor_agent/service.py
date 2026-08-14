from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ai_parser import OpenAIMovementParser
from .database import Database
from .excel_sync import ExcelSync
from .market_data import MarketDataService
from .models import FIELD_LABELS, MovementDraft, ServiceReply
from .parser import COMPANY_ALIASES, LocalMovementParser, normalized_text
from .research import OpenAIResearcher, RESEARCH_TYPES
from .research_sources import public_source_catalog


CONFIRM_WORDS = {"confirmar", "confirmo", "si confirmar", "guardar", "guardar movimiento"}
CANCEL_WORDS = {"cancelar", "cancelo", "descartar", "no cancelar", "olvidar"}


class InvestmentAgentService:
    def __init__(
        self,
        database_path: str | Path,
        template_path: str | Path,
        workbook_path: str | Path,
        local_parser: LocalMovementParser | None = None,
        ai_parser: OpenAIMovementParser | None = None,
        researcher: OpenAIResearcher | None = None,
        market_data: MarketDataService | None = None,
    ):
        self.db = Database(database_path)
        self.market = market_data or MarketDataService(self.db)
        self.excel = ExcelSync(template_path, workbook_path)
        self.local_parser = local_parser or LocalMovementParser()
        self.ai_parser = ai_parser or OpenAIMovementParser()
        self.researcher = researcher or OpenAIResearcher()
        self.last_excel_error: str | None = None
        try:
            self.excel.ensure_workbook()
        except Exception as error:  # La DB sigue operativa si Excel no está disponible.
            self.last_excel_error = str(error)

    def handle_message(self, session_id: str, text: str) -> dict[str, Any]:
        clean_text = text.strip()
        if not clean_text:
            return ServiceReply("Escribime una compra, venta, depósito o consulta.").to_dict()

        normalized = normalized_text(clean_text)
        pending = self.db.get_pending(session_id)

        if normalized in CANCEL_WORDS:
            if pending:
                self.db.clear_pending(session_id)
                return ServiceReply("Borrador descartado. No se guardó ningún movimiento.", "cancelled").to_dict()
            return ServiceReply("No hay ningún borrador pendiente para cancelar.").to_dict()

        if normalized in CONFIRM_WORDS:
            return self._confirm(session_id, pending)

        if pending and self._is_missing_query(normalized):
            return self._draft_reply(pending, parser_mode="local").to_dict()

        if not pending and self._is_confirmed_correction(normalized):
            return self._start_confirmed_correction(session_id, clean_text)

        movement_intent = pending is not None or self.local_parser.looks_like_movement(clean_text)

        if not movement_intent and self._is_research_query(normalized):
            return self.research(
                session_id,
                clean_text,
                self._infer_research_type(clean_text),
                include_advice=self._asks_for_advice(normalized),
            )

        if not movement_intent:
            command_reply = self._command(normalized)
            if command_reply:
                return command_reply.to_dict()

        draft, parser_mode, warning = self._parse(clean_text, pending)
        if not draft.is_transaction():
            return ServiceReply(
                self._help_text(warning),
                "help",
                parser_mode=parser_mode,
            ).to_dict()

        self.db.save_pending(session_id, draft)
        reply = self._draft_reply(draft, parser_mode, updated=pending is not None)
        if warning:
            reply.message += f"\n\nNota: {warning}"
        return reply.to_dict()

    def _parse(
        self, text: str, base: MovementDraft | None
    ) -> tuple[MovementDraft, str, str | None]:
        settings = self.db.get_settings()
        warning = None
        if self.ai_parser.available:
            try:
                draft = self.ai_parser.parse(text, base)
                draft = self.local_parser.parse(
                    text,
                    base=draft,
                    default_account=settings.get("default_account") or None,
                    default_currency=settings.get("default_currency") or None,
                )
                return draft, "openai", None
            except Exception as error:
                warning = f"la interpretación con IA falló y usé el modo local ({error})."

        draft = self.local_parser.parse(
            text,
            base=base,
            default_account=settings.get("default_account") or None,
            default_currency=settings.get("default_currency") or None,
        )
        return draft, "local", warning

    def _confirm(self, session_id: str, pending: MovementDraft | None) -> dict[str, Any]:
        if not pending:
            return ServiceReply("No hay ningún borrador pendiente para confirmar.").to_dict()
        missing = pending.missing_fields()
        if missing:
            reply = self._draft_reply(pending, parser_mode="local")
            reply.message = "Todavía no puedo guardarlo. " + reply.message
            return reply.to_dict()

        amended_ids = list(pending.amends_movement_ids)
        if amended_ids:
            movement_id, amended_ids = self.db.confirm_amendment(session_id, pending)
        else:
            movement_id = self.db.confirm_pending(session_id, pending)
        movement = self.db.get_movement(movement_id)
        cash_message = ""
        cash_delta = movement.get("cash_delta_applied") if movement else None
        if cash_delta is not None and movement:
            balance = next(
                (
                    item
                    for item in self.db.latest_cash_balances()
                    if item["account"] == movement["account"]
                    and item["currency"] == movement["currency"]
                ),
                None,
            )
            if balance:
                def spanish_amount(value: float) -> str:
                    return (
                        f"{value:,.2f}"
                        .replace(",", "_")
                        .replace(".", ",")
                        .replace("_", ".")
                    )

                action = "descontaron" if cash_delta < 0 else "sumaron"
                cash_message = (
                    f" Se {action} {spanish_amount(abs(float(cash_delta)))} "
                    f"{movement['currency']} del efectivo; saldo disponible: "
                    f"{spanish_amount(float(balance['amount']))} {movement['currency']}."
                )
        excel_synced = False
        excel_message = ""
        try:
            if amended_ids:
                row = self.excel.append_amendment(movement or {}, amended_ids)
                self.db.mark_excel_sync_many([movement_id, *amended_ids], True)
            else:
                row = self.excel.append(movement or {})
                self.db.mark_excel_sync(movement_id, True)
            excel_synced = True
            excel_message = f" También quedó copiado en Excel (fila {row})."
            self.last_excel_error = None
        except Exception as error:
            self.last_excel_error = str(error)
            if amended_ids:
                self.db.mark_excel_sync_many([movement_id, *amended_ids], False, str(error))
            else:
                self.db.mark_excel_sync(movement_id, False, str(error))
            excel_message = (
                " Se guardó correctamente en la base de datos, pero la copia a Excel quedó pendiente."
            )

        saved_label = (
            f"Enmienda #{movement_id} guardada y auditada."
            if amended_ids
            else f"Movimiento #{movement_id} guardado y auditado."
        )
        return ServiceReply(
            f"{saved_label}{cash_message}{excel_message}",
            "saved",
            movement_id=movement_id,
            excel_synced=excel_synced,
        ).to_dict()

    def _draft_reply(
        self, draft: MovementDraft, parser_mode: str, *, updated: bool = False
    ) -> ServiceReply:
        missing = draft.missing_fields()
        summary = self._format_draft(draft)
        if missing:
            labels = self._human_join([FIELD_LABELS[field] for field in missing])
            opening = "Actualicé el borrador" if updated else "Preparé el borrador"
            message = (
                f"{opening}, pero me falta {labels}.\n\n{summary}\n\n"
                "Decime esos datos o cualquier corrección en otro mensaje; no hace falta repetir todo."
            )
            kind = "missing"
        else:
            inferred_note = ""
            if draft.inferred_fields:
                inferred = self._human_join([FIELD_LABELS.get(field, field) for field in draft.inferred_fields])
                inferred_note = f"\n\nTomé {inferred} de tu configuración o del día actual. Revisalo antes de confirmar."
            if draft.amends_movement_ids:
                targets = self._human_join(
                    [f"#{value}" for value in draft.amends_movement_ids]
                )
                opening = f"Preparé una enmienda auditada para el movimiento {targets}:"
                amendment_note = (
                    "\n\nAl confirmar, el registro anterior quedará anulado, pero seguirá "
                    "disponible en la auditoría."
                )
            else:
                opening = "Actualicé el borrador:" if updated else "Este es el borrador:"
                amendment_note = ""
            message = (
                f"{opening}\n\n{summary}{inferred_note}{amendment_note}\n\n"
                "Podés corregir cualquier dato antes de guardarlo; por ejemplo: "
                "“el precio correcto es 330,50” o “cambiá el ticker a GOOG”.\n\n"
                "Respondé “confirmar” para guardarlo o “cancelar” para descartarlo."
            )
            kind = "confirmation"
        return ServiceReply(
            message,
            kind,
            draft=draft.to_dict(),
            missing_fields=missing,
            parser_mode=parser_mode,
        )

    def _start_confirmed_correction(self, session_id: str, text: str) -> dict[str, Any]:
        normalized = normalized_text(text)
        explicit_id = re.search(r"(?:\bmovimiento\s*#?\s*|#)(\d+)\b", normalized)
        candidates: list[dict[str, Any]] = []

        if explicit_id:
            movement = self.db.get_movement(int(explicit_id.group(1)))
            if movement and not movement.get("voided_at"):
                candidates = [movement]
        else:
            ticker = self.local_parser.ticker_from_text(text)
            if ticker:
                candidates = self.db.active_movements_for_ticker(ticker)
            elif re.search(r"\bultimo\s+movimiento\b", normalized):
                candidates = self.db.recent_movements(1)

        if not candidates:
            return ServiceReply(
                "No encontré un movimiento confirmado inequívoco para corregir. "
                "Indicame el ticker o el número, por ejemplo: “corregir movimiento #10”.",
                "clarification",
            ).to_dict()

        if len(candidates) > 1:
            lines = [
                f"• #{item['id']} · {item['trade_date']} · {item['movement_type']} · "
                f"{item.get('quantity') or '—'} {item.get('ticker') or ''}"
                for item in candidates[:8]
            ]
            return ServiceReply(
                "Encontré varios movimientos activos que podrían coincidir:\n"
                + "\n".join(lines)
                + "\n\nIndicame cuál querés corregir usando su número.",
                "clarification",
            ).to_dict()

        target = candidates[0]
        base = MovementDraft.from_dict(target)
        base.amends_movement_ids = [int(target["id"])]
        draft, parser_mode, warning = self._parse(text, base)
        draft.amends_movement_ids = [int(target["id"])]
        self.db.save_pending(session_id, draft)
        reply = self._draft_reply(draft, parser_mode, updated=True)
        if warning:
            reply.message += f"\n\nNota: {warning}"
        return reply.to_dict()

    def _command(self, normalized: str) -> ServiceReply | None:
        if normalized in {"ayuda", "hola", "inicio", "que podes hacer", "qué podés hacer"}:
            return ServiceReply(self._help_text(), "help")
        if self._is_missing_query(normalized):
            settings = self.db.get_settings()
            missing_config = []
            if not settings.get("default_account"):
                missing_config.append("cuenta habitual")
            if not settings.get("default_currency"):
                missing_config.append("moneda habitual")
            if missing_config:
                return ServiceReply(
                    "No hay movimientos pendientes. En Configuración todavía falta definir "
                    + self._human_join(missing_config)
                    + ".",
                    "status",
                )
            return ServiceReply("No hay movimientos pendientes ni datos básicos de configuración faltantes.", "status")
        if re.search(r"\b(?:cartera|posiciones|tenencias|resumen)\b", normalized):
            positions = self.db.positions()
            if not positions:
                return ServiceReply("Todavía no hay posiciones calculables a partir de movimientos confirmados.", "summary")
            lines = [f"• {item['ticker']}: {item['quantity']:g} unidades" for item in positions]
            return ServiceReply("Posiciones según tus movimientos confirmados:\n" + "\n".join(lines), "summary")
        if re.search(r"\b(?:ultimos|movimientos|historial)\b", normalized):
            movements = self.db.recent_movements(5)
            if not movements:
                return ServiceReply("Todavía no hay movimientos confirmados.", "history")
            lines = [
                f"• #{item['id']} · {item['trade_date']} · {item['movement_type']} · "
                f"{item.get('ticker') or '-'} · {item['account']}"
                for item in movements
            ]
            return ServiceReply("Últimos movimientos:\n" + "\n".join(lines), "history")
        return None

    def status(self, session_id: str) -> dict[str, Any]:
        pending = self.db.get_pending(session_id)
        return {
            "stats": self.db.stats(),
            "settings": self.db.get_settings(),
            "cash_balances": self.db.latest_cash_balances(),
            "pending": pending.to_dict() if pending else None,
            "pending_missing": pending.missing_fields() if pending else [],
            "ai_enabled": self.ai_parser.available,
            "research_enabled": self.researcher.available,
            "market_data": self.market.status(),
            "excel_ready": self.excel.workbook_path.exists() and self.last_excel_error is None,
            "excel_error": self.last_excel_error,
        }

    def update_settings(self, values: dict[str, str]) -> dict[str, Any]:
        currency = values.get("default_currency", "").upper().strip()
        account = values.get("default_account", "").strip()
        settings = self.db.update_settings(
            {"default_account": account, "default_currency": currency}
        )
        return {"settings": settings, "message": "Configuración guardada."}

    def movements(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.recent_movements(limit)

    def research(
        self,
        session_id: str,
        query: str,
        research_type: str = "question",
        include_advice: bool = False,
    ) -> dict[str, Any]:
        clean_query = query.strip()
        if research_type not in RESEARCH_TYPES:
            raise ValueError("Tipo de investigación no admitido.")
        if not clean_query:
            raise ValueError("Escribí qué querés investigar.")
        if len(clean_query) > 2000:
            raise ValueError("La consulta es demasiado extensa; usá hasta 2000 caracteres.")

        context = {
            **self._portfolio_context(),
            "advice_requested": include_advice,
        }
        result = self.researcher.research(
            clean_query,
            research_type,
            context,
            include_advice=include_advice,
        )
        report = self.db.save_research_report(
            session_id=session_id,
            research_type=research_type,
            query=clean_query,
            context=context,
            result=result,
        )
        return {
            **report,
            "kind": "research",
            "message": report["answer"],
        }

    def research_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.db.recent_research_reports(limit)

    def research_sources(self) -> list[dict[str, Any]]:
        return public_source_catalog(self.market.status())

    def portfolio_valuation(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.market.portfolio_valuation(refresh=refresh)

    def market_status(self) -> dict[str, Any]:
        return self.market.status()

    def macro_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.market.macro_snapshot(refresh=refresh)

    def sec_company_snapshot(
        self, symbol: str, *, refresh: bool = False
    ) -> dict[str, Any]:
        return self.market.sec_company_snapshot(symbol, refresh=refresh)

    def _portfolio_context(self) -> dict[str, Any]:
        settings = self.db.get_settings()
        context = {
            "positions": self.db.positions(),
            "cash_balances": self.db.latest_cash_balances(),
            "base_currency": settings.get("default_currency") or None,
            "account": settings.get("default_account") or None,
            "investment_goals": None,
            "risk_limits": None,
            "context_warning": (
                "Todavía no hay objetivos, horizonte ni límites de riesgo estructurados. "
                "No proponer tamaños de posición ni una asignación personalizada."
            ),
        }
        try:
            valuation = self.market.portfolio_valuation()
            context["market_valuation"] = {
                "status": valuation["status"],
                "generated_at": valuation["generated_at"],
                "reporting_currency": valuation["reporting_currency"],
                "summary": valuation["summary"],
                "positions": [
                    {
                        "ticker": item["ticker"],
                        "quantity": item["quantity"],
                        "average_cost": item.get("average_cost"),
                        "price": (item.get("quote") or {}).get("price"),
                        "price_source": (item.get("quote") or {}).get("provider"),
                        "price_as_of": (item.get("quote") or {}).get("market_timestamp"),
                        "market_value": item.get("market_value"),
                        "daily_change_percent": item.get("daily_change_percent"),
                        "unrealized_change_percent": item.get("unrealized_change_percent"),
                    }
                    for item in valuation["positions"]
                ],
                "warnings": valuation["warnings"],
            }
        except RuntimeError as error:
            context["market_valuation"] = {
                "status": "unavailable",
                "warning": str(error),
            }
        return context

    @staticmethod
    def _is_missing_query(normalized: str) -> bool:
        return bool(
            re.search(r"\b(?:que|qué)\s+(?:dato(?:s)?\s+)?(?:me\s+)?falta", normalized)
            or "datos faltantes" in normalized
        )

    @staticmethod
    def _is_research_query(normalized: str) -> bool:
        return bool(
            re.search(
                r"\b(?:investig|analiz|oportunidad|ganga|super\s*accion|analista|"
                r"noticia|consenso|recomend|conviene|opina|opinion|que\s+comprar|"
                r"qué\s+comprar|fuentes?)",
                normalized,
            )
        )

    @staticmethod
    def _is_confirmed_correction(normalized: str) -> bool:
        return bool(
            re.search(
                r"\b(?:corrige|corregi|corregir|corrijo|cambia|cambie|cambiar|"
                r"modifica|modifique|modificar|rectifica|rectificar)\b",
                normalized,
            )
        )

    @staticmethod
    def _asks_for_advice(normalized: str) -> bool:
        return bool(
            re.search(
                r"\b(?:comprar|compro|vender|vendo|mantener|reducir|conviene|"
                r"recomend(?:a|ame|acion)|opinion|opinión|que\s+harias|qué\s+harías)\b",
                normalized,
            )
        )

    @staticmethod
    def _infer_research_type(text: str) -> str:
        normalized = normalized_text(text)
        if re.search(r"\b(?:oportunidad|ganga|super\s*accion|que\s+comprar|qué\s+comprar)\b", normalized):
            return "opportunities"
        if "cartera" in normalized or "posiciones" in normalized or "tenencias" in normalized:
            return "portfolio"
        if re.search(r"\b[A-Z]{1,6}(?:\.[A-Z])?\b", text) or any(
            company in normalized for company in COMPANY_ALIASES
        ):
            return "asset"
        return "question"

    @staticmethod
    def _format_draft(draft: MovementDraft) -> str:
        values = (
            ("Tipo", draft.movement_type),
            ("Fecha", draft.trade_date),
            ("Cuenta", draft.account),
            ("Ticker", draft.ticker),
            ("Cantidad", f"{draft.quantity:g}" if draft.quantity is not None else None),
            ("Precio unitario", f"{draft.unit_price:g}" if draft.unit_price is not None else None),
            ("Monto efectivo", f"{draft.cash_amount:g}" if draft.cash_amount is not None else None),
            ("Moneda", draft.currency),
            ("Comisión", f"{draft.fee:g}" if draft.fee is not None else None),
        )
        return "\n".join(f"• {label}: {value if value not in (None, '') else '—'}" for label, value in values)

    @staticmethod
    def _human_join(values: list[str]) -> str:
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        return ", ".join(values[:-1]) + " y " + values[-1]

    @staticmethod
    def _help_text(warning: str | None = None) -> str:
        text = (
            "Puedo registrar compras, ventas, depósitos, retiros y dividendos. "
            "También podés preguntarme “¿qué me falta?”, “últimos movimientos”, “resumen de cartera” "
            "o pedirme una investigación con fuentes, por ejemplo “investigá Visa”.\n\n"
            "Ejemplo: “Compré 3 AAPL a 180 USD en Wallbit con 1 de comisión”."
        )
        if warning:
            text += "\n\nNota: " + warning
        return text
