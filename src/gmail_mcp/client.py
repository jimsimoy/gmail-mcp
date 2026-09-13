"""Thin async wrapper over the Gmail API v1 REST surface.

Every method that changes or sends anything calls :func:`access.require` first,
so a write attempted above the configured GMAIL_ACCESS_LEVEL raises
:class:`~gmail_mcp.access.AccessDenied` *before* any HTTP request is made —
this is the dispatch-time half of the two-gate enforcement described in
access.py (the other half is which tools server.py registers at all).

Rate limits: Gmail enforces a per-minute "Total Query Cost" quota per user,
and fetching N search results costs one `messages.get` call each on top of
the `messages.list` call itself — a search with a generous `max_results` can
trip it in a single burst (observed directly: 78 back-to-back message fetches
across two searches hit `RATE_LIMIT_EXCEEDED` on a freshly-created project).
`_request` backs off and retries on a detected rate-limit response, and
`search_messages`/`list_drafts` pace their per-item fetches — see
AGENTS.md §5 for the policy this implements and what a calling agent should
still do on top of it (state the request count, don't loop past a stop-and-
report failure that survives the retries here).
"""

from __future__ import annotations

import asyncio
import base64
import re
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Any, Mapping, Sequence

import httpx

from .access import AccessLevel, require
from .auth import TokenProvider
from .config import Settings

_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Retry policy for a detected rate-limit response — not for any other error.
_MAX_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BASE_DELAY_SECONDS = 5.0

#: Gap between each per-item fetch in a fan-out loop (search_messages,
#: list_drafts), so a large max_results doesn't burst the whole batch at once.
_PER_ITEM_DELAY_SECONDS = 0.3


def _is_rate_limit_response(status_code: int, body: str) -> bool:
    if status_code == 429:
        return True
    return status_code == 403 and (
        "rateLimitExceeded" in body or "RATE_LIMIT_EXCEEDED" in body
    )


class GmailError(RuntimeError):
    """Raised for a non-2xx response from the Gmail API, with its message extracted."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _build_mime(
    to: str,
    subject: str,
    body_text: str,
    *,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Build an RFC 2822 message and return it base64url-encoded for Gmail's `raw` field."""
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    return _b64url_encode(msg.as_bytes())


def _header(headers: Sequence[Mapping[str, str]], name: str) -> str | None:
    lname = name.lower()
    for h in headers:
        if h.get("name", "").lower() == lname:
            return h.get("value")
    return None


def _extract_text(payload: Mapping[str, Any]) -> tuple[str, bool]:
    """Walk a message payload for the best text body. Returns (text, is_html)."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body") or {}
    data = body.get("data")

    if mime_type == "text/plain" and data:
        return _b64url_decode(data).decode("utf-8", errors="replace"), False

    parts = payload.get("parts") or []
    # Prefer a text/plain leaf anywhere in the tree; fall back to text/html.
    plain = _find_part(parts, "text/plain")
    if plain is not None:
        return _b64url_decode(plain).decode("utf-8", errors="replace"), False

    html = _find_part(parts, "text/html")
    if html is not None:
        return _b64url_decode(html).decode("utf-8", errors="replace"), True

    if mime_type == "text/html" and data:
        return _b64url_decode(data).decode("utf-8", errors="replace"), True

    return "", False


def _find_part(parts: Sequence[Mapping[str, Any]], mime_type: str) -> str | None:
    for part in parts:
        if part.get("mimeType") == mime_type:
            data = (part.get("body") or {}).get("data")
            if data:
                return data
        nested = part.get("parts")
        if nested:
            found = _find_part(nested, mime_type)
            if found is not None:
                return found
    return None


def _strip_html(html: str) -> str:
    """Best-effort plain-text fallback when a message has no text/plain part."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def summarize_message(msg: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a Gmail API message resource into the shape tools return."""
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    text, is_html = _extract_text(payload)
    if is_html:
        text = _strip_html(text)
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "label_ids": msg.get("labelIds", []),
        "snippet": msg.get("snippet"),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "cc": _header(headers, "Cc"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "message_id_header": _header(headers, "Message-ID"),
        "body_text": text,
    }


class GmailClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = token_provider or TokenProvider(settings.credentials)
        self._http = http_client or httpx.AsyncClient(timeout=settings.timeout)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._tokens.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        static_kwargs = {k: v for k, v in kwargs.items() if k != "headers"}
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            token = await self._tokens.get_token()
            headers = dict(kwargs.get("headers") or {})
            headers["Authorization"] = f"Bearer {token}"
            response = await self._http.request(
                method, f"{_BASE_URL}{path}", headers=headers, **static_kwargs
            )
            if response.status_code >= 400:
                body = response.text
                if (
                    _is_rate_limit_response(response.status_code, body)
                    and attempt < _MAX_RATE_LIMIT_RETRIES
                ):
                    await asyncio.sleep(_RATE_LIMIT_BASE_DELAY_SECONDS * (2**attempt))
                    continue
                raise GmailError(
                    self._settings.redact(f"Gmail API error {response.status_code}: {body}")
                )
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        raise AssertionError("unreachable")  # loop always returns or raises above

    async def _fetch_each_paced(self, refs: Sequence[Mapping[str, Any]], fetch_one) -> list[Any]:
        """Fetch one item per ref via `fetch_one(ref)`, with a gap between calls.

        `search_messages`/`list_drafts` fan out into one full-item GET per
        search result — with no gap, a generous `max_results` bursts dozens of
        calls at once and can trip Gmail's per-minute quota on its own (this
        happened in practice: see the module docstring). `_request`'s own
        retry-on-rate-limit is the safety net; this loop is what avoids
        needing it in the first place.
        """
        results = []
        for i, ref in enumerate(refs):
            results.append(await fetch_one(ref))
            if i < len(refs) - 1:
                await asyncio.sleep(_PER_ITEM_DELAY_SECONDS)
        return results

    # ── Profile & labels (READONLY) ──────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        return await self._request("GET", "/profile")

    async def list_labels(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/labels")
        return data.get("labels", [])

    # ── Reading (READONLY) ───────────────────────────────────────────────────

    async def search_messages(
        self,
        query: str = "",
        max_results: int = 25,
        page_token: str | None = None,
        label_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": max(1, min(max_results, 500))}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = list(label_ids)
        data = await self._request("GET", "/messages", params=params)
        refs = data.get("messages", [])
        full_messages = await self._fetch_each_paced(
            refs, lambda ref: self._request("GET", f"/messages/{ref['id']}", params={"format": "full"})
        )
        messages = [summarize_message(m) for m in full_messages]
        return {
            "messages": messages,
            "next_page_token": data.get("nextPageToken"),
            "result_size_estimate": data.get("resultSizeEstimate"),
        }

    async def get_message(self, message_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/messages/{message_id}", params={"format": "full"})
        return summarize_message(data)

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/threads/{thread_id}", params={"format": "full"})
        return {
            "thread_id": data.get("id"),
            "messages": [summarize_message(m) for m in data.get("messages", [])],
        }

    # ── Drafts (BASIC) ────────────────────────────────────────────────────────

    async def list_drafts(self, max_results: int = 25, page_token: str | None = None) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.BASIC, "list_drafts")
        params: dict[str, Any] = {"maxResults": max(1, min(max_results, 500))}
        if page_token:
            params["pageToken"] = page_token
        data = await self._request("GET", "/drafts", params=params)
        full_drafts = await self._fetch_each_paced(
            data.get("drafts", []),
            lambda ref: self._request("GET", f"/drafts/{ref['id']}", params={"format": "full"}),
        )
        drafts = [self._summarize_draft(d) for d in full_drafts]
        return {"drafts": drafts, "next_page_token": data.get("nextPageToken")}

    async def get_draft(self, draft_id: str) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.BASIC, "get_draft")
        data = await self._request("GET", f"/drafts/{draft_id}", params={"format": "full"})
        return self._summarize_draft(data)

    async def create_draft(
        self,
        to: str,
        subject: str,
        body_text: str,
        cc: str | None = None,
        bcc: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.BASIC, "create_draft")
        _validate_recipients(to, cc, bcc)
        raw = _build_mime(to, subject, body_text, cc=cc, bcc=bcc, in_reply_to=in_reply_to, references=in_reply_to)
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        data = await self._request("POST", "/drafts", json={"message": message})
        return self._summarize_draft(data)

    async def update_draft(
        self,
        draft_id: str,
        to: str,
        subject: str,
        body_text: str,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.BASIC, "update_draft")
        _validate_recipients(to, cc, bcc)
        raw = _build_mime(to, subject, body_text, cc=cc, bcc=bcc)
        data = await self._request("PUT", f"/drafts/{draft_id}", json={"message": {"raw": raw}})
        return self._summarize_draft(data)

    async def delete_draft(self, draft_id: str) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.BASIC, "delete_draft")
        await self._request("DELETE", f"/drafts/{draft_id}")
        return {"deleted": draft_id}

    def _summarize_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        message = draft.get("message") or {}
        summary = summarize_message(message) if message.get("payload") else {"id": message.get("id")}
        return {"draft_id": draft.get("id"), **summary}

    # ── Sending & organizing (FULL) ──────────────────────────────────────────

    async def send_draft(self, draft_id: str) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.FULL, "send_draft")
        return await self._request("POST", "/drafts/send", json={"id": draft_id})

    async def send_message(
        self,
        to: str,
        subject: str,
        body_text: str,
        cc: str | None = None,
        bcc: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.FULL, "send_message")
        _validate_recipients(to, cc, bcc)
        raw = _build_mime(to, subject, body_text, cc=cc, bcc=bcc, in_reply_to=in_reply_to, references=in_reply_to)
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return await self._request("POST", "/messages/send", json=body)

    async def modify_message_labels(
        self,
        message_id: str,
        add_label_ids: Sequence[str] | None = None,
        remove_label_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.FULL, "modify_message_labels")
        payload = {
            "addLabelIds": list(add_label_ids or []),
            "removeLabelIds": list(remove_label_ids or []),
        }
        data = await self._request("POST", f"/messages/{message_id}/modify", json=payload)
        return summarize_message(data)

    async def trash_message(self, message_id: str) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.FULL, "trash_message")
        data = await self._request("POST", f"/messages/{message_id}/trash")
        return summarize_message(data)

    async def untrash_message(self, message_id: str) -> dict[str, Any]:
        require(self._settings.access_level, AccessLevel.FULL, "untrash_message")
        data = await self._request("POST", f"/messages/{message_id}/untrash")
        return summarize_message(data)


_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email_list(value: str) -> bool:
    """True if every comma-separated address in `value` has a plausible shape.

    Not a full RFC 5322 validator — just enough to catch "forgot the @" and
    similar mistakes before they become a silently-empty or malformed draft.
    Each entry may be a bare address or a "Display Name <addr>" form.
    """
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            return False
        addr = parseaddr(entry)[1]
        if not addr or not _ADDR_RE.match(addr):
            return False
    return True


def _validate_recipients(to: str, cc: str | None, bcc: str | None) -> None:
    """Fail fast on an empty/malformed address rather than silently drafting nothing."""
    if not to or not _looks_like_email_list(to):
        raise ValueError(f"'to' does not look like a valid email address: {to!r}")
    for label, value in (("cc", cc), ("bcc", bcc)):
        if value and not _looks_like_email_list(value):
            raise ValueError(f"'{label}' does not look like a valid email address: {value!r}")
