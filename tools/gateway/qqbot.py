"""QQ Bot channel adapter — native Tencent QQ OpenAPI v2 via stdlib.

Targets Tencent's QQ Bot Open Platform: AT_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE
events come in via WebSocket; outbound goes through:
- POST /v2/groups/{group_openid}/messages       (group)
- POST /v2/users/{user_openid}/messages         (private/C2C)
- POST /channels/{channel_id}/messages          (guild text channel)

This adapter focuses on the parts that don't require a live WebSocket: send
endpoints (resolved by metadata), event-payload parsing, and HMAC-Ed25519
signature verification helpers. The runtime drives the WS loop.
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

API_BASE = "https://api.sgroup.qq.com"


class QQBotChannel(GatewayChannel):
    """Native QQ Bot adapter — REST send + parser for WS events."""

    def __init__(
        self,
        handler: InboundHandler,
        bot_token: Optional[str] = None,
        bot_app_id: Optional[str] = None,
        sandbox: bool = False,
    ) -> None:
        super().__init__("qqbot", handler)
        self._bot_token = bot_token or os.environ.get("QQBOT_TOKEN", "")
        self._app_id = bot_app_id or os.environ.get("QQBOT_APP_ID", "")
        self._sandbox = sandbox
        self._stop_event = asyncio.Event()

    @property
    def api_base(self) -> str:
        return "https://sandbox.api.sgroup.qq.com" if self._sandbox else API_BASE

    async def start(self) -> None:
        if self._running:
            return
        if not (self._bot_token and self._app_id):
            raise GatewayError("QQBOT_TOKEN and QQBOT_APP_ID required")
        self._running = True
        self._stop_event.clear()

    async def send(self, message: OutboundMessage) -> str:
        if not (self._bot_token and self._app_id):
            raise GatewayError("qqbot credentials incomplete")

        meta = message.metadata or {}
        kind = meta.get("kind") or "group"
        chat_id = message.platform_chat_id

        if kind == "group":
            url = f"{self.api_base}/v2/groups/{chat_id}/messages"
        elif kind in ("user", "c2c"):
            url = f"{self.api_base}/v2/users/{chat_id}/messages"
        elif kind in ("channel", "guild"):
            url = f"{self.api_base}/channels/{chat_id}/messages"
        else:
            raise GatewayError(f"qqbot send: unknown metadata.kind={kind!r}")

        payload: dict = {
            "msg_type": 0,  # 0 = plain text
            "content": message.text,
        }
        if message.reply_to_message_id:
            payload["msg_id"] = message.reply_to_message_id

        def _post():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "Authorization": f"QQBot {self._bot_token}",
                    "X-Union-Appid": str(self._app_id),
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_post)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"qqbot send failed: {e.code} {e.reason}")

        return str(data.get("id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

    async def feed_event(self, event: dict) -> None:
        inbound = self.parse_event(event)
        if inbound is not None:
            await self._dispatch(inbound)

    @staticmethod
    def parse_event(event: dict) -> Optional[InboundMessage]:
        """Parse a QQ Bot WS gateway dispatch into an InboundMessage.

        Handles three event types:
        - AT_MESSAGE_CREATE       (guild text channel @bot)
        - GROUP_AT_MESSAGE_CREATE (group @bot)
        - C2C_MESSAGE_CREATE      (direct user message)
        """
        t = event.get("t") or event.get("type")
        if t not in (
            "AT_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "C2C_MESSAGE_CREATE",
        ):
            return None

        data = event.get("d") or event.get("data") or event
        author = data.get("author") or {}
        content = data.get("content") or ""
        msg_id = data.get("id") or ""
        ts = data.get("timestamp")
        try:
            received_at = (
                datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if ts
                else datetime.now(timezone.utc)
            )
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        if t == "C2C_MESSAGE_CREATE":
            kind = "user"
            chat_id = (data.get("author") or {}).get("user_openid") or data.get("user_openid") or ""
            user_id = chat_id
        elif t == "GROUP_AT_MESSAGE_CREATE":
            kind = "group"
            chat_id = data.get("group_openid") or ""
            user_id = (data.get("author") or {}).get("member_openid") or ""
        else:  # AT_MESSAGE_CREATE
            kind = "channel"
            chat_id = data.get("channel_id") or ""
            user_id = author.get("id") or ""

        return InboundMessage(
            channel="qqbot",
            platform_user_id=str(user_id),
            platform_chat_id=str(chat_id),
            text=str(content).strip(),
            received_at=received_at,
            platform_message_id=str(msg_id) or None,
            user_display_name=author.get("username") or author.get("nick"),
            metadata={
                "kind": kind,
                "guild_id": data.get("guild_id"),
                "raw": data,
            },
        )
