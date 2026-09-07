"""Matrix channel adapter — native Client-Server API r0/v3 over HTTP via stdlib.

No matrix-nio or third-party SDK. Uses urllib for HTTP and the long-poll /sync
endpoint for inbound. Outbound uses /rooms/{room}/send/m.room.message/{txn}.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage


class MatrixChannel(GatewayChannel):
    """Native Matrix adapter — Client-Server API r0/v3.

    Talks to a Matrix homeserver (Synapse, Conduit, Dendrite) over HTTP using
    only stdlib urllib. /sync long-poll feeds inbound messages.
    """

    def __init__(
        self,
        handler: InboundHandler,
        homeserver: Optional[str] = None,
        access_token: Optional[str] = None,
        user_id: Optional[str] = None,
        sync_timeout_ms: int = 30000,
    ) -> None:
        super().__init__("matrix", handler)
        self._homeserver = (
            homeserver or os.environ.get("MATRIX_HOMESERVER", "")
        ).rstrip("/")
        self._access_token = access_token or os.environ.get("MATRIX_ACCESS_TOKEN", "")
        self._user_id = user_id or os.environ.get("MATRIX_USER_ID", "")
        self._sync_timeout_ms = sync_timeout_ms
        self._next_batch: Optional[str] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._homeserver and self._access_token):
            raise GatewayError("MATRIX_HOMESERVER and MATRIX_ACCESS_TOKEN required")
        self._running = True
        self._stop_event.clear()
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not (self._homeserver and self._access_token):
            raise GatewayError("matrix credentials incomplete")

        room = message.platform_chat_id
        txn = uuid.uuid4().hex
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/"
            f"{urllib.parse.quote(room)}/send/m.room.message/{txn}"
        )
        body = {
            "msgtype": "m.text",
            "body": message.text,
        }
        if message.reply_to_message_id:
            body["m.relates_to"] = {
                "m.in_reply_to": {"event_id": message.reply_to_message_id}
            }

        def _put():
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                method="PUT",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_put)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"matrix send failed: {e.code} {e.reason}")

        return str(data.get("event_id", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sync_task = None

    async def _sync_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = await asyncio.to_thread(self._sync_once)
            except Exception:
                payload = None
                await asyncio.sleep(2.0)
                continue
            if payload is None:
                continue
            self._next_batch = payload.get("next_batch") or self._next_batch
            for inbound in self.parse_sync(payload):
                await self._dispatch(inbound)

    def _sync_once(self) -> Optional[dict]:
        params = {"timeout": str(self._sync_timeout_ms)}
        if self._next_batch:
            params["since"] = self._next_batch
        url = (
            f"{self._homeserver}/_matrix/client/v3/sync?"
            + urllib.parse.urlencode(params)
        )
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._sync_timeout_ms / 1000 + 5) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError:
            return None
        except urllib.error.URLError:
            return None

    @staticmethod
    def parse_sync(payload: dict) -> list[InboundMessage]:
        """Extract m.room.message events from a /sync response into InboundMessages."""
        out: list[InboundMessage] = []
        rooms = (payload.get("rooms") or {}).get("join") or {}
        for room_id, room_state in rooms.items():
            timeline = (room_state.get("timeline") or {}).get("events") or []
            for ev in timeline:
                if ev.get("type") != "m.room.message":
                    continue
                content = ev.get("content") or {}
                if content.get("msgtype") != "m.text":
                    continue
                ts_ms = ev.get("origin_server_ts") or 0
                try:
                    received_at = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                except (TypeError, ValueError):
                    received_at = datetime.now(timezone.utc)
                out.append(
                    InboundMessage(
                        channel="matrix",
                        platform_user_id=str(ev.get("sender", "")),
                        platform_chat_id=str(room_id),
                        text=str(content.get("body", "")),
                        received_at=received_at,
                        platform_message_id=str(ev.get("event_id", "")) or None,
                        user_display_name=None,
                        metadata={
                            "msgtype": content.get("msgtype"),
                            "format": content.get("format"),
                            "raw": ev,
                        },
                    )
                )
        return out
