"""Telegram channel adapter — native implementation using Bot API long-polling.

Uses urllib stdlib only — no python-telegram-bot or third-party SDK.
Follows the soul_native_first rule: speak the HTTP API directly.

Token is read from env TELEGRAM_BOT_TOKEN unless passed explicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30  # Telegram long-poll timeout in seconds
_MAX_TEXT_LENGTH = 4096  # Telegram hard limit


def _api_url(token: str, method: str) -> str:
    return _API_BASE.format(token=token, method=method)


def _http_get(url: str, params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{url}?{query}")
    with urllib.request.urlopen(req, timeout=_POLL_TIMEOUT + 5) as resp:
        return json.loads(resp.read().decode())


def _http_post(url: str, payload: dict, token: str) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


class TelegramChannel(GatewayChannel):
    """Native Telegram adapter — long-polling in, Bot API REST out.

    Long-polling runs in a background asyncio task. Each received update
    containing a text message is dispatched through the handler contract.
    The adapter ignores non-text updates (photos, stickers, etc).
    """

    def __init__(
        self,
        handler: InboundHandler,
        token: Optional[str] = None,
        allowed_user_ids: Optional[list[int]] = None,
    ) -> None:
        super().__init__("telegram", handler)
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._allowed_user_ids = set(allowed_user_ids) if allowed_user_ids else None
        self._offset: int = 0
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._token:
            raise GatewayError("TELEGRAM_BOT_TOKEN not configured")
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not self._token:
            raise GatewayError("TELEGRAM_BOT_TOKEN not configured")
        text = message.text[:_MAX_TEXT_LENGTH]
        payload: dict = {"chat_id": message.platform_chat_id, "text": text}
        if message.reply_to_message_id:
            payload["reply_to_message_id"] = message.reply_to_message_id
        url = _api_url(self._token, "sendMessage")
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _http_post(url, payload, self._token)
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            raise GatewayError(f"Telegram sendMessage {exc.code}: {body}") from exc
        if not result.get("ok"):
            raise GatewayError(f"Telegram sendMessage error: {result}")
        return str(result["result"]["message_id"])

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        """Long-poll getUpdates in a loop, dispatch each text message."""
        url = _api_url(self._token, "getUpdates")
        while not self._stop_event.is_set():
            try:
                data = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _http_get(url, {
                        "offset": self._offset,
                        "timeout": _POLL_TIMEOUT,
                        "allowed_updates": '["message"]',
                    }),
                )
            except (urllib.error.URLError, TimeoutError, OSError):
                await asyncio.sleep(3)
                continue

            if not data.get("ok"):
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue
                text = msg.get("text")
                if not text:
                    continue
                from_user = msg.get("from", {})
                user_id = from_user.get("id")
                if self._allowed_user_ids and user_id not in self._allowed_user_ids:
                    continue
                display = from_user.get("username") or from_user.get("first_name", "")
                inbound = InboundMessage(
                    channel="telegram",
                    platform_user_id=str(user_id),
                    platform_chat_id=str(msg["chat"]["id"]),
                    text=text,
                    platform_message_id=str(msg["message_id"]),
                    user_display_name=display,
                    metadata={"raw_update": update},
                )
                asyncio.create_task(self._dispatch(inbound))
