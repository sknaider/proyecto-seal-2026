"""LINE Messaging API channel adapter — native via stdlib.

No line-bot-sdk. Outbound uses POST /v2/bot/message/reply (with replyToken)
and POST /v2/bot/message/push (without). Inbound is fed via webhook events
parsed with parse_webhook_event(). Webhook signature verification is
provided as verify_signature() so the runtime's HTTP server can authenticate
incoming hits.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage

API_BASE = "https://api.line.me/v2/bot"


class LineChannel(GatewayChannel):
    """Native LINE Messaging API adapter."""

    def __init__(
        self,
        handler: InboundHandler,
        channel_access_token: Optional[str] = None,
        channel_secret: Optional[str] = None,
    ) -> None:
        super().__init__("line", handler)
        self._access_token = channel_access_token or os.environ.get(
            "LINE_CHANNEL_ACCESS_TOKEN", ""
        )
        self._channel_secret = channel_secret or os.environ.get("LINE_CHANNEL_SECRET", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._access_token:
            raise GatewayError("LINE_CHANNEL_ACCESS_TOKEN required")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not self._access_token:
            raise GatewayError("line credentials incomplete")

        reply_token = (message.metadata or {}).get("reply_token")
        if reply_token:
            url = f"{API_BASE}/message/reply"
            payload = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": message.text}],
            }
        else:
            url = f"{API_BASE}/message/push"
            payload = {
                "to": message.platform_chat_id,
                "messages": [{"type": "text", "text": message.text}],
            }

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
                return resp.read().decode("utf-8", errors="replace")

        try:
            await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"line send failed: {e.code} {e.reason}")

        return reply_token or ""

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    async def feed_webhook_event(self, event: dict) -> None:
        inbound = self.parse_webhook_event(event)
        if inbound is not None:
            await self._dispatch(inbound)

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """Verify a LINE webhook X-Line-Signature header (HMAC-SHA256, base64)."""
        if not self._channel_secret:
            return False
        expected = base64.b64encode(
            hmac.new(self._channel_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    def parse_webhook_event(event: dict) -> Optional[InboundMessage]:
        """Parse a single LINE webhook event into InboundMessage.

        Only message/text events are produced. Non-text or non-message events
        return None so the runtime can skip them.
        """
        if event.get("type") != "message":
            return None
        msg = event.get("message") or {}
        if msg.get("type") != "text":
            return None

        source = event.get("source") or {}
        # LINE source can be user/group/room — pick whichever id is present.
        chat_id = source.get("groupId") or source.get("roomId") or source.get("userId") or ""
        user_id = source.get("userId") or ""

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
            channel="line",
            platform_user_id=str(user_id),
            platform_chat_id=str(chat_id),
            text=str(msg.get("text", "")),
            received_at=received_at,
            platform_message_id=str(msg.get("id", "")) or None,
            user_display_name=None,
            metadata={
                "reply_token": event.get("replyToken"),
                "source_type": source.get("type"),
                "raw": event,
            },
        )
