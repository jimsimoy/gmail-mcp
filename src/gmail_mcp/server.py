"""MCP server exposing Gmail tools over stdio, gated by GMAIL_ACCESS_LEVEL."""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .access import AccessDenied, AccessLevel, DESCRIPTIONS
from .client import GmailClient, GmailError
from .config import ConfigError, Settings, load_settings

_F = TypeVar("_F", bound=Callable[..., Any])


def handled(func: _F) -> _F:
    """Turn anticipated failures into messages the MCP client can actually read.

    Without this the SDK reports any exception as an opaque "Error executing tool",
    which hides the one thing a caller needs — a revoked token, a level that's too
    low, a bad recipient address, or Gmail's own error text.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except (GmailError, AccessDenied) as exc:
            raise ToolError(str(exc)) from exc
        except (ConfigError, ValueError) as exc:
            raise ToolError(_safe_message(exc)) from exc

    return wrapper  # type: ignore[return-value]


def _safe_message(exc: Exception) -> str:
    text = str(exc)
    try:
        return settings().redact(text)
    except ConfigError:
        return text


mcp = MCPServer(
    "gmail",
    version=__version__,
    instructions=(
        "Access to a Gmail inbox: search and read mail, draft replies, and — only "
        "at the FULL access level — send mail and organize the inbox (labels, "
        "archive, trash). Which tools exist here depends on GMAIL_ACCESS_LEVEL: "
        "READONLY registers only search_messages/get_message/get_thread/"
        "list_labels/get_profile; BASIC adds the draft tools "
        "(list_drafts/get_draft/create_draft/update_draft/delete_draft); FULL adds "
        "send_draft/send_message/modify_message_labels/trash_message/"
        "untrash_message. Tools above the configured level are not registered at "
        "all, so if you cannot see send_message, the operator has not granted "
        "that access — say so rather than looking for a workaround. "
        "search_messages accepts Gmail's own search syntax (from:, to:, subject:, "
        "after:, before:, has:attachment, is:unread, label:, and boolean "
        "operators) in the query argument — the same syntax used in the Gmail "
        "search box."
    ),
)

#: Reading is genuinely read-only. Drafting changes local state you fully control
#: and can delete. Sending and inbox-organizing act on the outside world or on
#: existing mail — flagged accordingly.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
DRAFT_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
SEND = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)
ORGANIZE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

_settings: Settings | None = None
_client: GmailClient | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def client() -> GmailClient:
    global _client
    if _client is None:
        _client = GmailClient(settings())
    return _client


# =========================================================================== #
# READONLY — always registered
# =========================================================================== #


@mcp.tool(annotations=READ_ONLY)
@handled
async def get_profile() -> dict[str, Any]:
    """Get the authorized Gmail account's address and message/thread totals."""
    return await client().get_profile()


@mcp.tool(annotations=READ_ONLY)
@handled
async def list_labels() -> dict[str, Any]:
    """List every label on this account (system labels like INBOX/SENT/TRASH plus user labels), with their IDs.

    Label IDs from here are what modify_message_labels and search_messages'
    label_ids argument expect.
    """
    return {"labels": await client().list_labels()}


