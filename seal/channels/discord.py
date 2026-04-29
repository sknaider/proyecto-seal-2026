"""seal/channels/discord.py — Discord channel adapter, stdlib only.

Implements DiscordAdapter(ChannelAdapter) using the Discord REST API v10.
No external dependencies — pure urllib.request + json.

Config keys:
    bot_token       str   Discord Bot token (required)
    channel_ids     list  Channel IDs to listen on (required)
    poll_interval_s float Seconds between polls (default 2.0)
    filter_own      bool  Drop messages from this bot (default True)

Usage:
    adapter = DiscordAdapter({
        "bot_token": "Bot MTA...",
        "channel_ids": ["1234567890"],
    })
    await adapter.connect()
    async for event in adapter.events():
        print(event.text)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import AsyncIterator
from typing import Any, Mapping, Optional

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult

logger = logging.getLogger("seal.channels.discord")

_API = "https://discord.com/api/v10"
_DEFAULT_POLL_S = 2.0


class DiscordAdapter(ChannelAdapter):
    """Discord channel adapter — REST polling, no external deps.

    Discord Gateway WebSocket is not used; instead, we poll
    GET /channels/{id}/messages periodically with deduplication.
    Suitable for low-to-medium traffic bots.
    """

    channel = "discord"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        token = str(config["bot_token"])
        self._token        = token if token.startswith("Bot ") else f"Bot {token}"
        self._channel_ids: list[str] = list(config.get("channel_ids", []))  # type: ignore
        self._poll_s:  float = float(config.get("poll_interval_s", _DEFAULT_POLL_S))
        self._filter_own   = bool(config.get("filter_own", True))

        self._bot_id:       Optional[str] = None
        self._last_ids:     dict[str, str] = {}   # channel_id → last message snowflake
        self._lock          = threading.Lock()
        self._connected     = False
        self._last_event_at: Optional[float] = None
        self._last_error:   Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_event_loop()
        me = await loop.run_in_executor(None, self._get_me)
        self._bot_id = me.get("id")
        # Seed last_ids so we don't replay history on start
        for cid in self._channel_ids:
            msgs = await loop.run_in_executor(None, lambda c=cid: self._fetch_messages(c, limit=1))
            if msgs:
                self._last_ids[cid] = msgs[0]["id"]
        self._connected = True
        logger.info("DiscordAdapter connected (bot_id=%s)", self._bot_id)

    async def disconnect(self) -> None:
        self.stop()
        self._connected = False
        logger.info("DiscordAdapter disconnected")

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:
        loop = asyncio.get_event_loop()
        while not self.stopping:
            for cid in self._channel_ids:
                after = self._last_ids.get(cid)
                try:
                    msgs = await loop.run_in_executor(
                        None, lambda c=cid, a=after: self._fetch_messages(c, after=a)
                    )
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning("Discord poll error on %s: %s", cid, exc)
                    continue

                # Discord returns newest-first; reverse to process chronologically
                for msg in reversed(msgs):
                    self._last_ids[cid] = msg["id"]
                    if msg.get("type", 0) != 0:
                        continue  # skip system messages
                    if self._filter_own and msg.get("author", {}).get("id") == self._bot_id:
                        continue
                    evt = self._to_event(cid, msg)
                    self._last_event_at = time.time()
                    yield evt

            await asyncio.sleep(self._poll_s)

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: tuple[Attachment, ...] = (),
        metadata: Optional[Mapping[str, object]] = None,
    ) -> SendResult:
        payload: dict[str, Any] = {"content": text[:2000]}
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self._request("POST", f"/channels/{chat_id}/messages", payload)
            )
            return SendResult(channel="discord", chat_id=chat_id, message_id=resp["id"])
        except Exception as exc:
            return SendResult(channel="discord", chat_id=chat_id,
                              message_id="", success=False, error=str(exc))

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel       = "discord",
            connected     = self._connected,
            last_event_at = self._last_event_at,
            last_error    = self._last_error,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_me(self) -> dict:
        return self._request("GET", "/users/@me")

    def _fetch_messages(self, channel_id: str, *,
                        after: Optional[str] = None, limit: int = 50) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        qs = urllib.parse.urlencode(params)
        return self._request("GET", f"/channels/{channel_id}/messages?{qs}")

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None) -> Any:
        url  = f"{_API}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {
            "Authorization": self._token,
            "Content-Type":  "application/json",
            "User-Agent":    "SEAL/1.0",
        }
        req  = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _to_event(self, channel_id: str, msg: dict) -> MessageEvent:
        attachments = tuple(
            Attachment(kind="file", mime="application/octet-stream",
                       url=a.get("url"), filename=a.get("filename"),
                       size_bytes=a.get("size"))
            for a in msg.get("attachments", [])
        )
        return MessageEvent(
            channel     = "discord",
            chat_id     = channel_id,
            user_id     = msg["author"]["id"],
            text        = msg.get("content", ""),
            message_id  = msg["id"],
            attachments = attachments,
            reply_to    = (msg.get("message_reference") or {}).get("message_id"),
            is_dm       = False,
            metadata    = {"username": msg["author"].get("username", "")},
        )
