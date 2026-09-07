"""Tests for the Microsoft Teams adapter — parser + send-without-url checks, no live webhook."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.teams import TeamsChannel, _build_message_card
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_send_without_webhook_url_raises():
    saved = os.environ.pop("TEAMS_INCOMING_WEBHOOK_URL", None)
    try:
        ch = TeamsChannel(noop_handler, default_webhook_url="")
        raised = False
        try:
            await ch.send(OutboundMessage(channel="teams", platform_chat_id="", text="hi"))
        except GatewayError:
            raised = True
        assert raised
    finally:
        if saved is not None:
            os.environ["TEAMS_INCOMING_WEBHOOK_URL"] = saved


def test_message_card_basic_structure():
    card = _build_message_card("hello", title="Notice")
    assert card["@type"] == "MessageCard"
    assert card["@context"] == "https://schema.org/extensions"
    assert card["text"] == "hello"
    assert card["title"] == "Notice"


def test_message_card_no_title_when_omitted():
    card = _build_message_card("plain")
    assert "title" not in card
    assert card["text"] == "plain"


def test_parse_outgoing_webhook_basic():
    payload = {
        "id": "msg-1",
        "type": "message",
        "timestamp": "2026-04-30T20:00:00Z",
        "from": {"id": "user-42", "name": "William"},
        "conversation": {"id": "conv-99"},
        "text": "<at>SealBot</at> hola jarvis",
        "channelData": {"team": {"id": "team-1"}},
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
    }
    msg = TeamsChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.channel == "teams"
    assert msg.platform_user_id == "user-42"
    assert msg.platform_chat_id == "conv-99"
    assert msg.text == "<at>SealBot</at> hola jarvis"
    assert msg.platform_message_id == "msg-1"
    assert msg.user_display_name == "William"
    assert msg.metadata.get("service_url") == "https://smba.trafficmanager.net/amer/"
    assert msg.received_at.tzinfo is not None


def test_parse_outgoing_webhook_filters_non_message_type():
    """Activities with type != 'message' (e.g. 'conversationUpdate') are ignored."""
    payload = {
        "type": "conversationUpdate",
        "conversation": {"id": "c1"},
        "text": "joined",
    }
    assert TeamsChannel.parse_outgoing_webhook(payload) is None


def test_parse_outgoing_webhook_missing_required_returns_none():
    assert TeamsChannel.parse_outgoing_webhook({}) is None
    assert TeamsChannel.parse_outgoing_webhook({"text": "hi"}) is None
    assert TeamsChannel.parse_outgoing_webhook({"conversation": {"id": "c"}}) is None


def test_parse_outgoing_webhook_handles_missing_timestamp():
    payload = {
        "type": "message",
        "from": {"id": "u"},
        "conversation": {"id": "c"},
        "text": "no ts",
    }
    msg = TeamsChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


def test_parse_outgoing_webhook_handles_invalid_timestamp():
    payload = {
        "type": "message",
        "from": {"id": "u"},
        "conversation": {"id": "c"},
        "text": "bad ts",
        "timestamp": "not-a-date",
    }
    msg = TeamsChannel.parse_outgoing_webhook(payload)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


async def test_feed_outgoing_webhook_dispatches():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = TeamsChannel(capture)
    await ch.feed_outgoing_webhook(
        {
            "type": "message",
            "from": {"id": "u", "name": "x"},
            "conversation": {"id": "c"},
            "text": "via feed",
        }
    )
    assert len(received) == 1
    assert received[0].text == "via feed"


async def main() -> int:
    async_tests = [test_send_without_webhook_url_raises, test_feed_outgoing_webhook_dispatches]
    sync_tests = [
        test_message_card_basic_structure,
        test_message_card_no_title_when_omitted,
        test_parse_outgoing_webhook_basic,
        test_parse_outgoing_webhook_filters_non_message_type,
        test_parse_outgoing_webhook_missing_required_returns_none,
        test_parse_outgoing_webhook_handles_missing_timestamp,
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
