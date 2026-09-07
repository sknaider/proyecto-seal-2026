"""SEAL Matrix channel adapter — CS API v3, stdlib only.

Implements MatrixAdapter(ChannelAdapter) using the Matrix Client-Server API v3.
No external dependencies — pure urllib.request + json.

Config keys (passed as ``config`` dict):
    homeserver      str   Base URL, e.g. "http://localhost:8008"  [required]
    user_id         str   Full Matrix ID, e.g. "@nexus:localhost"
    password        str   Password for password-based login
    access_token    str   Pre-obtained access token (skips login)
    room_ids        list  Rooms to listen on, e.g. ["!abc:localhost"]
    device_id       str   Device ID (auto-generated if omitted)
    sync_timeout_ms int   Long-poll timeout in ms (default 30 000)
    filter_own      bool  Drop messages sent by this user (default True)

Usage:
    adapter = MatrixAdapter({
        "homeserver": "http://localhost:8008",
        "user_id": "@nexus:localhost",
        "password": "secret",
        "room_ids": ["!abc123:localhost"],
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
from typing import Any, Mapping

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult

logger = logging.getLogger("seal.channels.matrix")

_MATRIX_CS = "/_matrix/client/v3"
_DEFAULT_SYNC_TIMEOUT = 30_000  # ms


class MatrixAdapter(ChannelAdapter):
    """Matrix channel adapter — CS API v3, no external deps.

    Network IO runs in a thread-pool executor so the asyncio event loop
    stays unblocked during long-poll sync calls.
    """

    channel = "matrix"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        self._homeserver: str = str(config["homeserver"]).rstrip("/")
        self._user_id: str = str(config.get("user_id", ""))
        self._password: str = str(config.get("password", ""))
        self._access_token: str = str(config.get("access_token", ""))
        self._room_ids: list[str] = list(config.get("room_ids", []))  # type: ignore[arg-type]
        self._device_id: str = str(
            config.get("device_id", f"SEAL_{uuid.uuid4().hex[:8].upper()}")
        )
        self._sync_timeout_ms: int = int(config.get("sync_timeout_ms", _DEFAULT_SYNC_TIMEOUT))
        self._filter_own: bool = bool(config.get("filter_own", True))

        self._next_batch: str | None = None
        self._txn_lock = threading.Lock()
        self._txn_counter: int = 0
        self._connected: bool = False
        self._last_event_at: float | None = None
        self._last_error: str | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_running_loop()
        if not self._access_token:
            await loop.run_in_executor(None, self._do_login)
        # Initial sync with timeout=0 to fast-forward past old events
        response = await loop.run_in_executor(
            None, lambda: self._do_sync(since=None, timeout_ms=0)
        )
        self._next_batch = response.get("next_batch")
        self._connected = True
        logger.info("MatrixAdapter connected as %s", self._user_id)

    async def disconnect(self) -> None:
        if not self._connected:
            return
        self.stop()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._do_logout)
        except Exception as exc:
            logger.debug("Matrix logout error: %s", exc)
        self._connected = False
        logger.info("MatrixAdapter disconnected")

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:  # type: ignore[override]
        loop = asyncio.get_running_loop()
        backoff = 1.0
        while not self.stopping:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda: self._do_sync(
                        since=self._next_batch,
                        timeout_ms=self._sync_timeout_ms,
                    ),
                )
                self._next_batch = response.get("next_batch", self._next_batch)
                self._last_event_at = time.time()
                self._last_error = None
                backoff = 1.0
                for evt in self._parse_sync(response):
                    yield evt
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("MatrixAdapter sync error: %s — retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        attachments: tuple[Attachment, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> SendResult:
        with self._txn_lock:
            self._txn_counter += 1
            txn_id = f"seal_{int(time.time() * 1000)}_{self._txn_counter}"

        encoded_room = urllib.parse.quote(chat_id, safe="")
        url = (
            f"{self._homeserver}{_MATRIX_CS}"
            f"/rooms/{encoded_room}/send/m.room.message/{txn_id}"
        )
        body: dict[str, Any] = {"msgtype": "m.text", "body": text}
        if reply_to:
            body["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}

        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: self._request("PUT", url, body)
            )
            event_id = resp.get("event_id", txn_id)
            return SendResult(
                channel=self.channel,
                chat_id=chat_id,
                message_id=event_id,
                success=True,
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("MatrixAdapter send failed for %s: %s", chat_id, exc)
            return SendResult(
                channel=self.channel,
                chat_id=chat_id,
                message_id="",
                success=False,
                error=str(exc),
            )

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel=self.channel,
            connected=self._connected,
            last_event_at=self._last_event_at,
            last_error=self._last_error,
        )

    # ── Matrix CS API (blocking — run in executor) ────────────────────────────

    def _do_login(self) -> None:
        """POST /_matrix/client/v3/login — sets self._access_token."""
        url = f"{self._homeserver}{_MATRIX_CS}/login"
        payload = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": self._user_id},
            "password": self._password,
            "device_id": self._device_id,
            "initial_device_display_name": f"SEAL Agent {self._user_id}",
        }
        response = self._request("POST", url, payload)
        self._access_token = response["access_token"]
        self._user_id = response.get("user_id", self._user_id)
        logger.debug("Matrix login OK — device %s", response.get("device_id"))

    def _do_logout(self) -> None:
        """POST /_matrix/client/v3/logout."""
        url = f"{self._homeserver}{_MATRIX_CS}/logout"
        try:
            self._request("POST", url, {})
        except Exception:
            pass

    def _do_sync(self, *, since: str | None, timeout_ms: int) -> dict:
        """GET /_matrix/client/v3/sync?since=...&timeout=...

        Returns the raw sync response body.
        """
        params: dict[str, str] = {"timeout": str(timeout_ms)}
        if since:
            params["since"] = since
        if self._room_ids:
            # Build a rooms filter to limit sync to watched rooms
            room_filter = json.dumps({
                "room": {
                    "rooms": self._room_ids,
                    "timeline": {"limit": 50},
                    "state": {"limit": 0},
                }
            })
            params["filter"] = room_filter
        qs = urllib.parse.urlencode(params)
        url = f"{self._homeserver}{_MATRIX_CS}/sync?{qs}"
        return self._request("GET", url)

    # ── low-level HTTP ────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        """Perform an authenticated Matrix HTTP request.

        Returns parsed JSON response body.
        Raises urllib.error.URLError or HTTPError on network/server errors.
        """
        data = json.dumps(body).encode() if body is not None else None
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._sync_timeout_ms / 1000 + 5) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            try:
                detail = json.loads(body_bytes).get("error", exc.reason)
            except Exception:
                detail = exc.reason
            raise RuntimeError(f"Matrix HTTP {exc.code}: {detail}") from exc

    # ── event parsing ─────────────────────────────────────────────────────────

    def _parse_sync(self, response: dict) -> list[MessageEvent]:
        """Extract MessageEvents from a Matrix sync response."""
        events: list[MessageEvent] = []
        rooms_join = response.get("rooms", {}).get("join", {})
        for room_id, room_data in rooms_join.items():
            if self._room_ids and room_id not in self._room_ids:
                continue
            timeline = room_data.get("timeline", {}).get("events", [])
            for raw in timeline:
                evt = self._parse_room_event(room_id, raw)
                if evt is not None:
                    events.append(evt)
        return events

    def _parse_room_event(self, room_id: str, raw: dict) -> MessageEvent | None:
        """Convert a single raw Matrix event to MessageEvent or None."""
        if raw.get("type") != "m.room.message":
            return None
        content = raw.get("content", {})
        if content.get("msgtype") not in ("m.text", "m.notice", "m.emote"):
            return None
        sender = raw.get("sender", "")
        if self._filter_own and sender == self._user_id:
            return None

        text = content.get("body", "")
        event_id = raw.get("event_id", uuid.uuid4().hex)
        ts_ms = raw.get("origin_server_ts", int(time.time() * 1000))

        relates = content.get("m.relates_to", {})
        reply_to = relates.get("m.in_reply_to", {}).get("event_id")

        from datetime import datetime, timezone
        received_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        return MessageEvent(
            channel=self.channel,
            chat_id=room_id,
            user_id=sender,
            text=text,
            message_id=event_id,
            received_at=received_at,
            reply_to=reply_to,
            is_dm=False,
            metadata={"room_id": room_id, "event_type": raw.get("type", "")},
        )
