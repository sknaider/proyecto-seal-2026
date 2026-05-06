"""Signal channel adapter — native via signal-cli JSON-RPC daemon over HTTP/socket.

Talks to a locally-running signal-cli daemon (started separately) over its
JSON-RPC interface. We use stdlib urllib for HTTP and stdlib socket for the
unix-socket variant. No third-party Signal SDK.

The daemon endpoint is configured via SIGNAL_DAEMON_URL (default
http://127.0.0.1:8080/api/v1) or SIGNAL_DAEMON_SOCKET for unix sockets.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage


class SignalChannel(GatewayChannel):
    """Native Signal adapter — JSON-RPC to signal-cli daemon."""

    def __init__(
        self,
        handler: InboundHandler,
        account: Optional[str] = None,
        daemon_url: Optional[str] = None,
        unix_socket_path: Optional[str] = None,
        poll_interval_s: float = 5.0,
    ) -> None:
        super().__init__("signal", handler)
        self._account = account or os.environ.get("SIGNAL_ACCOUNT", "")
        self._daemon_url = daemon_url or os.environ.get(
            "SIGNAL_DAEMON_URL", "http://127.0.0.1:8080/api/v1"
        )
        self._socket_path = unix_socket_path or os.environ.get("SIGNAL_DAEMON_SOCKET", "")
        self._poll_interval_s = poll_interval_s
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not self._account:
            raise GatewayError("SIGNAL_ACCOUNT not configured")
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not self._account:
            raise GatewayError("SIGNAL_ACCOUNT not configured")

        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "id": "send-1",
            "params": {
                "account": self._account,
                "recipient": [message.platform_chat_id],
                "message": message.text,
            },
        }

        if self._socket_path:
            response = await asyncio.to_thread(self._unix_jsonrpc, payload)
        else:
            response = await asyncio.to_thread(self._http_jsonrpc, payload)

        if "error" in response:
            raise GatewayError(f"signal send failed: {response['error']}")
        result = response.get("result", {})
        return str(result.get("timestamp", ""))

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

    def _http_jsonrpc(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._daemon_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise GatewayError(f"signal http error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            raise GatewayError(f"signal url error: {e}")

    def _unix_jsonrpc(self, payload: dict) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self._socket_path)
            s.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if buf.endswith(b"\n"):
                    break
            return json.loads(buf.decode().strip() or "{}")
        finally:
            s.close()

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                msgs = await asyncio.to_thread(self._receive_once)
                for envelope in msgs:
                    inbound = self.parse_envelope(envelope)
                    await self._dispatch(inbound)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def _receive_once(self) -> list:
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "id": "recv-1",
            "params": {"account": self._account, "timeout": 1},
        }
        if self._socket_path:
            response = self._unix_jsonrpc(payload)
        else:
            response = self._http_jsonrpc(payload)
        if "error" in response:
            return []
        result = response.get("result")
        if isinstance(result, list):
            return result
        return []

    @staticmethod
    def parse_envelope(envelope: dict) -> InboundMessage:
        """Translate a signal-cli receive envelope into an InboundMessage."""
        env = envelope.get("envelope") or envelope
        data = env.get("dataMessage") or env.get("data_message") or {}
        sender = env.get("sourceNumber") or env.get("source") or ""
        sender_name = env.get("sourceName") or env.get("source_name")
        text = data.get("message") or data.get("body") or ""
        timestamp_ms = env.get("timestamp") or data.get("timestamp")
        try:
            received_at = (
                datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
                if timestamp_ms
                else datetime.now(timezone.utc)
            )
        except (TypeError, ValueError):
            received_at = datetime.now(timezone.utc)

        group_info = data.get("groupInfo") or data.get("group_info") or {}
        chat_id = group_info.get("groupId") or group_info.get("group_id") or sender

        return InboundMessage(
            channel="signal",
            platform_user_id=str(sender),
            platform_chat_id=str(chat_id),
            text=str(text),
            received_at=received_at,
            platform_message_id=str(timestamp_ms) if timestamp_ms else None,
            user_display_name=sender_name,
            metadata={"raw": env, "group": bool(group_info)},
        )
