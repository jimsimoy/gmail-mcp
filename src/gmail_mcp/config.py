"""Credential loading and runtime settings.

Everything sensitive comes from the environment (optionally via a .env file).
Nothing is ever written back to it, and every credential is redacted from any
text this package produces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .access import AccessLevel
from .auth import GmailCredentials


class ConfigError(RuntimeError):
    """Raised when credentials are missing or malformed."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one server process."""

    credentials: GmailCredentials
    access_level: AccessLevel = AccessLevel.READONLY
    default_max_results: int = 25
    timeout: float = 30.0

    def redact(self, text: str) -> str:
        """Strip any credential that leaked into a message."""
        for secret in (
            self.credentials.client_secret,
            self.credentials.refresh_token,
        ):
            if secret and secret in text:
                text = text.replace(secret, "***REDACTED***")
        return text


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Build :class:`Settings` from the environment.

    A .env file is loaded if present, but real environment variables always win
    so that an MCP client's ``env`` block can override the file.
    """
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        # Look next to the caller first, then at the installed project root.
        for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
            if candidate.is_file():
                load_dotenv(candidate, override=False)
                break

    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    missing = [
        name
        for name, value in (
            ("GMAIL_CLIENT_ID", client_id),
            ("GMAIL_CLIENT_SECRET", client_secret),
            ("GMAIL_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env — if you don't have a refresh token yet, "
            "run `gmail-mcp-authorize` once to get one."
        )

    try:
        access_level = AccessLevel.parse(os.environ.get("GMAIL_ACCESS_LEVEL"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    return Settings(
        credentials=GmailCredentials(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        ),
        access_level=access_level,
        default_max_results=_env_int("GMAIL_DEFAULT_MAX_RESULTS", 25),
        timeout=float(os.environ.get("GMAIL_TIMEOUT", "30") or 30),
    )
