"""Abstract base for SEAL gateway channels.

A GatewayChannel is the contract every platform adapter implements to bridge
external messaging platforms (Telegram, Discord, Slack, WhatsApp, etc) with
the SEAL agent runtime. Concrete channels handle the platform-specific transport
and translate to/from the InboundMessage / OutboundMessage contract.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional


class GatewayError(Exception):
    """Raised when a gateway operation fails irrecoverably."""


@dataclass(frozen=True)
class InboundMessage:
    """Normalized message arriving from any external platform."""

    channel: str
    platform_user_id: str
    platform_chat_id: str
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    platform_message_id: Optional[str] = None
    user_display_name: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundMessage:
    """Normalized message leaving SEAL toward any external platform."""

    channel: str
    platform_chat_id: str
    text: str
    reply_to_message_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


InboundHandler = Callable[[InboundMessage], Awaitable[Optional[OutboundMessage]]]


class GatewayChannel(ABC):
    """Abstract contract every channel adapter implements.

    Concrete subclasses (TelegramChannel, DiscordChannel, ...) wire platform-
    specific transports to this contract. The runtime calls start() to begin
    listening, send() to deliver outbound messages, and close() to shut down.
    """

    name: str

    def __init__(self, name: str, handler: InboundHandler) -> None:
        self.name = name
        self._handler = handler
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Begin listening for inbound messages. Idempotent."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> str:
        """Deliver an outbound message. Returns platform message id."""

    @abstractmethod
    async def close(self) -> None:
        """Stop listening and release resources. Idempotent."""

    @property
    def running(self) -> bool:
        return self._running

    async def _dispatch(self, inbound: InboundMessage) -> None:
        """Subclasses call this for every inbound message; routes to handler."""
        response = await self._handler(inbound)
        if response is not None:
            await self.send(response)

    async def __aenter__(self) -> "GatewayChannel":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
