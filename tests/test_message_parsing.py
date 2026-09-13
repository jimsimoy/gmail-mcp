"""Pure-function tests for MIME building and message parsing — no network."""

from __future__ import annotations

from email import message_from_bytes

from gmail_mcp.client import (
    _b64url_decode,
    _b64url_encode,
    _build_mime,
    _extract_text,
    _strip_html,
    summarize_message,
)


def _parse_mime(raw: str):
    return message_from_bytes(_b64url_decode(raw))


def test_b64url_round_trip():
    raw = b"hello, gmail \xf0\x9f\x93\xa7"
    assert _b64url_decode(_b64url_encode(raw)) == raw


def test_build_mime_contains_headers_and_body():
    raw = _build_mime("a@example.com", "Hi there", "Body text", cc="b@example.com")
    msg = _parse_mime(raw)
    assert msg["To"] == "a@example.com"
    assert msg["Subject"] == "Hi there"
    assert msg["Cc"] == "b@example.com"
    assert msg.get_payload(decode=True).decode("utf-8") == "Body text"


def test_build_mime_sets_reply_headers_when_given():
    # _build_mime takes In-Reply-To and References independently — it does not
    # infer one from the other. See test_access.py for GmailClient.create_draft,
    # which is where in_reply_to is copied into both headers for callers.
    raw = _build_mime(
        "a@example.com", "Re: Hi", "Body",
        in_reply_to="<abc@mail.gmail.com>", references="<abc@mail.gmail.com>",
    )
    msg = _parse_mime(raw)
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert msg["References"] == "<abc@mail.gmail.com>"


def test_build_mime_omits_reply_headers_when_not_given():
    raw = _build_mime("a@example.com", "Hi", "Body")
    msg = _parse_mime(raw)
    assert msg["In-Reply-To"] is None
    assert msg["References"] is None


def test_extract_text_from_simple_plain_payload():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64url_encode(b"plain body")},
    }
    text, is_html = _extract_text(payload)
    assert text == "plain body"
    assert is_html is False


def test_extract_text_prefers_plain_over_html_in_multipart():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64url_encode(b"<p>html</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64url_encode(b"plain wins")}},
        ],
    }
    text, is_html = _extract_text(payload)
    assert text == "plain wins"
    assert is_html is False


def test_extract_text_falls_back_to_html_when_no_plain_part():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64url_encode(b"<p>only html</p>")}},
        ],
    }
    text, is_html = _extract_text(payload)
    assert is_html is True
    assert "only html" in text


def test_extract_text_walks_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url_encode(b"nested plain")}},
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "xyz"}},
        ],
    }
    text, is_html = _extract_text(payload)
    assert text == "nested plain"


def test_strip_html_converts_breaks_and_paragraphs():
    html = "<p>Line one</p><p>Line two<br>continued</p>"
    text = _strip_html(html)
    assert "Line one" in text
    assert "Line two" in text
    assert "continued" in text
    assert "<" not in text


def test_strip_html_drops_script_and_style():
    html = "<style>.x{color:red}</style><p>visible</p><script>alert(1)</script>"
    text = _strip_html(html)
    assert "visible" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_summarize_message_extracts_headers_and_body():
    msg = {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "a snippet",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "Subject", "value": "Test subject"},
                {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
            ],
            "body": {"data": _b64url_encode(b"the body")},
        },
    }
    result = summarize_message(msg)
    assert result["id"] == "msg1"
    assert result["thread_id"] == "thread1"
    assert result["from"] == "sender@example.com"
    assert result["subject"] == "Test subject"
    assert result["message_id_header"] == "<abc@mail.gmail.com>"
    assert result["body_text"] == "the body"
    assert result["label_ids"] == ["INBOX", "UNREAD"]
