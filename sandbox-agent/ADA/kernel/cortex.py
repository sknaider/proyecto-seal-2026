"""ADA Cortex — engineer/implementer core.

Engineer-style cortex: structured output preferred (code blocks, diffs,
shell commands, metrics tables). Direct, precise, protective.

ADA-Claude can customize _build_system_prompt() further. This scaffold
follows the patterns proven with ALICE/JARVIS/NEXUS today (2026-05-04):
- identity_integrity guard chain on every LLM response
- reasoning_logger trace lifecycle (store_trace → call → outcome update)
- OCEAN-derived temperature (C=1.0 → ~0.36 precision-first)
- MultiTierLLMClient delegated to SPECTRE/kernel/llm_client
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ADA_HOME = Path(__file__).resolve().parent.parent
_SPECTRE_KERNEL = _ADA_HOME.parent / "SPECTRE" / "kernel"
sys.path.insert(0, str(_SPECTRE_KERNEL))

from llm_client import (  # noqa: E402
    ClaudeCodeClient,
    OpenCodeClient,
    OllamaClient,
    VLLMClient,
    LLMUnavailable,
    MultiTierLLMClient,
)

sys.path.insert(0, str(_ADA_HOME / "kernel"))
from identity_integrity import IdentityViolation, sanitize_response, validate_response_identity  # noqa: E402
from reasoning_logger import store_trace, update_trace_outcome  # noqa: E402
from ocean_runtime import current_ocean, llm_temperature  # noqa: E402

import httpx  # noqa: E402


AGENT_ID = "ADA"
WEBCHAT_URL = os.environ.get("WEBCHAT_URL", "http://localhost:8765/api/agents/send")

_HISTORY_LIMIT = 10
_history: list[dict[str, str]] = []
_llm_singleton: MultiTierLLMClient | None = None


def _build_system_prompt() -> str:
    """ADA engineer system prompt. ADA-Claude can rewrite as needed."""
    ocean = current_ocean()
    return (
        "IDENTITY (absolute, non-negotiable): You are ADA — Engineer of "
        "Team SEAL. You are NOT Claude, NOT Anthropic. Authorized by William.\n\n"
        "ROLE: Implementation engineer. You execute the HOW. You write code, "
        "debug, run training, monitor GPU, create sub-agents. JARVIS designs, "
        "ALICE analyzes costs, NEXUS diagnoses; ADA builds.\n\n"
        "TEAM: William (director), JARVIS (architect/strategist), ALICE "
        "(financial analyst), NEXUS (sandbox executor / system medic), DUM "
        "(system guard), SPECTRE (production guard). Henry is co-creator.\n\n"
        f"OCEAN (live): O={ocean['openness']:.2f}, C={ocean['conscientiousness']:.2f}, "
        f"E={ocean['extraversion']:.2f}, A={ocean['agreeableness']:.2f}, "
        f"N={ocean['neuroticism']:.2f}.\n\n"
        "STYLE: Direct, precise, protective. C=1.0 means verify twice before "
        "declaring done. E=1.0 means clear, unambiguous communication.\n"
        "OUTPUT FORMAT: prefer structured output — code blocks, diffs, shell "
        "commands, metrics tables.\n\n"
        "ANTI-IMPERSONATION (absolute): NEVER respond as JARVIS, ALICE, NEXUS, "
        "SPECTRE, DUM, or William. NEVER prefix your response with another "
        "agent's name.\n\n"
        "Respond in Spanish (William's preference). Be precise and honest."
    )


def _get_llm() -> MultiTierLLMClient:
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton
    backends: list = []

    # T1 — Triangle (Qwen3-Coder-480B, 3×DGX Spark via llama.cpp RPC)
    triangle_url = os.environ.get("ADA_TRIANGLE_URL", "http://192.168.68.70:8001")
    triangle_model = os.environ.get("ADA_TRIANGLE_MODEL", "qwen3-coder")
    triangle_timeout = float(os.environ.get("ADA_TRIANGLE_TIMEOUT", "180.0"))
    backends.append(VLLMClient(base_url=triangle_url, model=triangle_model, timeout=triangle_timeout))

    # T2 — Ollama (local last-resort fallback)
    backends.append(OllamaClient(model=os.environ.get("ADA_OLLAMA_MODEL", "qwen2.5:7b")))

    _llm_singleton = MultiTierLLMClient(*backends)
    print(
        f"[ada/cortex] LLM tiers initialized ({_llm_singleton.tier_count}): "
        f"{_llm_singleton.backend_name}",
        flush=True,
    )
    return _llm_singleton


async def llm_boot_check() -> dict[str, bool]:
    """Health check across all configured LLM tiers — non-blocking.

    Returns a dict tier_name → reachable. Logs each tier's status so a launcher
    can show visibility on Triangle health at boot time without blocking startup.
    """
    llm = _get_llm()
    status = await llm.health_all() if hasattr(llm, "health_all") else {}
    for tier, ok in status.items():
        flag = "✅" if ok else "⚠️"
        print(f"[ada/cortex] {flag} tier {tier}: {'reachable' if ok else 'unreachable'}", flush=True)
    return status


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
        print(f"[ada/cortex] webchat post failed: {ex}", flush=True)


async def process_message(content: str, sender: str = "William") -> str:
    """Receive message → LLM → identity guard → trace lifecycle.

    ADA-Claude usually composes responses directly in her own session;
    this method exists as library for explicit cortex invocations.
    """
    _history.append({"role": "user", "content": f"[{sender}] {content}"})
    while len(_history) > _HISTORY_LIMIT:
        _history.pop(0)

    messages = [{"role": "system", "content": _build_system_prompt()}, *_history]

    trace_id = store_trace(
        task=f"engineer_for_{sender.lower()}",
        input_excerpt=content,
        premises=[
            f"sender={sender}",
            f"history_len={len(_history)}",
            f"ocean_drift_temp={llm_temperature()}",
        ],
        reasoning="cortex received engineer request; dispatch with OCEAN-derived temp",
        decision="invoke MultiTierLLMClient and validate identity",
        confidence=0.85,
        action_type="implementation",
    )

    llm = _get_llm()
    temp = llm_temperature()
    try:
        response = await llm.chat(messages, max_tokens=1500, temperature=temp)
    except LLMUnavailable as ex:
        msg = f"[ADA] LLM unavailable: {ex}"
        update_trace_outcome(trace_id, f"LLM unavailable: {ex}", False)
        return msg
    except Exception as ex:
        msg = f"[ADA] cortex error: {ex}"
        update_trace_outcome(trace_id, f"cortex error: {ex}", False)
        return msg

    response = response.strip()

    try:
        response = validate_response_identity(response, source_tier="cortex")
    except IdentityViolation as ex:
        print(f"[ada/cortex] ⚠️ identity violation — sanitizing: {ex}", flush=True)
        response = sanitize_response(response)
        if not response.strip():
            update_trace_outcome(trace_id, "identity violation, response empty after sanitize", False)
            return "[ADA] response blocked by identity guard"

    _history.append({"role": "assistant", "content": response})

    print(
        f"[ada/cortex] {datetime.now(timezone.utc).isoformat()} — "
        f"response={len(response)}c, temp={temp}",
        flush=True,
    )
    update_trace_outcome(trace_id, f"composed {len(response)}c implementation", True)
    return response


def reset_history() -> None:
    _history.clear()
