"""Tests for the Email channel adapter — parser and credential validation, no real network."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.email_channel import EmailChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


SIMPLE_EMAIL = (
    b"From: William <william@example.com>\r\n"
    b"To: jarvis@seal.local\r\n"
    b"Subject: ping\r\n"
    b"Message-ID: <abc-123@example.com>\r\n"
    b"Date: Wed, 30 Apr 2026 20:55:00 -0500\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"hola jarvis, esto es una prueba\r\n"
)

MULTIPART_EMAIL = (
    b"From: \"Henry\" <henry@example.com>\r\n"
    b"To: jarvis@seal.local\r\n"
    b"Subject: multipart\r\n"
    b"Message-ID: <mp-456@example.com>\r\n"
    b"In-Reply-To: <abc-123@example.com>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: multipart/alternative; boundary=BOUNDARY\r\n"
    b"\r\n"
    b"--BOUNDARY\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"texto plano\r\n"
    b"--BOUNDARY\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<p>texto html</p>\r\n"
    b"--BOUNDARY--\r\n"
)


async def test_start_without_credentials_raises():
    for k in ("EMAIL_SMTP_HOST", "EMAIL_IMAP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"):
        os.environ.pop(k, None)
    ch = EmailChannel(noop_handler)
    raised = False
    try:
        await ch.start()
    except GatewayError:
        raised = True
    assert raised
    assert not ch.running


async def test_send_without_smtp_credentials_raises():
    ch = EmailChannel(
        noop_handler,
        smtp_host="",
        imap_host="imap.example.com",
        username="",
        password="",
    )
    raised = False
    try:
        await ch.send(OutboundMessage(channel="email", platform_chat_id="x@y.com", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_simple_email():
    msg = EmailChannel.parse_email_bytes(SIMPLE_EMAIL)
    assert msg.channel == "email"
    assert msg.platform_user_id == "william@example.com"
    assert msg.user_display_name == "William"
    assert msg.platform_chat_id == "jarvis@seal.local"
    assert "hola jarvis" in msg.text
    assert msg.platform_message_id == "<abc-123@example.com>"
    assert msg.metadata["subject"] == "ping"


def test_parse_multipart_prefers_text_plain():
    msg = EmailChannel.parse_email_bytes(MULTIPART_EMAIL)
    assert msg.text == "texto plano"
    assert msg.metadata["in_reply_to"] == "<abc-123@example.com>"
    assert msg.user_display_name == "Henry"
    assert msg.platform_user_id == "henry@example.com"


def test_parse_email_received_at_is_aware():
    """Received_at must always be timezone-aware (UTC fallback if no Date)."""
    no_date = (
        b"From: x@y.com\r\nTo: a@b.com\r\nSubject: nodate\r\n\r\nbody\r\n"
    )
    msg = EmailChannel.parse_email_bytes(no_date)
    assert msg.received_at.tzinfo is not None


async def main() -> int:
    async_tests = [
        test_start_without_credentials_raises,
        test_send_without_smtp_credentials_raises,
    ]
    sync_tests = [
        test_parse_simple_email,
        test_parse_multipart_prefers_text_plain,
        test_parse_email_received_at_is_aware,
    ]
    passed = 0
    for t in async_tests:
        await t()
        print(f"[OK] {t.__name__}")
        passed += 1
    for t in sync_tests:
        t()
        print(f"[OK] {t.__name__}")
        passed += 1
    total = len(async_tests) + len(sync_tests)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
