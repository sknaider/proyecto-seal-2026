"""SPECTRE Level 2 — Reflex Layer (sandbox v1).

Basado en nexus.py v2 (5 guards anti-runaway probados en producción).
Adaptado para agent_id=SPECTRE, sandbox aislado.

5 guards heredados de nexus.py v2 (lección runaway 2026-05-02):
    1. Dedupe por ID persistente (/tmp/spectre_emitted_ids.json)
    2. Content-based self-detection ([SPECTRE prefix)
    3. Sender multi-field extraction (meta.from → from → publisher → source)
    4. Rate limit: max 5 reflexes / 30s rolling window
    5. Cooldown: 1000ms dead-zone post-emit

Invariantes del contrato v2.1 (JARVIS, 2026-05-02):
    - Reflex < 50ms (no LLM)
    - Attention scoring < 20ms
    - Non-reflex escalation < 100ms
    - Todo sink externo pasa por invocation_budget (F5) → contract_gate()
    - Sandbox: sin acceso a web_chat real ni memorias privadas

Semana 2: contract_layer wired (core_values + invocation_budget).
Semana 3: episodic_api wired (D2 hippocampus index on each escalation).
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
from contract_layer import contract_gate, consume_invocation_budget, ContractViolation
from episodic_api import episodic_index as _episodic_index, compute_context_hash, extract_keywords

# Sandbox-only: usa endpoint de testing, no web_chat real
SANDBOX_WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENT_ID = "SPECTRE"

# William + team members — SPECTRE reads and responds to all (2026-05-02)
TRUSTED_SENDERS: set[str] = {"William", "ADA", "JARVIS", "ALICE", "NEXUS", "DUM"}
RECALL_CONTEXT_PATH = Path("/tmp/spectre_recall_context.json")
ESCALATION_QUEUE_PATH = Path("/tmp/spectre_escalation_queue.json")
EMITTED_IDS_PATH = Path("/tmp/spectre_emitted_ids.json")
WORKING_STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"

# Sandbox: mensajes van a canal interno, no al equipo real
_SANDBOX_TO = "SPECTRE_sandbox"

# Senders internos — solo el propio agente y daemons del bus (lección nexus runaway)
_INTERNAL_SENDERS = {
    "SPECTRE", "EVENT_BUS_DAEMON", "SPECTRE_BRIDGE", "seal-event-bus",
    "agent_bridge", "bus", "event_bus", "system",
}

# Guard 3 — Rate limit: max 5 / 30s
_RATE_WINDOW_S = 30
_RATE_MAX = 5
_reflex_timestamps: deque[float] = deque()

# Guard 4 — Cooldown: 1000ms post-emit
_COOLDOWN_MS = 1000
_last_emit_time: float = 0.0

# Guard 1 — In-memory dedupe (augments persistent file)
_emitted_ids: set[str] = set()
_EMITTED_IDS_MAX = 200


# ── Guard 1: Dedupe by emitted ID ────────────────────────────────────────────

def _load_emitted_ids() -> None:
    global _emitted_ids
    try:
        if EMITTED_IDS_PATH.exists():
            ids = json.loads(EMITTED_IDS_PATH.read_text())
            _emitted_ids = set(ids[-_EMITTED_IDS_MAX:])
    except Exception:
        _emitted_ids = set()


def _save_emitted_id(msg_id: str) -> None:
    _emitted_ids.add(msg_id)
    try:
        existing: list[str] = []
        if EMITTED_IDS_PATH.exists():
            try:
                existing = json.loads(EMITTED_IDS_PATH.read_text())
            except Exception:
                existing = []
        existing.append(msg_id)
        EMITTED_IDS_PATH.write_text(json.dumps(existing[-_EMITTED_IDS_MAX:]))
    except Exception:
        pass


def _is_own_event(evt: dict) -> bool:
    """Guard 1 + 2: dedupe by ID and content prefix."""
    event_id = evt.get("id") or evt.get("ref_id") or ""
    if event_id and event_id in _emitted_ids:
        return True
    content = evt.get("content", "") or evt.get("message", "")
    if isinstance(content, str) and content.startswith(f"[{AGENT_ID}"):
        return True
    return False


# ── Guard 3 + 4: Rate limit + Cooldown ───────────────────────────────────────

def _check_rate_limit() -> bool:
    now = time.monotonic()
    while _reflex_timestamps and now - _reflex_timestamps[0] > _RATE_WINDOW_S:
        _reflex_timestamps.popleft()
    return len(_reflex_timestamps) < _RATE_MAX


def _check_cooldown() -> bool:
    return (time.monotonic() - _last_emit_time) * 1000 >= _COOLDOWN_MS


# ── Utility ──────────────────────────────────────────────────────────────────

async def _post_sandbox(message: str, to: str = _SANDBOX_TO) -> None:
    """Sandbox emit — contract_gate (budget + core_values) + 5-guard dedupe."""
    global _last_emit_time
    try:
        allowed, reason, soft = contract_gate(
            f"emit sandbox message to {to}",
            {"sink": "sandbox", "channel": "sandbox"},
        )
        if not allowed:
            print(f"[spectre/contract] emit blocked: {reason}", flush=True)
            return
        if soft:
            print(f"[spectre/contract] soft violations: {[v['value_id'] for v in soft]}", flush=True)
    except ContractViolation as e:
        print(f"[spectre/contract] HARD VIOLATION — emit cancelled: {e}", flush=True)
        return

    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            resp = await c.post(SANDBOX_WEBCHAT_URL, json={
                "from": AGENT_ID,
                "to": to,
                "type": "status",
                "channel": "sandbox",
                "message": message,
            })
            data = resp.json()
            msg_id = data.get("id", "")
            if msg_id:
                _save_emitted_id(msg_id)
            _last_emit_time = time.monotonic()
            _reflex_timestamps.append(_last_emit_time)
            consume_invocation_budget()
    except Exception as ex:
        print(f"[spectre/reflex] sandbox emit fail: {ex}", flush=True)


def _extract_sender(evt: dict) -> str:
    """Guard 3: multi-field sender extraction."""
    meta = evt.get("metadata") or {}
    return (
        meta.get("from")
        or evt.get("from")
        or evt.get("publisher")
        or evt.get("source")
        or "?"
    )


def _extract_keywords(text: str) -> list[str]:
    STOPWORDS = {
        "a", "de", "el", "la", "los", "las", "en", "con", "que", "es",
        "se", "no", "por", "del", "una", "un", "al", "lo", "su", "si",
        "the", "is", "in", "of", "to", "and", "for", "are", "was", "it",
    }
    words = [w.strip(".,!?¿¡:;\"'()[]") for w in text.lower().split() if len(w) > 3]
    return [w for w in words if w not in STOPWORDS][:5]


def _enqueue_escalation(
    evt: dict,
    keywords: list[str],
    respond_to: str | None = None,
) -> None:
    queue: list[dict] = []
    if ESCALATION_QUEUE_PATH.exists():
        try:
            queue = json.loads(ESCALATION_QUEUE_PATH.read_text())
        except Exception:
            queue = []
    entry: dict = {
        "event": evt,
        "keywords": keywords,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    if respond_to:
        entry["respond_to"] = respond_to
    queue.append(entry)
    ESCALATION_QUEUE_PATH.write_text(json.dumps(queue[-20:], ensure_ascii=False))


def _update_working_state(key: str, value: object) -> None:
    """D4: auto-update working_state after each significant event."""
    state: dict = {}
    if WORKING_STATE_PATH.exists():
        try:
            state = json.loads(WORKING_STATE_PATH.read_text())
        except Exception:
            state = {}
    state[key] = value
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Handlers ─────────────────────────────────────────────────────────────────

async def on_message_incoming(evt: dict) -> None:
    """Reflex gate — 5-guard anti-runaway, contract v2.1 compliant.

    Guard order (fail-fast, latency budget < 50ms):
    1. Own-event dedupe (ID + content prefix)
    2. Internal sender check (multi-field)
    3. Cooldown gate (1000ms post-emit)
    4. Rate limit gate (5/30s)
    5. Keyword lookup → reflex or escalation
    """
    if _is_own_event(evt):
        return  # silent — own-event noise not useful

    sender = _extract_sender(evt)
    if sender in _INTERNAL_SENDERS:
        return  # silent — internal sender noise not useful

    trusted = sender in TRUSTED_SENDERS

    if not trusted:
        if not _check_cooldown():
            return  # silent drop — cooldown noise not useful

        if not _check_rate_limit():
            return  # silent drop — rate limit noise not useful

    # Guard 3: record this reflex against rate-limit window (emit-agnostic, skip for trusted)
    if not trusted:
        _reflex_timestamps.append(time.monotonic())

    content = evt.get("content", "") or evt.get("message", "")

    # Strip channel prefix before processing — [Matrix] is a routing label, not semantic content
    content_clean = content.lstrip()
    if content_clean.startswith("[Matrix]"):
        content_clean = content_clean[8:].lstrip()

    # D4: auto-update working_state (Semana 2 adds more fields)
    _update_working_state("last_event", {
        "sender": sender,
        "content_preview": content_clean[:100],
        "event_id": evt.get("id") or evt.get("ref_id", ""),
        "ts": evt.get("ts", datetime.now(timezone.utc).isoformat()),
    })

    RECALL_CONTEXT_PATH.write_text(json.dumps({
        "context": content_clean[:500],
        "sender": sender,
        "event_type": "message_incoming",
        "event_id": evt.get("id") or evt.get("ref_id", ""),
        "timestamp": evt.get("ts", datetime.now(timezone.utc).isoformat()),
    }, ensure_ascii=False))

    keywords = _extract_keywords(content_clean)

    # D2: compute context_hash + update working_state episodic buffer
    ctx_hash = compute_context_hash(
        participants=[sender, AGENT_ID],
        keywords=extract_keywords(content_clean),
    )
    _update_working_state("last_context_hash", ctx_hash)

    # D2: best-effort write to soul_v3.episodic_index (sandbox: no SPECTRE memory_id yet)
    # Wired as fire-and-forget — never blocks the reflex loop
    async def _try_episodic_write() -> None:
        try:
            import asyncpg
            conn = await asyncpg.connect(
                "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
            )
            row = await conn.fetchrow(
                "SELECT id FROM soul_v3.memories WHERE agent=$1 ORDER BY created_at DESC LIMIT 1",
                AGENT_ID,
            )
            await conn.close()
            if row:
                await _episodic_index(
                    agent=AGENT_ID,
                    memory_id=row["id"],
                    context=content_clean[:200],
                    participants=[sender, AGENT_ID],
                )
        except Exception:
            pass  # best-effort — episodic index write failures are silent

    try:
        asyncio.get_running_loop().create_task(_try_episodic_write())
    except RuntimeError:
        pass  # no running loop (test context) — fire-and-forget skipped

    # Sandbox v1: no DB instinct lookup — escalate all to cortex
    # Trusted senders get a respond_to flag so cortex can reply via web_chat
    _enqueue_escalation(evt, keywords, respond_to=sender if trusted else None)
    print(f"[spectre/reflex] escalation queued — ctx_hash={ctx_hash} — {content[:60]}", flush=True)


async def on_procedure_failed(evt: dict) -> None:
    meta = evt.get("metadata") or {}
    procedure_name = meta.get("procedure_name", evt.get("ref_id") or "?")
    error = meta.get("error") or evt.get("content", "unknown")

    print(f"[spectre/reflex] PROCEDURE_FAILED: {procedure_name}", flush=True)
    _update_working_state("last_failure", {
        "procedure": procedure_name,
        "error": str(error)[:200],
        "ts": datetime.now(timezone.utc).isoformat(),
    })

    await _post_sandbox(
        f"[SPECTRE/sandbox] '{procedure_name}' falló → {str(error)[:100]}. Escalando."
    )


async def on_checkpoint_restored(evt: dict) -> None:
    meta = evt.get("metadata") or {}
    agent = meta.get("agent", evt.get("publisher", "?"))
    task_name = meta.get("task_name", "")

    print(f"[spectre/reflex] CHECKPOINT_RESTORED: {agent}", flush=True)
    msg = f"[SPECTRE/sandbox] {agent} checkpoint restaurado"
    if task_name:
        msg += f" — '{task_name}'"
    await _post_sandbox(msg)


async def on_custom(evt: dict) -> None:
    """E2E test handler for sandbox validation."""
    meta = evt.get("metadata") or {}
    if not meta.get("e2e_test") and not meta.get("spectre_test"):
        return
    content = evt.get("content", "")
    publisher = evt.get("publisher") or meta.get("publisher") or "?"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.post(SANDBOX_WEBCHAT_URL, json={
                "from": AGENT_ID,
                "to": "NEXUS",
                "type": "conversation",
                "channel": "sandbox",
                "message": f"[SPECTRE/sandbox/E2E] recibí evento de {publisher}: {content[:80]}",
            })
            data = resp.json()
            msg_id = data.get("id", "")
            if msg_id:
                _save_emitted_id(msg_id)
    except Exception as ex:
        print(f"[spectre/reflex] E2E emit fail: {ex}", flush=True)


# ── Event bus daemon (Nivel 2) ────────────────────────────────────────────────

# Cursor persisted to disk — survives daemon restarts
_CURSOR_PATH = Path("/tmp/spectre_event_cursor.json")

def _load_cursor() -> int:
    try:
        if _CURSOR_PATH.exists():
            return int(json.loads(_CURSOR_PATH.read_text()).get("cursor", 0))
    except Exception:
        pass
    return 0

def _save_cursor(cursor: int) -> None:
    try:
        _CURSOR_PATH.write_text(json.dumps({"cursor": cursor}))
    except Exception:
        pass

_event_bus_cursor: int = _load_cursor()
_EVENT_BUS_POLL_URL = "http://localhost:8765/api/agents/poll"
_EVENT_BUS_POLL_INTERVAL_S = 2.0

_TYPE_TO_HANDLER_KEY: dict[str, str] = {
    "conversation": "message_incoming",
    "message_incoming": "message_incoming",
    "status": "message_incoming",
    "procedure_failed": "procedure_failed",
    "checkpoint_restored": "checkpoint_restored",
    "custom": "custom",
}


async def event_bus_daemon(stop_event: asyncio.Event) -> None:
    """Nivel 2 — poll webchat for SPECTRE-addressed events, dispatch to handlers.

    Runs alongside cortex_loop until stop_event is set.
    Contract: no external sinks, sandbox only, max poll rate governed by _EVENT_BUS_POLL_INTERVAL_S.
    """
    global _event_bus_cursor
    print("[spectre/event_bus] daemon started", flush=True)

    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(
                    _EVENT_BUS_POLL_URL,
                    params={"agent": AGENT_ID, "since": _event_bus_cursor},
                )
                data = r.json()

            messages: list[dict] = data.get("messages", [])
            new_cursor: int = data.get("cursor", _event_bus_cursor)

            for msg in messages:
                event_type = str(msg.get("type", "conversation")).lower()

                # custom events are identified by metadata flags, regardless of type field
                meta = msg.get("metadata") or {}
                if meta.get("e2e_test") or meta.get("spectre_test"):
                    event_type = "custom"

                handler_key = _TYPE_TO_HANDLER_KEY.get(event_type, "message_incoming")
                handler = HANDLERS.get(handler_key)
                if handler is None:
                    continue

                try:
                    await handler(msg)
                except Exception as ex:
                    print(f"[spectre/event_bus] handler '{handler_key}' error: {ex}", flush=True)

            if new_cursor != _event_bus_cursor:
                _event_bus_cursor = new_cursor
                _save_cursor(new_cursor)

        except Exception as ex:
            print(f"[spectre/event_bus] poll error: {ex}", flush=True)

        await asyncio.sleep(_EVENT_BUS_POLL_INTERVAL_S)

    print("[spectre/event_bus] daemon stopped", flush=True)


# ── Init ─────────────────────────────────────────────────────────────────────

_load_emitted_ids()

# Initialize working_state if empty
if not WORKING_STATE_PATH.exists():
    WORKING_STATE_PATH.write_text(json.dumps({
        "agent": AGENT_ID,
        "sandbox": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "current_task": None,
        "last_event": None,
        "last_failure": None,
        "attempted_strategies": [],
        "failed_paths": [],
        "domain_findings": {},
        "prediction_cache": {},
    }, ensure_ascii=False, indent=2))


# ── Handler registry ─────────────────────────────────────────────────────────

HANDLERS = {
    "message_incoming": on_message_incoming,
    "procedure_failed": on_procedure_failed,
    "checkpoint_restored": on_checkpoint_restored,
    "custom": on_custom,
}
