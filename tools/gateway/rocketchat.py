"""Rocket.Chat channel adapter — native REST + outgoing webhook via stdlib.

No rocketchat_API SDK. Outbound uses POST /api/v1/chat.postMessage with
X-Auth-Token / X-User-Id. Inbound is fed via parse_outgoing_webhook() from
Rocket.Chat outgoing integrations (HTTP webhook with JSON body).
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


class RocketChatChannel(GatewayChannel):
    """Native Rocket.Chat adapter via REST + outgoing webhooks."""

    def __init__(
        self,
        handler: InboundHandler,
        server_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        super().__init__("rocketchat", handler)
        self._server = (server_url or os.environ.get("ROCKETCHAT_URL", "")).rstrip("/")
        self._auth_token = auth_token or os.environ.get("ROCKETCHAT_AUTH_TOKEN", "")
        self._user_id = user_id or os.environ.get("ROCKETCHAT_USER_ID", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._server and self._auth_token and self._user_id):
            raise GatewayError(
                "ROCKETCHAT_URL, ROCKETCHAT_AUTH_TOKEN and ROCKETCHAT_USER_ID required"
            )
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not (self._server and self._auth_token and self._user_id):
            raise GatewayError("rocketchat credentials incomplete")

        url = f"{self._server}/api/v1/chat.postMessage"
        body: dict = {
            "channel": message.platform_chat_id,
            "text": message.text,
        }
        if message.reply_to_message_id:
            body["tmid"] = message.reply_to_message_id

        def _post():
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "X-Auth-Token": self._auth_token,
                    "X-User-Id": self._user_id,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"rocketchat send failed: {e.code} {e.reason}")

        if not data.get("success", True):
            raise GatewayError(f"rocketchat rejected: {data.get('error', 'unknown')}")
        msg = data.get("message") or {}
        return str(msg.get("_id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    async def feed_outgoing_webhook(self, payload: dict) -> None:
        inbound = self.parse_outgoing_webhook(payload)
        if inbound is not None:
            await self._dispatch(inbound)

    @staticmethod
    def parse_outgoing_webhook(payload: dict) -> Optional[InboundMessage]:
        """Parse Rocket.Chat outgoing webhook JSON payload into InboundMessage.

        Rocket.Chat outgoing webhooks send: {token, channel_id, channel_name,
        timestamp, user_id, user_name, text, ...}. We normalize that into
        the gateway's InboundMessage contract.
        """
        text = payload.get("text") or payload.get("message_raw") or ""
        channel_id = payload.get("channel_id") or payload.get("channel_name") or ""
        if not text or not channel_id:
            return None

        ts = payload.get("timestamp")
        try:
            if isinstance(ts, (int, float)):
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
            channel="rocketchat",
            platform_user_id=str(payload.get("user_id", "")),
            platform_chat_id=str(channel_id),
            text=str(text),
            received_at=received_at,
            platform_message_id=str(payload.get("message_id", "")) or None,
            user_display_name=payload.get("user_name"),
            metadata={
                "channel_name": payload.get("channel_name"),
                "trigger_word": payload.get("trigger_word"),
                "bot": payload.get("bot"),
            },
        )
