"""Tests for the LINE adapter — parser + signature + credential checks, no live API."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.line import LineChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_token_raises():
    saved = os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
    try:
        ch = LineChannel(noop_handler, channel_access_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = saved


async def test_send_without_token_raises():
    ch = LineChannel(noop_handler, channel_access_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="line", platform_chat_id="U1", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_webhook_event_text_message():
    event = {
        "type": "message",
        "replyToken": "tok123",
        "timestamp": 1730000000000,
        "source": {"type": "user", "userId": "Uabc"},
        "message": {"type": "text", "id": "m1", "text": "hola jarvis"},
    }
    msg = LineChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.channel == "line"
    assert msg.platform_user_id == "Uabc"
    assert msg.platform_chat_id == "Uabc"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "m1"
    assert msg.metadata.get("reply_token") == "tok123"
    assert msg.received_at.tzinfo is not None


def test_parse_webhook_event_group_uses_groupId_as_chat():
    event = {
        "type": "message",
        "replyToken": "tok",
        "timestamp": 1730000001000,
        "source": {"type": "group", "groupId": "Ggrp", "userId": "Uxyz"},
        "message": {"type": "text", "id": "m2", "text": "hola al grupo"},
    }
    msg = LineChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.platform_chat_id == "Ggrp"
    assert msg.platform_user_id == "Uxyz"
    assert msg.metadata.get("source_type") == "group"


def test_parse_webhook_event_filters_non_message_types():
    assert LineChannel.parse_webhook_event({"type": "follow", "source": {}}) is None
    assert LineChannel.parse_webhook_event({"type": "unfollow", "source": {}}) is None


def test_parse_webhook_event_filters_non_text_messages():
    event = {
        "type": "message",
        "source": {"userId": "U1"},
        "timestamp": 1730000002000,
        "message": {"type": "image", "id": "img1"},
    }
    assert LineChannel.parse_webhook_event(event) is None


def test_parse_webhook_event_handles_missing_timestamp():
    event = {
        "type": "message",
        "source": {"userId": "U1"},
        "message": {"type": "text", "id": "m3", "text": "no ts"},
    }
    msg = LineChannel.parse_webhook_event(event)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


def test_verify_signature_accepts_correct_hmac():
    secret = "topsecret"
    body = b'{"events":[]}'
    expected = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    ch = LineChannel(noop_handler, channel_access_token="t", channel_secret=secret)
    assert ch.verify_signature(body, expected) is True


def test_verify_signature_rejects_wrong_hmac():
    ch = LineChannel(noop_handler, channel_access_token="t", channel_secret="s")
    assert ch.verify_signature(b"body", "definitely-wrong") is False


def test_verify_signature_returns_false_without_secret():
    ch = LineChannel(noop_handler, channel_access_token="t", channel_secret="")
    assert ch.verify_signature(b"x", "anything") is False


async def test_feed_webhook_event_dispatches():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = LineChannel(capture, channel_access_token="t")
    await ch.feed_webhook_event(
        {
            "type": "message",
            "replyToken": "tok",
            "timestamp": 1730000003000,
            "source": {"type": "user", "userId": "Ufeed"},
            "message": {"type": "text", "id": "m4", "text": "via feed"},
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
        test_parse_webhook_event_group_uses_groupId_as_chat,
        test_parse_webhook_event_filters_non_message_types,
        test_parse_webhook_event_filters_non_text_messages,
        test_parse_webhook_event_handles_missing_timestamp,
        test_verify_signature_accepts_correct_hmac,
        test_verify_signature_rejects_wrong_hmac,
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
