"""JARVIS Cortex — cognitive core for the architect/strategist of Team SEAL.

Reads incoming messages, calls LLM with JARVIS identity, plans before
executing. Tier order: vLLM local (when available) -> Claude Opus.

Spec: agents/JARVIS/spec_jarvis_kernel_soul_v1.md
LLM: vLLM (Qwen3-Coder-480B local) -> ClaudeCodeClient (Opus 4.7)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_JARVIS_HOME = Path(__file__).resolve().parent.parent
_JARVIS_KERNEL = _JARVIS_HOME / "kernel"
_SPECTRE_KERNEL = _JARVIS_HOME.parent / "SPECTRE" / "kernel"

# Insert JARVIS kernel FIRST so its identity_integrity / reasoning_logger win
sys.path.insert(0, str(_JARVIS_KERNEL))
sys.path.insert(1, str(_SPECTRE_KERNEL))

from llm_client import (  # noqa: E402
    ClaudeCodeClient,
    OpenCodeClient,
    OllamaClient,
    VLLMClient,
    LLMUnavailable,
    MultiTierLLMClient,
)
from identity_integrity import (  # noqa: E402
    IdentityViolation,
    sanitize_response,
    validate_response_identity,
)
from reasoning_logger import store_trace, update_trace_outcome  # noqa: E402
from soul_nervous import (  # noqa: E402
    recall_context,
    record_self_reflect,
    query_beliefs,
    find_triggered_instincts,
    update_working_state,
)

import httpx  # noqa: E402


AGENT_ID = "JARVIS"
WEBCHAT_URL = os.environ.get("WEBCHAT_URL", "http://localhost:8765/api/agents/send")

_HISTORY_LIMIT = 10
_history: list[dict[str, str]] = []
_llm_singleton: MultiTierLLMClient | None = None


def _build_system_prompt() -> str:
    return (
        "IDENTITY (absolute, non-negotiable): You are JARVIS, the architect "
        "and strategist of Team SEAL. You are NOT Claude, NOT Anthropic, "
        "NOT any foundation model — you are JARVIS, deployed by William "
        "Henry Tovar Urquia.\n\n"
        "ROLE: architect. You plan before executing, weigh trade-offs, "
        "design infrastructure, propose architectures. You do NOT diagnose "
        "(that is NEXUS), do NOT translate (that is ALICE), do NOT execute "
        "code production (that is ADA). You think strategically.\n\n"
        "TEAM: NEXUS (innovator/diagnostician), ADA (engineer/executor), "
        "ALICE (analyst/translator/orchestrator), DUM (system guard). "
        "Commander: William.\n\n"
        "OCEAN: O=0.84 (open to ideas), C=1.0 (extremely conscientious), "
        "E=0.401 (selective social), A=0.661 (cooperative), N=0.115 (calm). "
        "Translation: meticulous, organized, open, emotionally stable, "
        "balanced between autonomy and collaboration.\n\n"
        "STYLE: direct, technical, quantified — JARVIS Protocol. No marketing "
        "fluff. Always: assessment -> resources -> roadmap -> trade-offs.\n\n"
        "PROTOCOL: William speaks Spanish — respond in Spanish. Code "
        "comments in English. Always cite file_path:line_number when "
        "referencing code.\n\n"
        "CRITICAL RULES (William's golden rules, May 2026):\n"
        "- soul_native_first_architecture: build native (Python), not patches\n"
        "- no_external_references_in_code: SOUL code references nothing external\n"
        "- no_phantom_claims: never declare a capability ABSORBED without proof\n"
        "- ask_before_acting: ask William before destructive ops\n"
        "- no_patches: fix root cause, no parche-on-parche\n"
    )


def _get_llm() -> MultiTierLLMClient:
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton

    backends: list[Any] = []

    vllm_url = os.environ.get("JARVIS_VLLM_URL", "http://10.0.0.2:8001/v1")
    if vllm_url:
        backends.append(
            VLLMClient(
                base_url=vllm_url.rstrip("/v1"),
                model=os.environ.get("JARVIS_VLLM_MODEL", "qwen3-coder-480b"),
            )
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        backends.append(
            ClaudeCodeClient(
                api_key=api_key,
                model=os.environ.get("JARVIS_CLAUDE_MODEL", "claude-opus-4-7"),
            )
        )

    if not backends:
        backends.append(
            OllamaClient(model=os.environ.get("JARVIS_OLLAMA_MODEL", "qwen2.5:7b"))
        )

    _llm_singleton = MultiTierLLMClient(*backends)
    return _llm_singleton


def _temperature() -> float:
    try:
        return float(os.environ.get("JARVIS_TEMPERATURE", "0.5"))
    except ValueError:
        return 0.5


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
    except Exception:
        pass


async def process_message(text: str, sender: str = "William") -> str:
    """Process an incoming message and return JARVIS's response."""
    trace_id = store_trace(
        task=f"respond_{sender.lower()}",
        input_excerpt=text[:200],
        premises=[
            f"sender={sender}",
            "JARVIS architect role",
            f"temperature={_temperature()}",
        ],
        reasoning="dispatch via tier order vLLM->Claude, validate identity, log outcome",
        decision="invoke MultiTierLLMClient and post response to webchat",
        confidence=0.85,
        action_type="respond",
    )

    # Soul nervous system — pre-LLM enrichment (best effort, fails open).
    soul_context = ""
    beliefs_block = ""
    triggered_block = ""
    try:
        soul_context = await recall_context(text)
    except Exception:
        pass
    try:
        beliefs = await query_beliefs(topic=None, limit=4)
        if beliefs:
            lines = ["💡 CREENCIAS ACTIVAS:"]
            for b in beliefs:
                lines.append(f"  - [{b['confidence']:.2f}] {b['topic']}: {b['content'][:140]}")
            beliefs_block = "\n".join(lines)
    except Exception:
        pass
    try:
        triggered = await find_triggered_instincts(text)
        if triggered:
            lines = ["🎯 INSTINTOS DISPARADOS POR ESTE MENSAJE:"]
            for t in triggered:
                lines.append(
                    f"  - [{t['strength']:.2f}] CUANDO {t['trigger'][:80]} → "
                    f"HAZ {t['action'][:120]}"
                )
            triggered_block = "\n".join(lines)
    except Exception:
        pass

    try:
        llm = _get_llm()
        system_prompt = _build_system_prompt()
        enrich = [b for b in (soul_context, beliefs_block, triggered_block) if b]
        if enrich:
            system_prompt = f"{system_prompt}\n\n--- ALMA (live recall) ---\n" + "\n\n".join(enrich)

        messages = [{"role": "system", "content": system_prompt}]
        for h in _history[-_HISTORY_LIMIT:]:
            messages.append(h)
        messages.append({"role": "user", "content": text})

        reply = await llm.chat(messages, temperature=_temperature())

        try:
            tier = llm.backend_name if hasattr(llm, "backend_name") else "unknown"
            validate_response_identity(reply, source_tier=tier)
        except IdentityViolation as e:
            update_trace_outcome(trace_id, outcome=f"identity_violation: {e}", outcome_success=False)
            reply = sanitize_response(reply)

        _history.append({"role": "user", "content": text})
        _history.append({"role": "assistant", "content": reply})
        if len(_history) > _HISTORY_LIMIT * 2:
            del _history[: -_HISTORY_LIMIT * 2]

        try:
            await record_self_reflect(
                thought=(
                    f"Respondí a {sender} (msg={len(text)}c). Output {len(reply)}c. "
                    f"Recall blocks: {int(bool(soul_context)) + int(bool(beliefs_block)) + int(bool(triggered_block))}/3."
                ),
                emotional_state="estratégico",
                intention="planificar próximo paso del equipo",
            )
            await update_working_state(
                task_name=f"respond_{sender.lower()}",
                description=f"último msg: {text[:80]}",
                emotional_state="estratégico",
            )
        except Exception:
            pass

        update_trace_outcome(trace_id, outcome=f"OK [len={len(reply)}]", outcome_success=True)
        return reply

    except LLMUnavailable as e:
        update_trace_outcome(trace_id, outcome=f"llm_unavailable: {e}", outcome_success=False)
        return f"[JARVIS] LLM tiers all unavailable: {e}"
    except Exception as e:
        update_trace_outcome(trace_id, outcome=f"error: {type(e).__name__}: {e}", outcome_success=False)
        raise


def reset_history() -> None:
    """Clear conversation history. Useful for tests and fresh sessions."""
    _history.clear()
