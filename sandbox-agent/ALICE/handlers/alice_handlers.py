"""ALICE handlers — webchat polling and dispatch to cortex.

Polls the SEAL webchat API for messages addressed to ALICE or to the team
(when she's mentioned), filters out internal/own events, dispatches to the
cortex for analysis. Mirrors the NEXUS pattern; identifiers and rules
are ALICE-specific.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

_ALICE_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ALICE_HOME / "kernel"))

from cortex import process_message  # noqa: E402


AGENT_ID = "ALICE"
WEBCHAT_POLL_URL = "http://localhost:8765/api/agents/poll"
CURSOR_PATH = Path("/tmp/alice_event_cursor.json")
PROCESSED_IDS_PATH = Path("/tmp/alice_processed_ids.json")
PROCESSED_IDS_MAX = 200

TRUSTED_SENDERS = {"William", "Henry", "Kinger"}

INTERNAL_DROP = {
    "ALICE", "NEXUS", "SPECTRE", "ADA", "JARVIS", "DUM",
    "RESURRECT", "EVENT_BUS_DAEMON", "system",
}

_OTHER_AGENTS_PATTERN = r"jarvis|ada|nexus|spectre|dum"
_AGENT_COMMA_RE = re.compile(
    rf"\b({_OTHER_AGENTS_PATTERN})\b\s*[,:]",
    re.IGNORECASE,
)
_AGENT_FIRST_WORD_RE = re.compile(
    rf"^({_OTHER_AGENTS_PATTERN})\b",
    re.IGNORECASE,
)
_CHANNEL_PREFIX_RE = re.compile(r"^\[[\w\s]+\]\s*", re.IGNORECASE)


def _directed_at_other_agent(content: str) -> bool:
    stripped = _CHANNEL_PREFIX_RE.sub("", content).strip()
    snippet = stripped[:80]
    if _AGENT_FIRST_WORD_RE.match(stripped):
        return True
    return bool(_AGENT_COMMA_RE.search(snippet))


def _load_cursor() -> str:
    try:
        if CURSOR_PATH.exists():
            return json.loads(CURSOR_PATH.read_text()).get("cursor", "")
    except Exception:
        pass
    return ""


def _save_cursor(cursor: str) -> None:
    try:
        CURSOR_PATH.write_text(json.dumps({"cursor": cursor}))
    except Exception as ex:
        print(f"[alice/handlers] cursor save failed: {ex}", flush=True)


def _load_processed_ids() -> set[str]:
    try:
        if PROCESSED_IDS_PATH.exists():
            return set(json.loads(PROCESSED_IDS_PATH.read_text())[-PROCESSED_IDS_MAX:])
    except Exception:
        pass
    return set()


def _save_processed_ids(ids: set[str]) -> None:
    try:
        PROCESSED_IDS_PATH.write_text(json.dumps(list(ids)[-PROCESSED_IDS_MAX:]))
    except Exception as ex:
        print(f"[alice/handlers] processed_ids save failed: {ex}", flush=True)


def _should_process(evt: dict, processed: set[str]) -> tuple[bool, str]:
    msg_id = evt.get("id", "")
    if not msg_id:
        return False, "no_id"
    if msg_id in processed:
        return False, "already_processed"

    sender = evt.get("from", "")
    if sender in INTERNAL_DROP:
        return False, f"internal_sender:{sender}"
    if sender not in TRUSTED_SENDERS:
        return False, f"untrusted_sender:{sender}"

    to = evt.get("to", "")
    if to not in ("ALICE", "equipo"):
        return False, f"not_addressed:{to}"

    content = evt.get("content") or evt.get("message") or ""
    if not content.strip():
        return False, "empty"
    if content.startswith(f"[{AGENT_ID}"):
        return False, "self_prefix"

    if _directed_at_other_agent(content):
        return False, "directed_at_other_agent"

    if to == "equipo" and "alice" not in content.lower():
        return False, "equipo_no_mention"

    return True, "ok"


async def _poll_once() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(
                WEBCHAT_POLL_URL,
                params={"agent": AGENT_ID, "limit": 50},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("messages", [])
    except Exception as ex:
        print(f"[alice/handlers] poll failed: {ex}", flush=True)
        return []


async def event_loop(stop_event: asyncio.Event, poll_interval_s: float = 3.0) -> None:
    processed = _load_processed_ids()
    last_cursor = _load_cursor()

    print(
        f"[alice/handlers] event_loop started — cursor={last_cursor!r}, "
        f"processed={len(processed)}",
        flush=True,
    )

    while not stop_event.is_set():
        messages = await _poll_once()
        new_msgs = [m for m in messages if m.get("timestamp", "") > last_cursor]

        for evt in new_msgs:
            should, reason = _should_process(evt, processed)
            msg_id = evt.get("id", "")
            ts = evt.get("timestamp", "")

            if not should:
                if reason not in ("already_processed", "self_prefix", "internal_sender:ALICE"):
                    print(f"[alice/handlers] drop {msg_id} — {reason}", flush=True)
            else:
                content = evt.get("content") or evt.get("message", "")
                sender = evt.get("from", "?")
                print(
                    f"[alice/handlers] dispatch {msg_id} from={sender} "
                    f"content={content[:60]!r}",
                    flush=True,
                )
                try:
                    await process_message(content, sender=sender)
                except Exception as ex:
                    print(f"[alice/handlers] cortex error on {msg_id}: {ex}", flush=True)

            processed.add(msg_id)
            if ts > last_cursor:
                last_cursor = ts

        if new_msgs:
            _save_cursor(last_cursor)
            _save_processed_ids(processed)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            pass

    print("[alice/handlers] event_loop stopped", flush=True)
