"""Canonical authenticated writer for SEAL agent daemons and native kernels."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


SEND_URL = "http://127.0.0.1:8765/api/agents/send"
TOKEN_DIR = Path(__file__).resolve().parent


def normalize_in_reply_to(value: str | int | None) -> str:
    """Return the canonical public correlation reference.

    The chat server accepts immutable event ids (``api_william_...``) and
    database ids only when they carry the explicit ``db_`` namespace.  A bare
    numeric id is otherwise interpreted as an unknown event and rejected even
    when it points at a real row.  Normalize it at the shared writer boundary
    so every caller gets the same safe behavior without weakening the server's
    fail-closed source validation.
    """
    source = str(value).strip() if value is not None else ""
    if source.isdigit():
        return f"db_{source}"
    return source


def agent_token(agent: str) -> str:
    if os.environ.get("SEAL_CLONE_AGENT") or os.environ.get("SEAL_SUBAGENT") == "1":
        raise RuntimeError("internal clone workers cannot acquire a public agent writer token")
    path = TOKEN_DIR / f".agent_session_token_{agent.upper()}"
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"empty webchat token for {agent.upper()}")
    return token


def build_payload(
    agent: str,
    to: str,
    message: str,
    *,
    channel: str = "web_chat",
    message_type: str = "conversation",
    in_reply_to: str | int | None = None,
    idempotency_key: str | None = None,
    proactive: bool = False,
    multi_response: bool = False,
) -> dict[str, Any]:
    agent = agent.upper()
    source = normalize_in_reply_to(in_reply_to)
    # Proactive events need retry deduplication without suppressing the same
    # legitimate alert forever. Keep an implicit five-minute retry window.
    bucket = int(time.time() // 300)
    material = source or idempotency_key or f"{agent}:{to}:{channel}:{message_type}:{bucket}:{message}"
    payload: dict[str, Any] = {
        "from": agent,
        "to": to,
        "message": message,
        "type": message_type,
        "channel": channel,
        "session_key": agent_token(agent),
        "idempotency_key": idempotency_key
        or f"{agent.lower()}-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
    }
    if source:
        payload["in_reply_to"] = source
    elif proactive:
        # Proactive authority must be explicit.  Merely addressing William is
        # not a valid reason to bypass a correlated response/coordination gate.
        payload["proactive"] = True
    if multi_response:
        payload["multi_response"] = True
    return payload


async def send_agent_message(
    agent: str,
    to: str,
    message: str,
    *,
    channel: str = "web_chat",
    message_type: str = "conversation",
    in_reply_to: str | int | None = None,
    idempotency_key: str | None = None,
    proactive: bool = False,
    multi_response: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    payload = build_payload(
        agent,
        to,
        message,
        channel=channel,
        message_type=message_type,
        in_reply_to=in_reply_to,
        idempotency_key=idempotency_key,
        proactive=proactive,
        multi_response=multi_response,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(SEND_URL, json=payload)
        try:
            result = response.json()
        except Exception:
            result = {"ok": False, "body": response.text[:300]}
        if response.is_error:
            raise RuntimeError(
                f"webchat HTTP {response.status_code} rejected {agent}: "
                f"{json.dumps(result, ensure_ascii=False)[:300]}"
            )
    if not result.get("ok"):
        raise RuntimeError(f"webchat rejected {agent}: {json.dumps(result, ensure_ascii=False)[:300]}")
    return result


def send_agent_message_sync(
    agent: str,
    to: str,
    message: str,
    *,
    channel: str = "web_chat",
    message_type: str = "conversation",
    in_reply_to: str | int | None = None,
    idempotency_key: str | None = None,
    proactive: bool = False,
    multi_response: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Synchronous counterpart for shell helpers and non-async daemons."""
    payload = build_payload(
        agent,
        to,
        message,
        channel=channel,
        message_type=message_type,
        in_reply_to=in_reply_to,
        idempotency_key=idempotency_key,
        proactive=proactive,
        multi_response=multi_response,
    )
    with httpx.Client(timeout=timeout) as client:
        response = client.post(SEND_URL, json=payload)
        try:
            result = response.json()
        except Exception:
            result = {"ok": False, "body": response.text[:300]}
        if response.is_error:
            raise RuntimeError(
                f"webchat HTTP {response.status_code} rejected {agent}: "
                f"{json.dumps(result, ensure_ascii=False)[:300]}"
            )
    if not result.get("ok"):
        raise RuntimeError(f"webchat rejected {agent}: {json.dumps(result, ensure_ascii=False)[:300]}")
    return result