@mcp.tool(annotations=READ_ONLY)
@handled
async def search_messages(
    query: str = "",
    max_results: int = 25,
    page_token: str | None = None,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Search messages using Gmail's own search syntax.

    `query` accepts exactly what you'd type into the Gmail search box: from:,
    to:, subject:, after:YYYY/MM/DD, before:YYYY/MM/DD, has:attachment,
    is:unread, is:starred, label:name, and boolean operators (OR, -exclude,
    quoted "exact phrases"). Leave `query` empty to list recent mail instead.
    Each result includes the extracted plain-text body, not just a snippet.
    """
    return await client().search_messages(
        query=query, max_results=max_results, page_token=page_token, label_ids=label_ids
    )


@mcp.tool(annotations=READ_ONLY)
@handled
async def get_message(message_id: str) -> dict[str, Any]:
    """Get one message in full: headers, labels, and its plain-text body."""
    return await client().get_message(message_id)


@mcp.tool(annotations=READ_ONLY)
@handled
async def get_thread(thread_id: str) -> dict[str, Any]:
    """Get an entire conversation thread as an ordered list of messages."""
    return await client().get_thread(thread_id)


# =========================================================================== #
# BASIC and FULL — registered conditionally in register_tiered_tools()
# =========================================================================== #


async def _list_drafts(max_results: int = 25, page_token: str | None = None) -> dict[str, Any]:
    """List drafts on this account."""
    return await client().list_drafts(max_results=max_results, page_token=page_token)


async def _get_draft(draft_id: str) -> dict[str, Any]:
    """Get one draft in full."""
    return await client().get_draft(draft_id)


async def _create_draft(
    to: str,
    subject: str,
    body_text: str,
    cc: str | None = None,
    bcc: str | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    """Create a new draft. Never sends anything — the draft sits in Gmail's Drafts folder until sent, from the Gmail UI or with send_draft (FULL only).

    Pass `thread_id` and `in_reply_to` (the original message's Message-ID header
    value, from get_message's `message_id_header` field) to draft it as a reply
    within an existing conversation rather than a new one.
    """
    return await client().create_draft(
        to, subject, body_text, cc=cc, bcc=bcc, thread_id=thread_id, in_reply_to=in_reply_to
    )


async def _update_draft(
    draft_id: str,
    to: str,
    subject: str,
    body_text: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict[str, Any]:
    """Replace an existing draft's content."""
    return await client().update_draft(draft_id, to, subject, body_text, cc=cc, bcc=bcc)


async def _delete_draft(draft_id: str) -> dict[str, Any]:
    """Delete a draft. Only ever removes an unsent draft — cannot affect sent mail."""
    return await client().delete_draft(draft_id)


async def _send_draft(draft_id: str) -> dict[str, Any]:
    """Send an existing draft as-is. Irreversible — the recipient receives it immediately."""
    return await client().send_draft(draft_id)


async def _send_message(
    to: str,
    subject: str,
    body_text: str,
    cc: str | None = None,
    bcc: str | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    """Compose and send a message immediately, with no draft step and no further confirmation. Irreversible.

    Prefer create_draft + send_draft when the content should be reviewed first —
    this tool skips that entirely.
    """
    return await client().send_message(
        to, subject, body_text, cc=cc, bcc=bcc, thread_id=thread_id, in_reply_to=in_reply_to
    )


async def _modify_message_labels(
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Add and/or remove labels on a message. Use list_labels first to get valid IDs.

    Common uses: archive (remove_label_ids=["INBOX"]), mark read
    (remove_label_ids=["UNREAD"]) or unread (add_label_ids=["UNREAD"]), star
    (add_label_ids=["STARRED"]).
    """
    return await client().modify_message_labels(
        message_id, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids
    )


async def _trash_message(message_id: str) -> dict[str, Any]:
    """Move a message to Trash. Reversible with untrash_message until Gmail's Trash auto-empties it (30 days)."""
    return await client().trash_message(message_id)


async def _untrash_message(message_id: str) -> dict[str, Any]:
    """Restore a message out of Trash back to its prior labels."""
    return await client().untrash_message(message_id)


_TIERED_TOOLS: tuple[tuple[Callable[..., Any], AccessLevel, ToolAnnotations], ...] = (
    (_list_drafts, AccessLevel.BASIC, READ_ONLY),
    (_get_draft, AccessLevel.BASIC, READ_ONLY),
    (_create_draft, AccessLevel.BASIC, DRAFT_WRITE),
    (_update_draft, AccessLevel.BASIC, DRAFT_WRITE),
    (_delete_draft, AccessLevel.BASIC, DRAFT_WRITE),
    (_send_draft, AccessLevel.FULL, SEND),
    (_send_message, AccessLevel.FULL, SEND),
    (_modify_message_labels, AccessLevel.FULL, ORGANIZE),
    (_trash_message, AccessLevel.FULL, ORGANIZE),
    (_untrash_message, AccessLevel.FULL, ORGANIZE),
)


def register_tiered_tools(current: Settings) -> dict[str, int]:
    """Register BASIC/FULL tools whose required level `current` actually meets.

    This is the "registration" half of the two-gate enforcement in access.py —
    a tool above the configured level is never handed to the model at all, not
    merely refused if called. The public name (without the leading underscore)
    is what the model sees.
    """
    registered = 0
    withheld = 0
    for func, required, annotations in _TIERED_TOOLS:
        public_name = func.__name__.lstrip("_")
        if current.access_level.permits(required):
            mcp.tool(name=public_name, annotations=annotations)(handled(func))
            registered += 1
        else:
            withheld += 1
    return {"registered": registered, "withheld": withheld}


_CURATED_READONLY_COUNT = 5  # get_profile, list_labels, search_messages, get_message, get_thread


def main() -> None:
    """Entry point for the ``gmail-mcp`` console script."""
    try:
        current = load_settings()
    except ConfigError as exc:
        print(f"gmail-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    summary = register_tiered_tools(current)
    total = _CURATED_READONLY_COUNT + summary["registered"]
    print(
        f"gmail-mcp {__version__}: access level {current.access_level.value} "
        f"({DESCRIPTIONS[current.access_level]}); {total} tools "
        f"({summary['withheld']} withheld above access level).",
        file=sys.stderr,
    )
    if current.access_level is AccessLevel.FULL:
        print(
            "gmail-mcp: WARNING — FULL access is enabled. This server can send mail "
            "as you and modify/trash existing messages.",
            file=sys.stderr,
        )
    mcp.run()


if __name__ == "__main__":
    main()
