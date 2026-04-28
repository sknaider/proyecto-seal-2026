"""In-memory channel adapter — for tests, dev fixtures, and contract validation.

The adapter holds two queues:
    inbox  — messages pushed by tests, surfaced to events()
    outbox — messages sent through send(), inspectable by tests

No network IO. Deterministic. Safe to use in unit tests.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import time
from typing import Mapping

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult


@dataclass(slots=True)
class _Outbound:
    chat_id: str
    text: str
    reply_to: str | None
    attachments: tuple[Attachment, ...]
    metadata: Mapping[str, object]
    sent_at: float = field(default_factory=time)


class InMemoryAdapter(ChannelAdapter):
    """Channel adapter backed by asyncio.Queue — no IO."""

    channel = "inmemory"

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config or {})
        self._inbox: asyncio.Queue[MessageEvent] = asyncio.Queue()
        self._outbox: list[_Outbound] = []
        self._connected = False
        self._counter = 0
        self._last_event_at: float | None = None

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def events(self) -> AsyncIterator[MessageEvent]:
        while not self.stopping:
            getter = asyncio.create_task(self._inbox.get())
            stopper = asyncio.create_task(self._stop_event.wait())
            done, _pending = await asyncio.wait(
                {getter, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            if stopper in done:
                getter.cancel()
                break
            stopper.cancel()
            event = getter.result()
            self._last_event_at = time()
            yield event

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        attachments: tuple[Attachment, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> SendResult:
        if not self._connected:
            return SendResult(
                channel=self.channel,
                chat_id=chat_id,
                message_id="",
                success=False,
                error="not connected",
            )
        self._counter += 1
        msg_id = f"out-{self._counter}"
        self._outbox.append(
            _Outbound(
                chat_id=chat_id,
                text=text,
                reply_to=reply_to,
                attachments=attachments,
                metadata=metadata or {},
            )
        )
        return SendResult(
            channel=self.channel, chat_id=chat_id, message_id=msg_id, success=True
        )

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel=self.channel,
            connected=self._connected,
            last_event_at=self._last_event_at,
            pending_outbound=0,
        )

    # -- test helpers ------------------------------------------------------

    def push_inbound(self, event: MessageEvent) -> None:
        """Inject an inbound message synchronously from tests."""
        self._inbox.put_nowait(event)

    def outbox(self) -> list[_Outbound]:
        return list(self._outbox)

    def clear_outbox(self) -> None:
        self._outbox.clear()
