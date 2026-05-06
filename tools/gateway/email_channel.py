"""Email channel adapter — native SMTP send + IMAP receive via stdlib.

No third-party email SDK. Outbound uses smtplib/email.message; inbound polls
IMAP IDLE-style with imaplib. Connection details come from env or constructor.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Optional

from .base import GatewayChannel, GatewayError, InboundHandler, InboundMessage, OutboundMessage


class EmailChannel(GatewayChannel):
    """Native email gateway — SMTP for send, IMAP for receive."""

    def __init__(
        self,
        handler: InboundHandler,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        imap_host: Optional[str] = None,
        imap_port: int = 993,
        username: Optional[str] = None,
        password: Optional[str] = None,
        from_address: Optional[str] = None,
        poll_interval_s: float = 30.0,
    ) -> None:
        super().__init__("email", handler)
        self._smtp_host = smtp_host or os.environ.get("EMAIL_SMTP_HOST", "")
        self._smtp_port = smtp_port
        self._imap_host = imap_host or os.environ.get("EMAIL_IMAP_HOST", "")
        self._imap_port = imap_port
        self._username = username or os.environ.get("EMAIL_USERNAME", "")
        self._password = password or os.environ.get("EMAIL_PASSWORD", "")
        self._from_address = from_address or self._username
        self._poll_interval_s = poll_interval_s
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        if not (self._smtp_host and self._imap_host and self._username and self._password):
            raise GatewayError("email credentials incomplete")
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> str:
        if not (self._smtp_host and self._username and self._password):
            raise GatewayError("smtp credentials incomplete")

        msg = EmailMessage()
        msg["From"] = self._from_address
        msg["To"] = message.platform_chat_id
        msg["Subject"] = message.metadata.get("subject", "(no subject)")
        if message.reply_to_message_id:
            msg["In-Reply-To"] = message.reply_to_message_id
            msg["References"] = message.reply_to_message_id
        msg.set_content(message.text)

        def _smtp_send():
            ctx = ssl.create_default_context()
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(self._username, self._password)
                s.send_message(msg)

        try:
            await asyncio.to_thread(_smtp_send)
        except Exception as e:
            raise GatewayError(f"smtp send failed: {e}")

        return msg.get("Message-ID", "")

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

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                continue

    def _poll_once(self) -> None:
        """Pull UNSEEN messages from INBOX and dispatch them."""
        m = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        try:
            m.login(self._username, self._password)
            m.select("INBOX")
            status, data = m.search(None, "UNSEEN")
            if status != "OK":
                return
            for num in data[0].split():
                status, fetched = m.fetch(num, "(RFC822)")
                if status != "OK" or not fetched or not fetched[0]:
                    continue
                raw = fetched[0][1]
                inbound = self.parse_email_bytes(raw)
                asyncio.run(self._dispatch(inbound))
        finally:
            try:
                m.logout()
            except Exception:
                pass

    @staticmethod
    def parse_email_bytes(raw: bytes) -> InboundMessage:
        """Parse a raw RFC 822 email into an InboundMessage."""
        msg = email.message_from_bytes(raw)
        from_header = msg.get("From", "")
        from_address = ""
        from_name = None
        addrs = getaddresses([from_header])
        if addrs:
            from_name, from_address = addrs[0]

        # Get plaintext body, prefer text/plain over text/html
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        else:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace") if isinstance(payload, bytes) else str(payload)

        date_header = msg.get("Date")
        try:
            received_at = parsedate_to_datetime(date_header) if date_header else datetime.now(timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)

        return InboundMessage(
            channel="email",
            platform_user_id=from_address or "",
            platform_chat_id=msg.get("To", "") or "",
            text=body.strip(),
            received_at=received_at,
            platform_message_id=msg.get("Message-ID") or None,
            user_display_name=from_name or None,
            metadata={
                "subject": msg.get("Subject", ""),
                "in_reply_to": msg.get("In-Reply-To"),
                "cc": msg.get("Cc", ""),
            },
        )
