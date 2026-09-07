"""Bluesky channel adapter — native AT Protocol XRPC over HTTP via stdlib.

No atproto SDK. Outbound creates a record at app.bsky.feed.post via
com.atproto.repo.createRecord. Inbound polls notifications +
app.bsky.feed.getAuthorFeed for replies/mentions.
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

DEFAULT_PDS = "https://bsky.social"


class BlueskyChannel(GatewayChannel):
    """Native Bluesky/AT Protocol adapter."""

    def __init__(
        self,
        handler: InboundHandler,
        identifier: Optional[str] = None,
        password: Optional[str] = None,
        pds_url: Optional[str] = None,
        poll_interval_s: float = 30.0,
    ) -> None:
        super().__init__("bluesky", handler)
        self._identifier = identifier or os.environ.get("BLUESKY_IDENTIFIER", "")
        self._password = password or os.environ.get("BLUESKY_PASSWORD", "")
        self._pds = (pds_url or os.environ.get("BLUESKY_PDS", DEFAULT_PDS)).rstrip("/")
        self._access_jwt: Optional[str] = None
        self._refresh_jwt: Optional[str] = None
        self._did: Optional[str] = None
        self._handle: Optional[str] = None
        self._poll_interval_s = poll_interval_s
        self._last_seen_indexed_at: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._identifier and self._password):
            raise GatewayError("BLUESKY_IDENTIFIER and BLUESKY_PASSWORD required")
        await asyncio.to_thread(self._login)
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not self._access_jwt or not self._did:
            raise GatewayError("bluesky session not authenticated")

        record = {
            "$type": "app.bsky.feed.post",
            "text": message.text,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if message.reply_to_message_id:
            record["reply"] = {
                "root": {"uri": message.reply_to_message_id},
                "parent": {"uri": message.reply_to_message_id},
            }

        payload = {
            "repo": self._did,
            "collection": "app.bsky.feed.post",
            "record": record,
        }

        def _create():
            req = urllib.request.Request(
                f"{self._pds}/xrpc/com.atproto.repo.createRecord",
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._access_jwt}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())

        try:
            data = await asyncio.to_thread(_create)
        except urllib.error.HTTPError as e:
            raise GatewayError(f"bluesky send failed: {e.code} {e.reason}")

        return str(data.get("uri", ""))

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None

    def _login(self) -> None:
        url = f"{self._pds}/xrpc/com.atproto.server.createSession"
        body = {"identifier": self._identifier, "password": self._password}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise GatewayError(f"bluesky login failed: {e.code} {e.reason}")

        self._access_jwt = data.get("accessJwt")
        self._refresh_jwt = data.get("refreshJwt")
        self._did = data.get("did")
        self._handle = data.get("handle")
        if not (self._access_jwt and self._did):
            raise GatewayError("bluesky login response missing accessJwt/did")

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                notifications = await asyncio.to_thread(self._list_notifications)
                for inbound in self.parse_notifications(notifications, self._last_seen_indexed_at):
                    await self._dispatch(inbound)
                # Track newest indexedAt for next poll cursor.
                latest = self._latest_indexed_at(notifications)
                if latest:
                    self._last_seen_indexed_at = latest
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def _list_notifications(self) -> dict:
        url = f"{self._pds}/xrpc/app.bsky.notification.listNotifications"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._access_jwt}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError:
            return {}
        except urllib.error.URLError:
            return {}

    @staticmethod
    def _latest_indexed_at(payload: dict) -> Optional[str]:
        notifications = payload.get("notifications") or []
        if not notifications:
            return None
        return max((n.get("indexedAt") or "") for n in notifications) or None

    @staticmethod
    def parse_notifications(payload: dict, since: Optional[str]) -> list[InboundMessage]:
        """Translate Bluesky notifications into InboundMessages.

        Considers notifications with reason 'mention' or 'reply'. The `since`
        cursor (last seen indexedAt ISO string) filters older ones.
        """
        out: list[InboundMessage] = []
        for n in payload.get("notifications") or []:
            reason = n.get("reason")
            if reason not in ("mention", "reply"):
                continue
            indexed_at = n.get("indexedAt") or ""
            if since and indexed_at <= since:
                continue
            author = n.get("author") or {}
            record = n.get("record") or {}
            uri = n.get("uri") or ""
            try:
                received_at = (
                    datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
                    if indexed_at
                    else datetime.now(timezone.utc)
                )
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            except Exception:
                received_at = datetime.now(timezone.utc)

            out.append(
                InboundMessage(
                    channel="bluesky",
                    platform_user_id=str(author.get("did", "")),
                    platform_chat_id=str(author.get("did", "")),
                    text=str(record.get("text", "")),
                    received_at=received_at,
                    platform_message_id=uri or None,
                    user_display_name=author.get("displayName") or author.get("handle"),
                    metadata={"reason": reason, "raw": n},
                )
            )
        return out
