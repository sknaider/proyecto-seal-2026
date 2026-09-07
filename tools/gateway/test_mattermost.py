"""Tests for the Mattermost channel adapter — parser + credential checks, no live server."""

import asyncio
import json
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.mattermost import MattermostChannel
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_u = os.environ.pop("MATTERMOST_URL", None)
    saved_t = os.environ.pop("MATTERMOST_TOKEN", None)
    try:
        ch = MattermostChannel(noop_handler, server_url="", access_token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_u is not None:
            os.environ["MATTERMOST_URL"] = saved_u
        if saved_t is not None:
            os.environ["MATTERMOST_TOKEN"] = saved_t


async def test_send_without_credentials_raises():
    ch = MattermostChannel(noop_handler, server_url="", access_token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="mattermost", platform_chat_id="ch1", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_post_event_string_post():
    event = {
        "event": "posted",
        "data": {
            "post": json.dumps(
                {
                    "id": "p1",
                    "user_id": "u1",
                    "channel_id": "c1",
                    "message": "hola jarvis",
                    "create_at": 1730000000000,
                }
            ),
            "sender_name": "@william",
            "channel_name": "town-square",
            "team_id": "t1",
        },
    }
    msg = MattermostChannel.parse_post_event(event)
    assert msg is not None
    assert msg.channel == "mattermost"
    assert msg.platform_user_id == "u1"
    assert msg.platform_chat_id == "c1"
    assert msg.text == "hola jarvis"
    assert msg.platform_message_id == "p1"
    assert msg.user_display_name == "@william"


def test_parse_post_event_dict_post():
    """Some Mattermost servers deliver the post as a dict (not JSON string)."""
    event = {
        "event": "posted",
        "data": {
            "post": {
                "id": "p2",
                "user_id": "u2",
                "channel_id": "c2",
                "message": "ping",
                "create_at": 1730000001000,
            },
            "sender_name": "henry",
        },
    }
    msg = MattermostChannel.parse_post_event(event)
    assert msg is not None
    assert msg.text == "ping"


def test_parse_post_event_filters_non_posted():
    assert MattermostChannel.parse_post_event({"event": "typing", "data": {}}) is None
    assert MattermostChannel.parse_post_event({}) is None


def test_parse_post_event_handles_invalid_post_json():
    event = {"event": "posted", "data": {"post": "not-json{"}}
    assert MattermostChannel.parse_post_event(event) is None


def test_parse_outgoing_webhook_basic():
    form = {
        "text": "hola via webhook",
        "channel_id": "c-webhook",
        "user_id": "u-webhook",
        "user_name": "william",
        "channel_name": "ops",
        "trigger_word": "@seal",
        "post_id": "p-w-1",
        "team_domain": "myteam",
    }
    msg = MattermostChannel.parse_outgoing_webhook(form)
    assert msg is not None
    assert msg.channel == "mattermost"
    assert msg.platform_user_id == "u-webhook"
    assert msg.platform_chat_id == "c-webhook"
    assert msg.text == "hola via webhook"
    assert msg.platform_message_id == "p-w-1"
    assert msg.metadata.get("trigger_word") == "@seal"


def test_parse_outgoing_webhook_returns_none_on_missing_fields():
    assert MattermostChannel.parse_outgoing_webhook({}) is None
    assert MattermostChannel.parse_outgoing_webhook({"text": "hi"}) is None  # no channel_id
    assert MattermostChannel.parse_outgoing_webhook({"channel_id": "c"}) is None  # no text


async def test_feed_post_event_dispatches_to_handler():
    received: list[InboundMessage] = []

    async def capture(msg):
        received.append(msg)
        return None

    ch = MattermostChannel(capture, server_url="https://mm.example", access_token="t")
    await ch.feed_post_event(
        {
            "event": "posted",
            "data": {
                "post": json.dumps(
                    {
                        "id": "p3",
                        "user_id": "u3",
                        "channel_id": "c3",
                        "message": "via feed",
                        "create_at": 1700000000000,
                    }
                )
            },
        }
    )
    assert len(received) == 1
    assert received[0].text == "via feed"


async def main() -> int:
    async_tests = [
        test_start_without_credentials_raises,
        test_send_without_credentials_raises,
        test_feed_post_event_dispatches_to_handler,
    ]
    sync_tests = [
        test_parse_post_event_string_post,
        test_parse_post_event_dict_post,
        test_parse_post_event_filters_non_posted,
        test_parse_post_event_handles_invalid_post_json,
        test_parse_outgoing_webhook_basic,
        test_parse_outgoing_webhook_returns_none_on_missing_fields,
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
