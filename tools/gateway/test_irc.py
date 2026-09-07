"""Tests for the IRC channel adapter — parser + credential checks, no live server."""

import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.irc import IRCChannel
from tools.gateway.base import GatewayError, OutboundMessage


async def noop_handler(_):
    return None


async def test_start_without_credentials_raises():
    saved_h = os.environ.pop("IRC_HOST", None)
    saved_n = os.environ.pop("IRC_NICK", None)
    try:
        ch = IRCChannel(noop_handler, host="", nickname="")
        raised = False
        try:
            await ch.start()
        except GatewayError:
            raised = True
        assert raised
        assert not ch.running
    finally:
        if saved_h is not None:
            os.environ["IRC_HOST"] = saved_h
        if saved_n is not None:
            os.environ["IRC_NICK"] = saved_n


async def test_send_without_writer_raises():
    ch = IRCChannel(noop_handler, host="irc.example.com", nickname="seal")
    raised = False
    try:
        await ch.send(OutboundMessage(channel="irc", platform_chat_id="#seal", text="hi"))
    except GatewayError:
        raised = True
    assert raised


def test_parse_privmsg_basic():
    line = ":william!user@host PRIVMSG #seal :hola jarvis"
    msg = IRCChannel.parse_privmsg(line)
    assert msg is not None
    assert msg.channel == "irc"
    assert msg.platform_user_id == "william"
    assert msg.platform_chat_id == "#seal"
    assert msg.text == "hola jarvis"
    assert msg.user_display_name == "william"


def test_parse_privmsg_direct_message():
    line = ":henry!h@host PRIVMSG seal-bot :ping privado"
    msg = IRCChannel.parse_privmsg(line)
    assert msg is not None
    assert msg.platform_chat_id == "seal-bot"
    assert msg.text == "ping privado"


def test_parse_privmsg_ignores_non_privmsg():
    assert IRCChannel.parse_privmsg(":server.example 001 seal :Welcome") is None
    assert IRCChannel.parse_privmsg("PING :server") is None
    assert IRCChannel.parse_privmsg(":nick!u@h JOIN #seal") is None


def test_parse_privmsg_handles_message_with_spaces():
    line = ":alice!a@h PRIVMSG #c :hello world how are you"
    msg = IRCChannel.parse_privmsg(line)
    assert msg is not None
    assert msg.text == "hello world how are you"


def test_parse_privmsg_returns_none_on_garbage():
    assert IRCChannel.parse_privmsg("") is None
    assert IRCChannel.parse_privmsg("not a real line") is None


def test_env_channels_parsed_from_csv():
    """IRC_CHANNELS env var is parsed as comma-separated list when channels arg is None."""
    os.environ["IRC_CHANNELS"] = "#seal, #ai, #ops"
    try:
        ch = IRCChannel(noop_handler, host="x", nickname="n")
        assert "#seal" in ch._channels
        assert "#ai" in ch._channels
        assert "#ops" in ch._channels
    finally:
        del os.environ["IRC_CHANNELS"]


async def main() -> int:
    async_tests = [test_start_without_credentials_raises, test_send_without_writer_raises]
    sync_tests = [
        test_parse_privmsg_basic,
        test_parse_privmsg_direct_message,
        test_parse_privmsg_ignores_non_privmsg,
        test_parse_privmsg_handles_message_with_spaces,
        test_parse_privmsg_returns_none_on_garbage,
        test_env_channels_parsed_from_csv,
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
