"""SEAL WhatsApp channel adapter — Evolution API v2, stdlib only.

Implements WhatsAppAdapter(ChannelAdapter) using the Evolution API HTTP REST
interface.  No external dependencies — pure urllib.request + json.

Config keys (passed as ``config`` dict):
    base_url       str    Evolution API base URL, e.g. "http://localhost:8080"
    instance_name  str    Evolution instance name, e.g. "seal-wa"
    api_key        str    API key set in Evolution API (apikey header)
    poll_interval  float  Seconds between message polls (default 2.0)
    since_ts       int    Unix timestamp to start polling from (default: now)
    ignore_from_me bool   Drop outbound messages from this instance (default True)

Usage:
    adapter = WhatsAppAdapter({
        "base_url":      "http://localhost:8080",
        "instance_name": "seal-wa",
        "api_key":       "my-api-key",
    })
    await adapter.connect()
    async for event in adapter.events():
        print(event.text, event.user_id)

    await adapter.send("5521912345678@s.whatsapp.net", "hola!")

Evolution API endpoints used:
    GET  /instance/connectionState/{instance}    — verify connection on connect()
    POST /message/sendText/{instance}            — outbound text
    POST /message/findMessages/{instance}        — poll inbound messages
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult

logger = logging.getLogger("seal.channels.whatsapp")

_DEFAULT_POLL_INTERVAL = 2.0
_REQUEST_TIMEOUT = 10


class WhatsAppAdapter(ChannelAdapter):
    """WhatsApp channel adapter powered by Evolution API v2.

    Network IO runs in a thread-pool executor so the asyncio event loop
    stays unblocked during HTTP calls and poll sleeps.
    """

    channel = "whatsapp"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        self._base_url: str = str(config["base_url"]).rstrip("/")
        self._instance: str = str(config["instance_name"])
        self._api_key: str = str(config["api_key"])
        self._poll_interval: float = float(
            config.get("poll_interval", _DEFAULT_POLL_INTERVAL)
        )
        self._ignore_from_me: bool = bool(config.get("ignore_from_me", True))

        self._connected: bool = False
        self._last_event_at: Optional[float] = None
        self._last_error: Optional[str] = None

        # Only surface messages newer than this timestamp (seconds).
        self._since_ts: int = int(config.get("since_ts", int(time.time())))
        # De-duplicate within a polling window.
        self._seen_ids: set[str] = set()

        self._txn_lock = threading.Lock()
        self._txn_counter: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Verify instance is open on the Evolution API server."""
        if self._connected:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._check_connection)
        self._connected = True
        logger.info("WhatsAppAdapter connected — instance '%s'", self._instance)

    async def disconnect(self) -> None:
        """Stop polling and mark as disconnected."""
        if not self._connected:
            return
        self.stop()
        self._connected = False
        logger.info("WhatsAppAdapter disconnected — instance '%s'", self._instance)

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:  # type: ignore[override]
        """Yield inbound MessageEvents by polling Evolution API."""
        loop = asyncio.get_event_loop()
        backoff = 1.0
        while not self.stopping:
            try:
                msgs = await loop.run_in_executor(None, self._poll_messages)
                self._last_event_at = time.time()
                self._last_error = None
                backoff = 1.0
                for raw in msgs:
                    evt = self._parse_message(raw)
                    if evt is not None:
                        yield evt
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning(
                    "WhatsAppAdapter poll error: %s — retrying in %.0fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            # Interruptible sleep — exits immediately if stop() is called.
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                break  # stop was signaled
            except asyncio.TimeoutError:
                pass  # normal — keep polling

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: tuple[Attachment, ...] = (),
        metadata: Optional[Mapping[str, object]] = None,
    ) -> SendResult:
        """POST /message/sendText/{instance} — send text to a WhatsApp JID or phone."""
        with self._txn_lock:
            self._txn_counter += 1
            local_counter = self._txn_counter

        url = f"{self._base_url}/message/sendText/{self._instance}"
        body: dict[str, Any] = {"number": chat_id, "text": text}

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self._request("POST", url, body)
            )
            msg_id = resp.get("key", {}).get("id") or f"wa_{local_counter}"
            return SendResult(
                channel=self.channel,
                chat_id=chat_id,
                message_id=msg_id,
                success=True,
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("WhatsAppAdapter send failed to %s: %s", chat_id, exc)
            return SendResult(
                channel=self.channel,
                chat_id=chat_id,
                message_id="",
                success=False,
                error=str(exc),
            )

    # ── observability ─────────────────────────────────────────────────────────

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel=self.channel,
            connected=self._connected,
            last_event_at=self._last_event_at,
            last_error=self._last_error,
        )

    # ── Evolution API helpers (blocking — run in executor) ─────────────────────

    def _check_connection(self) -> None:
        """GET /instance/connectionState/{instance} — raise if not 'open'."""
        url = f"{self._base_url}/instance/connectionState/{self._instance}"
        resp = self._request("GET", url)
        state = resp.get("instance", {}).get("state", "")
        if state != "open":
            raise RuntimeError(
                f"WhatsApp instance '{self._instance}' state={state!r} (expected 'open')"
            )

    def _poll_messages(self) -> list[dict]:
        """POST /message/findMessages/{instance} — return new messages since _since_ts.

        Deduplicates by message ID so the same message is never yielded twice even
        if it appears in multiple overlapping polling windows.
        """
        url = f"{self._base_url}/message/findMessages/{self._instance}"
        body: dict[str, Any] = {
            "where": {"messageTimestamp": {"gte": self._since_ts}},
            "count": 50,
            "page": 1,
        }
        resp = self._request("POST", url, body)

        raw_msgs = resp.get("messages", {})
        if isinstance(raw_msgs, list):
            records = raw_msgs
        else:
            records = raw_msgs.get("records", [])

        new_msgs: list[dict] = []
        for msg in records:
            msg_id = msg.get("key", {}).get("id", "")
            if msg_id and msg_id in self._seen_ids:
                continue
            if msg_id:
                self._seen_ids.add(msg_id)
            new_msgs.append(msg)

        if new_msgs:
            latest = max(m.get("messageTimestamp", 0) for m in new_msgs)
            if latest > self._since_ts:
                self._since_ts = latest

        return new_msgs

    def _parse_message(self, raw: dict) -> Optional[MessageEvent]:
        """Convert a raw Evolution API message dict to a MessageEvent."""
        key = raw.get("key", {})
        from_me: bool = bool(key.get("fromMe", False))
        if self._ignore_from_me and from_me:
            return None

        remote_jid: str = key.get("remoteJid", "")
        msg_id: str = key.get("id") or uuid.uuid4().hex

        msg_content = raw.get("message", {})
        text: str = (
            msg_content.get("conversation")
            or msg_content.get("extendedTextMessage", {}).get("text")
            or ""
        )
        if not text:
            return None  # skip media-only messages without a caption

        ts_sec: int = int(raw.get("messageTimestamp", int(time.time())))
        received_at = datetime.fromtimestamp(ts_sec, tz=timezone.utc)

        is_group: bool = remote_jid.endswith("@g.us")

        return MessageEvent(
            channel=self.channel,
            chat_id=remote_jid,
            user_id=remote_jid,
            text=text,
            message_id=msg_id,
            received_at=received_at,
            is_dm=not is_group,
            metadata={
                "push_name": raw.get("pushName", ""),
                "instance": self._instance,
                "source": raw.get("source", ""),
                "message_type": raw.get("messageType", ""),
                "from_me": from_me,
            },
        )

    # ── low-level HTTP ────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> dict:
        """Perform an authenticated Evolution API HTTP request.

        Returns parsed JSON response body.
        Raises RuntimeError wrapping HTTPError details on non-2xx responses.
        """
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "apikey": self._api_key,
        }
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw_body = exc.read()
            try:
                detail = json.loads(raw_body).get("message", exc.reason)
            except Exception:
                detail = exc.reason
            raise RuntimeError(
                f"Evolution API HTTP {exc.code}: {detail}"
            ) from exc
