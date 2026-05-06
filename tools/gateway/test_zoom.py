"""Tests for the Zoom Team Chat adapter — parser + signature + credential checks."""

import asyncio
import hashlib
import hmac
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.zoom import ZoomChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_token_raises():
    saved = os.environ.pop("ZOOM_ACCESS_TOKEN", None)
    try:
        ch = ZoomChannel(noop_handler, access_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["ZOOM_ACCESS_TOKEN"] = saved


async def test_send_without_token_raises():
    ch = ZoomChannel(noop_handler, access_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="zoom", platform_chat_id="user@x.com", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_webhook_event_basic():
    event = {
        "event": "chat_message.sent",
        "event_ts": 1730000000000,
        "payload": {
            "object": {
                "message_id": "m-123",
                "message": "hola jarvis",
                "sender": "user-abc",
                "sender_display_name": "William",
                "channel_id": "ch-7",
            }
        },
    }
    msg = ZoomChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.channel == "zoom"
    assert msg.platform_user_id == "user-abc"
    assert msg.platform_chat_id == "ch-7"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "m-123"
    assert msg.user_display_name == "William"
    assert msg.received_at.tzinfo is not None


def test_parse_webhook_event_one_to_one_uses_to_jid():
    event = {
        "event": "chat_message.sent",
        "event_ts": "2026-04-30T20:00:00Z",
        "payload": {
            "object": {
                "message_id": "m-200",
                "message": "ping",
                "sender": "u-1",
                "to_jid": "user@xmpp.zoom.us",
            }
        },
    }
    msg = ZoomChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.platform_chat_id == "user@xmpp.zoom.us"


def test_parse_webhook_event_filters_other_event_types():
    assert ZoomChannel.parse_webhook_event({"event": "user.created"}) is None
    assert ZoomChannel.parse_webhook_event({}) is None


def test_parse_webhook_event_returns_none_on_empty_message():
    event = {
        "event": "chat_message.sent",
        "payload": {"object": {"message": "", "sender": "u"}},
    }
    assert ZoomChannel.parse_webhook_event(event) is None


def test_parse_webhook_event_handles_invalid_timestamp():
    event = {
        "event": "chat_message.sent",
        "event_ts": "not-a-date",
        "payload": {"object": {"message": "x", "sender": "u", "channel_id": "c"}},
    }
    msg = ZoomChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


def test_verify_signature_accepts_correct():
    secret = "topsecret"
    body = b'{"event":"x"}'
    ts = "1730000000"
    expected = (
        "v0="
        + hmac.new(
            secret.encode(),
            f"v0:{ts}:{body.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    ch = ZoomChannel(noop_handler, access_token="t", webhook_secret=secret)
    assert ch.verify_signature(body, ts, expected) is True


def test_verify_signature_rejects_wrong():
    ch = ZoomChannel(noop_handler, access_token="t", webhook_secret="s")
    assert ch.verify_signature(b"x", "1", "v0=deadbeef") is False


def test_verify_signature_returns_false_without_secret():
    ch = ZoomChannel(noop_handler, access_token="t", webhook_secret="")
    assert ch.verify_signature(b"x", "1", "anything") is False


async def test_feed_webhook_event_dispatches():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = ZoomChannel(capture, access_token="t")
    await ch.feed_webhook_event(
        {
            "event": "chat_message.sent",
            "payload": {"object": {"message": "via feed", "sender": "u", "channel_id": "c"}},
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
        test_parse_webhook_event_basic,
        test_parse_webhook_event_one_to_one_uses_to_jid,
        test_parse_webhook_event_filters_other_event_types,
        test_parse_webhook_event_returns_none_on_empty_message,
        test_parse_webhook_event_handles_invalid_timestamp,
        test_verify_signature_accepts_correct,
        test_verify_signature_rejects_wrong,
        test_verify_signature_returns_false_without_secret,
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
