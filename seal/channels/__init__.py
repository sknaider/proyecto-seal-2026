"""SEAL Channels — multi-platform messaging infrastructure.

Provides a unified interface for receiving and sending messages across
external platforms (chat, email, webhooks). Each channel runs as an
adapter implementing ChannelAdapter; the runner orchestrates them in a
single asyncio event loop.

Public surface:
    ChannelAdapter   — abstract base for adapters
    MessageEvent     — normalized inbound message
    SendResult       — normalized outbound result
    ChannelRunner    — orchestrator
    CredentialLock   — token-scoped concurrency guard
"""
from __future__ import annotations

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import MessageEvent, SendResult
from seal.channels.lock import CredentialLock
from seal.channels.runner import ChannelRunner

__all__ = [
    "ChannelAdapter",
    "ChannelHealth",
    "MessageEvent",
    "SendResult",
    "ChannelRunner",
    "CredentialLock",
]
