"""seal/channels/slack.py — Slack channel adapter, stdlib only.

Implements SlackAdapter(ChannelAdapter) using the Slack Web API.
No external dependencies — pure urllib.request + json.

Config keys:
    bot_token       str   Slack Bot token (xoxb-...) [required]
    channel_ids     list  Channel IDs to listen on, e.g. ["C01234567"] [required]
    poll_interval_s float Seconds between polls (default 3.0)
    filter_own      bool  Drop messages from this bot (default True)

Usage:
    adapter = SlackAdapter({
        "bot_token": "xoxb-...",
        "channel_ids": ["C01234567"],
    })
    await adapter.connect()
    async for event in adapter.events():
        print(event.text)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from typing import Any, Mapping, Optional

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult

logger = logging.getLogger("seal.channels.slack")

_API = "https://slack.com/api"
_DEFAULT_POLL_S = 3.0


class SlackAdapter(ChannelAdapter):
    """Slack channel adapter — Web API polling, no external deps.

    Polls conversations.history per channel with `oldest` cursor for
    incremental fetch. Sends via chat.postMessage.
    """

    channel = "slack"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        self._token        = str(config["bot_token"])
        self._channel_ids: list[str] = list(config.get("channel_ids", []))  # type: ignore
        self._poll_s       = float(config.get("poll_interval_s", _DEFAULT_POLL_S))
        self._filter_own   = bool(config.get("filter_own", True))

        self._bot_id:        Optional[str] = None
        self._oldest:        dict[str, str] = {}   # channel_id → last ts
        self._connected      = False
        self._last_event_at: Optional[float] = None
        self._last_error:    Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, self._auth_test)
        self._bot_id = info.get("user_id")
        # Seed oldest so we don't replay history
        now_ts = str(time.time())
        for cid in self._channel_ids:
            self._oldest[cid] = now_ts
        self._connected = True
        logger.info("SlackAdapter connected (bot_id=%s)", self._bot_id)

    async def disconnect(self) -> None:
        self.stop()
        self._connected = False
        logger.info("SlackAdapter disconnected")

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:
        loop = asyncio.get_event_loop()
        while not self.stopping:
            for cid in self._channel_ids:
                oldest = self._oldest.get(cid, "0")
                try:
                    result = await loop.run_in_executor(
                        None, lambda c=cid, o=oldest: self._history(c, o)
                    )
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning("Slack poll error on %s: %s", cid, exc)
                    continue

                msgs = result.get("messages", [])
                # Slack returns newest-first; reverse for chronological order
                for msg in reversed(msgs):
                    ts = msg.get("ts", "")
                    if not ts or ts <= oldest:
                        continue
                    self._oldest[cid] = ts
                    if msg.get("subtype"):
                        continue  # skip bot_message, channel_join, etc.
                    if self._filter_own and msg.get("user") == self._bot_id:
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
        payload: dict[str, Any] = {"channel": chat_id, "text": text}
        if reply_to:
            payload["thread_ts"] = reply_to
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self._post("chat.postMessage", payload)
            )
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "unknown"))
            return SendResult(channel="slack", chat_id=chat_id,
                              message_id=resp["ts"])
        except Exception as exc:
            return SendResult(channel="slack", chat_id=chat_id,
                              message_id="", success=False, error=str(exc))

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel       = "slack",
            connected     = self._connected,
            last_event_at = self._last_event_at,
            last_error    = self._last_error,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _auth_test(self) -> dict:
        return self._post("auth.test", {})

    def _history(self, channel_id: str, oldest: str) -> dict:
        params = {
            "channel": channel_id,
            "oldest":  oldest,
            "limit":   50,
            "inclusive": False,
        }
        qs = urllib.parse.urlencode(params)
        return self._get(f"conversations.history?{qs}")

    def _get(self, endpoint: str) -> dict:
        url = f"{_API}/{endpoint}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._token}",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _post(self, method: str, payload: dict) -> dict:
        url  = f"{_API}/{method}"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json; charset=utf-8",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _to_event(self, channel_id: str, msg: dict) -> MessageEvent:
        files = msg.get("files", [])
        attachments = tuple(
            Attachment(kind="file", mime=f.get("mimetype", "application/octet-stream"),
                       url=f.get("url_private"), filename=f.get("name"),
                       size_bytes=f.get("size"))
            for f in files
        )
        return MessageEvent(
            channel    = "slack",
            chat_id    = channel_id,
            user_id    = msg.get("user", ""),
            text       = msg.get("text", ""),
            message_id = msg.get("ts", ""),
            attachments= attachments,
            reply_to   = msg.get("thread_ts") if msg.get("thread_ts") != msg.get("ts") else None,
            is_dm      = channel_id.startswith("D"),
        )
