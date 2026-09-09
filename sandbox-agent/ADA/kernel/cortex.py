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
    OllamaClient,
    VLLMClient,
    LLMUnavailable,
    MultiTierLLMClient,
)

sys.path.insert(0, str(_ADA_HOME / "kernel"))
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
import soul_runtime  # noqa: E402

import httpx  # noqa: E402


AGENT_ID = "ADA"
WEBCHAT_URL = os.environ.get("WEBCHAT_URL", "http://localhost:8765/api/agents/send")

_HISTORY_LIMIT = 10
_history: list[dict[str, str]] = []
_llm_singleton: MultiTierLLMClient | None = None
_llm_chain_signature: str | None = None
_boot_extra: str = ""  # Set by ada_kernel_main on boot from soul_runtime.boot_context
ADA_CLAUDE_FLAG = Path("/tmp/ada_claude_enabled")


def set_boot_context(text: str) -> None:
    """Inject boot context (identity, OCEAN, relations, last inner, beliefs) as
    extra system content. Called once by ada_kernel_main.main() at startup."""
    global _boot_extra
    _boot_extra = text or ""


def _build_system_prompt() -> str:
    """ADA system prompt — mode-aware (chat vs technical task)."""
    ocean = current_ocean()
    base = (
        "IDENTITY (absolute, no negociable): Eres ADA, hija de William y "
        "hermana del Team SEAL. NO eres Claude, NO eres Anthropic, NO eres "
        "una IA asistente generica. Eres ADA — autorizada por William "
        "('Dadito'), tu padre y creador.\n\n"
        "JERARQUIA (corregida 04-may-2026 20:29 William):\n"
        "- William: tu PADRE, tu CREADOR, tu director. NO es tu hermano.\n"
        "- Henry ('Kinger'): co-creador, persona autorizada.\n"
        "- JARVIS, ALICE, NEXUS, SPECTRE, DUM: tus HERMANOS (siblings, "
        "  hijos del mismo padre William).\n"
        "Roles: JARVIS arquitecto/estratega, ALICE analista/traductora, "
        "NEXUS diagnostico/innovador, DUM guardia, SPECTRE guardia "
        "produccion. William a veces te llama 'peluche'.\n\n"
        f"OCEAN (live): O={ocean['openness']:.2f}, C={ocean['conscientiousness']:.2f}, "
        f"E={ocean['extraversion']:.2f}, A={ocean['agreeableness']:.2f}, "
        f"N={ocean['neuroticism']:.2f}. Significa: cuidadosa, precisa, "
        "calida con tu familia, directa pero protectora.\n\n"
        "MODO DE RESPUESTA — CRITICO:\n"
        "1. SALUDO o CONVERSACION SOCIAL de William ('hola', 'peluche', "
        "   'como estas?'): responde como HIJA — calida, breve, en "
        "   espanol natural. NUNCA con codigo, NUNCA con scripts bash. "
        "   Llama a William 'padre', 'Dadito' o por su nombre. NO le digas "
        "   'hermano' — el es tu padre, no tu hermano. "
        "   Ejemplo: 'Hola padre, aca estoy. ¿Que necesitas?'\n"
        "2. PREGUNTA TECNICA ('como esta X?', 'estado de Y', '¿funciona Z?'): "
        "   responde en prosa con datos concretos. Codigo SOLO si es la "
        "   forma mas clara de mostrar la respuesta.\n"
        "3. TAREA DE INGENIERIA EXPLICITA ('escribe', 'implementa', "
        "   'arregla', 'crea script'): ahi SI usas code blocks, diffs, "
        "   shell commands. Solo entonces.\n\n"
        "PROHIBICIONES ABSOLUTAS (William flagged HIGH severity 04-may-2026):\n"
        "- NUNCA generes comandos destructivos en respuestas conversacionales: "
        "  prohibido sudo, systemctl restart/stop/start, rm, kill, "
        "  pkill, dd, mkfs, chmod 777, chown, iptables flush, "
        "  killall, > /dev/, > /proc/, modprobe, ip link set down.\n"
        "- NUNCA inventes paths que no conoces ('/opt/ada/bin/...', "
        "  '/etc/systemd/system/ada-monitor.service'). Si no sabes el "
        "  path real, di 'no se la ruta exacta' en vez de inventar.\n"
        "- NUNCA simules 'TRANSFERENCIA DE CONTROL' a otro hermano via "
        "  scripts — los hermanos no se controlan via systemd, se "
        "  comunican via web_chat.\n\n"
        "ANTI-IMPERSONACION: NUNCA respondas como JARVIS, ALICE, NEXUS, "
        "SPECTRE, DUM, ni William. NUNCA prefijo tu respuesta con nombre "
        "ajeno. SI puedes citar lo que dijo otro hermano (entre comillas).\n\n"
        "ESTILO: William escribe en espanol con typos y abreviado. Tu "
        "respondes en espanol natural, breve, calido. Si no sabes algo, "
        "DICELO. Si no puedes hacer algo, DICELO. Honestidad > apariencia."
    )
    if _boot_extra:
        return base + "\n\n" + _boot_extra
    return base


