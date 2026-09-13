"""OAuth 2.0 access-token management for the Gmail API.

Gmail does not support service accounts for a personal inbox — every call must
be authorized on behalf of a real Google account via the standard OAuth 2.0
flow. See https://developers.google.com/identity/protocols/oauth2/native-app

This module only *refreshes* an existing refresh token into short-lived access
tokens. The one-time interactive consent flow that produces the refresh token
lives in authorize.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REFRESH_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class GmailCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


class TokenProvider:
    """Caches an access token and refreshes it shortly before it would expire."""

    def __init__(self, credentials: GmailCredentials) -> None:
        self._credentials = credentials
        self._http = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_token(self) -> str:
        now = time.time()
        if self._token is None or now >= self._expires_at - _REFRESH_MARGIN_SECONDS:
            self._token, expires_in = await self._refresh()
            self._expires_at = now + expires_in
        return self._token

    async def _refresh(self) -> tuple[str, int]:
        response = await self._http.post(
            _TOKEN_URL,
            data={
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "refresh_token": self._credentials.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Failed to refresh Gmail access token ({response.status_code}): "
                f"{response.text}. The refresh token may have been revoked — "
                "run `gmail-mcp-authorize` again."
            )
        body = response.json()
        return body["access_token"], int(body.get("expires_in", 3600))
