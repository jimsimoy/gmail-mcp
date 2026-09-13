"""Access levels.

One environment variable, ``GMAIL_ACCESS_LEVEL``, decides how much of the Gmail
API this server will expose. It is enforced in two independent places:

1. **Registration** — tools above the configured level are never registered, so
   the model is not even shown that they exist.
2. **Dispatch** — :class:`~gmail_mcp.client.GmailClient` refuses any operation
   the level does not permit, so a tool that somehow reached the client anyway
   still cannot act.

The default is READONLY. Raising it is a deliberate act by whoever owns the
inbox, and FULL in particular grants the ability to send mail as you.

This is a second, *tighter* fence inside a wider one: the OAuth scope granted
once in the browser (see authorize.py) is the outer, Google-enforced ceiling —
this server always requests the union of scopes for all three levels up front,
specifically so that raising GMAIL_ACCESS_LEVEL later never requires re-running
consent. GMAIL_ACCESS_LEVEL is the inner fence, and it can only narrow that
ceiling, never exceed it.
"""

from __future__ import annotations

from enum import Enum


class AccessDenied(PermissionError):
    """Raised when an operation exceeds the configured access level."""


class AccessLevel(str, Enum):
    """How much of the Gmail API is exposed."""

    READONLY = "READONLY"
    BASIC = "BASIC"
    FULL = "FULL"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def permits(self, required: "AccessLevel | str") -> bool:
        """Whether this level is sufficient for an operation requiring ``required``."""
        return self.rank >= AccessLevel(required).rank

    @classmethod
    def parse(cls, value: str | None, default: "AccessLevel | None" = None) -> "AccessLevel":
        text = (value or "").strip().upper()
        if not text:
            return default or cls.READONLY
        try:
            return cls(text)
        except ValueError as exc:
            raise ValueError(
                f"GMAIL_ACCESS_LEVEL={value!r} is not valid. Use one of: "
                f"{', '.join(level.value for level in cls)}."
            ) from exc


_RANK: dict[AccessLevel, int] = {
    AccessLevel.READONLY: 0,
    AccessLevel.BASIC: 1,
    AccessLevel.FULL: 2,
}

#: What each level means, in one line — used in errors and in the server's instructions.
DESCRIPTIONS: dict[AccessLevel, str] = {
    AccessLevel.READONLY: "read only — list, search, and read messages/threads; cannot change anything",
    AccessLevel.BASIC: "read + drafts — everything in READONLY, plus create/edit/delete drafts; cannot send or touch existing mail",
    AccessLevel.FULL: "read + send + organize — everything in BASIC, plus sending mail as you, and labeling/archiving/trashing existing messages",
}

#: Gmail OAuth scopes needed to reach each level. Cumulative — FULL's list is the
#: full consent request; authorize.py always requests the FULL set as the ceiling,
#: regardless of which GMAIL_ACCESS_LEVEL the server runs at day to day.
SCOPES_FOR_LEVEL: dict[AccessLevel, tuple[str, ...]] = {
    AccessLevel.READONLY: (
        "https://www.googleapis.com/auth/gmail.readonly",
    ),
    AccessLevel.BASIC: (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ),
    AccessLevel.FULL: (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    ),
}

#: The scope set requested at one-time consent — always the FULL ceiling. See the
#: module docstring: this is what lets GMAIL_ACCESS_LEVEL move later without a
#: new browser round-trip.
CONSENT_SCOPES: tuple[str, ...] = SCOPES_FOR_LEVEL[AccessLevel.FULL]

#: Deliberately never requested at any level: https://mail.google.com/, the
#: broadest Gmail scope. It adds permanent (non-Trash) deletion and the ability
#: to change account settings — forwarding, filters, IMAP/POP toggles. Those are
#: account-configuration changes, not "read or send mail" operations, and they
#: are not reversible the way sending or trashing a message is. Out of scope for
#: this server at any access level; there is no tool that needs it.


def require(current: AccessLevel, needed: AccessLevel | str, operation: str) -> None:
    """Raise :class:`AccessDenied` unless ``current`` is sufficient for ``needed``."""
    needed_level = AccessLevel(needed)
    if not current.permits(needed_level):
        raise AccessDenied(
            f"'{operation}' requires GMAIL_ACCESS_LEVEL={needed_level.value} but this server "
            f"is running as {current.value} ({DESCRIPTIONS[current]}). Raising the level lets "
            "this server act further on your Gmail account; see the README's Access Levels "
            "section before you do."
        )
