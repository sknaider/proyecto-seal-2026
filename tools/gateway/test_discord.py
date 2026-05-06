"""Tests for the Discord channel adapter — focuses on the parts that don't require live network."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.discord import DiscordChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_token_raises():
    """start() must raise GatewayError when DISCORD_BOT_TOKEN is missing."""
    saved = os.environ.pop("DISCORD_BOT_TOKEN", None)
    try:
        ch = DiscordChannel(noop_handler, token="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved is not None:
            os.environ["DISCORD_BOT_TOKEN"] = saved


async def test_send_without_token_raises():
    """send() must raise GatewayError when token is missing, even before start."""
    ch = DiscordChannel(noop_handler, token="")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="discord", platform_chat_id="c", text="x"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_message_create_extracts_fields():
    """parse_message_create() pulls author + channel + content from a Discord payload."""
    payload = {
        "t": "MESSAGE_CREATE",
        "d": {
            "id": "111",
            "channel_id": "222",
            "guild_id": "333",
            "content": "hello world",
            "author": {"id": "user-42", "username": "neo", "global_name": "Neo"},
        },
    }
    msg = DiscordChannel.parse_message_create(payload)
    assert msg.channel == "discord"
    assert msg.platform_user_id == "user-42"
    assert msg.platform_chat_id == "222"
    assert msg.text == "hello world"
    assert msg.platform_message_id == "111"
    assert msg.user_display_name == "Neo"
    assert msg.metadata.get("guild_id") == "333"


def test_parse_message_create_handles_missing_fields():
    """Empty/partial payloads do not crash; produce InboundMessage with empty defaults."""
    msg = DiscordChannel.parse_message_create({"d": {}})
    assert msg.channel == "discord"
    assert msg.platform_user_id == ""
    assert msg.platform_chat_id == ""
    assert msg.text == ""


async def main() -> int:
    async_tests = [test_start_without_token_raises, test_send_without_token_raises]
    sync_tests = [test_parse_message_create_extracts_fields, test_parse_message_create_handles_missing_fields]

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
