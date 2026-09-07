"""seal/channels/signal.py — Signal channel adapter, stdlib only.

Implements SignalAdapter(ChannelAdapter) via signal-cli JSON-RPC API.
No external dependencies — pure urllib.request + json.

Requires signal-cli running with --http flag:
    signal-cli --config ~/.local/share/signal-cli daemon --http 127.0.0.1:8080

Config keys:
    phone_number    str   Registered Signal phone number (e.g. "+51999...")  [required]
    api_url         str   signal-cli HTTP API base URL (default "http://127.0.0.1:8080")
    allowed_senders list  Whitelist of phone numbers (empty = allow all)
    poll_interval_s float Seconds between polls (default 3.0)

Usage:
    adapter = SignalAdapter({
        "phone_number": "+51999000111",
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

logger = logging.getLogger("seal.channels.signal")

_DEFAULT_API = "http://127.0.0.1:8080"
_DEFAULT_POLL_S = 3.0


class SignalAdapter(ChannelAdapter):
    """Signal channel adapter — signal-cli REST API, no external deps.

    Polls POST /v1/receive/{number} for inbound messages.
    Sends via POST /v2/send.
    """

    channel = "signal"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        self._number       = str(config["phone_number"])
        self._api          = str(config.get("api_url", _DEFAULT_API)).rstrip("/")
        self._allowed: set[str] = set(config.get("allowed_senders") or [])  # type: ignore
        self._poll_s       = float(config.get("poll_interval_s", _DEFAULT_POLL_S))

        self._connected      = False
        self._last_event_at: Optional[float] = None
        self._last_error:    Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._ping)
            self._connected = True
            logger.info("SignalAdapter connected (%s via %s)", self._number, self._api)
        except Exception as exc:
            self._last_error = str(exc)
            logger.error("SignalAdapter cannot reach signal-cli at %s: %s", self._api, exc)
            raise

    async def disconnect(self) -> None:
        self.stop()
        self._connected = False
        logger.info("SignalAdapter disconnected")

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:
        loop = asyncio.get_event_loop()
        while not self.stopping:
            try:
                msgs = await loop.run_in_executor(None, self._receive)
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("Signal receive error: %s", exc)
                await asyncio.sleep(self._poll_s)
                continue

            for envelope in msgs:
                evt = self._parse_envelope(envelope)
                if evt is None:
                    continue
                if self._allowed and evt.user_id not in self._allowed:
                    logger.debug("Signal: dropping message from unlisted sender %s", evt.user_id)
                    continue
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
        payload: dict[str, Any] = {
            "message":   text,
            "number":    self._number,
            "recipients": [chat_id],
        }
        if reply_to:
            payload["quote_timestamp"] = int(reply_to)
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self._request("POST", "/v2/send", payload)
            )
            ts = str(resp.get("timestamp", ""))
            return SendResult(channel="signal", chat_id=chat_id, message_id=ts)
        except Exception as exc:
            return SendResult(channel="signal", chat_id=chat_id,
                              message_id="", success=False, error=str(exc))

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel       = "signal",
            connected     = self._connected,
            last_event_at = self._last_event_at,
            last_error    = self._last_error,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _ping(self) -> dict:
        return self._request("GET", "/v1/health")

    def _receive(self) -> list[dict]:
        encoded = urllib.parse.quote(self._number, safe="")
        result  = self._request("GET", f"/v1/receive/{encoded}")
        if isinstance(result, list):
            return result
        return []

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None) -> Any:
        url  = f"{self._api}{path}"
        data = json.dumps(body).encode() if body else None
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def _parse_envelope(self, envelope: dict) -> Optional[MessageEvent]:
        inner = envelope.get("envelope", envelope)
        data  = inner.get("dataMessage") or inner.get("syncMessage", {}).get("sentMessage")
        if not data:
            return None
        text = data.get("message", "")
        if not isinstance(text, str) or not text.strip():
            return None
        sender     = inner.get("source") or inner.get("sourceNumber", "")
        group_info = data.get("groupInfo")
        chat_id    = group_info.get("groupId") if group_info else sender
        ts         = str(data.get("timestamp", int(time.time() * 1000)))
        attachments = tuple(
            Attachment(kind="file", mime=a.get("contentType", "application/octet-stream"),
                       filename=a.get("filename"), size_bytes=a.get("size"))
            for a in data.get("attachments", [])
        )
        return MessageEvent(
            channel    = "signal",
            chat_id    = chat_id or sender,
            user_id    = sender,
            text       = text.strip(),
            message_id = ts,
            attachments= attachments,
            is_dm      = group_info is None,
        )
