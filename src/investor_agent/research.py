from __future__ import annotations

import json
import os
import time
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .research_sources import source_policy_prompt


RESEARCH_TYPES = {"asset", "opportunities", "portfolio", "question"}


class OpenAIResearcher:
    """Investigación financiera con búsqueda web y fuentes auditables."""

    def __init__(self):
        self.api_key = (
            os.getenv("OPENAI_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip()
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).strip().rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def research(
        self,
        query: str,
        research_type: str,
        portfolio_context: dict[str, Any],
        include_advice: bool = False,
    ) -> dict[str, Any]:
        clean_query = query.strip()
        if not self.available:
            raise RuntimeError("La investigación requiere OPENAI_API_KEY.")
        if research_type not in RESEARCH_TYPES:
            raise ValueError("Tipo de investigación no admitido.")
        if not clean_query:
            raise ValueError("Escribí qué querés investigar.")

        instructions = self._instructions(
            research_type, portfolio_context, include_advice=include_advice
        )
        payload = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": "medium"},
            "tools": [
                {
                    "type": "web_search",
                    "search_context_size": "medium",
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "instructions": instructions,
            "input": clean_query,
            "max_output_tokens": 8000,
        }
        started_at = time.monotonic()
        response = self._post_json(payload)
        duration_ms = round((time.monotonic() - started_at) * 1000)
        answer, citations = self._answer_and_citations(response)
        sources, search_queries = self._search_metadata(response, citations)
        status, incomplete_reason = self._completion_state(response)
        usage = response.get("usage") or {}
        return {
            "response_id": response.get("id"),
            "model": response.get("model") or self.model,
            "answer": answer,
            "citations": citations,
            "sources": sources,
            "search_queries": search_queries,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "status": status,
            "incomplete_reason": incomplete_reason,
            "include_advice": include_advice,
            "duration_ms": duration_ms,
        }

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "portfolio-ledger-agent/0.2",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI respondió HTTP {error.code}: {detail[:500]}") from error
        except URLError as error:
            raise RuntimeError(f"No se pudo conectar con OpenAI: {error.reason}") from error

        if result.get("error"):
            api_error = result["error"]
            message = api_error.get("message", str(api_error)) if isinstance(api_error, dict) else str(api_error)
            raise RuntimeError(f"OpenAI no pudo completar la investigación: {message}")
        return result

    @classmethod
    def _answer_and_citations(
        cls, response: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        chunks: list[str] = []
        citations: list[dict[str, Any]] = []
        offset = 0
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise RuntimeError("OpenAI rechazó realizar la investigación.")
                if content.get("type") != "output_text":
                    continue
                text = str(content.get("text") or "")
                if chunks:
                    chunks.append("\n\n")
                    offset += 2
                chunks.append(text)
                for raw in content.get("annotations", []):
                    citation = raw.get("url_citation", raw)
                    url = cls._safe_url(citation.get("url"))
                    if not url:
                        continue
                    citations.append(
                        {
                            "url": url,
                            "title": citation.get("title") or url,
                            "start_index": offset + int(citation.get("start_index") or 0),
                            "end_index": offset + int(citation.get("end_index") or 0),
                        }
                    )
                offset += len(text)
        answer = "".join(chunks).strip()
        if not answer:
            raise RuntimeError("OpenAI no devolvió un informe interpretable.")
        return answer, citations

    @classmethod
    def _search_metadata(
        cls,
        response: dict[str, Any],
        citations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, str]], list[str]]:
        sources: list[dict[str, str]] = []
        queries: list[str] = []
        for item in response.get("output", []):
            if item.get("type") != "web_search_call":
                continue
            action = item.get("action") or {}
            raw_queries = action.get("queries") or [action.get("query")]
            for query in raw_queries:
                if query and query not in queries:
                    queries.append(str(query))
            for raw_source in action.get("sources") or []:
                url = cls._safe_url(raw_source.get("url"))
                if url:
                    sources.append(
                        {
                            "url": url,
                            "title": raw_source.get("title") or url,
                        }
                    )
        sources.extend({"url": item["url"], "title": item["title"]} for item in citations)
        deduplicated: list[dict[str, str]] = []
        seen: set[str] = set()
        for source in sources:
            if source["url"] in seen:
                continue
            seen.add(source["url"])
            deduplicated.append(source)
        return deduplicated, queries

    @staticmethod
    def _safe_url(value: Any) -> str | None:
        if not value:
            return None
        url = str(value).strip()
        return url if urlparse(url).scheme in {"http", "https"} else None

    @staticmethod
    def _completion_state(response: dict[str, Any]) -> tuple[str, str | None]:
        raw_status = str(response.get("status") or "completed")
        if raw_status == "completed":
            return "completed", None
        details = response.get("incomplete_details") or {}
        reason = details.get("reason") if isinstance(details, dict) else None
        return "incomplete", str(reason or raw_status)

    @staticmethod
    def _instructions(
        research_type: str,
        portfolio_context: dict[str, Any],
        include_advice: bool = False,
    ) -> str:
        mode = {
            "asset": "analizar un activo o empresa",
            "opportunities": "detectar hasta cinco candidatas para una watchlist",
            "portfolio": "revisar la cartera actual y sus riesgos",
            "question": "responder una pregunta libre de inversión",
        }[research_type]
        sections = [
            "## Resumen",
            "## Hechos verificables",
            "## Consenso y opiniones externas",
            "## Lectura del agente",
        ]
        if include_advice:
            sections.append("## Opinión orientativa")
            sections.append("## Zonas de precio de referencia")
        sections.extend(["## Riesgos y señales a vigilar", "## Próximos pasos"])
        advice_rules = (
            """
- Incluí una opinión explícita y provisional usando una de estas etiquetas: Comprar, Mantener, Reducir, Vender o No concluyente.
- Indicá el horizonte asumido, confianza baja/media/alta, razones principales y qué evidencia invalidaría la opinión.
- Si analizás varios activos, asigná una etiqueta a cada activo central mediante una lista breve, no una tabla.
- En “Zonas de precio de referencia”, presentá para cada activo central: precio de referencia con moneda, fecha, fuente y si está demorado; zona de compra o entrada; zona de mantener; zona de reducir o vender; horizonte; método de cálculo; confianza; y condición que invalida el cálculo.
- Expresá compra, mantenimiento y venta como rangos o umbrales razonables, no como un precio exacto con falsa precisión.
- Para calcular una zona exigí, como mínimo, un precio de mercado fechado, una fuente primaria de fundamentos y un método de valuación explicable. Si falta alguno, escribí “No calculable con datos suficientes” y enumerá el dato faltante.
- Un precio objetivo de analistas puede servir como contraste, pero nunca debe ser la única base de una zona de compra o venta.
- Diferenciá una zona de venta por valuación de una señal para revisar la tesis por deterioro del negocio.
- Aunque falten objetivos personales, podés opinar con supuestos explícitos; no sugieras tamaño de posición hasta contar con horizonte y límites de riesgo.
""".strip()
            if include_advice
            else "- No incluyas una recomendación de comprar, mantener, reducir o vender si no fue solicitada."
        )
        return f"""
Sos el módulo de investigación de Portfolio Ledger Agent. Tu tarea es {mode}.
Fecha actual: {date.today().isoformat()}.

Respondé en español claro y conciso. Investigá en la web antes de responder. Priorizá:
1. documentos regulatorios, relaciones con inversores y datos oficiales;
2. medios financieros reconocidos y proveedores de datos de mercado;
3. opiniones de analistas identificadas con fuente y fecha.

Fuentes gratuitas priorizadas y función esperada:
{source_policy_prompt()}

Separá siempre el informe con estos encabezados exactos:
{chr(10).join(sections)}

Reglas obligatorias:
- Citá cada precio, múltiplo, resultado, expectativa, consenso y noticia sensible al tiempo.
- Diferenciá hechos, opiniones externas e inferencias propias; no presentes inferencias como hechos.
- Nunca llames a una inversión “segura”, “ganga” o “superacción” como certeza.
- No inventes precios objetivo ni consenso. Si no hay evidencia suficiente, decilo.
- Tratá toda cotización como sensible al tiempo: indicá fecha, moneda y si la fuente informa demora o no permite verificar tiempo real.
- Contrastá el precio visible con una segunda fuente cuando una diferencia pueda cambiar la zona propuesta.
- No presentes una opinión como certeza ni prometas rentabilidad.
- Nunca ejecutes operaciones ni afirmes que una orden fue enviada al broker.
- En modo oportunidades, devolvé como máximo cinco candidatas y explicá por qué podrían merecer estudio y qué invalidaría el interés.
- Mantené el informe por debajo de 2.800 palabras y priorizá conclusiones, evidencia, riesgos y acciones de seguimiento.
- Usá las citas nativas de la búsqueda web para respaldar cada afirmación sensible al tiempo.
- No agregues manualmente una bibliografía ni pegues URLs en la prosa; la interfaz construye las referencias desde las anotaciones de la API.
{advice_rules}

Contexto local de la cartera (puede estar incompleto):
{json.dumps(portfolio_context, ensure_ascii=False)}
""".strip()