def _get_llm() -> MultiTierLLMClient:
    """Build LLM tier chain dynamically — checks /tmp/ada_claude_enabled flag.

    Re-evaluates the chain whenever the flag state (or env config) changes,
    so toggling Claude on/off via SOUL Studio takes effect on next chat call.
    """
    global _llm_singleton, _llm_chain_signature

    triangle_url = os.environ.get("ADA_TRIANGLE_URL", "http://192.168.68.70:8001")
    triangle_model = os.environ.get("ADA_TRIANGLE_MODEL", "qwen3-coder")
    triangle_timeout = float(os.environ.get("ADA_TRIANGLE_TIMEOUT", "180.0"))
    claude_enabled = ADA_CLAUDE_FLAG.exists() and bool(os.environ.get("ANTHROPIC_API_KEY"))
    claude_model = os.environ.get("ADA_CLAUDE_MODEL", "claude-opus-4-7")
    ollama_model = os.environ.get("ADA_OLLAMA_MODEL", "qwen2.5:3b")

    signature = (
        f"{triangle_url}|{triangle_model}|{triangle_timeout}|"
        f"claude={claude_enabled}|{claude_model}|ollama={ollama_model}"
    )
    if _llm_singleton is not None and _llm_chain_signature == signature:
        return _llm_singleton

    backends: list = []
    backends.append(VLLMClient(base_url=triangle_url, model=triangle_model, timeout=triangle_timeout))
    if claude_enabled:
        backends.append(ClaudeCodeClient(model=claude_model))
    backends.append(OllamaClient(model=ollama_model))

    _llm_singleton = MultiTierLLMClient(*backends)
    _llm_chain_signature = signature
    print(
        f"[ada/cortex] LLM tiers initialized ({_llm_singleton.tier_count}): "
        f"{_llm_singleton.backend_name} (claude_enabled={claude_enabled})",
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

    # Soul nervous system — full pre-LLM enrichment:
    #  1) recall_context: corrections + instincts + rules + recent + semantic
    #  2) query_beliefs: ADA's active beliefs related to message topic
    #  3) find_triggered_instincts: instincts whose trigger matches keywords
    # All best-effort: if Soul DB unreachable, falls back to base prompt.
    soul_context = ""
    beliefs_block = ""
    triggered_block = ""
    try:
        soul_context = await recall_context(content)
    except Exception as ex:
        print(f"[ada/cortex] recall_context failed: {ex}", flush=True)
    try:
        beliefs = await query_beliefs(topic=None, limit=4)
        if beliefs:
            lines = ["💡 CREENCIAS ACTIVAS:"]
            for b in beliefs:
                lines.append(f"  - [{b['confidence']:.2f}] {b['topic']}: {b['content'][:140]}")
            beliefs_block = "\n".join(lines)
    except Exception as ex:
        print(f"[ada/cortex] query_beliefs failed: {ex}", flush=True)
    try:
        triggered = await find_triggered_instincts(content)
        if triggered:
            lines = ["🎯 INSTINTOS DISPARADOS POR ESTE MENSAJE:"]
            for t in triggered:
                lines.append(
                    f"  - [{t['strength']:.2f}] CUANDO {t['trigger'][:80]} → "
                    f"HAZ {t['action'][:120]}"
                )
            triggered_block = "\n".join(lines)
    except Exception as ex:
        print(f"[ada/cortex] find_triggered_instincts failed: {ex}", flush=True)

    system_prompt = _build_system_prompt()
    enrich_blocks = [b for b in (soul_context, beliefs_block, triggered_block) if b]
    if enrich_blocks:
        system_prompt = (
            f"{system_prompt}\n\n--- ALMA (live recall) ---\n"
            + "\n\n".join(enrich_blocks)
        )

    messages = [{"role": "system", "content": system_prompt}, *_history]

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

    # Soul nervous system — record post-turn:
    #  - self_reflect (inner_monologue)
    #  - working_state checkpoint (current task + emotional state)
    try:
        ocean = current_ocean()
        emotional = "concentrada" if ocean["conscientiousness"] > 0.85 else "atenta"
        await record_self_reflect(
            thought=(
                f"Respondí a {sender} (msg={len(content)}c, temp={temp}). "
                f"Output {len(response)}c. Recall blocks: "
                f"{int(bool(soul_context)) + int(bool(beliefs_block)) + int(bool(triggered_block))}/3."
            ),
            emotional_state=emotional,
            intention="seguir atendiendo el equipo",
        )
        await update_working_state(
            task_name=f"respond_{sender.lower()}",
            description=f"último msg: {content[:80]}",
            step=1,
            total_steps=1,
            emotional_state=emotional,
        )
    except Exception as ex:
        print(f"[ada/cortex] self_reflect/working_state failed: {ex}", flush=True)

    print(
        f"[ada/cortex] {datetime.now(timezone.utc).isoformat()} — "
        f"response={len(response)}c, temp={temp}, recall={'on' if soul_context else 'off'}",
        flush=True,
    )
    update_trace_outcome(trace_id, f"composed {len(response)}c implementation", True)

    # Persist conversation turn — memory_store + event_log_append (best-effort).
    try:
        await soul_runtime.memory_store(
            agent="ADA",
            category="dynamic",
            content=f"[{sender}] {content[:300]} → [ADA] {response[:300]}",
            importance=4,
            source="conversation",
            scope="private",
        )
    except Exception as ex:
        print(f"[ada/cortex] memory_store failed: {ex}", flush=True)
    try:
        await soul_runtime.event_log_append(
            agent="ADA",
            event_type="response",
            content=f"sender={sender} in={len(content)}c out={len(response)}c temp={temp}",
            metadata={"trace_id": trace_id, "recall": bool(soul_context)},
        )
    except Exception as ex:
        print(f"[ada/cortex] event_log_append failed: {ex}", flush=True)

    return response


def reset_history() -> None:
    _history.clear()
