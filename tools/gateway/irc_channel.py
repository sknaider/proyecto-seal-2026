"""IRC channel adapter — pure stdlib, no third-party dependencies.

Implements GatewayChannel for IRC networks.
Connects via TCP, authenticates with NICK/USER/PASS, joins channels,
and delivers outbound messages via PRIVMSG.

Inbound messages are parsed from PRIVMSG lines; the handler callback
receives InboundMessage for each one.

Usage:
    irc = IRCChannel(
        host="irc.libera.chat", port=6667,
        nick="seal-bot", channel="#seal-team",
    )
    async with irc:
        await irc.send("Hello team!")
        async for msg in irc.listen():
            print(msg.sender, msg.text)
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, List, Optional

from .base import GatewayChannel, GatewayError, InboundMessage, OutboundMessage

_PRIVMSG_RE = re.compile(r"^:([^!]+)!?\S*\s+PRIVMSG\s+(\S+)\s+:(.*)$")
_PING_RE    = re.compile(r"^PING\s+:?(.+)$")


class IRCError(GatewayError):
    """Raised on IRC protocol or connection errors."""


@dataclass
class IRCConfig:
    host:       str
    port:       int         = 6667
    nick:       str         = "seal-bot"
    user:       str         = "seal-bot"
    realname:   str         = "SEAL Agent"
    password:   Optional[str] = None
    channel:    str         = "#general"
    use_tls:    bool        = False
    timeout:    int         = 30
    max_lines:  int         = 500


class IRCChannel(GatewayChannel):
    """IRC channel adapter — async, pure stdlib asyncio.

    Supports:
      • Plain TCP + TLS (ssl=True)
      • PASS authentication (NickServ-style or server password)
      • PING/PONG keepalive
      • PRIVMSG send + receive
      • Graceful QUIT
    """

    platform = "irc"

    def __init__(self, config: IRCConfig) -> None:
        self._cfg:    IRCConfig                        = config
        self._reader: Optional[asyncio.StreamReader]   = None
        self._writer: Optional[asyncio.StreamWriter]   = None
        self._joined: bool                             = False
        self._handlers: List[Callable[[InboundMessage], None]] = []

    # ── GatewayChannel interface ─────────────────────────────────────────────

    async def start(self) -> None:
        """Open TCP connection and perform IRC handshake."""
        try:
            if self._cfg.use_tls:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._cfg.host, self._cfg.port, ssl=ctx),
                    timeout=self._cfg.timeout,
                )
            else:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._cfg.host, self._cfg.port),
                    timeout=self._cfg.timeout,
                )
        except (OSError, asyncio.TimeoutError) as e:
            raise IRCError(f"IRC connect failed: {e}") from e

        await self._handshake()

    async def close(self) -> None:
        if self._writer:
            try:
                await self._send_raw("QUIT :SEAL offline")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None
        self._joined = False

    async def send(self, message: OutboundMessage) -> str:
        """Send a PRIVMSG to the configured channel."""
        if not self._writer:
            raise IRCError("not connected")
        target = message.channel or self._cfg.channel
        for line in message.text.splitlines():
            if line.strip():
                await self._send_raw(f"PRIVMSG {target} :{line}")
        return f"irc:{target}:{int(time.time())}"

    async def listen(self) -> AsyncIterator[InboundMessage]:
        """Yield InboundMessage for each PRIVMSG received."""
        if not self._reader:
            raise IRCError("not connected")
        lines_read = 0
        while lines_read < self._cfg.max_lines:
            try:
                raw = await asyncio.wait_for(
                    self._reader.readline(), timeout=self._cfg.timeout,
                )
            except asyncio.TimeoutError:
                await self._send_raw("PING :seal-keepalive")
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            lines_read += 1

            ping_m = _PING_RE.match(line)
            if ping_m:
                await self._send_raw(f"PONG :{ping_m.group(1)}")
                continue

            priv_m = _PRIVMSG_RE.match(line)
            if priv_m:
                sender, target, text = priv_m.groups()
                msg = InboundMessage(
                    channel=target,
                    platform_user_id=sender,
                    platform_chat_id=target,
                    text=text,
                    user_display_name=sender,
                    metadata={"line": line, "platform": "irc"},
                )
                for h in self._handlers:
                    h(msg)
                yield msg

    def add_handler(self, fn: Callable[[InboundMessage], None]) -> None:
        self._handlers.append(fn)

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _send_raw(self, line: str) -> None:
        if self._writer is None:
            raise IRCError("not connected")
        self._writer.write(f"{line}\r\n".encode("utf-8"))
        await self._writer.drain()

    async def _handshake(self) -> None:
        """Send PASS/NICK/USER and wait for RPL_WELCOME (001)."""
        if self._cfg.password:
            await self._send_raw(f"PASS {self._cfg.password}")
        await self._send_raw(f"NICK {self._cfg.nick}")
        await self._send_raw(
            f"USER {self._cfg.user} 0 * :{self._cfg.realname}"
        )
        # Drain until 001 (RPL_WELCOME) or error
        deadline = time.monotonic() + self._cfg.timeout
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self._reader.readline(), timeout=5)
            except asyncio.TimeoutError:
                raise IRCError("IRC handshake timed out")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if " 001 " in line:
                await self._send_raw(f"JOIN {self._cfg.channel}")
                self._joined = True
                return
            if " 433 " in line:
                raise IRCError(f"Nick already in use: {self._cfg.nick}")
            if " 464 " in line or " 465 " in line:
                raise IRCError("IRC server rejected password")
        raise IRCError("IRC handshake never received RPL_WELCOME")

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @classmethod
    def from_env(cls) -> "IRCChannel":
        """Build from environment variables."""
        import os
        return cls(IRCConfig(
            host     = os.environ.get("IRC_HOST",    "irc.libera.chat"),
            port     = int(os.environ.get("IRC_PORT", "6667")),
            nick     = os.environ.get("IRC_NICK",    "seal-bot"),
            user     = os.environ.get("IRC_USER",    "seal-bot"),
            realname = os.environ.get("IRC_REALNAME","SEAL Agent"),
            password = os.environ.get("IRC_PASSWORD") or None,
            channel  = os.environ.get("IRC_CHANNEL", "#seal-team"),
            use_tls  = os.environ.get("IRC_TLS",     "").lower() in ("1","true","yes"),
        ))
