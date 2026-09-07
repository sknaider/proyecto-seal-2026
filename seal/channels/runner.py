"""Channel runner — orchestrates many adapters in a single event loop.

The runner owns:
    * adapter lifecycle (connect, disconnect, restart on fatal failure)
    * an inbound queue fed by every adapter's events()
    * dispatch hooks (callable per inbound event)
    * health aggregation across all adapters
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import MessageEvent


logger = logging.getLogger("seal.channels.runner")

InboundHook = Callable[[MessageEvent], Awaitable[None]]


class ChannelRunner:
    """Run N adapters concurrently and dispatch their inbound events."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._hooks: list[InboundHook] = []
        self._stopping = False

    # -- registration ------------------------------------------------------

    def register(self, adapter: ChannelAdapter) -> None:
        if adapter.channel in self._adapters:
            raise ValueError(f"adapter for {adapter.channel} already registered")
        self._adapters[adapter.channel] = adapter

    def unregister(self, channel: str) -> None:
        self._adapters.pop(channel, None)

    def add_inbound_hook(self, hook: InboundHook) -> None:
        self._hooks.append(hook)

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await asyncio.gather(*(a.connect() for a in self._adapters.values()))
        for channel, adapter in self._adapters.items():
            self._tasks[channel] = asyncio.create_task(
                self._consume(adapter), name=f"channel:{channel}"
            )

    async def stop(self) -> None:
        self._stopping = True
        for adapter in self._adapters.values():
            adapter.stop()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await asyncio.gather(
            *(a.disconnect() for a in self._adapters.values()),
            return_exceptions=True,
        )
        self._tasks.clear()

    # -- send fan-out ------------------------------------------------------

    async def send(self, channel: str, chat_id: str, text: str, **kwargs):
        adapter = self._adapters.get(channel)
        if adapter is None:
            raise KeyError(f"no adapter registered for {channel!r}")
        return await adapter.send(chat_id, text, **kwargs)

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, ChannelHealth]:
        return {ch: a.healthcheck() for ch, a in self._adapters.items()}

    # -- internals ---------------------------------------------------------

    async def _consume(self, adapter: ChannelAdapter) -> None:
        try:
            async for event in adapter.events():
                if self._stopping:
                    break
                await self._dispatch(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("adapter %s crashed", adapter.channel)

    async def _dispatch(self, event: MessageEvent) -> None:
        for hook in self._hooks:
            try:
                await hook(event)
            except Exception:
                logger.exception(
                    "inbound hook failed for channel=%s msg=%s",
                    event.channel,
                    event.message_id,
                )
