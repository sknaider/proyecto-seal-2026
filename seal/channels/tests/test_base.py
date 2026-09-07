"""Contract tests for seal/channels base infrastructure.

Run:
    python3 -m pytest seal/channels/tests/test_base.py -v

Or standalone:
    python3 -m unittest seal.channels.tests.test_base -v
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from seal.channels.base import ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult
from seal.channels.inmemory import InMemoryAdapter
from seal.channels.lock import CredentialLock
from seal.channels.runner import ChannelRunner


def _ev(text: str = "hola", chat_id: str = "room1", msg_id: str = "m1") -> MessageEvent:
    return MessageEvent(
        channel="inmemory",
        chat_id=chat_id,
        user_id="william",
        text=text,
        message_id=msg_id,
    )


class MessageEventTests(unittest.TestCase):
    def test_starts_with_command(self) -> None:
        ev = _ev(text="  /status now")
        self.assertTrue(ev.starts_with_command("/status"))
        self.assertFalse(ev.starts_with_command("/usage"))

    def test_command_body_strips_prefix_and_lspace(self) -> None:
        ev = _ev(text="/status   on demand ")
        self.assertEqual(ev.command_body("/status"), "on demand ")

    def test_attachments_default_empty_tuple(self) -> None:
        ev = _ev()
        self.assertEqual(ev.attachments, ())

    def test_immutable(self) -> None:
        ev = _ev()
        with self.assertRaises(Exception):
            ev.text = "mutated"  # frozen dataclass


class CredentialLockTests(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = CredentialLock(Path(tmp), "telegram", "secret-token-A")
            self.assertTrue(lock.acquire())
            try:
                self.assertTrue(lock.lock_path.exists())
            finally:
                lock.release()

    def test_second_acquire_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = CredentialLock(Path(tmp), "telegram", "shared-token")
            b = CredentialLock(Path(tmp), "telegram", "shared-token")
            self.assertTrue(a.acquire())
            try:
                self.assertFalse(b.acquire())
            finally:
                a.release()

    def test_different_credentials_dont_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = CredentialLock(Path(tmp), "telegram", "token-A")
            b = CredentialLock(Path(tmp), "telegram", "token-B")
            self.assertTrue(a.acquire())
            self.assertTrue(b.acquire())
            a.release()
            b.release()

    def test_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with CredentialLock(Path(tmp), "discord", "abc") as lock:
                self.assertTrue(lock.lock_path.exists())


class InMemoryAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_disconnect(self) -> None:
        adapter = InMemoryAdapter()
        self.assertFalse(adapter.healthcheck().connected)
        await adapter.connect()
        self.assertTrue(adapter.healthcheck().connected)
        await adapter.disconnect()
        self.assertFalse(adapter.healthcheck().connected)

    async def test_send_returns_success_when_connected(self) -> None:
        adapter = InMemoryAdapter()
        await adapter.connect()
        result = await adapter.send("room1", "hi")
        self.assertIsInstance(result, SendResult)
        self.assertTrue(result.success)
        self.assertEqual(adapter.outbox()[0].text, "hi")

    async def test_send_fails_when_disconnected(self) -> None:
        adapter = InMemoryAdapter()
        result = await adapter.send("room1", "hi")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "not connected")

    async def test_events_yield_pushed(self) -> None:
        adapter = InMemoryAdapter()
        await adapter.connect()
        adapter.push_inbound(_ev(text="A", msg_id="1"))
        adapter.push_inbound(_ev(text="B", msg_id="2"))
        seen: list[str] = []

        async def consume() -> None:
            async for ev in adapter.events():
                seen.append(ev.text)
                if len(seen) >= 2:
                    adapter.stop()

        await asyncio.wait_for(consume(), timeout=1.0)
        self.assertEqual(seen, ["A", "B"])
        await adapter.disconnect()

    async def test_health_reports_connected_state(self) -> None:
        adapter = InMemoryAdapter()
        h = adapter.healthcheck()
        self.assertIsInstance(h, ChannelHealth)
        self.assertEqual(h.channel, "inmemory")


class ChannelRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_and_dispatch(self) -> None:
        adapter = InMemoryAdapter()
        runner = ChannelRunner()
        runner.register(adapter)

        seen: list[MessageEvent] = []

        async def hook(ev: MessageEvent) -> None:
            seen.append(ev)

        runner.add_inbound_hook(hook)
        await runner.start()
        try:
            adapter.push_inbound(_ev(text="hello", msg_id="x"))
            # give the consumer task a tick to dispatch
            for _ in range(20):
                if seen:
                    break
                await asyncio.sleep(0.01)
        finally:
            await runner.stop()

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].text, "hello")

    async def test_double_register_rejected(self) -> None:
        runner = ChannelRunner()
        runner.register(InMemoryAdapter())
        with self.assertRaises(ValueError):
            runner.register(InMemoryAdapter())

    async def test_send_via_runner(self) -> None:
        adapter = InMemoryAdapter()
        runner = ChannelRunner()
        runner.register(adapter)
        await runner.start()
        try:
            result = await runner.send("inmemory", "room1", "yo")
            self.assertTrue(result.success)
            self.assertEqual(adapter.outbox()[0].text, "yo")
        finally:
            await runner.stop()

    async def test_send_unknown_channel_raises(self) -> None:
        runner = ChannelRunner()
        with self.assertRaises(KeyError):
            await runner.send("nope", "x", "y")

    async def test_hook_exception_does_not_kill_consumer(self) -> None:
        adapter = InMemoryAdapter()
        runner = ChannelRunner()
        runner.register(adapter)

        seen: list[str] = []

        async def bad_hook(ev: MessageEvent) -> None:
            raise RuntimeError("boom")

        async def good_hook(ev: MessageEvent) -> None:
            seen.append(ev.text)

        runner.add_inbound_hook(bad_hook)
        runner.add_inbound_hook(good_hook)
        await runner.start()
        try:
            adapter.push_inbound(_ev(text="ok"))
            for _ in range(20):
                if seen:
                    break
                await asyncio.sleep(0.01)
        finally:
            await runner.stop()
        self.assertEqual(seen, ["ok"])


if __name__ == "__main__":
    unittest.main()
