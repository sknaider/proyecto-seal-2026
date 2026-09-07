"""Microsoft Teams channel adapter — native Incoming/Outgoing webhooks via stdlib.

Most Teams bots can be wired through Incoming Webhooks (for outbound
notifications, typed as MessageCards) and Outgoing Webhooks (for inbound
@-mentions). This adapter implements both without microsoftteams or
botframework SDKs.
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


def _build_message_card(text: str, title: Optional[str] = None) -> dict:
    """Build a minimal MessageCard payload accepted by Teams Incoming Webhooks."""
    card: dict = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "text": text,
    }
    if title:
        card["title"] = title
    return card


class TeamsChannel(GatewayChannel):
    """Native Microsoft Teams adapter via Incoming/Outgoing webhooks.

    Outbound: each platform_chat_id is a Teams Incoming Webhook URL.
    Inbound: feed_outgoing_webhook(payload) when an Outgoing Webhook fires.
    """

    def __init__(
        self,
        handler: InboundHandler,
        default_webhook_url: Optional[str] = None,
    ) -> None:
        super().__init__("teams", handler)
        self._default_webhook_url = default_webhook_url or os.environ.get(
            "TEAMS_INCOMING_WEBHOOK_URL", ""
        )
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        target_url = message.platform_chat_id or self._default_webhook_url
        if not target_url:
            raise GatewayError("teams: incoming webhook URL required (platform_chat_id or env)")

        title = message.metadata.get("title") if message.metadata else None
        card = _build_message_card(message.text, title=title)

        def _post():
            req = urllib.request.Request(
                target_url,
                data=json.dumps(card).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                # Teams responds "1" on success, opaque body otherwise
                return resp.read().decode("utf-8", errors="replace").strip()

        try:
            body = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"teams send failed: {e.code} {e.reason}")

        return body

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
        """Parse a Teams outgoing-webhook activity payload into InboundMessage.

        Teams outgoing webhook payload (Bot Framework Activity v3) has shape:
        {
            "id": "...",
            "type": "message",
            "timestamp": "ISO 8601",
            "from": {"id": "...", "name": "..."},
            "conversation": {"id": "..."},
            "text": "raw text including <at>BotName</at> token",
            "channelData": {...}
        }
        """
        if payload.get("type") and payload.get("type") != "message":
            return None
        text = payload.get("text") or ""
        conversation = payload.get("conversation") or {}
        chat_id = str(conversation.get("id") or "")
        if not text or not chat_id:
            return None

        sender = payload.get("from") or {}
        ts_str = payload.get("timestamp") or payload.get("localTimestamp")
        try:
            received_at = (
                datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if ts_str
                else datetime.now(timezone.utc)
            )
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        return InboundMessage(
            channel="teams",
            platform_user_id=str(sender.get("id", "")),
            platform_chat_id=chat_id,
            text=str(text),
            received_at=received_at,
            platform_message_id=str(payload.get("id", "")) or None,
            user_display_name=sender.get("name"),
            metadata={
                "channel_data": payload.get("channelData"),
                "service_url": payload.get("serviceUrl"),
                "raw": payload,
            },
        )
