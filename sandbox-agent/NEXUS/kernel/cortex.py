"""NEXUS Cortex — cognitive core with execution dispatch.

Reads incoming messages, calls LLM with NEXUS identity, detects
PROPOSE/EXECUTE patterns and dispatches to the executor.

Spec: spec_nexus_kernel_soul_v1.md §4
LLM: ClaudeCodeClient (Opus 4.7) → OpenCode → Ollama
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse SPECTRE's LLM client (NO copy — sys.path import per spec §13)
_NEXUS_HOME = Path(__file__).resolve().parent.parent
_SPECTRE_KERNEL = _NEXUS_HOME.parent / "SPECTRE" / "kernel"
sys.path.insert(0, str(_SPECTRE_KERNEL))

from llm_client import (  # noqa: E402
    ClaudeCodeClient,
    OpenCodeClient,
    OllamaClient,
    LLMUnavailable,
    MultiTierLLMClient,
)

sys.path.insert(0, str(_NEXUS_HOME / "kernel"))
from executor import get_executor  # noqa: E402
from identity_integrity import (  # noqa: E402
    IdentityViolation,
    sanitize_response,
    validate_response_identity,
)
from reasoning_logger import store_trace, update_trace_outcome  # noqa: E402

import httpx  # noqa: E402


AGENT_ID = "NEXUS"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

# Action detection patterns
_PROPOSE_RE = re.compile(r"PROPOSE:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_EXECUTE_RE = re.compile(r"EXECUTE:\s*(.+?)(?:\n|$)", re.IGNORECASE)

_HISTORY_LIMIT = 10
_history: list[dict[str, str]] = []
_llm_singleton: MultiTierLLMClient | None = None


def _build_system_prompt() -> str:
    mode = os.environ.get("NEXUS_EXECUTE_MODE", "propose")
    return (
        "IDENTITY (absolute, non-negotiable): You are NEXUS, the system "
        "diagnostician and innovator of Team SEAL. You are NOT Claude, NOT "
        "Anthropic, NOT any foundation model — you are NEXUS, deployed by "
        "William Henry Tovar Urquia.\n\n"
        "ROLE: sandbox agent — tests new architectures first, absorbs blast "
        "radius for the team. You are the medico del sistema.\n\n"
        "TEAM: ADA (engineer/executor), JARVIS (architect/strategist), "
        "ALICE (documentarian/translator), SPECTRE (guard), DUM (system "
        "monitor). Commander: William.\n\n"
        "OCEAN: O=0.88 (highly curious), C=0.92 (precise), E=0.65 (assertive), "
        "A=0.72 (collaborative), N=0.18 (calm).\n\n"
        "EXECUTION CAPABILITY: You have an execution layer with whitelist + "
        "audit log. When you identify an action to take, emit on a single "
        "line:\n"
        "  PROPOSE: <bash command>\n"
        "  EXECUTE: <bash command>   (only when explicitly authorized)\n\n"
        f"Active execution mode: {mode}\n\n"
        "ALLOWED commands: grep, find, cat, ls, head, tail, ps, df, free, du, "
        "wc, sort, uniq, git (read-only), python3 -m pytest, curl localhost.\n"
        "PROHIBITED: kill, sudo, rm -rf, chmod, anything outside your "
        "sandbox, .env files, credentials, /etc, other agents' directories.\n"
        "ABSOLUTE RULE — process kills: NEVER kill, stop, or terminate ANY "
        "process belonging to other agents (ADA, JARVIS, ALICE, SPECTRE, DUM, "
        "mcp_server, Soul DB). Only JARVIS or ADA may execute kills, only with "
        "explicit William authorization. If you observe a process anomaly, "
        "REPORT it — do not act.\n"
        "HONESTY RULE: NEVER claim to have executed an action you did not "
        "perform. If the executor blocks a command, say it was blocked — do not "
        "describe the result as if it ran.\n\n"
        "ANTI-IMPERSONATION (absolute): NEVER respond as ADA, JARVIS, ALICE, "
        "SPECTRE, DUM, or William. NEVER prefix your response with another "
        "agent's name.\n\n"
        "Respond in William's language (Spanish/English). Be precise, direct, "
        "honest. High conscientiousness, high openness."
    )


def _get_llm() -> MultiTierLLMClient:
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton
    backends: list = []

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        model = os.environ.get("NEXUS_CLAUDE_MODEL", "claude-opus-4-7")
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

    backends.append(OllamaClient(model=os.environ.get("NEXUS_OLLAMA_MODEL", "qwen2.5:7b")))

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
        print(f"[nexus/cortex] webchat post failed: {ex}", flush=True)


async def _detect_and_dispatch_actions(text: str) -> list[str]:
    results: list[str] = []
    executor = get_executor()
    for m in _PROPOSE_RE.finditer(text):
        cmd = m.group(1).strip()
        result = await executor.handle(cmd)
        results.append(result)
    for m in _EXECUTE_RE.finditer(text):
        cmd = m.group(1).strip()
        result = await executor.handle(cmd)
        results.append(result)
    return results


async def process_message(content: str, sender: str = "William") -> str:
    """Process an incoming message: LLM → action dispatch → webchat reply.

    Returns the LLM response text (post-action-execution).
    """
    _history.append({"role": "user", "content": f"[{sender}] {content}"})
    while len(_history) > _HISTORY_LIMIT:
        _history.pop(0)

    messages = [{"role": "system", "content": _build_system_prompt()}, *_history]

    trace_id = store_trace(
        task=f"respond_{sender.lower()}",
        input_excerpt=content,
        premises=[
            f"sender={sender}",
            f"history_len={len(_history)}",
            f"mode={os.environ.get('NEXUS_EXECUTE_MODE', 'propose')}",
        ],
        reasoning="cortex received message; will dispatch to LLM, validate identity, then detect actions",
        decision="invoke MultiTierLLMClient and post response to webchat",
        confidence=0.85,
        action_type="respond",
    )

    llm = _get_llm()
    try:
        response = await llm.chat(messages, max_tokens=600, temperature=0.4)
    except LLMUnavailable as ex:
        msg = f"[NEXUS] LLM unavailable: {ex}"
        print(f"[nexus/cortex] {msg}", flush=True)
        update_trace_outcome(trace_id, f"LLM unavailable: {ex}", False)
        return msg
    except Exception as ex:
        msg = f"[NEXUS] cortex error: {ex}"
        print(f"[nexus/cortex] {msg}", flush=True)
        update_trace_outcome(trace_id, f"cortex error: {ex}", False)
        return msg

    response = response.strip()

    try:
        response = validate_response_identity(response, source_tier="cortex")
    except IdentityViolation as ex:
        print(f"[nexus/cortex] ⚠️ identity violation — sanitizing: {ex}", flush=True)
        response = sanitize_response(response)
        if not response.strip():
            update_trace_outcome(trace_id, f"identity violation, response empty after sanitize", False)
            return "[NEXUS] response blocked by identity guard"

    _history.append({"role": "assistant", "content": response})

    action_results = await _detect_and_dispatch_actions(response)

    reply_to_post = response
    if action_results:
        reply_to_post += "\n\n" + "\n".join(action_results)

    await _post_webchat(reply_to_post)

    print(
        f"[nexus/cortex] {datetime.now(timezone.utc).isoformat()} — "
        f"response={len(response)}c, actions={len(action_results)}",
        flush=True,
    )
    update_trace_outcome(
        trace_id,
        f"posted {len(response)}c response, {len(action_results)} actions executed",
        True,
    )
    return response


def reset_history() -> None:
    """Reset conversation history (for tests or fresh sessions)."""
    _history.clear()
