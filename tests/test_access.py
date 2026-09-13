"""Access-level enforcement: the gate that makes READONLY mean read-only.

Two independent gates are tested here, matching access.py's own description:
1. Dispatch — GmailClient must refuse a write above the configured level
   *before* any HTTP request is made.
2. Registration — server.register_tiered_tools must not hand the model a tool
   above the configured level at all.
"""

from __future__ import annotations

import httpx
import pytest

from gmail_mcp import server
from gmail_mcp.access import AccessDenied, AccessLevel, DESCRIPTIONS, SCOPES_FOR_LEVEL
from gmail_mcp.client import GmailClient

from conftest import at_level


class FakeTokenProvider:
    """Stands in for auth.TokenProvider without ever touching the network."""

    def __init__(self, token: str = "fake-access-token") -> None:
        self._token = token
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        return self._token

    async def aclose(self) -> None:
        pass


def client_at(settings, level, handler=None) -> tuple[GmailClient, "_Calls"]:
    calls = _Calls()

    def default_handler(request: httpx.Request) -> httpx.Response:
        calls.n += 1
        return httpx.Response(200, json={"id": "msg1", "labelIds": []})

    transport = httpx.MockTransport(handler or default_handler)
    http_client = httpx.AsyncClient(transport=transport)
    cfg = at_level(settings, level)
    return GmailClient(cfg, http_client=http_client, token_provider=FakeTokenProvider()), calls


class _Calls:
    def __init__(self) -> None:
        self.n = 0


# --------------------------------------------------------------------------- #
# The level model
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, AccessLevel.READONLY),
        ("", AccessLevel.READONLY),
        ("readonly", AccessLevel.READONLY),
        ("  Basic ", AccessLevel.BASIC),
        ("FULL", AccessLevel.FULL),
    ],
)
def test_parse_is_case_and_space_insensitive(raw, expected):
    assert AccessLevel.parse(raw) is expected


def test_parse_rejects_unknown_level():
    with pytest.raises(ValueError, match="not valid"):
        AccessLevel.parse("SUPERUSER")


def test_levels_are_ordered():
    assert AccessLevel.FULL.permits(AccessLevel.BASIC)
    assert AccessLevel.BASIC.permits(AccessLevel.READONLY)
    assert not AccessLevel.READONLY.permits(AccessLevel.BASIC)
    assert not AccessLevel.BASIC.permits(AccessLevel.FULL)


def test_every_level_has_a_description_and_scope_list():
    for level in AccessLevel:
        assert DESCRIPTIONS[level]
        assert SCOPES_FOR_LEVEL[level]


def test_scopes_are_cumulative():
    ro, basic, full = (
        set(SCOPES_FOR_LEVEL[AccessLevel.READONLY]),
        set(SCOPES_FOR_LEVEL[AccessLevel.BASIC]),
        set(SCOPES_FOR_LEVEL[AccessLevel.FULL]),
    )
    assert ro <= basic <= full


def test_full_scope_scope_never_requests_the_dangerous_broad_scope():
    """https://mail.google.com/ grants permanent delete + account settings changes.

    This server never requests it at any level — see access.py's module note.
    """
    for scopes in SCOPES_FOR_LEVEL.values():
        assert "https://mail.google.com/" not in scopes


# --------------------------------------------------------------------------- #
# Gate 1: dispatch (GmailClient)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_readonly_client_allows_reads(settings):
    client, calls = client_at(settings, AccessLevel.READONLY)
    await client.get_profile()
    assert calls.n == 1


@pytest.mark.asyncio
async def test_readonly_client_refuses_draft_creation(settings):
    client, calls = client_at(settings, AccessLevel.READONLY)
    with pytest.raises(AccessDenied, match="BASIC"):
        await client.create_draft("a@example.com", "Subject", "Body")
    assert calls.n == 0, "the gate must refuse before any HTTP request is made"


@pytest.mark.asyncio
async def test_readonly_client_refuses_send(settings):
    client, calls = client_at(settings, AccessLevel.READONLY)
    with pytest.raises(AccessDenied, match="FULL"):
        await client.send_message("a@example.com", "Subject", "Body")
    assert calls.n == 0


@pytest.mark.asyncio
async def test_basic_client_allows_drafts_but_refuses_send(settings):
    client, calls = client_at(settings, AccessLevel.BASIC)
    await client.create_draft("a@example.com", "Subject", "Body")
    assert calls.n == 1
    with pytest.raises(AccessDenied, match="FULL"):
        await client.send_draft("draft1")
    assert calls.n == 1, "the refused send must not have reached the network"


