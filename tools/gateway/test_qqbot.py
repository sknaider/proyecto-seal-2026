"""Tests for the QQ Bot adapter — parser + credential checks, no live API."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.qqbot import QQBotChannel, API_BASE
from tools.gateway.base import GatewayError, OutboundMessage, InboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_t = os.environ.pop("QQBOT_TOKEN", None)
    saved_a = os.environ.pop("QQBOT_APP_ID", None)
    try:
        ch = QQBotChannel(noop_handler, bot_token="", bot_app_id="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_t is not None:
            os.environ["QQBOT_TOKEN"] = saved_t
        if saved_a is not None:
            os.environ["QQBOT_APP_ID"] = saved_a


async def test_send_without_credentials_raises():
    ch = QQBotChannel(noop_handler, bot_token="", bot_app_id="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="qqbot", platform_chat_id="g1", text="hi"))
    except GatewayError:
        raised = True
    assert raised


async def test_send_unknown_kind_raises():
    ch = QQBotChannel(noop_handler, bot_token="t", bot_app_id="a")
    raised = False
    try:
        await ch.send(
            OutboundMessage(
                channel="qqbot",
                platform_chat_id="x",
                text="hi",
                metadata={"kind": "weird"},
            )
        )
    except GatewayError:
        raised = True
    assert raised


def test_api_base_sandbox_vs_production():
    prod = QQBotChannel(noop_handler, bot_token="t", bot_app_id="a", sandbox=False)
    sand = QQBotChannel(noop_handler, bot_token="t", bot_app_id="a", sandbox=True)
    assert prod.api_base == API_BASE
    assert sand.api_base == "https://sandbox.api.sgroup.qq.com"


def test_parse_at_message_create():
    event = {
        "t": "AT_MESSAGE_CREATE",
        "d": {
            "id": "msg-100",
            "channel_id": "ch-7",
            "guild_id": "g-3",
            "content": "<@!bot> hola jarvis",
            "timestamp": "2026-04-30T20:00:00+00:00",
            "author": {"id": "u-9", "username": "william"},
        },
    }
    msg = QQBotChannel.parse_event(event)
    assert msg is not None
    assert msg.channel == "qqbot"
    assert msg.platform_user_id == "u-9"
    assert msg.platform_chat_id == "ch-7"
    assert "hola jarvis" in msg.text
    assert msg.metadata.get("kind") == "channel"
    assert msg.metadata.get("guild_id") == "g-3"
    assert msg.received_at.tzinfo is not None


def test_parse_group_at_message_create():
    event = {
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "msg-200",
            "group_openid": "G-abc",
            "content": "@bot hola",
            "timestamp": "2026-04-30T20:01:00Z",
            "author": {"member_openid": "M-1"},
        },
    }
    msg = QQBotChannel.parse_event(event)
    assert msg is not None
    assert msg.platform_chat_id == "G-abc"
    assert msg.platform_user_id == "M-1"
    assert msg.metadata.get("kind") == "group"


def test_parse_c2c_message_create():
    event = {
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "id": "msg-300",
            "user_openid": "U-zzz",
            "content": "hola privado",
            "timestamp": "2026-04-30T20:02:00Z",
            "author": {"user_openid": "U-zzz"},
        },
    }
    msg = QQBotChannel.parse_event(event)
    assert msg is not None
    assert msg.platform_chat_id == "U-zzz"
    assert msg.platform_user_id == "U-zzz"
    assert msg.metadata.get("kind") == "user"


def test_parse_event_filters_unknown_types():
    assert QQBotChannel.parse_event({"t": "READY"}) is None
    assert QQBotChannel.parse_event({"t": "GUILD_CREATE", "d": {}}) is None
    assert QQBotChannel.parse_event({}) is None


def test_parse_event_handles_invalid_timestamp():
    event = {
        "t": "AT_MESSAGE_CREATE",
        "d": {
            "id": "x",
            "channel_id": "c",
            "content": "x",
            "timestamp": "not-a-date",
            "author": {"id": "u"},
        },
    }
    msg = QQBotChannel.parse_event(event)
    assert msg is not None
    assert msg.received_at.tzinfo is not None


async def main() -> int:
    async_tests = [
        test_start_without_credentials_raises,
        test_send_without_credentials_raises,
        test_send_unknown_kind_raises,
    ]
    sync_tests = [
        test_api_base_sandbox_vs_production,
        test_parse_at_message_create,
        test_parse_group_at_message_create,
        test_parse_c2c_message_create,
        test_parse_event_filters_unknown_types,
        test_parse_event_handles_invalid_timestamp,
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
