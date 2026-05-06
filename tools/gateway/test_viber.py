"""Tests for the Viber adapter — parser + signature + credential checks, no live API."""

import asyncio
import hashlib
import hmac
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.viber import ViberChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_token_raises():
    saved = os.environ.pop("VIBER_AUTH_TOKEN", None)
    try:
        ch = ViberChannel(noop_handler, auth_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["VIBER_AUTH_TOKEN"] = saved


async def test_send_without_token_raises():
    ch = ViberChannel(noop_handler, auth_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="viber", platform_chat_id="user-1", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_webhook_event_text_message():
    event = {
        "event": "message",
        "timestamp": 1730000000000,
        "message_token": "tok-42",
        "sender": {
            "id": "user-1",
            "name": "William",
            "country": "PE",
            "language": "es",
        },
        "message": {"type": "text", "text": "hola jarvis", "tracking_data": "track-1"},
    }
    msg = ViberChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.channel == "viber"
    assert msg.platform_user_id == "user-1"
    assert msg.platform_chat_id == "user-1"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "tok-42"
    assert msg.user_display_name == "William"
    assert msg.metadata.get("country") == "PE"
    assert msg.metadata.get("tracking_data") == "track-1"
    assert msg.received_at.tzinfo is not None


def test_parse_webhook_event_filters_other_event_types():
    assert ViberChannel.parse_webhook_event({"event": "subscribed"}) is None
    assert ViberChannel.parse_webhook_event({"event": "delivered"}) is None
    assert ViberChannel.parse_webhook_event({"event": "seen"}) is None
    assert ViberChannel.parse_webhook_event({}) is None


def test_parse_webhook_event_filters_non_text_messages():
    event = {
        "event": "message",
        "sender": {"id": "u"},
        "message": {"type": "picture", "media": "https://x"},
    }
    assert ViberChannel.parse_webhook_event(event) is None


def test_parse_webhook_event_returns_none_on_empty_text():
    event = {
        "event": "message",
        "sender": {"id": "u"},
        "message": {"type": "text", "text": ""},
    }
    assert ViberChannel.parse_webhook_event(event) is None


def test_parse_webhook_event_handles_missing_timestamp():
    event = {
        "event": "message",
        "sender": {"id": "u"},
        "message": {"type": "text", "text": "no ts"},
    }
    msg = ViberChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


def test_verify_signature_accepts_correct():
    secret = "topsecret"
    body = b'{"event":"message"}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ch = ViberChannel(noop_handler, auth_token=secret)
    assert ch.verify_signature(body, expected) is True


def test_verify_signature_rejects_wrong():
    ch = ViberChannel(noop_handler, auth_token="s")
    assert ch.verify_signature(b"x", "deadbeef") is False


def test_verify_signature_returns_false_without_token():
    ch = ViberChannel(noop_handler, auth_token="")
    assert ch.verify_signature(b"x", "anything") is False


async def test_feed_webhook_event_dispatches():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = ViberChannel(capture, auth_token="t")
    await ch.feed_webhook_event(
        {
            "event": "message",
            "timestamp": 1730000000000,
            "message_token": "tok",
            "sender": {"id": "u-feed", "name": "x"},
            "message": {"type": "text", "text": "via feed"},
        }
    )
    assert len(received) == 1
    assert received[0].text == "via feed"


async def main() -> int:
    async_tests = [
        test_start_without_token_raises,
        test_send_without_token_raises,
        test_feed_webhook_event_dispatches,
    ]
    sync_tests = [
        test_parse_webhook_event_text_message,
        test_parse_webhook_event_filters_other_event_types,
        test_parse_webhook_event_filters_non_text_messages,
        test_parse_webhook_event_returns_none_on_empty_text,
        test_parse_webhook_event_handles_missing_timestamp,
        test_verify_signature_accepts_correct,
        test_verify_signature_rejects_wrong,
        test_verify_signature_returns_false_without_token,
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
