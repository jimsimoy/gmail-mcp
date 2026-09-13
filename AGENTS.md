# AGENTS.md — Operating Guide for AI Agents

Instructions for an AI agent working **on** this repository or working **through**
the MCP server it provides. Humans, see [README.md](./README.md).

---

## 1. Official API references

This server is a thin, hand-written wrapper over Gmail's published REST API — there is no
codegen step (unlike this author's `mailchimp-mcp`, which generates one tool per Mailchimp
operation from an OpenAPI spec). When a tool's behaviour is unclear, or when extending the
server, go to the source rather than guessing.

| Resource | URL | Use it for |
|---|---|---|
| Gmail API reference | https://developers.google.com/gmail/api/reference/rest | Exact request/response shapes for every endpoint used in `client.py` |
| Gmail search syntax | https://support.google.com/mail/answer/7190 | What `search_messages`'s `query` argument accepts |
| OAuth 2.0 for installed apps | https://developers.google.com/identity/protocols/oauth2/native-app | The flow `authorize.py` implements |
| OAuth 2.0 scopes for Gmail | https://developers.google.com/gmail/api/auth/scopes | What each scope in `access.SCOPES_FOR_LEVEL` actually permits |

---

## 2. Initializing the server

### Install

```bash
git clone git@github.com:jimsimoy/gmail-mcp.git
cd gmail-mcp
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

### Configure

The full walkthrough (Google Cloud project, OAuth consent screen, Desktop app client,
`gmail-mcp-authorize`) is in the README's [Authentication](README.md#authentication) section — do
not skip straight to editing `.env` without reading it once, since the three `GMAIL_*` values
cannot be produced any other way.

```bash
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
GMAIL_ACCESS_LEVEL=READONLY   # READONLY | BASIC | FULL — see README §Access Levels
```

### Verify before wiring up a client

```bash
./.venv/bin/python -m pytest -q      # 100% offline — no real Gmail account touched
./.venv/bin/gmail-mcp                # starts the stdio server; prints the tool count to stderr
```

---

## 3. Architecture, in one pass

- **`access.py`** — the `AccessLevel` enum (`READONLY`/`BASIC`/`FULL`), what Gmail OAuth scopes
  each level maps to, and `require()`, the function every write path in `client.py` calls before
  making a request.
- **`auth.py`** — turns a long-lived refresh token into a short-lived access token, cached and
  renewed automatically. Never does anything interactive.
- **`authorize.py`** — the *only* place a browser opens. Run once per Google account, produces the
  refresh token `auth.py` then lives on. Always requests the `FULL` scope ceiling — see its
  module docstring for why.
- **`client.py`** — `GmailClient`, one async method per Gmail operation, plus the pure functions
  that build outgoing MIME (`_build_mime`) and parse incoming messages (`_extract_text`,
  `summarize_message`). Every method whose operation requires more than `READONLY` calls
  `access.require()` as its first line — that ordering matters, since it's what guarantees a
  denied call never reaches `self._request()`.
- **`server.py`** — the actual MCP tool definitions. The five `READONLY` tools are registered
  unconditionally at import time with `@mcp.tool()`. The ten `BASIC`/`FULL` tools are defined as
  plain (undecorated) functions and registered conditionally by `register_tiered_tools()`, called
  from `main()` after `.env` has loaded — this is what makes a withheld tool invisible to the
  model, not merely refused if called.

**When adding a new tool:** decide its required level first. If it's `READONLY`, add it directly
with `@mcp.tool(annotations=READ_ONLY)` next to the others. If it's `BASIC`/`FULL`, add the
implementation to `client.py` with a `require()` call as its first line, add a thin wrapper
function in `server.py`, and add it to the `_TIERED_TOOLS` tuple with its required level and
`ToolAnnotations`. Add a corresponding case to `tests/test_access.py`'s registration-count test
and dispatch-gate tests — a new gated tool with no test for its gate is exactly the kind of change
that silently defeats the two-gate design.

---

## 4. Working through the MCP server itself (as a calling agent)

- **Check what's actually registered before assuming a capability.** `GMAIL_ACCESS_LEVEL` is set
  by whoever configured this server's client entry, not by you. If `send_message` isn't in your
  tool list, that's the operator's choice — say so, don't look for another way to send mail.
- **`search_messages` takes Gmail's own search syntax**, not a natural-language query — translate
  "emails from Brenda at IdeaSpace around July 2016" into
  `from:ideaspacefoundation.org after:2016/06/01 before:2016/08/01` yourself before calling it.
- **Prefer `create_draft` over `send_message`/`send_draft` when in doubt.** A draft costs nothing
  and can be reviewed; a send is irreversible the moment the tool call returns success. If you are
  not certain the human wants a message to go out immediately, draft it and say so, rather than
  sending and explaining afterward.
- **`trash_message` is soft-delete** (recoverable via `untrash_message` for ~30 days); there is no
  tool for permanent deletion at any access level — see access.py's module note on why
  `https://mail.google.com/` is never requested.
