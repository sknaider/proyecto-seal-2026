"""Slack channel adapter — native Slack Web API + Events API via stdlib.

Uses the Web API (chat.postMessage) for outbound and parses Events API
payloads for inbound. The Events API HTTP receiver is left pluggable so the
runtime can mount it on whichever HTTP server it already has.
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

SLACK_API = "https://slack.com/api"


class SlackChannel(GatewayChannel):
    """Native Slack adapter — Web API for send, Events API for receive."""

    def __init__(
        self,
        handler: InboundHandler,
        bot_token: Optional[str] = None,
        signing_secret: Optional[str] = None,
    ) -> None:
        super().__init__("slack", handler)
        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._signing_secret = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._bot_token:
            raise GatewayError("SLACK_BOT_TOKEN not configured")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not self._bot_token:
            raise GatewayError("SLACK_BOT_TOKEN not configured")
        url = f"{SLACK_API}/chat.postMessage"
        payload = {
            "channel": message.platform_chat_id,
            "text": message.text,
        }
        if message.reply_to_message_id:
            payload["thread_ts"] = message.reply_to_message_id
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise GatewayError(f"slack send failed: {e.code} {e.reason}")

        if not data.get("ok"):
            raise GatewayError(f"slack rejected message: {data.get('error', 'unknown')}")
        return str(data.get("ts", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    async def handle_event_payload(self, payload: dict) -> Optional[dict]:
        """Process a Slack Events API HTTP body. Returns response body if any.

        For url_verification challenges, returns {"challenge": ...}. For
        message events, dispatches to the handler and returns None.
        """
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        if payload.get("type") == "event_callback":
            event = payload.get("event") or {}
            if event.get("type") == "message" and not event.get("bot_id"):
                inbound = self.parse_message_event(event)
                await self._dispatch(inbound)

        return None

    @staticmethod
    def parse_message_event(event: dict) -> InboundMessage:
        """Translate a Slack 'message' event into an InboundMessage."""
        return InboundMessage(
            channel="slack",
            platform_user_id=str(event.get("user", "")),
            platform_chat_id=str(event.get("channel", "")),
            text=str(event.get("text", "")),
            received_at=datetime.now(timezone.utc),
            platform_message_id=str(event.get("ts", "")) or None,
            user_display_name=event.get("username"),
            metadata={
                "team": event.get("team"),
                "thread_ts": event.get("thread_ts"),
                "raw": event,
            },
        )
