"""Abstract base class for channel adapters.

A channel adapter wraps a single external platform (chat service, email,
webhook). Adapters expose an asyncio interface so a single runner can
multiplex many channels in one event loop.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

from seal.channels.event import Attachment, MessageEvent, SendResult


logger = logging.getLogger("seal.channels")


@dataclass(slots=True)
class ChannelHealth:
    """Health snapshot reported by an adapter."""

    channel: str
    connected: bool
    last_event_at: float | None = None
    last_error: str | None = None
    pending_outbound: int = 0
    fatal: bool = False


class ChannelAdapter(ABC):
    """Single-platform adapter contract.

    Lifecycle (called by ChannelRunner):
        connect()     -> open connection, start any background tasks
        events()      -> async iterator of inbound MessageEvent
        send()        -> outbound message to chat_id
        healthcheck() -> snapshot for monitoring
        disconnect()  -> graceful shutdown

    Implementations must be safe to instantiate without performing any
    network IO; defer all IO to connect().
    """

    #: Short stable identifier (e.g. "matrix", "telegram"). Lowercase.
    channel: str

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = config
        self._stop_event = asyncio.Event()

    # -- lifecycle ----------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection. Must be idempotent."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection and release resources. Must be idempotent."""

    # -- io -----------------------------------------------------------------

    @abstractmethod
    def events(self) -> AsyncIterator[MessageEvent]:
        """Yield inbound messages until stop() is called.

        Implementations should respect ``self._stop_event`` so the runner
        can shut down cleanly.
        """

    @abstractmethod
    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        attachments: tuple[Attachment, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> SendResult:
        """Send text + optional attachments to chat_id."""

    # -- observability ------------------------------------------------------

    @abstractmethod
    def healthcheck(self) -> ChannelHealth:
        """Return current health snapshot. Must not block."""

    # -- helpers for subclasses --------------------------------------------

    def stop(self) -> None:
        """Signal events() iterator to terminate."""
        self._stop_event.set()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()
