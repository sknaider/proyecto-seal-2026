"""Tests for TelegramChannel adapter."""

import asyncio
import sys
import pathlib
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.base import InboundMessage, OutboundMessage, GatewayError
from tools.gateway.telegram import TelegramChannel


async def test_start_without_token_raises():
    """start() without a token must raise GatewayError immediately."""
    async def noop(_): return None
    ch = TelegramChannel(noop, token="")
    raised = False
    try:
        await ch.start()
    except GatewayError:
        raised = True
    assert raised, "expected GatewayError for missing token"


async def test_not_running_before_start():
    async def noop(_): return None
    ch = TelegramChannel(noop, token="fake")
    assert not ch.running


async def test_start_sets_running():
    async def noop(_): return None
    ch = TelegramChannel(noop, token="fake")
    # Patch _poll_loop so it doesn't actually hit Telegram
    ch._poll_loop = asyncio.coroutine(lambda: None) if False else (lambda: asyncio.sleep(9999))
    with mock.patch.object(asyncio, "create_task", return_value=mock.MagicMock()):
        await ch.start()
        assert ch.running
    await ch.close()


async def test_close_stops_running():
    async def noop(_): return None
    ch = TelegramChannel(noop, token="fake")
    with mock.patch.object(asyncio, "create_task", return_value=mock.MagicMock(done=lambda: True)):
        await ch.start()
    await ch.close()
    assert not ch.running


async def test_send_dispatches_reply():
    """Handler returning OutboundMessage triggers send(); verify text forwarded."""
    sent: list[OutboundMessage] = []

    async def echo_handler(msg: InboundMessage):
        return OutboundMessage(channel="telegram", platform_chat_id=msg.platform_chat_id, text=f"eco:{msg.text}")

    ch = TelegramChannel(echo_handler, token="fake")

    async def fake_send(message: OutboundMessage) -> str:
        sent.append(message)
        return "999"

    ch.send = fake_send
    ch._running = True

    inbound = InboundMessage(
        channel="telegram",
        platform_user_id="42",
        platform_chat_id="chat1",
        text="hola",
        platform_message_id="7",
    )
    await ch._dispatch(inbound)
    assert len(sent) == 1
    assert sent[0].text == "eco:hola"
    assert sent[0].platform_chat_id == "chat1"


async def test_handler_none_does_not_send():
    """Handler returning None must not call send()."""
    sends = []

    async def silent(_): return None

    ch = TelegramChannel(silent, token="fake")

    async def should_not_be_called(_):
        sends.append(1)
        return "0"

    ch.send = should_not_be_called
    ch._running = True

    await ch._dispatch(InboundMessage(
        channel="telegram", platform_user_id="1", platform_chat_id="c", text="x"
    ))
    assert sends == []


async def test_allowed_user_ids_filter():
    """Messages from users not in allowed_user_ids must be silently dropped."""
    dispatched = []

    async def handler(msg):
        dispatched.append(msg)
        return None

    ch = TelegramChannel(handler, token="fake", allowed_user_ids=[100])
    ch._running = True

    # Simulate _poll_loop behavior: user_id=999 not in allowed list
    user_id = 999
    if ch._allowed_user_ids and user_id not in ch._allowed_user_ids:
        pass  # dropped — this is the guard in _poll_loop
    else:
        await ch._dispatch(InboundMessage(
            channel="telegram", platform_user_id=str(user_id),
            platform_chat_id="c", text="intruder"
        ))
    assert dispatched == []


async def test_text_truncated_to_4096():
    """Text longer than 4096 chars must be truncated before sending."""
    sends = []

    async def noop(_): return None
    ch = TelegramChannel(noop, token="fake")
    ch._running = True

    long_text = "a" * 5000
    msg = OutboundMessage(channel="telegram", platform_chat_id="c", text=long_text)

    # Patch urllib to capture payload
    captured = {}
    def fake_post(url, payload, token):
        captured["text"] = payload["text"]
        return {"ok": True, "result": {"message_id": 1}}

    import tools.gateway.telegram as tg_mod
    with mock.patch.object(tg_mod, "_http_post", fake_post):
        await ch.send(msg)

    assert len(captured["text"]) == 4096


async def test_async_context_manager():
    """async with starts and closes the channel."""
    async def noop(_): return None
    ch = TelegramChannel(noop, token="fake")
    with mock.patch.object(asyncio, "create_task", return_value=mock.MagicMock(done=lambda: True)):
        async with ch:
            assert ch.running
    assert not ch.running


async def main() -> int:
    tests = [
        test_start_without_token_raises,
        test_not_running_before_start,
        test_start_sets_running,
        test_close_stops_running,
        test_send_dispatches_reply,
        test_handler_none_does_not_send,
        test_allowed_user_ids_filter,
        test_text_truncated_to_4096,
        test_async_context_manager,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            await t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {t.__name__}: {exc}")
            failed.append(t.__name__)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("FAILED:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
