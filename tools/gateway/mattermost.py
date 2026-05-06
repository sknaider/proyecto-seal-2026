"""Mattermost channel adapter — native v4 REST + WebSocket via stdlib.

No mattermostdriver SDK. Outbound uses POST /api/v4/posts. Inbound is fed
either by an externally driven WebSocket consumer that pipes events through
parse_post_event(), or by an HTTP webhook (Mattermost outgoing webhook) that
pipes form-encoded payloads through parse_outgoing_webhook().
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage


class MattermostChannel(GatewayChannel):
    """Native Mattermost adapter via REST API v4."""

    def __init__(
        self,
        handler: InboundHandler,
        server_url: Optional[str] = None,
        access_token: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> None:
        super().__init__("mattermost", handler)
        self._server = (server_url or os.environ.get("MATTERMOST_URL", "")).rstrip("/")
        self._token = access_token or os.environ.get("MATTERMOST_TOKEN", "")
        self._team_id = team_id or os.environ.get("MATTERMOST_TEAM_ID", "")
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._server and self._token):
            raise GatewayError("MATTERMOST_URL and MATTERMOST_TOKEN required")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not (self._server and self._token):
            raise GatewayError("mattermost credentials incomplete")

        url = f"{self._server}/api/v4/posts"
        body: dict = {
            "channel_id": message.platform_chat_id,
            "message": message.text,
        }
        if message.reply_to_message_id:
            body["root_id"] = message.reply_to_message_id

        def _post():
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"mattermost send failed: {e.code} {e.reason}")

        return str(data.get("id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    async def feed_post_event(self, event: dict) -> None:
        """Push a Mattermost WebSocket 'posted' event into the handler."""
        inbound = self.parse_post_event(event)
        if inbound is not None:
            await self._dispatch(inbound)

    async def feed_outgoing_webhook(self, form: dict) -> None:
        """Push an outgoing webhook payload into the handler."""
        inbound = self.parse_outgoing_webhook(form)
        if inbound is not None:
            await self._dispatch(inbound)

    @staticmethod
    def parse_post_event(event: dict) -> Optional[InboundMessage]:
        """Parse a Mattermost WebSocket event of type 'posted' into InboundMessage."""
        if event.get("event") != "posted":
            return None
        data = event.get("data") or {}
        post_str = data.get("post")
        if not post_str:
            return None
        try:
            post = json.loads(post_str) if isinstance(post_str, str) else post_str
        except json.JSONDecodeError:
            return None

        ts_ms = post.get("create_at") or 0
        try:
            received_at = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            received_at = datetime.now(timezone.utc)

        return InboundMessage(
            channel="mattermost",
            platform_user_id=str(post.get("user_id", "")),
            platform_chat_id=str(post.get("channel_id", "")),
            text=str(post.get("message", "")),
            received_at=received_at,
            platform_message_id=str(post.get("id", "")) or None,
            user_display_name=data.get("sender_name"),
            metadata={
                "channel_name": data.get("channel_name"),
                "channel_type": data.get("channel_type"),
                "team_id": data.get("team_id"),
                "raw": post,
            },
        )

    @staticmethod
    def parse_outgoing_webhook(form: dict) -> Optional[InboundMessage]:
        """Parse a Mattermost outgoing-webhook form payload into InboundMessage."""
        text = form.get("text") or ""
        channel_id = form.get("channel_id") or ""
        if not text or not channel_id:
            return None
        return InboundMessage(
            channel="mattermost",
            platform_user_id=str(form.get("user_id", "")),
            platform_chat_id=str(channel_id),
            text=str(text),
            received_at=datetime.now(timezone.utc),
            platform_message_id=str(form.get("post_id", "")) or None,
            user_display_name=form.get("user_name"),
            metadata={
                "channel_name": form.get("channel_name"),
                "team_domain": form.get("team_domain"),
                "trigger_word": form.get("trigger_word"),
            },
        )
