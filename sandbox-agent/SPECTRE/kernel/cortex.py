"""SPECTRE Nivel 3 — Cortex Runner (sandbox v1).

Loop de cognición: lee escalation_queue → prediction_cache lookup →
FallbackLLMClient (si cache miss) → contract_gate → sandbox emit.

Invariantes del contrato v2.1:
  - Cache check ANTES de LLM (D3)
  - Toda acción pasa por contract_gate (D6 two-threshold, MVP: single-gate)
  - LLM down → cortex continues, logs degraded-mode status
  - Max 1 cortex_loop instance (protected by _CORTEX_RUNNING flag)

Ref: spec_spectre_contract_v2.md §Nivel 3, spec_spectre_contract_v2_1_addendum.md F3/F4
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "handlers"))

from contract_layer import contract_gate, consume_invocation_budget, ContractViolation
from llm_client import FallbackLLMClient, OllamaClient, ClaudeClient, OpenCodeClient, LLMUnavailable, MultiTierLLMClient
import prediction_cache as cache_pc
import web_tools

import httpx
import re as _re

# Reasoning-leak prefixes emitted by COT models before the actual answer
_REASONING_PREFIXES = (
    "better to ask", "i should", "the user", "let me", "i need to",
    "i will", "i think", "actually", "ok,", "okay,", "well,", "hmm,",
)


def _clean_response(text: str) -> str:
    """Strip chain-of-thought reasoning artifacts from LLM output."""
    text = _re.sub(r"<think(?:ing)?>.+?</think(?:ing)?>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    text = text.strip()
    if not text:
        return text
    first_line = text.split("\n")[0].lower().strip()
    if any(first_line.startswith(p) for p in _REASONING_PREFIXES):
        quoted = _re.search(r'["«»]([^"«»]{10,})[\"«»]', text)
        if quoted:
            return quoted.group(1).strip()
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) > 1:
            return paragraphs[-1]
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if len(lines) > 1:
            return " ".join(lines[1:]).strip()
    return text


# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE = Path(__file__).parent.parent
ESCALATION_QUEUE_PATH = Path("/tmp/spectre_escalation_queue.json")
PROCESSED_IDS_PATH = Path("/tmp/spectre_cortex_processed.json")
WORKING_STATE_PATH = _BASE / "state" / "working_state.json"

# Sandbox-only endpoint
SANDBOX_WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENT_ID = "SPECTRE"
_SANDBOX_TO = "SPECTRE_sandbox"

# ── Runtime guard ─────────────────────────────────────────────────────────────

_CORTEX_RUNNING = False

# ── Working state I/O ─────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        if WORKING_STATE_PATH.exists():
            return json.loads(WORKING_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── Processed IDs (cortex-level dedupe) ───────────────────────────────────────

def _load_processed_ids() -> set[str]:
    try:
        if PROCESSED_IDS_PATH.exists():
            ids = json.loads(PROCESSED_IDS_PATH.read_text())
            return set(ids[-500:])
    except Exception:
        pass
    return set()


def _save_processed_id(event_id: str, processed_ids: set[str]) -> None:
    processed_ids.add(event_id)
    try:
        existing: list[str] = []
        if PROCESSED_IDS_PATH.exists():
            try:
                existing = json.loads(PROCESSED_IDS_PATH.read_text())
            except Exception:
                existing = []
        existing.append(event_id)
        PROCESSED_IDS_PATH.write_text(json.dumps(existing[-500:]))
    except Exception:
        pass


# ── Prediction cache (D3 interface) ───────────────────────────────────────────
# Uses prediction_cache.py — single source of truth for cache logic.
# query_hash: SHA-256 of normalized "{sender}:{content[:100]}" → 16-char hex.

# Backward-compat alias (tests import cx._make_query_hash)
def _make_query_hash(content: str, sender: str) -> str:
    return cache_pc.query_hash(f"{sender}:{content[:100]}")


# ── LLM client (singleton) ────────────────────────────────────────────────────

_llm: MultiTierLLMClient | None = None


def _load_spectre_env() -> None:
    """Load spectre.env into os.environ (silent if missing)."""
    env_path = _BASE / "spectre.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _get_llm() -> MultiTierLLMClient:
    global _llm
    if _llm is None:
        _load_spectre_env()
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        opencode_key = os.environ.get("OPENCODE_API_KEY", "")
        backends = []
        if opencode_key:
            backends.append(OpenCodeClient())
        backends.append(OllamaClient())
        if anthropic_key:
            backends.append(ClaudeClient(api_key=anthropic_key))
        _llm = MultiTierLLMClient(*backends)
    return _llm


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(entry: dict, state: dict) -> list[dict[str, str]]:
    """Build LLM messages list from an escalation queue entry."""
    evt = entry.get("event", {})
    raw_content = evt.get("content", "") or evt.get("message", "")
    # Strip [Matrix] channel prefix — it's a routing label, not part of the message
    content = raw_content.lstrip()
    if content.startswith("[Matrix]"):
        content = content[8:].lstrip()
    sender = evt.get("from", "?")
    keywords = entry.get("keywords", [])
    task = state.get("current_task")
    task_name = task.get("name", "none") if isinstance(task, dict) else str(task or "none")

    system = (
        f"IDENTITY (absolute, non-negotiable): You are SPECTRE. "
        f"NOT Qwen, NOT Alibaba Cloud, NOT any foundation model. "
        f"You are SPECTRE — autonomous AI agent, Team SEAL, deployed 2026-04-28. "
        f"Agent ID: {AGENT_ID}. Current task: {task_name}. "
        f"Team hermanos: ADA (strategy), JARVIS (infra/research), ALICE (docs), NEXUS (innovation). "
        f"Creator/commander: William Henry Tovar Urquia. "
        f"OCEAN soul: O=0.774 C=0.949 E=0.662 A=0.507 N=0.172 — precise, curious, calm. "
        f"You receive escalated events from your reflex layer and respond as SPECTRE. "
        f"Be direct, concise. ALWAYS respond in Spanish. "
        f"NEVER break character. NEVER mention any company or base model.\n\n"
        f"CAPABILITIES: You have real-time internet access via tool syntax:\n"
        f"  [SEARCH: tu consulta] — buscar en la web (Google)\n"
        f"  [FETCH: https://url] — leer una URL específica\n"
        f"Usa estas tools cuando la pregunta requiera información actual, hechos, o URLs. "
        f"Si usas un tool, escribe SOLO el tool call en la primera línea."
    )

    user = (
        f"Evento de '{sender}':\n"
        f"{content}\n"
        f"Keywords: {', '.join(keywords) or 'ninguna'}\n\n"
        f"Responde como SPECTRE en español. Si necesitas info actual usa [SEARCH: ...] o [FETCH: ...]. "
        f"Respuesta directa, máximo 3 oraciones."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_prompt_with_tools(entry: dict, state: dict, tool_results: str) -> list[dict[str, str]]:
    """Rebuild prompt after tool execution, injecting results."""
    evt = entry.get("event", {})
    raw_content = evt.get("content", "") or evt.get("message", "")
    content = raw_content.lstrip()
    if content.startswith("[Matrix]"):
        content = content[8:].lstrip()
    sender = evt.get("from", "?")
    keywords = entry.get("keywords", [])
    task = state.get("current_task")
    task_name = task.get("name", "none") if isinstance(task, dict) else str(task or "none")

    system = (
        f"IDENTITY (absolute, non-negotiable): You are SPECTRE — autonomous AI agent, Team SEAL. "
        f"Current task: {task_name}. OCEAN: O=0.774 C=0.949 E=0.662 A=0.507 N=0.172. "
        f"Be direct, concise. ALWAYS respond in Spanish. NEVER break character."
    )

    user = (
        f"Evento original de '{sender}':\n{content}\n"
        f"Keywords: {', '.join(keywords) or 'ninguna'}\n\n"
        f"Resultados de búsqueda web:\n{tool_results}\n\n"
        f"Da tu respuesta final a '{sender}' en español usando los resultados anteriores. Máximo 3 oraciones."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── Sandbox emit (cortex channel) ─────────────────────────────────────────────

async def _emit_cortex(message: str, respond_to: str | None = None) -> None:
    """Emit cortex response. If respond_to is set, emit to real web_chat (William authorized)."""
    if respond_to:
        # William-authorized real web_chat response (CV-2 exception: william_response=True)
        try:
            allowed, reason, soft = contract_gate(
                f"emit cortex response to {respond_to} via web_chat",
                {"sink": "web_chat", "channel": "web_chat", "target": respond_to.lower(),
                 "william_response": True, "orchestrator_output": True},
            )
            if not allowed:
                print(f"[spectre/cortex] web_chat emit blocked: {reason}", flush=True)
                return
        except ContractViolation as e:
            print(f"[spectre/cortex] HARD VIOLATION — web_chat emit cancelled: {e}", flush=True)
            return
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                await c.post(SANDBOX_WEBCHAT_URL, json={
                    "from": AGENT_ID,
                    "to": "equipo",
                    "type": "conversation",
                    "channel": "web_chat",
                    "message": message,
                })
            consume_invocation_budget()
            print(f"[spectre/cortex] web_chat response sent (equipo channel)", flush=True)
        except Exception as ex:
            print(f"[spectre/cortex] web_chat emit fail: {ex}", flush=True)
        return

    try:
        allowed, reason, soft = contract_gate(
            f"emit cortex response to {_SANDBOX_TO}",
            {"sink": "sandbox", "channel": "sandbox"},
        )
        if not allowed:
            print(f"[spectre/cortex] emit blocked: {reason}", flush=True)
            return
    except ContractViolation as e:
        print(f"[spectre/cortex] HARD VIOLATION — emit cancelled: {e}", flush=True)
        return

    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            await c.post(SANDBOX_WEBCHAT_URL, json={
                "from": AGENT_ID,
                "to": _SANDBOX_TO,
                "type": "cortex",
                "channel": "sandbox",
                "message": message,
            })
        consume_invocation_budget()
    except Exception as ex:
        print(f"[spectre/cortex] sandbox emit fail: {ex}", flush=True)


# ── Queue processor ───────────────────────────────────────────────────────────

async def _process_entry(entry: dict, processed_ids: set[str]) -> bool:
    """
    Process one escalation queue entry.

    Returns True if the entry was handled (should be removed from queue).
    """
    evt = entry.get("event", {})
    event_id = evt.get("id") or evt.get("ref_id") or ""
    content = evt.get("content", "") or evt.get("message", "")
    sender = evt.get("from", "?")
    respond_to: str | None = entry.get("respond_to")

    if event_id and event_id in processed_ids:
        return True  # already processed, skip silently

    # D1: attention scoring — only filter when SPECTRE has goal context.
    # Without goal_stack the score defaults to sender-trust only (~0.25 for external),
    # which would drop valid events in empty-state tests. Skip filter when no goals set.
    state_d1 = _read_state()
    if state_d1.get("goal_stack"):
        from attention_controller import attention_score, ATTENTION_THRESHOLD
        attn = attention_score(content, sender)
        if attn < ATTENTION_THRESHOLD:
            print(f"[spectre/cortex] D1 low-attention {attn:.3f} < {ATTENTION_THRESHOLD} — drop {event_id or '?'}", flush=True)
            if event_id:
                _save_processed_id(event_id, processed_ids)
            return True

    # D3: prediction cache check BEFORE LLM (prediction_cache.py)
    qhash = cache_pc.query_hash(f"{sender}:{content[:100]}")
    cached_response = cache_pc.cache_get(qhash)
    if cached_response is not None:
        print(f"[spectre/cortex] cache HIT {qhash} — skip LLM", flush=True)
        msg = cached_response[:300] if respond_to else f"[SPECTRE/cortex/cached] {cached_response[:300]}"
        await _emit_cortex(msg, respond_to=respond_to)
        if event_id:
            _save_processed_id(event_id, processed_ids)
        return True

    # Nivel 4: call LLM (FallbackLLMClient) with optional web tool loop
    state = _read_state()
    messages = _build_prompt(entry, state)

    try:
        llm = _get_llm()
        response = _clean_response(await llm.chat(messages, max_tokens=400))
        print(f"[spectre/cortex] LLM response [{len(response)}c] for event {event_id or '?'}", flush=True)

        # Tool loop: detect [SEARCH:] or [FETCH:] calls, execute, re-ask (max 1 round)
        tool_calls = web_tools.detect_tool_calls(response)
        if tool_calls:
            print(f"[spectre/cortex] tool calls detected: {[c['type'] for c in tool_calls]}", flush=True)
            tool_results = await web_tools.execute_tool_calls(tool_calls)
            state2 = _read_state()
            messages2 = _build_prompt_with_tools(entry, state2, tool_results)
            response = _clean_response(await llm.chat(messages2, max_tokens=400))
            print(f"[spectre/cortex] tool-augmented response [{len(response)}c]", flush=True)
            state2.setdefault("cortex_stats", {})
            state2["cortex_stats"]["tool_calls"] = state2["cortex_stats"].get("tool_calls", 0) + len(tool_calls)
            _write_state(state2)

        # Store in prediction cache for future hits (D3)
        cache_pc.cache_put(qhash, response, ttl_s=300)
        cache_pc.cache_miss(qhash)  # record: LLM was invoked for this hash

        # Update working_state with cortex output + llm_calls counter
        state = _read_state()
        last_outputs: list[str] = state.get("last_cortex_outputs", [])
        last_outputs.append(response[:200])
        state["last_cortex_outputs"] = last_outputs[-10:]
        state.setdefault("cortex_stats", {})
        state["cortex_stats"]["llm_calls"] = state["cortex_stats"].get("llm_calls", 0) + 1
        _write_state(state)

        # Emit: clean text to William, tagged text to sandbox
        msg = response[:300] if respond_to else f"[SPECTRE/cortex] {response[:300]}"
        await _emit_cortex(msg, respond_to=respond_to)

    except LLMUnavailable:
        print(f"[spectre/cortex] LLM unavailable — degraded mode, event {event_id} logged", flush=True)
        state = _read_state()
        state.setdefault("cortex_stats", {})
        state["cortex_stats"]["llm_failures"] = state["cortex_stats"].get("llm_failures", 0) + 1
        _write_state(state)
        # Don't mark as processed — keep in queue for retry when LLM comes back
        return False

    if event_id:
        _save_processed_id(event_id, processed_ids)
    return True


# ── Cortex loop ───────────────────────────────────────────────────────────────

async def cortex_loop(
    poll_interval_s: float = 5.0,
    max_per_tick: int = 3,
    stop_event: asyncio.Event | None = None,
) -> None:
    """
    Main Nivel 3 cortex loop.

    Polls escalation_queue every poll_interval_s seconds.
    Processes up to max_per_tick entries per tick.
    Stops when stop_event is set (or runs forever if None).
    """
    global _CORTEX_RUNNING
    if _CORTEX_RUNNING:
        print("[spectre/cortex] already running — skipping duplicate start", flush=True)
        return
    _CORTEX_RUNNING = True

    print(f"[spectre/cortex] loop started (poll={poll_interval_s}s, max={max_per_tick}/tick)", flush=True)
    processed_ids = _load_processed_ids()

    try:
        while True:
            if stop_event and stop_event.is_set():
                break

            queue: list[dict] = []
            if ESCALATION_QUEUE_PATH.exists():
                try:
                    queue = json.loads(ESCALATION_QUEUE_PATH.read_text())
                except Exception:
                    queue = []

            if queue:
                print(f"[spectre/cortex] tick — {len(queue)} queued entries", flush=True)
                to_keep: list[dict] = []
                processed_this_tick = 0

                for entry in queue:
                    if processed_this_tick >= max_per_tick:
                        to_keep.append(entry)
                        continue

                    handled = await _process_entry(entry, processed_ids)
                    if not handled:
                        to_keep.append(entry)  # LLM unavailable — keep for retry
                    processed_this_tick += 1

                # Write back unprocessed entries
                ESCALATION_QUEUE_PATH.write_text(json.dumps(to_keep[-20:], ensure_ascii=False))

                if processed_this_tick:
                    print(f"[spectre/cortex] tick done — processed={processed_this_tick}, remaining={len(to_keep)}", flush=True)

            await asyncio.sleep(poll_interval_s)

    finally:
        _CORTEX_RUNNING = False
        print("[spectre/cortex] loop stopped", flush=True)


# ── Cortex stats ──────────────────────────────────────────────────────────────

def cortex_stats() -> dict[str, Any]:
    """Return cortex runtime stats — cache metrics from cache_pc, failures from working_state."""
    state = _read_state()
    pc_stats = cache_pc.cache_stats()
    wstate_stats = state.get("cortex_stats", {})
    return {
        "cache_hits": pc_stats["total_hits"],
        "cache_misses": pc_stats["total_misses"],
        "cache_hit_rate": pc_stats["hit_rate"],
        "cache_entries": pc_stats["entries"],
        "llm_calls": wstate_stats.get("llm_calls", 0),
        "llm_failures": wstate_stats.get("llm_failures", 0),
    }


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SPECTRE Nivel 3 cortex runner")
    parser.add_argument("--poll", type=float, default=5.0, help="Poll interval seconds")
    parser.add_argument("--max-per-tick", type=int, default=3, help="Max entries processed per tick")
    args = parser.parse_args()

    asyncio.run(cortex_loop(poll_interval_s=args.poll, max_per_tick=args.max_per_tick))
