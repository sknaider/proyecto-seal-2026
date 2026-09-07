"""Webhook receiver — event-driven HTTP server for SEAL.

Listens on a configurable port and routes incoming HTTP webhook events
to registered handlers. Supports HMAC-SHA256 signature verification
(GitHub, standard X-Hub-Signature-256 style).

SEAL parity: SEAL subscribes to GitHub events, custom alerts — all
without writing plugin code. SEAL now does the same natively.

Usage:
    server = WebhookServer(port=9100)
    server.register("github", handler=my_handler, secret="mysecret")
    asyncio.run(server.serve())

The server runs a minimal asyncio HTTP server — no frameworks, no third-party libs.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

LOG = logging.getLogger("seal.webhooks")


class WebhookError(Exception):
    """Raised when a webhook cannot be processed."""


@dataclass(frozen=True)
class WebhookEvent:
    """Normalised inbound webhook payload."""

    source: str
    event_type: str
    payload: dict[str, Any]
    raw_body: bytes
    headers: dict[str, str]
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


WebhookHandler = Callable[[WebhookEvent], Awaitable[Optional[str]]]


@dataclass
class _RouteConfig:
    source: str
    handler: WebhookHandler
    secret: Optional[str]
    path: str


# ── HMAC helpers ─────────────────────────────────────────────────────────────


def verify_github_signature(body: bytes, secret: str, signature_header: str) -> bool:
    """Verify GitHub X-Hub-Signature-256 header against HMAC-SHA256."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _compute_hmac(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, secret: str, signature: str) -> bool:
    """Generic HMAC-SHA256 verification — works for GitHub and compatible services."""
    return hmac.compare_digest(_compute_hmac(body, secret), signature)


# ── Minimal async HTTP server ─────────────────────────────────────────────────


class WebhookServer:
    """Minimal asyncio HTTP server that dispatches POST requests to registered handlers.

    Path routing: POST /<source> → handler for that source.
    Everything else: 404.

    Signature verification: if a route has a secret, the incoming
    X-Hub-Signature-256 (or X-Signature-256) header is checked before the
    handler is called. Requests with bad signatures get 401.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9100) -> None:
        self.host = host
        self.port = port
        self._routes: dict[str, _RouteConfig] = {}
        self._server: Optional[asyncio.Server] = None
        self._running = False

    def register(
        self,
        source: str,
        handler: WebhookHandler,
        secret: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        """Register a webhook source.

        source: short identifier, e.g. "github", "grafana", "custom"
        handler: async callable(WebhookEvent) → Optional[str]  (response body)
        secret:  HMAC secret for signature verification; None to skip
        path:    URL path override; defaults to /<source>
        """
        effective_path = (path or f"/{source}").lstrip("/")
        self._routes[effective_path] = _RouteConfig(
            source=source,
            handler=handler,
            secret=secret,
            path=effective_path,
        )

    async def serve(self) -> None:
        """Start serving until stopped via close()."""
        self._running = True
        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        LOG.info("WebhookServer listening on %s:%d", self.host, self.port)
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._running = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # ── request handling ─────────────────────────────────────────────────────

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._process_request(reader, writer)
        except Exception as exc:
            LOG.exception("webhook handler error: %s", exc)
            await self._respond(writer, 500, b"Internal Server Error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        first_line = (await reader.readline()).decode(errors="replace").strip()
        if not first_line:
            await self._respond(writer, 400, b"Bad Request")
            return

        parts = first_line.split()
        if len(parts) < 2:
            await self._respond(writer, 400, b"Bad Request")
            return
        method, path = parts[0], parts[1]

        headers: dict[str, str] = {}
        while True:
            line = (await reader.readline()).decode(errors="replace").strip()
            if not line:
                break
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        content_length = int(headers.get("content-length", "0"))
        body = await reader.read(content_length) if content_length > 0 else b""

        if method != "POST":
            await self._respond(writer, 405, b"Method Not Allowed")
            return

        route_key = path.lstrip("/").split("?")[0]
        route = self._routes.get(route_key)
        if route is None:
            await self._respond(writer, 404, b"Not Found")
            return

        if route.secret:
            sig = headers.get("x-hub-signature-256") or headers.get("x-signature-256", "")
            if not verify_signature(body, route.secret, sig):
                await self._respond(writer, 401, b"Invalid signature")
                return

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"raw": body.decode(errors="replace")}

        event_type = (
            headers.get("x-github-event")
            or headers.get("x-event-type")
            or "webhook"
        )

        event = WebhookEvent(
            source=route.source,
            event_type=event_type,
            payload=payload,
            raw_body=body,
            headers=headers,
        )

        try:
            response_body = await route.handler(event)
        except Exception as exc:
            LOG.exception("handler for %s raised: %s", route.source, exc)
            await self._respond(writer, 500, b"Handler error")
            return

        resp = (response_body or "ok").encode()
        await self._respond(writer, 200, resp)

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter, status: int, body: bytes
    ) -> None:
        status_text = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
                       404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"
                       }.get(status, "Unknown")
        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: text/plain\r\n"
            "\r\n"
        ).encode() + body
        writer.write(response)
        await writer.drain()


# ── Convenience factory ───────────────────────────────────────────────────────


def from_env() -> WebhookServer:
    """Create a WebhookServer from env vars.

    SEAL_WEBHOOK_HOST  (default 0.0.0.0)
    SEAL_WEBHOOK_PORT  (default 9100)
    """
    return WebhookServer(
        host=os.environ.get("SEAL_WEBHOOK_HOST", "0.0.0.0"),
        port=int(os.environ.get("SEAL_WEBHOOK_PORT", "9100")),
    )
