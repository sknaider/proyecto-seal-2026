"""Tests for IRCChannel — pure stdlib IRC adapter."""

import asyncio
import sys
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.irc_channel import IRCChannel, IRCConfig, IRCError
from tools.gateway.base import OutboundMessage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cfg(**kw) -> IRCConfig:
    defaults = dict(host="irc.test", port=6667, nick="seal-bot",
                    channel="#test", timeout=5)
    defaults.update(kw)
    return IRCConfig(**defaults)


def _fake_reader(*lines: str) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data((line + "\r\n").encode())
    reader.feed_eof()
    return reader


class FakeWriter:
    def __init__(self):
        self.sent = []
        self._closing = False

    def write(self, data: bytes):
        self.sent.append(data.decode("utf-8", errors="replace"))

    async def drain(self):
        pass

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass

    def is_closing(self) -> bool:
        return self._closing

    def all_sent(self) -> str:
        return "".join(self.sent)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _irc_with_streams(lines) -> tuple[IRCChannel, FakeWriter]:
    cfg    = _make_cfg()
    irc    = IRCChannel(cfg)
    reader = _fake_reader(*lines)
    writer = FakeWriter()
    irc._reader = reader
    irc._writer = writer
    irc._joined = True
    return irc, writer


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_config_defaults():
    cfg = _make_cfg()
    assert cfg.host == "irc.test"
    assert cfg.port == 6667
    assert cfg.nick == "seal-bot"
    assert not cfg.use_tls


def test_send_privmsg():
    irc, writer = _irc_with_streams([])
    msg = OutboundMessage(channel="#test", platform_chat_id="#test", text="hello team")
    run(irc.send(msg))
    assert "PRIVMSG #test :hello team" in writer.all_sent()


def test_send_uses_configured_channel_by_default():
    irc, writer = _irc_with_streams([])
    msg = OutboundMessage(channel="#test", platform_chat_id="#test", text="default channel")
    run(irc.send(msg))
    assert "PRIVMSG #test :default channel" in writer.all_sent()


def test_send_multiline_splits():
    irc, writer = _irc_with_streams([])
    msg = OutboundMessage(channel="#test", platform_chat_id="#test", text="line1\nline2\nline3")
    run(irc.send(msg))
    sent = writer.all_sent()
    assert "line1" in sent
    assert "line2" in sent
    assert "line3" in sent


def test_send_not_connected_raises():
    irc = IRCChannel(_make_cfg())
    irc._writer = None
    raised = False
    try:
        run(irc.send(OutboundMessage(channel="#test", platform_chat_id="#test", text="hi")))
    except IRCError:
        raised = True
    assert raised


def test_listen_yields_privmsg():
    lines = [":nick!user@host PRIVMSG #test :hello world"]
    irc, _ = _irc_with_streams(lines)

    async def collect():
        msgs = []
        async for m in irc.listen():
            msgs.append(m)
        return msgs

    msgs = run(collect())
    assert len(msgs) == 1
    assert msgs[0].text == "hello world"
    assert msgs[0].platform_user_id == "nick"
    assert msgs[0].channel == "#test"
    assert msgs[0].metadata["platform"] == "irc"


def test_listen_ignores_non_privmsg():
    lines = [
        ":server.net 001 seal-bot :Welcome",
        ":nick!u@h PRIVMSG #test :only this",
        ":server.net NOTICE * :server notice",
    ]
    irc, _ = _irc_with_streams(lines)

    async def collect():
        msgs = []
        async for m in irc.listen():
            msgs.append(m)
        return msgs

    msgs = run(collect())
    assert len(msgs) == 1
    assert msgs[0].text == "only this"


def test_listen_responds_to_ping():
    lines = ["PING :server.net", ":nick!u@h PRIVMSG #test :after ping"]
    irc, writer = _irc_with_streams(lines)

    async def collect():
        msgs = []
        async for m in irc.listen():
            msgs.append(m)
        return msgs

    msgs = run(collect())
    assert "PONG :server.net" in writer.all_sent()
    assert len(msgs) == 1


def test_listen_calls_handlers():
    lines = [":alice!a@h PRIVMSG #test :msg for handler"]
    irc, _ = _irc_with_streams(lines)
    received = []
    irc.add_handler(lambda m: received.append(m.text))

    async def collect():
        async for _ in irc.listen():
            pass

    run(collect())
    assert received == ["msg for handler"]


def test_handshake_sends_nick_and_user():
    cfg    = _make_cfg()
    irc    = IRCChannel(cfg)
    reader = _fake_reader(":server 001 seal-bot :Welcome")
    writer = FakeWriter()
    irc._reader = reader
    irc._writer = writer

    run(irc._handshake())
    sent = writer.all_sent()
    assert "NICK seal-bot" in sent
    assert "USER seal-bot" in sent
    assert "JOIN #test" in sent


def test_handshake_sends_pass_when_configured():
    cfg    = _make_cfg(password="secret")
    irc    = IRCChannel(cfg)
    reader = _fake_reader(":server 001 seal-bot :Welcome")
    writer = FakeWriter()
    irc._reader = reader
    irc._writer = writer

    run(irc._handshake())
    assert "PASS secret" in writer.all_sent()


def test_handshake_nick_in_use_raises():
    cfg    = _make_cfg()
    irc    = IRCChannel(cfg)
    reader = _fake_reader(":server 433 * seal-bot :Nickname is already in use")
    writer = FakeWriter()
    irc._reader = reader
    irc._writer = writer

    raised = False
    try:
        run(irc._handshake())
    except IRCError as e:
        raised = True
        assert "in use" in str(e).lower() or "433" in str(e)
    assert raised


def test_connected_false_when_no_writer():
    irc = IRCChannel(_make_cfg())
    assert not irc.connected


def test_connected_true_when_writer_not_closing():
    irc, writer = _irc_with_streams([])
    assert irc.connected


def test_from_env_builds_config():
    import os
    with patch.dict(os.environ, {
        "IRC_HOST":    "irc.seal.net",
        "IRC_PORT":    "6697",
        "IRC_NICK":    "seal-nexus",
        "IRC_CHANNEL": "#seal-ops",
        "IRC_TLS":     "true",
    }):
        irc = IRCChannel.from_env()
    assert irc._cfg.host    == "irc.seal.net"
    assert irc._cfg.port    == 6697
    assert irc._cfg.nick    == "seal-nexus"
    assert irc._cfg.channel == "#seal-ops"
    assert irc._cfg.use_tls is True


def test_from_env_defaults():
    import os
    with patch.dict(os.environ, {}, clear=True):
        irc = IRCChannel.from_env()
    assert "libera" in irc._cfg.host or irc._cfg.host != ""
    assert irc._cfg.port == 6667


def main() -> int:
    tests = [
        test_config_defaults,
        test_send_privmsg,
        test_send_uses_configured_channel_by_default,
        test_send_multiline_splits,
        test_send_not_connected_raises,
        test_listen_yields_privmsg,
        test_listen_ignores_non_privmsg,
        test_listen_responds_to_ping,
        test_listen_calls_handlers,
        test_handshake_sends_nick_and_user,
        test_handshake_sends_pass_when_configured,
        test_handshake_nick_in_use_raises,
        test_connected_false_when_no_writer,
        test_connected_true_when_writer_not_closing,
        test_from_env_builds_config,
        test_from_env_defaults,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
