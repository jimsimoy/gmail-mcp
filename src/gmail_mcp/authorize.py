"""One-time interactive OAuth 2.0 consent flow for the Gmail API.

Gmail does not support service accounts for a personal @gmail.com inbox — there
is no "share this inbox with a robot" concept the way there is for a Doc or a
Sheet. Every call has to be authorized on behalf of a real Google account via
the standard OAuth 2.0 flow. See
https://developers.google.com/identity/protocols/oauth2/native-app

Run this once with `gmail-mcp-authorize`. It opens a browser, asks you to sign
in and grant access, then prints a refresh token to put in GMAIL_REFRESH_TOKEN.
The MCP server itself (server.py / auth.py) never does this interactive step —
it only refreshes the token this produces.

This always requests the FULL scope ceiling (access.CONSENT_SCOPES), regardless
of what GMAIL_ACCESS_LEVEL the server will actually run at. That is deliberate:
GMAIL_ACCESS_LEVEL narrows what this server chooses to use day to day, but
raising it later should never require you to come back here and re-consent.

Before running this, you need one Google Cloud project with:
  1. The Gmail API enabled (APIs & Services -> Library -> "Gmail API" -> Enable).
  2. An OAuth consent screen configured (APIs & Services -> OAuth consent screen).
     "External" + "Testing" is fine for a single-user tool like this one — you
     do not need Google's app-verification review for your own account to use
     it in Testing mode.
  3. An OAuth 2.0 Client ID of type "Desktop app" (APIs & Services ->
     Credentials -> Create Credentials -> OAuth client ID -> Desktop app).
     Copy its Client ID and Client Secret — you'll be asked for them below.

Uses the OAuth 2.0 "installed app" / loopback flow described at
https://developers.google.com/identity/protocols/oauth2/native-app
"""

from __future__ import annotations

import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from .access import CONSENT_SCOPES

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            body = b"Authorized. You can close this tab and return to the terminal."
        else:
            _CallbackHandler.error = params.get("error", ["unknown_error"])[0]
            body = f"Authorization failed: {_CallbackHandler.error}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence default request logging
        pass


def main() -> None:
    client_id = os.environ.get("GMAIL_CLIENT_ID") or input("GMAIL_CLIENT_ID: ").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET") or input("GMAIL_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        print("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are required.", file=sys.stderr)
        raise SystemExit(1)

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    # Use "localhost" (not 127.0.0.1) to match the redirect_uri registered on
    # Desktop-app OAuth clients in Google Cloud Console.
    redirect_uri = f"http://localhost:{port}/"

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(CONSENT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force a refresh_token even on a re-auth
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print("This will request FULL access (read, drafts, send, labels/trash) — the")
    print("ceiling for this server. GMAIL_ACCESS_LEVEL controls what it actually")
    print("uses day to day; see the README before raising it above READONLY.\n")
    print("Opening browser for Google sign-in and Gmail consent...")
    print(f"If it doesn't open automatically, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server.handle_request()  # blocks until the redirect hits us, once
    server.server_close()

    if _CallbackHandler.error or not _CallbackHandler.auth_code:
        print(f"Authorization failed: {_CallbackHandler.error}", file=sys.stderr)
        raise SystemExit(1)

    response = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _CallbackHandler.auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        print(f"Token exchange failed ({response.status_code}): {response.text}", file=sys.stderr)
        raise SystemExit(1)

    body = response.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        print(
            "No refresh_token in the response. This usually means this Google account "
            "already granted consent before without `prompt=consent` — revoke prior "
            "access at https://myaccount.google.com/permissions and try again.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("\nSuccess. Put this in your .env:\n")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={refresh_token}")
    print("GMAIL_ACCESS_LEVEL=READONLY  # raise only when you actually need drafts/send")


if __name__ == "__main__":
    main()
