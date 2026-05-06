"""Tests for the Slack channel adapter — covers parts that don't need live Slack."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.slack import SlackChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_token_raises():
    saved = os.environ.pop("SLACK_BOT_TOKEN", None)
    try:
        ch = SlackChannel(noop_handler, bot_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["SLACK_BOT_TOKEN"] = saved


async def test_send_without_token_raises():
    ch = SlackChannel(noop_handler, bot_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="slack", platform_chat_id="C1", text="x"))
    except GatewayError:
        raised = True
    assert raised


async def test_url_verification_challenge():
    """Slack url_verification handshake must echo the challenge token."""
    ch = SlackChannel(noop_handler, bot_token="xoxb-fake")
    response = await ch.handle_event_payload(
        {"type": "url_verification", "challenge": "abc123"}
    )
    assert response == {"challenge": "abc123"}


async def test_event_callback_dispatches_message():
    """A message event triggers the handler with a normalized InboundMessage."""
    received: list[InboundMessage] = []

    async def capture_handler(msg: InboundMessage):
        received.append(msg)
        return None

    ch = SlackChannel(capture_handler, bot_token="xoxb-fake")
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "user": "U42",
            "channel": "C99",
            "text": "hola jarvis",
            "ts": "1700000000.000100",
            "team": "T1",
        },
    }
    response = await ch.handle_event_payload(payload)
    assert response is None
    assert len(received) == 1
    assert received[0].channel == "slack"
    assert received[0].platform_user_id == "U42"
    assert received[0].platform_chat_id == "C99"
    assert received[0].text == "hola jarvis"
    assert received[0].metadata.get("team") == "T1"


async def test_bot_messages_are_ignored():
    """Messages with bot_id must NOT be dispatched to the handler (avoid loops)."""
    received: list[InboundMessage] = []

    async def capture_handler(msg: InboundMessage):
        received.append(msg)
        return None

    ch = SlackChannel(capture_handler, bot_token="xoxb-fake")
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "user": "U42",
            "channel": "C99",
            "text": "from a bot",
            "ts": "1700000000.000200",
            "bot_id": "B1",
        },
    }
    await ch.handle_event_payload(payload)
    assert received == []


def test_parse_message_event_pulls_fields():
    event = {
        "type": "message",
        "user": "U7",
        "channel": "C7",
        "text": "ping",
        "ts": "1700000000.000300",
        "thread_ts": "1700000000.000100",
        "team": "T7",
    }
    msg = SlackChannel.parse_message_event(event)
    assert msg.channel == "slack"
    assert msg.platform_user_id == "U7"
    assert msg.platform_chat_id == "C7"
    assert msg.text == "ping"
    assert msg.platform_message_id == "1700000000.000300"
    assert msg.metadata.get("thread_ts") == "1700000000.000100"


async def main() -> int:
    async_tests = [
        test_start_without_token_raises,
        test_send_without_token_raises,
        test_url_verification_challenge,
        test_event_callback_dispatches_message,
        test_bot_messages_are_ignored,
    ]
    sync_tests = [test_parse_message_event_pulls_fields]
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
