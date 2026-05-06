"""IRC channel adapter — native RFC 1459/2812 over stdlib asyncio sockets.

No third-party IRC library. Implements just enough of the protocol to log in,
join channels, send PRIVMSG, and parse incoming PRIVMSG lines into the
gateway's normalized contract.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage


class IRCChannel(GatewayChannel):
    """Native IRC adapter — asyncio TCP socket + line-based protocol."""

    def __init__(
        self,
        handler: InboundHandler,
        host: Optional[str] = None,
        port: int = 6697,
        nickname: Optional[str] = None,
        username: Optional[str] = None,
        realname: Optional[str] = None,
        password: Optional[str] = None,
        channels: Optional[list[str]] = None,
        use_tls: bool = True,
    ) -> None:
        super().__init__("irc", handler)
        self._host = host or os.environ.get("IRC_HOST", "")
        self._port = port
        self._nickname = nickname or os.environ.get("IRC_NICK", "")
        self._username = username or self._nickname
        self._realname = realname or self._nickname
        self._password = password or os.environ.get("IRC_PASSWORD", "")
        env_channels = os.environ.get("IRC_CHANNELS", "")
        self._channels = channels or (
            [c.strip() for c in env_channels.split(",") if c.strip()] if env_channels else []
        )
        self._use_tls = use_tls
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._host and self._nickname):
            raise GatewayError("IRC_HOST and IRC_NICK required")
        self._running = True
        self._stop_event.clear()
        await self._connect_and_register()
        self._read_task = asyncio.create_task(self._read_loop())

    async def send(self, message: OutboundMessage) -> str:
        if self._writer is None:
            raise GatewayError("irc not connected")
        target = message.platform_chat_id
        # Split into lines so we don't send embedded \r\n which IRC servers reject.
        for line in message.text.splitlines() or [""]:
            if not line:
                continue
            await self._send_raw(f"PRIVMSG {target} :{line}")
        return ""

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._writer is not None:
            try:
                await self._send_raw("QUIT :seal closing")
            except Exception:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass
            self._read_task = None

    async def _connect_and_register(self) -> None:
        if self._use_tls:
            import ssl

            ctx = ssl.create_default_context()
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port, ssl=ctx
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)

        if self._password:
            await self._send_raw(f"PASS {self._password}")
        await self._send_raw(f"NICK {self._nickname}")
        await self._send_raw(f"USER {self._username} 0 * :{self._realname}")

    async def _send_raw(self, line: str) -> None:
        if self._writer is None:
            raise GatewayError("irc writer not initialized")
        self._writer.write((line + "\r\n").encode("utf-8", errors="replace"))
        await self._writer.drain()

    async def _read_loop(self) -> None:
        joined: set[str] = set()
        while not self._stop_event.is_set() and self._reader is not None:
            try:
                raw = await self._reader.readline()
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(1.0)
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue

            # PING handling — protocol heartbeat.
            if line.startswith("PING "):
                await self._send_raw("PONG " + line[5:])
                continue

            # 001 = welcome → join configured channels.
            if " 001 " in line and joined != set(self._channels):
                for ch in self._channels:
                    if ch not in joined:
                        await self._send_raw(f"JOIN {ch}")
                        joined.add(ch)
                continue

            # PRIVMSG → dispatch to handler.
            inbound = self.parse_privmsg(line)
            if inbound is not None:
                await self._dispatch(inbound)

    @staticmethod
    def parse_privmsg(line: str) -> Optional[InboundMessage]:
        """Parse a raw IRC line. Returns None if it's not a PRIVMSG."""
        if not line.startswith(":"):
            return None
        try:
            prefix, _, rest = line[1:].partition(" ")
            command, _, params = rest.partition(" ")
            if command != "PRIVMSG":
                return None
            target, _, trailing = params.partition(" :")
            if not trailing:
                return None
            sender_nick = prefix.split("!", 1)[0]
            return InboundMessage(
                channel="irc",
                platform_user_id=sender_nick,
                platform_chat_id=target,
                text=trailing,
                received_at=datetime.now(timezone.utc),
                platform_message_id=None,
                user_display_name=sender_nick,
                metadata={"prefix": prefix, "raw": line},
            )
        except Exception:
            return None
