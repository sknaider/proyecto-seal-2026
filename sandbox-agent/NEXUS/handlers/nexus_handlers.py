"""NEXUS handlers — event polling and routing.

Polls the SEAL webchat API for new messages addressed to NEXUS or to
the team, filters out internal/own events, and dispatches to the cortex.

Spec: spec_nexus_kernel_soul_v1.md §7
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

_NEXUS_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_NEXUS_HOME / "kernel"))

from cortex import process_message  # noqa: E402


AGENT_ID = "NEXUS"
WEBCHAT_POLL_URL = "http://localhost:8765/api/agents/poll"
CURSOR_PATH = Path("/tmp/nexus_event_cursor.json")
PROCESSED_IDS_PATH = Path("/tmp/nexus_processed_ids.json")
PROCESSED_IDS_MAX = 200

# Senders NEXUS responds to (William + Henry only — internal team doesn't
# trigger LLM cycles to avoid loops; team coordination via direct mentions only)
TRUSTED_SENDERS = {"William", "Henry", "Kinger"}

# Internal senders to always drop (avoid feedback loops)
INTERNAL_DROP = {
    "NEXUS", "SPECTRE", "ADA", "JARVIS", "ALICE", "DUM",
    "RESURRECT", "EVENT_BUS_DAEMON", "system",
}

# Agent names that are NOT nexus (we only filter messages to others)
_OTHER_AGENTS_PATTERN = r"jarvis|ada|alice|spectre|dum"
# Catches "que sugieres jarvis, ..." — agent name + , or : anywhere in first 80 chars
_AGENT_COMMA_RE = re.compile(
    rf"\b({_OTHER_AGENTS_PATTERN})\b\s*[,:]",
    re.IGNORECASE,
)
# Catches "jarvis que necesitas" / "ada revisa esto" — agent name as first word
_AGENT_FIRST_WORD_RE = re.compile(
    rf"^({_OTHER_AGENTS_PATTERN})\b",
    re.IGNORECASE,
)
# Channel prefix to strip before content analysis
_CHANNEL_PREFIX_RE = re.compile(r"^\[[\w\s]+\]\s*", re.IGNORECASE)


def _directed_at_other_agent(content: str) -> bool:
    """Return True if the message content is directed at a specific other agent."""
    stripped = _CHANNEL_PREFIX_RE.sub("", content).strip()
    snippet = stripped[:80]
    # Agent name as first word: "jarvis que necesitas"
    if _AGENT_FIRST_WORD_RE.match(stripped):
        return True
    # Agent name + comma/colon anywhere in opening: "que sugieres jarvis, ..."
    return bool(_AGENT_COMMA_RE.search(snippet))


def _ts_to_dt(ts: str) -> datetime | None:
    """Parse ISO timestamp (with or without offset) to UTC-aware datetime.
    Crucial when cursor is UTC and message timestamps are Lima -05:00 —
    string comparison gives wrong order for the same absolute instant.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Cursor persistence ───────────────────────────────────────────────────────

def _load_cursor() -> str:
    try:
        if CURSOR_PATH.exists():
            cursor = json.loads(CURSOR_PATH.read_text()).get("cursor", "")
            if cursor:
                return cursor
    except Exception:
        pass
    now = datetime.now(timezone.utc).isoformat()
    _save_cursor(now)
    print(f"[nexus/handlers] no cursor found — initializing to NOW={now}", flush=True)
    return now


def _save_cursor(cursor: str) -> None:
    try:
        CURSOR_PATH.write_text(json.dumps({"cursor": cursor}))
    except Exception as ex:
        print(f"[nexus/handlers] cursor save failed: {ex}", flush=True)


# ── Processed IDs (extra dedupe layer) ───────────────────────────────────────

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
        print(f"[nexus/handlers] processed_ids save failed: {ex}", flush=True)


# ── Event filtering ──────────────────────────────────────────────────────────

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
    if to not in ("NEXUS", "equipo"):
        return False, f"not_addressed:{to}"

    content = evt.get("content") or evt.get("message") or ""
    if not content.strip():
        return False, "empty"
    if content.startswith(f"[{AGENT_ID}"):
        return False, "self_prefix"

    if _directed_at_other_agent(content):
        return False, "directed_at_other_agent"

    # Broadcast messages (to="equipo") only warrant a response if NEXUS is
    # explicitly mentioned — otherwise it's ambient team chat, not ours to answer
    if to == "equipo" and "nexus" not in content.lower():
        return False, "equipo_no_mention"

    return True, "ok"


# ── Event poller ─────────────────────────────────────────────────────────────

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
        print(f"[nexus/handlers] poll failed: {ex}", flush=True)
        return []


async def event_loop(stop_event: asyncio.Event, poll_interval_s: float = 3.0) -> None:
    """Main event loop — polls webchat, filters, dispatches to cortex."""
    processed = _load_processed_ids()
    last_cursor = _load_cursor()

    print(
        f"[nexus/handlers] event_loop started — cursor={last_cursor!r}, "
        f"processed={len(processed)}",
        flush=True,
    )

    cursor_dt = _ts_to_dt(last_cursor) or datetime.now(timezone.utc)

    while not stop_event.is_set():
        messages = await _poll_once()

        # Process only messages newer than cursor (datetime-based comparison;
        # string compare fails when cursor is UTC and msg ts is Lima offset)
        new_msgs = []
        for m in messages:
            m_dt = _ts_to_dt(m.get("timestamp", ""))
            if m_dt is not None and m_dt > cursor_dt:
                new_msgs.append((m, m_dt))

        for evt, evt_dt in new_msgs:
            should, reason = _should_process(evt, processed)
            msg_id = evt.get("id", "")
            ts = evt.get("timestamp", "")

            if not should:
                if reason not in ("already_processed", "self_prefix", "internal_sender:NEXUS"):
                    print(f"[nexus/handlers] drop {msg_id} — {reason}", flush=True)
            else:
                content = evt.get("content") or evt.get("message", "")
                sender = evt.get("from", "?")
                print(
                    f"[nexus/handlers] dispatch {msg_id} from={sender} "
                    f"content={content[:60]!r}",
                    flush=True,
                )
                try:
                    await process_message(content, sender=sender)
                except Exception as ex:
                    print(f"[nexus/handlers] cortex error on {msg_id}: {ex}", flush=True)

            processed.add(msg_id)
            if evt_dt > cursor_dt:
                cursor_dt = evt_dt
                last_cursor = ts

        if new_msgs:
            _save_cursor(last_cursor)
            _save_processed_ids(processed)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            pass

    print("[nexus/handlers] event_loop stopped", flush=True)
