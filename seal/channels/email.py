"""seal/channels/email.py — Email channel adapter, stdlib only.

Implements EmailAdapter(ChannelAdapter) using IMAP (receive) + SMTP (send).
No external dependencies — pure imaplib + smtplib + email.

Config keys:
    imap_host       str   IMAP server host (e.g. "imap.gmail.com")   [required]
    imap_port       int   IMAP port (default 993, SSL)
    smtp_host       str   SMTP server host (e.g. "smtp.gmail.com")   [required]
    smtp_port       int   SMTP port (default 587, STARTTLS)
    address         str   Agent email address                         [required]
    password        str   Password or app-specific password           [required]
    allowed_senders list  Whitelist of sender addresses (empty = allow all)
    poll_interval_s float Seconds between mailbox checks (default 15.0)
    mailbox         str   IMAP mailbox folder to watch (default "INBOX")

Usage:
    adapter = EmailAdapter({
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "address":   "agent@example.com",
        "password":  "app-password",
    })
    await adapter.connect()
    async for event in adapter.events():
        print(event.text)
"""
from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import re
import smtplib
import ssl
import time
from collections.abc import AsyncIterator
from email.header import decode_header as _decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr
from typing import Any, Mapping, Optional

from seal.channels.base import ChannelAdapter, ChannelHealth
from seal.channels.event import Attachment, MessageEvent, SendResult

logger = logging.getLogger("seal.channels.email")

_DEFAULT_POLL_S  = 15.0
_DEFAULT_MAILBOX = "INBOX"

# Automated sender patterns — silently ignored
_NOREPLY = re.compile(
    r"noreply|no.reply|donotreply|mailer.daemon|postmaster|bounce|"
    r"notifications@|automated@|auto.confirm|auto.reply",
    re.IGNORECASE,
)


def _decode_str(value: Any) -> str:
    if not value:
        return ""
    parts = []
    for fragment, charset in _decode_header(str(value)):
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(fragment))
    return " ".join(parts)


class EmailAdapter(ChannelAdapter):
    """Email channel adapter — IMAP poll + SMTP send, stdlib only."""

    channel = "email"

    def __init__(self, config: Mapping[str, object]) -> None:
        super().__init__(config)
        self._imap_host  = str(config["imap_host"])
        self._imap_port  = int(config.get("imap_port", 993))
        self._smtp_host  = str(config["smtp_host"])
        self._smtp_port  = int(config.get("smtp_port", 587))
        self._address    = str(config["address"])
        self._password   = str(config["password"])
        self._allowed: set[str] = {
            a.lower() for a in (config.get("allowed_senders") or [])  # type: ignore
        }
        self._poll_s     = float(config.get("poll_interval_s", _DEFAULT_POLL_S))
        self._mailbox    = str(config.get("mailbox", _DEFAULT_MAILBOX))

        self._imap:        Optional[imaplib.IMAP4_SSL] = None
        self._seen_uids:   set[bytes] = set()
        self._connected    = False
        self._last_event_at: Optional[float] = None
        self._last_error:  Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._imap_connect)
        self._connected = True
        logger.info("EmailAdapter connected (%s)", self._address)

    async def disconnect(self) -> None:
        self.stop()
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None
        self._connected = False
        logger.info("EmailAdapter disconnected")

    # ── io ────────────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[MessageEvent]:
        loop = asyncio.get_event_loop()
        while not self.stopping:
            try:
                messages = await loop.run_in_executor(None, self._fetch_new)
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("Email fetch error: %s", exc)
                await loop.run_in_executor(None, self._imap_reconnect)
                await asyncio.sleep(self._poll_s)
                continue

            for evt in messages:
                if self._allowed and evt.user_id.lower() not in self._allowed:
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
        subject = (metadata or {}).get("subject", "SEAL Agent Reply")
        loop = asyncio.get_event_loop()
        try:
            msg_id = await loop.run_in_executor(
                None, lambda: self._smtp_send(chat_id, str(subject), text, reply_to)
            )
            return SendResult(channel="email", chat_id=chat_id, message_id=msg_id)
        except Exception as exc:
            return SendResult(channel="email", chat_id=chat_id,
                              message_id="", success=False, error=str(exc))

    def healthcheck(self) -> ChannelHealth:
        return ChannelHealth(
            channel       = "email",
            connected     = self._connected,
            last_event_at = self._last_event_at,
            last_error    = self._last_error,
        )

    # ── IMAP helpers ──────────────────────────────────────────────────────────

    def _imap_connect(self) -> None:
        ctx  = ssl.create_default_context()
        imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, ssl_context=ctx)
        imap.login(self._address, self._password)
        imap.select(self._mailbox)
        # Seed seen UIDs with what's already in the inbox
        _, data = imap.uid("search", None, "ALL")
        if data and data[0]:
            for uid in data[0].split():
                self._seen_uids.add(uid)
        self._imap = imap

    def _imap_reconnect(self) -> None:
        try:
            if self._imap:
                self._imap.logout()
        except Exception:
            pass
        self._imap = None
        try:
            self._imap_connect()
        except Exception as exc:
            self._last_error = str(exc)

    def _fetch_new(self) -> list[MessageEvent]:
        if not self._imap:
            return []
        try:
            self._imap.select(self._mailbox)
            _, data = self._imap.uid("search", None, "ALL")
        except imaplib.IMAP4.error:
            self._imap_reconnect()
            return []

        if not data or not data[0]:
            return []

        all_uids  = set(data[0].split())
        new_uids  = all_uids - self._seen_uids
        self._seen_uids = all_uids

        results: list[MessageEvent] = []
        for uid in new_uids:
            try:
                _, msg_data = self._imap.uid("fetch", uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                evt = self._parse_email(uid.decode(), raw)
                if evt:
                    results.append(evt)
            except Exception as exc:
                logger.warning("Error parsing email uid=%s: %s", uid, exc)
        return results

    def _parse_email(self, uid: str, raw: bytes) -> Optional[MessageEvent]:
        msg   = email_lib.message_from_bytes(raw)
        from_ = _decode_str(msg.get("From", ""))
        _, sender_addr = parseaddr(from_)
        if not sender_addr:
            return None
        if _NOREPLY.search(sender_addr):
            return None
        subject = _decode_str(msg.get("Subject", "(no subject)"))
        body    = self._extract_body(msg)
        if not body.strip():
            return None
        text = f"Subject: {subject}\n\n{body}"
        return MessageEvent(
            channel    = "email",
            chat_id    = sender_addr.lower(),
            user_id    = sender_addr.lower(),
            text       = text,
            message_id = uid,
            is_dm      = True,
            metadata   = {"subject": subject, "from": from_},
        )

    @staticmethod
    def _extract_body(msg: email_lib.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not part.get("Content-Disposition"):
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    return payload.decode(charset, errors="replace") if payload else ""
        else:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            return payload.decode(charset, errors="replace") if payload else ""
        return ""

    # ── SMTP helpers ──────────────────────────────────────────────────────────

    def _smtp_send(self, to: str, subject: str, body: str,
                   in_reply_to: Optional[str]) -> str:
        mime = MIMEMultipart()
        mime["From"]    = self._address
        mime["To"]      = to
        mime["Date"]    = formatdate(localtime=True)
        mime["Subject"] = subject
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"]  = in_reply_to
        msg_id = f"<seal-{int(time.time()*1000)}@seal>"
        mime["Message-ID"] = msg_id
        mime.attach(MIMEText(body, "plain", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.login(self._address, self._password)
            smtp.sendmail(self._address, [to], mime.as_bytes())
        return msg_id
