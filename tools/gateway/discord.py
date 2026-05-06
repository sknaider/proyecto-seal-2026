"""Discord channel adapter — native implementation using Discord's Gateway WebSocket and REST API.

Uses urllib + websockets stdlib equivalents so we keep the soul_native_first
rule intact. No third-party Discord SDK; we speak the protocol directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage

DISCORD_API = "https://discord.com/api/v10"
DISCORD_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"


class DiscordChannel(GatewayChannel):
    """Native Discord adapter — WebSocket gateway in, REST API out.

    Token is read from env DISCORD_BOT_TOKEN unless passed explicitly. The
    actual WebSocket consumer is wired in start() but the IO loop is left
    pluggable so unit tests can drive _dispatch directly via the parent class.
    """

    def __init__(
        self,
        handler: InboundHandler,
        token: Optional[str] = None,
        intents: int = 1 << 9 | 1 << 15,
    ) -> None:
        super().__init__("discord", handler)
        self._token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self._intents = intents
        self._ws_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._token:
            raise GatewayError("DISCORD_BOT_TOKEN not configured")
        self._running = True
        self._stop_event.clear()
        self._ws_task = asyncio.create_task(self._run_gateway())

    async def send(self, message: OutboundMessage) -> str:
        if not self._token:
            raise GatewayError("DISCORD_BOT_TOKEN not configured")
        url = f"{DISCORD_API}/channels/{message.platform_chat_id}/messages"
        payload = {"content": message.text}
        if message.reply_to_message_id:
            payload["message_reference"] = {"message_id": message.reply_to_message_id}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bot {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return str(data.get("id", ""))
        except urllib.error.HTTPError as e:
            raise GatewayError(f"discord send failed: {e.code} {e.reason}")

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None

    async def _run_gateway(self) -> None:
        """WebSocket gateway loop. Reconnects on disconnect until stop_event."""
        while not self._stop_event.is_set():
            try:
                await self._gateway_session()
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(2.0)

    async def _gateway_session(self) -> None:
        """One gateway session — placeholder hook for the WS implementation."""
        await self._stop_event.wait()

    @staticmethod
    def parse_message_create(payload: dict) -> InboundMessage:
        """Translate a Discord MESSAGE_CREATE event payload into InboundMessage."""
        d = payload.get("d", payload)
        author = d.get("author", {}) or {}
        return InboundMessage(
            channel="discord",
            platform_user_id=str(author.get("id", "")),
            platform_chat_id=str(d.get("channel_id", "")),
            text=str(d.get("content", "")),
            received_at=datetime.now(timezone.utc),
            platform_message_id=str(d.get("id", "")) or None,
            user_display_name=author.get("global_name") or author.get("username"),
            metadata={"guild_id": d.get("guild_id"), "raw": d},
        )
