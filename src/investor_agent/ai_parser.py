from __future__ import annotations

import json
import os
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import MovementDraft


class OpenAIMovementParser:
    """Extrae datos con GPT mediante OpenAI sin ejecutar operaciones."""

    def __init__(self):
        # Aceptamos temporalmente el nombre anterior para no obligar a copiar
        # nuevamente una credencial que ya está guardada de forma privada.
        self.api_key = (
            os.getenv("OPENAI_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        configured_model = (
            os.getenv("OPENAI_MODEL", "").strip()
            or os.getenv("OPENROUTER_PARSER_MODEL", "").strip()
            or "gpt-5.6-terra"
        )
        self.model = configured_model.removeprefix("openai/")
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).strip().rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def parse(self, text: str, base: MovementDraft | None = None) -> MovementDraft:
        if not self.available:
            raise RuntimeError("La integración con OpenAI no está configurada.")

        current = base.to_dict() if base else {}
        instructions = f"""
Extrae datos de un movimiento de inversión escrito en español.
Fecha actual: {date.today().isoformat()}.
Tipos admitidos: Compra, Venta, Depósito, Retiro, Dividendo, Interés, Comisión, Impuesto, Otro.
Una frase que agrega a la cartera acciones ya compradas describe una Compra.
Si el usuario da día y mes sin año, usa la ocurrencia pasada más reciente respecto de la fecha actual.
No inventes valores. Usa null cuando el usuario no dio el dato.
Normaliza ticker y moneda a mayúsculas. Las fechas deben ser YYYY-MM-DD.
Si hay un borrador actual, interpreta el mensaje como una corrección parcial: reemplaza solo los campos que el usuario corrige y conserva todos los demás.
Si el usuario dice que un valor correcto reemplaza a otro incorrecto, usa el correcto y no reutilices el descartado.
El resultado es solo un borrador; nunca afirmes que la operación fue ejecutada o guardada.
Borrador actual: {current}
""".strip()

        schema = self._schema()
        payload = {
            "model": self.model,
            "store": False,
            "instructions": instructions,
            "input": text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "movement_draft",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        response = self._post_json(payload)
        parsed = self._output_json(response)

        draft = MovementDraft.from_dict(current)
        for key, value in parsed.items():
            if value is not None:
                setattr(draft, key, value)
        draft.source_text = " | ".join(filter(None, [draft.source_text, text.strip()]))
        return draft

    def _post_json(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "portfolio-ledger-agent/0.1",
        }
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI respondió HTTP {error.code}: {detail[:300]}") from error
        except URLError as error:
            raise RuntimeError(f"No se pudo conectar con OpenAI: {error.reason}") from error

    @staticmethod
    def _output_json(response: dict) -> dict:
        if response.get("error"):
            error = response["error"]
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise RuntimeError(f"OpenAI no pudo generar la respuesta: {message}")
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise ValueError("La IA rechazó interpretar el mensaje.")
                if content.get("type") == "output_text":
                    return json.loads(content["text"])
        raise ValueError("La IA no devolvió un movimiento interpretable.")

    @staticmethod
    def _schema() -> dict:
        nullable_string = {"type": ["string", "null"]}
        nullable_number = {"type": ["number", "null"]}
        properties = {
            "movement_type": {
                "anyOf": [
                    {
                        "type": "string",
                        "enum": [
                            "Compra",
                            "Venta",
                            "Depósito",
                            "Retiro",
                            "Dividendo",
                            "Interés",
                            "Comisión",
                            "Impuesto",
                            "Otro",
                        ],
                    },
                    {"type": "null"},
                ]
            },
            "trade_date": nullable_string,
            "account": nullable_string,
            "ticker": nullable_string,
            "quantity": nullable_number,
            "unit_price": nullable_number,
            "cash_amount": nullable_number,
            "currency": nullable_string,
            "fee": nullable_number,
            "notes": nullable_string,
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }


# Alias temporal para no romper importaciones realizadas durante la configuración.
OpenRouterMovementParser = OpenAIMovementParser
