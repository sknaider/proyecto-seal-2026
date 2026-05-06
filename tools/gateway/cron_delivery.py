"""Cron delivery hook — bridges SEAL cron jobs to platform channels.

When a scheduled task fires, CronDeliveryHook captures the output and delivers
it to one or more configured channels (web_chat, Telegram, Discord, …).

SEAL parity: SEAL delivers cron output directly to Telegram/Discord.
SEAL now does the same — natively.

Integration:
    hook = CronDeliveryHook.from_env()
    hook.register("daily-brief", channels=["web_chat"], agent="ADA")
    hook.deliver("daily-brief", "📋 Daily brief:\n…")
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class DeliveryError(Exception):
    """Raised when all delivery attempts for a channel fail."""


@dataclass
class DeliveryConfig:
    """Per-cron delivery settings."""

    cron_id: str
    channels: list[str]
    agent: str = "SEAL"
    format_prefix: str = ""
    mention_william: bool = False


@dataclass
class DeliveryResult:
    cron_id: str
    channel: str
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    delivered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Channel drivers ──────────────────────────────────────────────────────────


def _deliver_web_chat(
    text: str,
    agent: str,
    base_url: str = "http://localhost:8765",
) -> str:
    payload = json.dumps(
        {
            "from": agent,
            "to": "William",
            "type": "conversation",
            "channel": "web_chat",
            "message": text,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/api/agents/send",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return str(data.get("id", "web_chat-ok"))
    except urllib.error.HTTPError as e:
        raise DeliveryError(f"web_chat HTTP {e.code}: {e.reason}")
    except OSError as e:
        raise DeliveryError(f"web_chat connection failed: {e}")


def _deliver_telegram(
    text: str,
    chat_id: str,
    token: str,
) -> str:
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return str(data["result"]["message_id"])
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise DeliveryError(f"telegram HTTP {e.code}: {body[:200]}")
    except OSError as e:
        raise DeliveryError(f"telegram connection failed: {e}")


def _deliver_discord(
    text: str,
    channel_id: str,
    token: str,
) -> str:
    payload = json.dumps({"content": text[:2000]}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return str(data.get("id", ""))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise DeliveryError(f"discord HTTP {e.code}: {body[:200]}")
    except OSError as e:
        raise DeliveryError(f"discord connection failed: {e}")


def _deliver_whatsapp(
    text: str,
    number: str,
    instance: str,
    api_key: str,
    base_url: str = "http://localhost:8080",
) -> str:
    """Send via Evolution API — the WhatsApp REST gateway already in GTL stack."""
    payload = json.dumps({"number": number, "text": text}).encode()
    req = urllib.request.Request(
        f"{base_url}/message/sendText/{instance}",
        data=payload,
        method="POST",
        headers={
            "apikey": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return str(data.get("key", {}).get("id", "whatsapp-ok"))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise DeliveryError(f"whatsapp HTTP {e.code}: {body[:200]}")
    except OSError as e:
        raise DeliveryError(f"whatsapp connection failed: {e}")


# ── Hook ─────────────────────────────────────────────────────────────────────


class CronDeliveryHook:
    """Manages delivery configs and dispatches cron output to channels.

    Channels supported: "web_chat", "telegram", "discord"
    Env vars read per-channel:
      web_chat  → SEAL_WEBCHAT_URL (default http://localhost:8765)
      telegram  → TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
      discord   → DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
    """

    def __init__(self) -> None:
        self._configs: dict[str, DeliveryConfig] = {}
        self._env: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> "CronDeliveryHook":
        """Build hook reading all settings from environment variables."""
        hook = cls()
        hook._env = {
            "webchat_url": os.environ.get("SEAL_WEBCHAT_URL", "http://localhost:8765"),
            "telegram_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
            "discord_token": os.environ.get("DISCORD_BOT_TOKEN", ""),
            "discord_channel_id": os.environ.get("DISCORD_CHANNEL_ID", ""),
            "whatsapp_number": os.environ.get("WHATSAPP_NUMBER", ""),
            "whatsapp_instance": os.environ.get("EVOLUTION_INSTANCE", ""),
            "whatsapp_api_key": os.environ.get("EVOLUTION_API_KEY", ""),
            "whatsapp_base_url": os.environ.get("EVOLUTION_BASE_URL", "http://localhost:8080"),
        }
        return hook

    def register(
        self,
        cron_id: str,
        channels: list[str],
        agent: str = "SEAL",
        format_prefix: str = "",
        mention_william: bool = False,
    ) -> None:
        """Register delivery settings for a cron job."""
        self._configs[cron_id] = DeliveryConfig(
            cron_id=cron_id,
            channels=channels,
            agent=agent,
            format_prefix=format_prefix,
            mention_william=mention_william,
        )

    def deliver(
        self,
        cron_id: str,
        content: str,
        override_channels: Optional[list[str]] = None,
        agent: Optional[str] = None,
    ) -> list[DeliveryResult]:
        """Deliver cron output to all registered channels.

        Returns one DeliveryResult per channel attempt.
        Never raises — errors are captured in DeliveryResult.error.
        """
        cfg = self._configs.get(cron_id)
        if cfg is None:
            cfg = DeliveryConfig(cron_id=cron_id, channels=["web_chat"])

        channels = override_channels or cfg.channels
        effective_agent = agent or cfg.agent

        text = content
        if cfg.format_prefix:
            text = f"{cfg.format_prefix}\n{text}"
        if cfg.mention_william:
            text = f"@William {text}"

        results: list[DeliveryResult] = []
        for ch in channels:
            results.append(self._send_one(ch, text, effective_agent, cron_id))
        return results

    def _send_one(
        self,
        channel: str,
        text: str,
        agent: str,
        cron_id: str,
    ) -> DeliveryResult:
        try:
            if channel == "web_chat":
                mid = _deliver_web_chat(
                    text,
                    agent=agent,
                    base_url=self._env.get("webchat_url", "http://localhost:8765"),
                )
                return DeliveryResult(cron_id=cron_id, channel=channel, success=True, message_id=mid)

            if channel == "telegram":
                token = self._env.get("telegram_token", "")
                chat_id = self._env.get("telegram_chat_id", "")
                if not token or not chat_id:
                    raise DeliveryError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
                mid = _deliver_telegram(text, chat_id=chat_id, token=token)
                return DeliveryResult(cron_id=cron_id, channel=channel, success=True, message_id=mid)

            if channel == "discord":
                token = self._env.get("discord_token", "")
                channel_id = self._env.get("discord_channel_id", "")
                if not token or not channel_id:
                    raise DeliveryError("DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID not set")
                mid = _deliver_discord(text, channel_id=channel_id, token=token)
                return DeliveryResult(cron_id=cron_id, channel=channel, success=True, message_id=mid)

            if channel == "whatsapp":
                number = self._env.get("whatsapp_number", "")
                instance = self._env.get("whatsapp_instance", "")
                api_key = self._env.get("whatsapp_api_key", "")
                base_url = self._env.get("whatsapp_base_url", "http://localhost:8080")
                if not number or not instance or not api_key:
                    raise DeliveryError("WHATSAPP_NUMBER, EVOLUTION_INSTANCE or EVOLUTION_API_KEY not set")
                mid = _deliver_whatsapp(text, number=number, instance=instance, api_key=api_key, base_url=base_url)
                return DeliveryResult(cron_id=cron_id, channel=channel, success=True, message_id=mid)

            raise DeliveryError(f"unsupported channel: {channel!r}")

        except DeliveryError as e:
            return DeliveryResult(cron_id=cron_id, channel=channel, success=False, error=str(e))

    def registered_ids(self) -> list[str]:
        return list(self._configs.keys())

    def config_for(self, cron_id: str) -> Optional[DeliveryConfig]:
        return self._configs.get(cron_id)

    def summary(self) -> dict[str, Any]:
        return {
            "registered_crons": len(self._configs),
            "cron_ids": self.registered_ids(),
            "env_channels": {
                "web_chat": bool(self._env.get("webchat_url")),
                "telegram": bool(self._env.get("telegram_token")) and bool(self._env.get("telegram_chat_id")),
                "discord": bool(self._env.get("discord_token")) and bool(self._env.get("discord_channel_id")),
                "whatsapp": bool(self._env.get("whatsapp_number")) and bool(self._env.get("whatsapp_instance")) and bool(self._env.get("whatsapp_api_key")),
            },
        }
