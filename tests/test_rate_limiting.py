"""_request's retry-on-rate-limit, and the pacing between per-item fetches in
search_messages/list_drafts — the two things that were missing when a real
search against a fresh Google Cloud project's low initial quota tripped
Gmail's 'Total Query Cost per minute per user' limit in practice.

asyncio.sleep is monkeypatched to a fast, call-counting no-op throughout, so
this suite stays instant despite exercising real backoff delays.
"""

from __future__ import annotations

import httpx
import pytest

from gmail_mcp import client as client_module
from gmail_mcp.client import GmailClient, GmailError

from conftest import at_level
from test_access import FakeTokenProvider, _Calls


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Replace asyncio.sleep with an instant no-op that records every call."""
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    return calls


def rate_limit_response(status_code=429, reason="RATE_LIMIT_EXCEEDED"):
    body = f'{{"error": {{"code": {status_code}, "errors": [{{"reason": "{reason}"}}]}}}}'
    return httpx.Response(status_code, text=body)


def make_client(settings, handler):
    calls = _Calls()

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls.n += 1
        return handler(request, calls.n)

    transport = httpx.MockTransport(counting_handler)
    http_client = httpx.AsyncClient(transport=transport)
    return GmailClient(settings, http_client=http_client, token_provider=FakeTokenProvider()), calls


# --------------------------------------------------------------------------- #
# _request retry-on-rate-limit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_request_retries_on_429_then_succeeds(settings, fast_sleep):
    def handler(request, n):
        if n == 1:
            return rate_limit_response(429)
        return httpx.Response(200, json={"emailAddress": "a@example.com"})

    client, calls = make_client(settings, handler)
    result = await client.get_profile()

    assert result == {"emailAddress": "a@example.com"}
    assert calls.n == 2
    assert len(fast_sleep) == 1  # exactly one backoff before the retry


@pytest.mark.asyncio
async def test_request_retries_on_403_with_rate_limit_reason_then_succeeds(settings, fast_sleep):
    def handler(request, n):
        if n == 1:
            return rate_limit_response(403, "rateLimitExceeded")
        return httpx.Response(200, json={"ok": True})

    client, calls = make_client(settings, handler)
    result = await client.get_profile()

    assert result == {"ok": True}
    assert calls.n == 2


@pytest.mark.asyncio
async def test_request_does_not_retry_on_an_unrelated_403(settings, fast_sleep):
    def handler(request, n):
        return httpx.Response(403, text='{"error": {"errors": [{"reason": "forbidden"}]}}')

    client, calls = make_client(settings, handler)
    with pytest.raises(GmailError, match="403"):
        await client.get_profile()

    assert calls.n == 1  # no retry attempted
    assert fast_sleep == []


@pytest.mark.asyncio
async def test_request_gives_up_after_max_retries(settings, fast_sleep):
    def handler(request, n):
        return rate_limit_response(429)

    client, calls = make_client(settings, handler)
    with pytest.raises(GmailError, match="429"):
        await client.get_profile()

    # 1 initial attempt + _MAX_RATE_LIMIT_RETRIES retries
    assert calls.n == client_module._MAX_RATE_LIMIT_RETRIES + 1
    assert len(fast_sleep) == client_module._MAX_RATE_LIMIT_RETRIES


@pytest.mark.asyncio
async def test_retry_backoff_grows_between_attempts(settings, fast_sleep):
    def handler(request, n):
        if n <= 2:
            return rate_limit_response(429)
        return httpx.Response(200, json={"ok": True})

    client, calls = make_client(settings, handler)
    await client.get_profile()

    assert len(fast_sleep) == 2
    assert fast_sleep[1] > fast_sleep[0]  # exponential, not flat


# --------------------------------------------------------------------------- #
# Pacing between per-item fetches
# --------------------------------------------------------------------------- #


def _list_and_get_handler(list_path, item_path_prefix, ids):
    def handler(request, n):
        if request.url.path.endswith(list_path):
            return httpx.Response(200, json={"messages": [{"id": i} for i in ids], "drafts": [{"id": i} for i in ids]})
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1], "payload": {"headers": []}})

    return handler


@pytest.mark.asyncio
async def test_search_messages_paces_between_item_fetches(settings, fast_sleep):
    client, calls = make_client(settings, _list_and_get_handler("/messages", "/messages/", ["m1", "m2", "m3"]))

    result = await client.search_messages(query="x", max_results=10)

    assert len(result["messages"]) == 3
    # 3 items -> 2 gaps, none after the last
    assert len(fast_sleep) == 2
    assert all(s == client_module._PER_ITEM_DELAY_SECONDS for s in fast_sleep)


@pytest.mark.asyncio
async def test_search_messages_with_one_result_does_not_pace_at_all(settings, fast_sleep):
    client, calls = make_client(settings, _list_and_get_handler("/messages", "/messages/", ["m1"]))

    result = await client.search_messages(query="x")

    assert len(result["messages"]) == 1
    assert fast_sleep == []


@pytest.mark.asyncio
async def test_list_drafts_paces_between_item_fetches(settings, fast_sleep):
    settings = at_level(settings, client_module.AccessLevel.BASIC)
    client, calls = make_client(settings, _list_and_get_handler("/drafts", "/drafts/", ["d1", "d2"]))

    await client.list_drafts()

    assert len(fast_sleep) == 1  # 2 items -> 1 gap
