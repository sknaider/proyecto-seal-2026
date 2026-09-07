"""Viber bot channel adapter — native via stdlib + Viber REST API.

No viberbot SDK. Outbound uses POST /pa/send_message with X-Viber-Auth-Token.
Inbound is fed by Viber webhook events parsed with parse_webhook_event().
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage

API_BASE = "https://chatapi.viber.com/pa"


class ViberChannel(GatewayChannel):
    """Native Viber Public Account adapter."""

    def __init__(
        self,
        handler: InboundHandler,
        auth_token: Optional[str] = None,
        bot_name: Optional[str] = None,
        bot_avatar: Optional[str] = None,
    ) -> None:
        super().__init__("viber", handler)
        self._auth_token = auth_token or os.environ.get("VIBER_AUTH_TOKEN", "")
        self._bot_name = bot_name or os.environ.get("VIBER_BOT_NAME", "SEAL")
        self._bot_avatar = bot_avatar or os.environ.get("VIBER_BOT_AVATAR", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._auth_token:
            raise GatewayError("VIBER_AUTH_TOKEN required")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not self._auth_token:
            raise GatewayError("viber credentials incomplete")

        payload: dict = {
            "receiver": message.platform_chat_id,
            "min_api_version": 1,
            "sender": {"name": self._bot_name},
            "type": "text",
            "text": message.text,
        }
        if self._bot_avatar:
            payload["sender"]["avatar"] = self._bot_avatar

        def _post():
            req = urllib.request.Request(
                f"{API_BASE}/send_message",
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "X-Viber-Auth-Token": self._auth_token,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"viber send failed: {e.code} {e.reason}")

        # Viber returns {status, status_message, message_token, ...}
        if data.get("status") not in (0, "0"):
            raise GatewayError(f"viber rejected: {data.get('status_message', 'unknown')}")
        return str(data.get("message_token", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify Viber webhook X-Viber-Content-Signature (HMAC-SHA256 hex)."""
        if not self._auth_token:
            return False
        digest = hmac.new(self._auth_token.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, signature)

    async def feed_webhook_event(self, event: dict) -> None:
        inbound = self.parse_webhook_event(event)
        if inbound is not None:
            await self._dispatch(inbound)

    @staticmethod
    def parse_webhook_event(event: dict) -> Optional[InboundMessage]:
        """Parse a Viber webhook 'message' event into InboundMessage.

        Viber sends event types: subscribed, unsubscribed, conversation_started,
        message, seen, delivered, failed, webhook. We only convert 'message'
        with type='text' into inbound; others return None.
        """
        if event.get("event") != "message":
            return None
        msg = event.get("message") or {}
        if msg.get("type") != "text":
            return None
        text = msg.get("text") or ""
        if not text:
            return None

        sender = event.get("sender") or {}
        sender_id = sender.get("id") or ""

        ts_ms = event.get("timestamp")
        try:
            received_at = (
                datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                if ts_ms is not None
                else datetime.now(timezone.utc)
            )
        except (TypeError, ValueError):
            received_at = datetime.now(timezone.utc)

        return InboundMessage(
            channel="viber",
            platform_user_id=str(sender_id),
            platform_chat_id=str(sender_id),
            text=str(text),
            received_at=received_at,
            platform_message_id=str(event.get("message_token", "")) or None,
            user_display_name=sender.get("name"),
            metadata={
                "tracking_data": msg.get("tracking_data"),
                "country": sender.get("country"),
                "language": sender.get("language"),
                "raw": event,
            },
        )
