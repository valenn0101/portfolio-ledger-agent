from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from investor_agent.config import load_env_file  # noqa: E402

load_env_file(BASE_DIR / ".env")

from investor_agent import InvestmentAgentService  # noqa: E402
from investor_agent.auth import LoginThrottle, SessionAuth  # noqa: E402


DATA_DIR = Path(os.getenv("AGENT_DATA_DIR", str(BASE_DIR / "data")))
SERVICE = InvestmentAgentService(
    database_path=DATA_DIR / "inversiones.db",
    template_path=BASE_DIR / "assets" / "plantilla_base.xlsx",
    workbook_path=DATA_DIR / "movimientos.xlsx",
)
AUTH = SessionAuth.from_env()
LOGIN_THROTTLE = LoginThrottle()


def valid_session_id(value: str | None) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        return value
    return "local"


def query_flag(query: dict[str, list[str]], name: str) -> bool:
    return str(query.get(name, [""])[0]).lower() in {"1", "true", "yes", "si", "sí"}


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "PortfolioLedgerAgent/0.1"

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        query = parse_qs(request.query)
        session_id = valid_session_id(query.get("session_id", [None])[0])

        if request.path == "/api/health":
            self._json({"status": "ok"})
            return
        if request.path == "/login":
            if self._is_authenticated():
                self._redirect("/")
            else:
                self._file(BASE_DIR / "static" / "login.html")
            return
        if request.path in {"/app.css", "/login.css", "/login.js"}:
            self._file(BASE_DIR / "static" / request.path.lstrip("/"))
            return
        if not self._is_authenticated():
            self._unauthorized(request.path)
            return

        if request.path == "/api/session":
            self._json(
                {
                    "authenticated": True,
                    "auth_enabled": AUTH.enabled,
                    "username": AUTH.settings.username if AUTH.enabled else None,
                }
            )
        elif request.path == "/api/status":
            self._json(SERVICE.status(session_id))
        elif request.path == "/api/movements":
            self._json({"movements": SERVICE.movements(30)})
        elif request.path == "/api/research":
            raw_limit = query.get("limit", ["10"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                limit = 10
            self._json({"reports": SERVICE.research_reports(limit)})
        elif request.path == "/api/research/sources":
            self._json({"sources": SERVICE.research_sources()})
        elif request.path == "/api/portfolio/valuation":
            self._json(SERVICE.portfolio_valuation(refresh=query_flag(query, "refresh")))
        elif request.path == "/api/portfolio/consensus":
            self._json(SERVICE.portfolio_consensus(refresh=query_flag(query, "refresh")))
        elif request.path == "/api/market/consensus":
            symbol = query.get("symbol", [""])[0]
            try:
                self._json(
                    SERVICE.analyst_consensus(
                        symbol,
                        refresh=query_flag(query, "refresh"),
                        details=query_flag(query, "details"),
                    )
                )
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        elif request.path == "/api/market/status":
            self._json(SERVICE.market_status())
        elif request.path == "/api/market/macro":
            try:
                self._json(SERVICE.macro_snapshot(refresh=query_flag(query, "refresh")))
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        elif request.path == "/api/market/sec":
            symbol = query.get("symbol", [""])[0]
            try:
                self._json(
                    SERVICE.sec_company_snapshot(
                        symbol, refresh=query_flag(query, "refresh")
                    )
                )
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        elif request.path == "/api/excel":
            self._file(SERVICE.excel.workbook_path, download_name="movimientos.xlsx")
        elif request.path == "/":
            self._file(BASE_DIR / "static" / "index.html")
        elif request.path in {"/app.css", "/app.js"}:
            self._file(BASE_DIR / "static" / request.path.lstrip("/"))
        else:
            self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "El cuerpo de la solicitud no es válido."}, HTTPStatus.BAD_REQUEST)
            return

        if request.path == "/api/login":
            if not AUTH.enabled:
                self._json({"authenticated": True, "auth_enabled": False})
                return
            client_id = self.client_address[0]
            if not LOGIN_THROTTLE.is_allowed(client_id):
                self._json(
                    {"error": "Demasiados intentos. Esperá cinco minutos antes de volver a probar."},
                    HTTPStatus.TOO_MANY_REQUESTS,
                    headers={"Retry-After": "300"},
                )
                return
            username = str(payload.get("username", ""))
            password = str(payload.get("password", ""))
            if not AUTH.authenticate(username, password):
                LOGIN_THROTTLE.record_failure(client_id)
                self._json(
                    {"error": "Usuario o contraseña incorrectos."},
                    HTTPStatus.UNAUTHORIZED,
                )
                return
            LOGIN_THROTTLE.reset(client_id)
            secure = AUTH.should_secure_cookie(self.headers.get("X-Forwarded-Proto"))
            self._json(
                {"authenticated": True, "username": AUTH.settings.username},
                headers={"Set-Cookie": AUTH.issue_cookie(username, secure=secure)},
            )
            return
        if request.path == "/api/logout":
            secure = AUTH.should_secure_cookie(self.headers.get("X-Forwarded-Proto"))
            self._json(
                {"authenticated": False},
                headers={"Set-Cookie": AUTH.clear_cookie(secure=secure)},
            )
            return
        if not self._is_authenticated():
            self._unauthorized(request.path)
            return

        if request.path == "/api/message":
            session_id = valid_session_id(payload.get("session_id"))
            try:
                result = SERVICE.handle_message(session_id, str(payload.get("message", "")))
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
                return
            self._json(result)
        elif request.path == "/api/settings":
            result = SERVICE.update_settings(payload)
            self._json(result)
        elif request.path == "/api/research":
            if not SERVICE.researcher.available:
                self._json(
                    {"error": "La investigación requiere configurar OPENAI_API_KEY."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            session_id = valid_session_id(payload.get("session_id"))
            try:
                result = SERVICE.research(
                    session_id,
                    str(payload.get("query", "")),
                    str(payload.get("research_type", "question")),
                    include_advice=payload.get("include_advice") is True,
                )
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except RuntimeError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
                return
            self._json(result)
        else:
            self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Solicitud demasiado grande")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _json(
        self,
        payload: dict,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, download_name: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self._json({"error": "El archivo todavía no está disponible."}, HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self) -> bool:
        return AUTH.is_authenticated(self.headers.get("Cookie"))

    def _unauthorized(self, request_path: str) -> None:
        if request_path.startswith("/api/"):
            self._json(
                {"error": "La sesión no es válida. Volvé a iniciar sesión."},
                HTTPStatus.UNAUTHORIZED,
            )
        else:
            self._redirect("/login")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )

    def log_message(self, format: str, *args) -> None:
        if os.getenv("AGENT_VERBOSE") == "1":
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio Ledger Agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AgentHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Portfolio Ledger Agent listo en {url}")
    print("Para detenerlo, presioná Ctrl+C.")
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAgente detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
