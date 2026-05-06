"""Tests for Rocket.Chat adapter — parser + credential checks, no live server."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.rocketchat import RocketChatChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    for k in ("ROCKETCHAT_URL", "ROCKETCHAT_AUTH_TOKEN", "ROCKETCHAT_USER_ID"):
        os.environ.pop(k, None)
    ch = RocketChatChannel(noop_handler)
    raised = False
    try:
        await ch.start()
    except GatewayError:
        raised = True
    assert raised
    assert not ch.running


async def test_send_without_credentials_raises():
    ch = RocketChatChannel(noop_handler, server_url="", auth_token="", user_id="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="rocketchat", platform_chat_id="#general", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_outgoing_webhook_basic():
    payload = {
        "token": "tok",
        "channel_id": "c1",
        "channel_name": "general",
        "timestamp": "2026-04-30T20:00:00Z",
        "user_id": "u1",
        "user_name": "william",
        "text": "hola jarvis",
        "message_id": "m1",
        "trigger_word": "@seal",
    }
    msg = RocketChatChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.channel == "rocketchat"
    assert msg.platform_user_id == "u1"
    assert msg.platform_chat_id == "c1"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "m1"
    assert msg.user_display_name == "william"
    assert msg.metadata.get("trigger_word") == "@seal"
    assert msg.received_at.tzinfo is not None


def test_parse_outgoing_webhook_unix_timestamp_ms():
    payload = {
        "channel_id": "c",
        "user_id": "u",
        "text": "ms",
        "timestamp": 1730000000000,
    }
    msg = RocketChatChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


def test_parse_outgoing_webhook_falls_back_to_message_raw():
    payload = {"channel_id": "c", "user_id": "u", "message_raw": "raw body"}
    msg = RocketChatChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.text == "raw body"


def test_parse_outgoing_webhook_returns_none_on_missing_fields():
    assert RocketChatChannel.parse_outgoing_webhook({}) is None
    assert RocketChatChannel.parse_outgoing_webhook({"text": "hi"}) is None
    assert RocketChatChannel.parse_outgoing_webhook({"channel_id": "c"}) is None


async def test_feed_outgoing_webhook_dispatches():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = RocketChatChannel(
        capture,
        server_url="https://rc.example",
        auth_token="t",
        user_id="u",
    )
    await ch.feed_outgoing_webhook(
        {"channel_id": "c", "user_id": "u", "text": "via feed"}
    )
    assert len(received) == 1
    assert received[0].text == "via feed"


def test_parse_outgoing_webhook_handles_invalid_timestamp():
    payload = {"channel_id": "c", "user_id": "u", "text": "x", "timestamp": "not-a-date"}
    msg = RocketChatChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


async def main() -> int:
    async_tests = [
        test_start_without_credentials_raises,
        test_send_without_credentials_raises,
        test_feed_outgoing_webhook_dispatches,
    ]
    sync_tests = [
        test_parse_outgoing_webhook_basic,
        test_parse_outgoing_webhook_unix_timestamp_ms,
        test_parse_outgoing_webhook_falls_back_to_message_raw,
        test_parse_outgoing_webhook_returns_none_on_missing_fields,
        test_parse_outgoing_webhook_handles_invalid_timestamp,
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
