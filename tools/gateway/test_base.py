"""Tests for the abstract GatewayChannel contract."""

import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.base import (
    GatewayChannel,
    GatewayError,
    InboundMessage,
    OutboundMessage,
)


class MemoryChannel(GatewayChannel):
    """In-memory test double — records sends, lets tests inject inbound."""

    def __init__(self, handler):
        super().__init__("memory", handler)
        self.sent: list[OutboundMessage] = []

    async def start(self) -> None:
        self._running = True

    async def send(self, message: OutboundMessage) -> str:
        if not self._running:
            raise GatewayError("channel not started")
        self.sent.append(message)
        return f"mem-{len(self.sent)}"

    async def close(self) -> None:
        self._running = False

    async def simulate_inbound(self, inbound: InboundMessage) -> None:
        await self._dispatch(inbound)


async def test_lifecycle_and_dispatch():
    """Channel starts, dispatches inbound to handler, delivers handler reply."""

    async def echo_handler(msg: InboundMessage):
        return OutboundMessage(
            channel=msg.channel,
            platform_chat_id=msg.platform_chat_id,
            text=f"echo: {msg.text}",
        )

    ch = MemoryChannel(echo_handler)
    assert not ch.running
    await ch.start()
    assert ch.running

    await ch.simulate_inbound(
        InboundMessage(
            channel="memory",
            platform_user_id="u1",
            platform_chat_id="c1",
            text="hola",
        )
    )

    assert len(ch.sent) == 1
    assert ch.sent[0].text == "echo: hola"
    assert ch.sent[0].platform_chat_id == "c1"

    await ch.close()
    assert not ch.running


async def test_handler_returning_none_does_not_send():
    """Handler returning None means no outbound delivery."""

    async def silent_handler(msg: InboundMessage):
        return None

    ch = MemoryChannel(silent_handler)
    await ch.start()
    await ch.simulate_inbound(
        InboundMessage(
            channel="memory",
            platform_user_id="u1",
            platform_chat_id="c1",
            text="ignored",
        )
    )
    assert ch.sent == []
    await ch.close()


async def test_send_before_start_raises():
    """Sending on a non-started channel must raise GatewayError."""

    async def noop(_):
        return None

    ch = MemoryChannel(noop)
    raised = False
    try:
        await ch.send(OutboundMessage(channel="memory", platform_chat_id="c1", text="x"))
    except GatewayError:
        raised = True
    assert raised


async def test_async_context_manager():
    """async with starts and closes the channel."""

    async def noop(_):
        return None

    ch = MemoryChannel(noop)
    async with ch:
        assert ch.running
    assert not ch.running


async def main() -> int:
    tests = [
        test_lifecycle_and_dispatch,
        test_handler_returning_none_does_not_send,
        test_send_before_start_raises,
        test_async_context_manager,
    ]
    passed = 0
    for t in tests:
        await t()
        print(f"[OK] {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