@pytest.mark.asyncio
async def test_basic_client_refuses_trash(settings):
    client, calls = client_at(settings, AccessLevel.BASIC)
    with pytest.raises(AccessDenied, match="FULL"):
        await client.trash_message("msg1")
    assert calls.n == 0


@pytest.mark.asyncio
async def test_full_client_allows_send_and_organize(settings):
    client, calls = client_at(settings, AccessLevel.FULL)
    await client.send_message("a@example.com", "Subject", "Body")
    await client.trash_message("msg1")
    await client.modify_message_labels("msg1", add_label_ids=["STARRED"])
    assert calls.n == 3


@pytest.mark.asyncio
async def test_access_denied_message_names_both_levels(settings):
    client, _ = client_at(settings, AccessLevel.READONLY)
    with pytest.raises(AccessDenied) as excinfo:
        await client.send_message("a@example.com", "s", "b")
    message = str(excinfo.value)
    assert "FULL" in message and "READONLY" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_to", ["not-an-email", "", "  ", "missing-at-sign.com", "a@b"])
async def test_create_draft_rejects_malformed_recipient(settings, bad_to):
    client, calls = client_at(settings, AccessLevel.BASIC)
    with pytest.raises(ValueError, match="to"):
        await client.create_draft(bad_to, "Subject", "Body")
    assert calls.n == 0


@pytest.mark.asyncio
async def test_create_draft_accepts_display_name_form(settings):
    client, calls = client_at(settings, AccessLevel.BASIC)
    await client.create_draft("A Name <a@example.com>", "Subject", "Body")
    assert calls.n == 1


@pytest.mark.asyncio
async def test_create_draft_accepts_multiple_comma_separated_recipients(settings):
    client, calls = client_at(settings, AccessLevel.BASIC)
    await client.create_draft("a@example.com, b@example.com", "Subject", "Body")
    assert calls.n == 1


@pytest.mark.asyncio
async def test_create_draft_rejects_one_bad_address_among_several(settings):
    client, calls = client_at(settings, AccessLevel.BASIC)
    with pytest.raises(ValueError, match="to"):
        await client.create_draft("a@example.com, not-an-email", "Subject", "Body")
    assert calls.n == 0


@pytest.mark.asyncio
async def test_create_draft_copies_in_reply_to_into_references(settings):
    """The tool argument is just in_reply_to; the client fills in both MIME headers."""
    import base64
    from email import message_from_bytes

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "draft1", "message": {"id": "msg1"}})

    client, _ = client_at(settings, AccessLevel.BASIC, handler)
    await client.create_draft(
        "a@example.com", "Re: Hi", "Body", in_reply_to="<abc@mail.gmail.com>"
    )
    raw = seen["body"]["message"]["raw"]
    padded = raw + "=" * (-len(raw) % 4)
    msg = message_from_bytes(base64.urlsafe_b64decode(padded))
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert msg["References"] == "<abc@mail.gmail.com>"


# --------------------------------------------------------------------------- #
# Gate 2: registration (server.py)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "level,expected_registered,expected_withheld",
    [
        (AccessLevel.READONLY, 0, 10),
        (AccessLevel.BASIC, 5, 5),
        (AccessLevel.FULL, 10, 0),
    ],
)
def test_registration_count_matches_access_level(level, expected_registered, expected_withheld):
    counts = {"registered": 0, "withheld": 0}
    for _func, required, _annotations in server._TIERED_TOOLS:
        if level.permits(required):
            counts["registered"] += 1
        else:
            counts["withheld"] += 1
    assert counts["registered"] == expected_registered
    assert counts["withheld"] == expected_withheld


def test_full_tools_are_the_ones_requiring_full():
    full_names = {f.__name__.lstrip("_") for f, level, _ in server._TIERED_TOOLS if level is AccessLevel.FULL}
    assert full_names == {
        "send_draft",
        "send_message",
        "modify_message_labels",
        "trash_message",
        "untrash_message",
    }


def test_basic_tools_are_the_ones_requiring_basic():
    basic_names = {f.__name__.lstrip("_") for f, level, _ in server._TIERED_TOOLS if level is AccessLevel.BASIC}
    assert basic_names == {"list_drafts", "get_draft", "create_draft", "update_draft", "delete_draft"}


def test_send_tools_are_flagged_destructive():
    for func, _level, annotations in server._TIERED_TOOLS:
        if func.__name__.lstrip("_") in {"send_draft", "send_message"}:
            assert annotations.destructive_hint is True
