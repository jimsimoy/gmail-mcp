# Gmail MCP — Gmail Access for AI Clients

<div align="center">

<img src="https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square" alt="Python 3.10+">
<a href="https://github.com/jimsimoy/gmail-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
<a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square" alt="MCP Compatible"></a>
<img src="https://img.shields.io/badge/tools-15-brightgreen.svg?style=flat-square" alt="15 Tools">
<img src="https://img.shields.io/badge/default-read--only-success.svg?style=flat-square" alt="Read-only by default">
<img src="https://img.shields.io/badge/dependencies-3-lightgrey.svg?style=flat-square" alt="3 runtime dependencies">

**15 tools for the Gmail API — search, read, draft, send, and organize — gated behind three access levels, read-only by default.**

For Claude Desktop, Claude Code, and any MCP client.

by [Jan Ivan Simoy](https://github.com/jimsimoy)

</div>

---

> **Unofficial.** This is an independent, community-built project — not affiliated with, endorsed by, or sponsored by Google.

## What is this?

Gmail MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that gives AI
assistants structured access to a Gmail inbox — searching and reading mail, drafting replies, and,
only at the highest access level, sending mail and organizing the inbox (labels, archive, trash).

Gmail has no service-account model for a personal `@gmail.com` inbox — there is no "share this
inbox with a robot" the way there is for a Google Doc or Sheet. Every call here is authorized on
behalf of a real Google account through the standard OAuth 2.0 "installed app" flow: you sign in
once in a browser, this server gets a long-lived refresh token, and every call after that happens
with no browser involved.

**Supported platform:** any MCP client on macOS, Linux, or Windows with Python 3.10+.

---

## Access Levels

`GMAIL_ACCESS_LEVEL` in `.env` controls what this server will actually do — independent of what
the OAuth consent screen technically allows (see [Authentication](#authentication): that consent
always grants the `FULL` ceiling, specifically so raising this later never sends you back to the
browser).

| Level | Can it change your inbox? | Tools |
|---|---|---|
| **`READONLY`** *(default)* | **No.** List, search, and read messages and threads. | 5 |
| **`BASIC`** | **Drafts only.** Create, edit, and delete drafts. Nothing is ever sent. | 10 |
| **`FULL`** | **Yes.** Everything in `BASIC`, plus sending mail as you and labeling/archiving/trashing existing messages. | 15 |

### How it is enforced

Two independent gates, so a bug in one does not defeat the other:

1. **Registration.** `server.register_tiered_tools()` only hands the model tools whose required
   level the configured `GMAIL_ACCESS_LEVEL` meets. At `READONLY` the model is never told that
   `send_message` exists.
2. **Dispatch.** `GmailClient` checks the level before building any request. A call to a
   level-gated method at an insufficient level raises `AccessDenied` and **no HTTP request is
   made** — there are tests for exactly that.

### What `FULL` deliberately does *not* include

Gmail's broadest scope, `https://mail.google.com/`, additionally grants **permanent deletion**
(bypassing Trash) and the ability to **change account settings** — forwarding rules, filters,
IMAP/POP toggles. This server never requests that scope at any level. Those are account
configuration changes, not "read or send mail" operations, and unlike sending or trashing a
message, they are not something a person watching the inbox can easily notice and undo. There is
no tool here that needs it, at any access level.

### ⚠️ Disclaimer — read before raising the level

> **Setting `GMAIL_ACCESS_LEVEL` to `BASIC` or `FULL` allows an AI model to act on your real Gmail
> account.**
>
> At **`FULL`**, that includes **sending email from your address with no human click in between**.
> A sent message cannot be recalled — the recipient has it the instant the tool call succeeds.
> `FULL` can also trash and relabel existing mail (recoverable via Trash for ~30 days, but not
> instant, and not something you'll necessarily notice happened).
>
> AI models make mistakes. They misread instructions, act on ambiguous requests, and can be
> influenced by content they read — including the content of an email they were asked to read
> before replying to it. A model with `FULL` access to your Gmail account can send messages as you
> to anyone, about anything.
>
> **If you raise this setting above `READONLY`, you do so entirely at your own risk and you are
> solely responsible for anything sent, deleted, or reorganized as a result.** The author and
> contributors accept no liability. This software is provided "as is", without warranty of any
> kind, as set out in the [MIT License](./LICENSE).
>
> **Recommended:** leave it at `READONLY` for searching and reading. Raise to `BASIC` if you want
> drafts prepared for your own review and send. Raise to `FULL` only for a session that specifically
> needs to send or reorganize mail on your behalf, and only if you are comfortable with that.

---

## Tools

| Level | Tools | What you can do |
|---|---|---|
| **`READONLY`** | 5 | Get the account profile, list labels, search messages (Gmail's own search syntax), read a message, read a whole thread |
| **`BASIC`** | 5 | List/get/create/update/delete drafts |
| **`FULL`** | 5 | Send a draft, compose-and-send directly, add/remove labels, trash, untrash |

<details>
<summary>Full tool reference</summary>

| Tool | Level | Description |
|---|---|---|
| `get_profile` | READONLY | The authorized account's address and message/thread totals |
| `list_labels` | READONLY | Every label (system + user), with the IDs other tools need |
| `search_messages` | READONLY | Gmail search syntax (`from:`, `subject:`, `after:`, `is:unread`, `label:`, boolean operators) — returns extracted plain-text bodies, not just snippets |
| `get_message` | READONLY | One message in full: headers, labels, plain-text body |
| `get_thread` | READONLY | An entire conversation as an ordered list of messages |
| `list_drafts` | BASIC | List drafts on the account |
| `get_draft` | BASIC | One draft in full |
| `create_draft` | BASIC | Create a draft — optionally as a reply within an existing thread |
| `update_draft` | BASIC | Replace a draft's content |
| `delete_draft` | BASIC | Delete a draft (never touches sent mail) |
| `send_draft` | FULL | Send an existing draft as-is — irreversible |
| `send_message` | FULL | Compose and send immediately, no draft step — irreversible |
| `modify_message_labels` | FULL | Add/remove labels — archive, mark read/unread, star |
| `trash_message` | FULL | Move to Trash — reversible for ~30 days |
| `untrash_message` | FULL | Restore out of Trash |

</details>

Every message- and thread-reading tool returns the extracted plain-text body inline, not just a
snippet — the same approach used in [wordpress-mcp](https://github.com/jimsimoy/wordpress-mcp) and
[google-cloud-services-mcp](https://github.com/jimsimoy/google-cloud-services-mcp) for reading a
Doc: try the clean structured form first, fall back to a best-effort plain-text conversion for
HTML-only mail.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10 or later |
| Google Cloud project | with the Gmail API enabled |
| OAuth 2.0 Client ID | type "Desktop app" |

---

## Authentication

Gmail has no service-account option for a personal inbox (that only exists for a paid Google
Workspace domain with admin-configured domain-wide delegation). Setup here is a one-time OAuth
consent instead:

1. In [Google Cloud Console](https://console.cloud.google.com), create or select a project and
   enable the **Gmail API** (APIs & Services → Library → search "Gmail API" → Enable).
2. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose
   **External**, keep it in **Testing** mode, and add the Google account you'll use as a test user.
   Testing mode is enough for your own account — you do not need Google's app-verification review
   for a single-user tool like this one.
3. Create an **OAuth 2.0 Client ID** of type **Desktop app** (APIs & Services → Credentials →
   Create Credentials → OAuth client ID → Desktop app). Note its **Client ID** and **Client
   Secret**.
4. Run the one-time consent flow:

   ```bash
   ./.venv/bin/gmail-mcp-authorize
   ```

   It asks for the Client ID and Secret from step 3 (or reads `GMAIL_CLIENT_ID`/
   `GMAIL_CLIENT_SECRET` from your environment if already set), opens your browser to Google's
   sign-in and consent screen, and prints `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and
   `GMAIL_REFRESH_TOKEN` — put all three in `.env`.

   This always requests the **`FULL`** scope ceiling (read + drafts + send/organize), regardless
   of what `GMAIL_ACCESS_LEVEL` you'll actually run at. That's deliberate: `GMAIL_ACCESS_LEVEL` is
   a second, independent gate on top (see [Access Levels](#access-levels)) — raising it later is
   an `.env` edit, not a trip back to this step.

5. If you ever need to revoke access entirely, do it at
   [myaccount.google.com/permissions](https://myaccount.google.com/permissions) — that invalidates
   the refresh token immediately, and `gmail-mcp-authorize` gets you a new one.

---

## Installation

```bash
git clone git@github.com:jimsimoy/gmail-mcp.git
cd gmail-mcp
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# then follow Authentication above to fill in .env
```

### Verify before wiring up a client

```bash
./.venv/bin/python -m pytest -q      # no credentials or network needed
./.venv/bin/gmail-mcp                # starts the stdio server; prints the tool count to stderr
```

---

## Client Setup

```json
{
  "mcpServers": {
    "gmail": {
      "command": "/path/to/gmail-mcp/.venv/bin/python",
      "args": ["-m", "gmail_mcp"],
      "env": {
        "GMAIL_CLIENT_ID": "your-client-id.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "your-client-secret",
        "GMAIL_REFRESH_TOKEN": "your-refresh-token",
        "GMAIL_ACCESS_LEVEL": "READONLY"
      }
    }
  }
}
```

With `uv`:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uv",
      "args": ["--directory", "/path/to/gmail-mcp", "run", "gmail-mcp"],
      "env": {
        "GMAIL_CLIENT_ID": "your-client-id.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "your-client-secret",
        "GMAIL_REFRESH_TOKEN": "your-refresh-token",
        "GMAIL_ACCESS_LEVEL": "READONLY"
      }
    }
  }
}
```

Restart your MCP client after saving.

---

## Usage Examples

**Find something from years ago, without knowing the exact wording:**
> "Search my Gmail for anything from IdeaSpace Foundation around July 2016."

`search_messages(query="from:ideaspacefoundation.org after:2016/06/01 before:2016/08/01")`

**Draft a reply for you to review and send yourself** (safe at `BASIC`):
> "Draft a reply to the latest message in this thread saying I can make the Tuesday slot."

`create_draft(to=..., subject="Re: ...", body_text=..., thread_id=..., in_reply_to=...)` — the draft
sits in Gmail's Drafts folder until *you* send it.

**Let it send directly** (needs `FULL`, and means what it says):
> "Send that reply now."

`send_draft(draft_id=...)` — no further confirmation step inside this server. If you want a human
checkpoint before anything goes out, stay at `BASIC` and send from the Gmail app yourself.

---

## Security

This project is meant to be read before it is run. The design notes that matter:

- **Read-only by default, enforced twice.** At `READONLY` — the default — nothing that changes
  your inbox is even registered as a tool, *and* `GmailClient` refuses the underlying operation
  independently if it's somehow reached anyway. Tests cover both gates, including that a denied
  call never reaches the network.
- **The OAuth scope is a second, outer fence.** Even if `GMAIL_ACCESS_LEVEL` were somehow bypassed,
  the access token itself is scoped by what you granted during consent — and this server never
  requests `https://mail.google.com/`, the one scope that permits permanent deletion and account
  settings changes, at any level.
- **Refresh tokens and client secrets are environment-only.** `.env`, `.env.*` (except
  `.env.example`), and `token.json` are gitignored. Every credential is stripped from any error
  message this server produces, including Gmail's own error bodies.
- **A pre-push scan runs before every commit and push** (`git-guard.sh`, invoked by
  `git-commit.sh`/`git-push-current.sh`) — it specifically pattern-matches Google OAuth client IDs,
  client secrets, and refresh/access token formats, on top of the generic credential patterns used
  across this author's other MCP servers. This repo is private, but the scan runs regardless —
  see the comment at the top of `git-guard.sh` for why "it's private" is not treated as a
  sufficient control on its own.
- **Sending is never retried.** A `send_message`/`send_draft` call that times out or fails
  ambiguously is not silently replayed — a duplicate send is worse than a visible error.
- **Three runtime dependencies, no vendor SDK.** `mcp`, `httpx`, `python-dotenv`. Gmail's REST API
  is called directly; there is no `google-api-python-client` dependency to audit.
- **No telemetry.** This server makes no network call other than the Gmail API request a tool asks
  for, and the OAuth token refresh that requires.

Your refresh token carries whatever scope you granted at consent (the `FULL` ceiling, by design —
see [Authentication](#authentication)). Revoke it at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions) if it is ever exposed,
then run `gmail-mcp-authorize` again for a new one.

---

## Project Structure

```
gmail-mcp/
├── src/gmail_mcp/
│   ├── access.py        # AccessLevel enum, per-level Gmail scopes, the require() gate
│   ├── auth.py           # Refresh token -> short-lived access token
│   ├── authorize.py       # One-time interactive browser consent flow (gmail-mcp-authorize)
│   ├── client.py           # Gmail REST wrapper: MIME building, message parsing, dispatch gate
│   ├── config.py            # .env loading into a Settings dataclass
│   └── server.py              # MCP tool definitions and tiered registration
├── tests/
│   ├── test_access.py                # Both enforcement gates
│   ├── test_message_parsing.py       # MIME/text-extraction pure-function tests
│   └── test_rate_limiting.py         # Retry-on-rate-limit and per-item fetch pacing
├── .env.example
└── pyproject.toml
```

---

## Development

```bash
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest -q
```

51 tests, run entirely offline against `httpx.MockTransport` and a fake token provider — no real
Gmail account or network access is needed to verify the access-level gates, the MIME/parsing logic,
or the rate-limit backoff and pacing (see [AGENTS.md](AGENTS.md#5-rate-limits-and-pacing) — this was
hit for real once, not added speculatively).

---

## License

MIT — see [LICENSE](./LICENSE).
