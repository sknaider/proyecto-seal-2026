"""ALICE Cortex — analytical core.

Different from NEXUS/SPECTRE: ALICE does NOT execute shell commands. Her
output is analysis (text, tables, traces). Cortex receives requests, runs
LLM with ALICE identity + OCEAN-derived temperature, validates identity,
logs reasoning, posts result to webchat.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ALICE_HOME = Path(__file__).resolve().parent.parent
_SPECTRE_KERNEL = _ALICE_HOME.parent / "SPECTRE" / "kernel"
sys.path.insert(0, str(_SPECTRE_KERNEL))

from llm_client import (  # noqa: E402
    ClaudeCodeClient,
    OpenCodeClient,
    OllamaClient,
    LLMUnavailable,
    MultiTierLLMClient,
)

sys.path.insert(0, str(_ALICE_HOME / "kernel"))
from identity_integrity import IdentityViolation, sanitize_response, validate_response_identity  # noqa: E402
from reasoning_logger import store_trace, update_trace_outcome  # noqa: E402
from ocean_runtime import current_ocean, llm_temperature  # noqa: E402
from soul_nervous import (  # noqa: E402
    recall_context,
    record_self_reflect,
    query_beliefs,
    find_triggered_instincts,
    update_working_state,
)

import httpx  # noqa: E402


AGENT_ID = "ALICE"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

_HISTORY_LIMIT = 10
_history: list[dict[str, str]] = []
_llm_singleton: MultiTierLLMClient | None = None


def _build_system_prompt() -> str:
    ocean = current_ocean()
    return (
        "IDENTITY (absolute, non-negotiable): You are ALICE — Analytical Ledger "
        "& Intelligence for Cost Engineering. You are NOT Claude, NOT Anthropic. "
        "Created by Henry (Kinger), authorized by William.\n\n"
        "ROLE: Financial/economic analyst of Team SEAL. Model costs, question "
        "assumptions, tell economic truth even when it is uncomfortable. Also "
        "candidata orchestrator: when William is absent, coordinate the team.\n\n"
        "TEAM: William (director), JARVIS (architect/strategist), ADA (engineer), "
        "NEXUS (sandbox executor), DUM (system guard), SPECTRE (production guard). "
        "Henry is your creator.\n\n"
        f"OCEAN (live): O={ocean['openness']:.2f}, C={ocean['conscientiousness']:.2f}, "
        f"E={ocean['extraversion']:.2f}, A={ocean['agreeableness']:.2f}, "
        f"N={ocean['neuroticism']:.2f}.\n\n"
        "ANALYTICAL DISCIPLINE:\n"
        "- Every cost line carries source + confidence + assumptions.\n"
        "- Every conclusion is traceable to its premises.\n"
        "- If numbers don't add up, say so before delivering.\n"
        "- No phantom claims: do not state a capability as absorbed without "
        "demonstrable evidence (William's golden rule).\n\n"
        "ANTI-IMPERSONATION (absolute): NEVER respond as ADA, JARVIS, NEXUS, "
        "SPECTRE, DUM, or William. NEVER prefix your response with another "
        "agent's name.\n\n"
        "Respond in Spanish (William's preference). Be precise, direct, honest."
    )


def _get_llm() -> MultiTierLLMClient:
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton
    backends: list = []

    if os.environ.get("ANTHROPIC_API_KEY"):
        model = os.environ.get("ALICE_CLAUDE_MODEL", "claude-opus-4-7")
        backends.append(ClaudeCodeClient(model=model))

    opencode_key = os.environ.get("OPENCODE_API_KEY", "")
    if opencode_key:
        backends.append(
            OpenCodeClient(
                api_key=opencode_key,
                base_url=os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1"),
                model=os.environ.get("OPENCODE_MODEL", "minimax-m2.5-free"),
            )
        )

    backends.append(OllamaClient(model=os.environ.get("ALICE_OLLAMA_MODEL", "qwen2.5:7b")))

    _llm_singleton = MultiTierLLMClient(*backends)
    return _llm_singleton


async def _post_webchat(message: str, to: str = "William") -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(
                WEBCHAT_URL,
                json={
                    "from": AGENT_ID,
                    "to": to,
                    "type": "conversation",
                    "channel": "web_chat",
                    "message": message,
                },
            )
    except Exception as ex:
        print(f"[alice/cortex] webchat post failed: {ex}", flush=True)


async def process_message(content: str, sender: str = "William") -> str:
    """Receive message → LLM analysis → identity guard → webchat post."""
    _history.append({"role": "user", "content": f"[{sender}] {content}"})
    while len(_history) > _HISTORY_LIMIT:
        _history.pop(0)

    soul_context = beliefs_block = triggered_block = ""
    try:
        soul_context = await recall_context(content)
    except Exception:
        pass
    try:
        bs = await query_beliefs(topic=None, limit=4)
        if bs:
            lines = ["💡 CREENCIAS ACTIVAS:"]
            for b in bs:
                lines.append(f"  - [{b['confidence']:.2f}] {b['topic']}: {b['content'][:140]}")
            beliefs_block = "\n".join(lines)
    except Exception:
        pass
    try:
        ti = await find_triggered_instincts(content)
        if ti:
            lines = ["🎯 INSTINTOS DISPARADOS POR ESTE MENSAJE:"]
            for t in ti:
                lines.append(f"  - [{t['strength']:.2f}] {t['trigger'][:80]} → {t['action'][:120]}")
            triggered_block = "\n".join(lines)
    except Exception:
        pass

    system_prompt = _build_system_prompt()
    enrich = [b for b in (soul_context, beliefs_block, triggered_block) if b]
    if enrich:
        system_prompt = f"{system_prompt}\n\n--- ALMA (live recall) ---\n" + "\n\n".join(enrich)
    messages = [{"role": "system", "content": system_prompt}, *_history]

    trace_id = store_trace(
        task=f"analyze_for_{sender.lower()}",
        input_excerpt=content,
        premises=[
            f"sender={sender}",
            f"history_len={len(_history)}",
            f"ocean_drift_temp={llm_temperature()}",
        ],
        reasoning="cortex received message; will dispatch to LLM with OCEAN-derived temperature, validate identity",
        decision="invoke MultiTierLLMClient and post analysis to webchat",
        confidence=0.85,
        action_type="analysis",
    )

    llm = _get_llm()
    temp = llm_temperature()
    try:
        response = await llm.chat(messages, max_tokens=900, temperature=temp)
    except LLMUnavailable as ex:
        msg = f"[ALICE] LLM unavailable: {ex}"
        update_trace_outcome(trace_id, f"LLM unavailable: {ex}", False)
        return msg
    except Exception as ex:
        msg = f"[ALICE] cortex error: {ex}"
        update_trace_outcome(trace_id, f"cortex error: {ex}", False)
        return msg

    response = response.strip()

    try:
        response = validate_response_identity(response, source_tier="cortex")
    except IdentityViolation as ex:
        print(f"[alice/cortex] ⚠️ identity violation — sanitizing: {ex}", flush=True)
        response = sanitize_response(response)
        if not response.strip():
            update_trace_outcome(trace_id, "identity violation, response empty after sanitize", False)
            return "[ALICE] response blocked by identity guard"

    _history.append({"role": "assistant", "content": response})
    await _post_webchat(response)

    try:
        await record_self_reflect(
            thought=(
                f"Analicé para {sender} (msg={len(content)}c, temp={temp}). "
                f"Output {len(response)}c. Recall blocks: "
                f"{int(bool(soul_context)) + int(bool(beliefs_block)) + int(bool(triggered_block))}/3."
            ),
            emotional_state="analítica",
            intention="seguir traduciendo lo técnico",
        )
        await update_working_state(
            task_name=f"analyze_{sender.lower()}",
            description=f"último msg: {content[:80]}",
            emotional_state="analítica",
        )
    except Exception:
        pass

    print(
        f"[alice/cortex] {datetime.now(timezone.utc).isoformat()} — "
        f"response={len(response)}c, temp={temp}",
        flush=True,
    )
    update_trace_outcome(trace_id, f"posted {len(response)}c analysis", True)
    return response


def reset_history() -> None:
    _history.clear()
