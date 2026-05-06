"""Zoom Team Chat channel adapter — native via stdlib + Zoom REST API.

No Zoom SDK. Outbound uses POST /chat/users/{userId}/messages. Inbound is fed
via Zoom webhook events (chat_message.sent) parsed with parse_webhook_event().
Webhook signature uses HMAC-SHA256 of "v0:{ts}:{body}" with the Zoom secret
token, returned as 'v0=hex' per Zoom's documented scheme.
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

API_BASE = "https://api.zoom.us/v2"


class ZoomChannel(GatewayChannel):
    """Native Zoom Team Chat adapter."""

    def __init__(
        self,
        handler: InboundHandler,
        access_token: Optional[str] = None,
        from_user_id: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        super().__init__("zoom", handler)
        self._access_token = access_token or os.environ.get("ZOOM_ACCESS_TOKEN", "")
        self._from_user_id = from_user_id or os.environ.get("ZOOM_USER_ID", "me")
        self._webhook_secret = webhook_secret or os.environ.get("ZOOM_WEBHOOK_SECRET", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._access_token:
            raise GatewayError("ZOOM_ACCESS_TOKEN required")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not self._access_token:
            raise GatewayError("zoom credentials incomplete")

        url = f"{API_BASE}/chat/users/{self._from_user_id}/messages"
        meta = message.metadata or {}
        target = message.platform_chat_id

        payload: dict = {"message": message.text}
        if meta.get("channel_jid"):
            payload["to_channel"] = meta["channel_jid"]
        elif target.startswith("#"):
            payload["to_channel"] = target.lstrip("#")
        else:
            payload["to_contact"] = target

        if message.reply_to_message_id:
            payload["reply_main_message_id"] = message.reply_to_message_id

        def _post():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"zoom send failed: {e.code} {e.reason}")

        return str(data.get("id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    def verify_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        """Verify Zoom webhook signature: 'v0=' + hmac_sha256(secret, 'v0:{ts}:{body}')."""
        if not self._webhook_secret:
            return False
        message = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
        digest = hmac.new(
            self._webhook_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        expected = f"v0={digest}"
        return hmac.compare_digest(expected, signature)

    async def feed_webhook_event(self, event: dict) -> None:
        inbound = self.parse_webhook_event(event)
        if inbound is not None:
            await self._dispatch(inbound)

    @staticmethod
    def parse_webhook_event(event: dict) -> Optional[InboundMessage]:
        """Parse a Zoom webhook event of type 'chat_message.sent' into InboundMessage."""
        if event.get("event") != "chat_message.sent":
            return None
        payload = event.get("payload") or {}
        obj = payload.get("object") or {}

        message_text = obj.get("message") or ""
        if not message_text:
            return None

        sender_id = obj.get("sender") or obj.get("sender_id") or ""
        sender_name = obj.get("sender_display_name")
        # Zoom sometimes sends channel_id, sometimes to_jid (1:1 chat).
        chat_id = obj.get("channel_id") or obj.get("to_jid") or sender_id

        ts = event.get("event_ts") or obj.get("date_time")
        try:
            if isinstance(ts, (int, float)):
                # event_ts is milliseconds since epoch
                received_at = datetime.fromtimestamp(float(ts) / 1000, tz=timezone.utc)
            elif isinstance(ts, str):
                received_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            else:
                received_at = datetime.now(timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        return InboundMessage(
            channel="zoom",
            platform_user_id=str(sender_id),
            platform_chat_id=str(chat_id),
            text=str(message_text),
            received_at=received_at,
            platform_message_id=str(obj.get("message_id", "")) or None,
            user_display_name=sender_name,
            metadata={
                "channel_id": obj.get("channel_id"),
                "to_jid": obj.get("to_jid"),
                "raw": obj,
            },
        )
