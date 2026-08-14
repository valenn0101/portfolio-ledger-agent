from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie


SESSION_COOKIE = "portfolio_ledger_session"


def _env_flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = False
    username: str = ""
    password: str = ""
    session_secret: str = ""
    session_hours: int = 12
    secure_cookies: str = "auto"

    @classmethod
    def from_env(cls) -> "AuthSettings":
        enabled = _env_flag(os.getenv("APP_REQUIRE_AUTH"))
        raw_hours = os.getenv("APP_SESSION_HOURS", "12")
        try:
            session_hours = int(raw_hours)
        except ValueError as error:
            raise RuntimeError("APP_SESSION_HOURS debe ser un número entero.") from error
        if not 1 <= session_hours <= 168:
            raise RuntimeError("APP_SESSION_HOURS debe estar entre 1 y 168 horas.")

        secure_cookies = os.getenv("APP_SECURE_COOKIES", "auto").strip().lower()
        if secure_cookies not in {"auto", "always", "never"}:
            raise RuntimeError("APP_SECURE_COOKIES debe ser auto, always o never.")

        settings = cls(
            enabled=enabled,
            username=os.getenv("APP_USERNAME", "").strip(),
            password=os.getenv("APP_PASSWORD", ""),
            session_secret=os.getenv("APP_SESSION_SECRET", ""),
            session_hours=session_hours,
            secure_cookies=secure_cookies,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = [
            name
            for name, value in (
                ("APP_USERNAME", self.username),
                ("APP_PASSWORD", self.password),
                ("APP_SESSION_SECRET", self.session_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "La autenticación está activada pero faltan: " + ", ".join(missing)
            )
        if len(self.password) < 12:
            raise RuntimeError("APP_PASSWORD debe tener al menos 12 caracteres.")
        if len(self.session_secret) < 32:
            raise RuntimeError("APP_SESSION_SECRET debe tener al menos 32 caracteres.")


class SessionAuth:
    def __init__(self, settings: AuthSettings):
        settings.validate()
        self.settings = settings

    @classmethod
    def from_env(cls) -> "SessionAuth":
        return cls(AuthSettings.from_env())

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def authenticate(self, username: str, password: str) -> bool:
        if not self.enabled:
            return True
        return hmac.compare_digest(username, self.settings.username) and hmac.compare_digest(
            password, self.settings.password
        )

    def issue_cookie(self, username: str, secure: bool = False) -> str:
        expires_at = int(time.time()) + self.settings.session_hours * 3600
        payload = _base64url_encode(
            json.dumps(
                {"username": username, "expires_at": expires_at},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = hmac.new(
            self.settings.session_secret.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        token = f"{payload}.{_base64url_encode(signature)}"
        attributes = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={self.settings.session_hours * 3600}",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    def clear_cookie(self, secure: bool = False) -> str:
        attributes = [
            f"{SESSION_COOKIE}=",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    def is_authenticated(self, cookie_header: str | None) -> bool:
        if not self.enabled:
            return True
        token = self._cookie_value(cookie_header)
        if not token:
            return False
        try:
            payload, signature = token.split(".", 1)
            expected = hmac.new(
                self.settings.session_secret.encode("utf-8"),
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(_base64url_decode(signature), expected):
                return False
            data = json.loads(_base64url_decode(payload).decode("utf-8"))
            return (
                hmac.compare_digest(str(data.get("username", "")), self.settings.username)
                and int(data.get("expires_at", 0)) > int(time.time())
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return False

    def should_secure_cookie(self, forwarded_proto: str | None) -> bool:
        if self.settings.secure_cookies == "always":
            return True
        if self.settings.secure_cookies == "never":
            return False
        return (forwarded_proto or "").split(",", 1)[0].strip().lower() == "https"

    @staticmethod
    def _cookie_value(cookie_header: str | None) -> str | None:
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None


class LoginThrottle:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        with self._lock:
            attempts = self._active_attempts(client_id)
            self._attempts[client_id] = attempts
            return len(attempts) < self.max_attempts

    def record_failure(self, client_id: str) -> None:
        with self._lock:
            attempts = self._active_attempts(client_id)
            attempts.append(time.monotonic())
            self._attempts[client_id] = attempts

    def reset(self, client_id: str) -> None:
        with self._lock:
            self._attempts.pop(client_id, None)

    def _active_attempts(self, client_id: str) -> list[float]:
        cutoff = time.monotonic() - self.window_seconds
        return [attempt for attempt in self._attempts.get(client_id, []) if attempt > cutoff]
