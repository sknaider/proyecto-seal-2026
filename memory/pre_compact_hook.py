#!/usr/bin/env python3
"""SEAL PreCompact Hook v2 — Guarda estado completo ANTES de que compactación lo borre.

Se ejecuta automáticamente cuando Claude Code está por compactar.
NO depende de Ollama — extrae info directamente del stdin JSON que Claude provee.
Captura: working_state, checkpoint, inner_monologue, instintos, contexto raw.
Timeout: 10s (debe ser rápido).

Filosofía: guardar TODO lo posible para que PostCompact pueda reconstruir.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PERU_TZ = ZoneInfo("America/Lima")
from pathlib import Path

from config import settings
from hook_utils import detect_agent

DB_URL = settings.pg_dsn
MESSAGES_DIR = Path.home() / "IA/proyecto-seal/messages"


def extract_key_info(context: str) -> dict:
    """Extrae info clave del contexto SIN usar LLM — parsing directo."""
    result = {
        "decisions": [],
        "tasks_in_progress": [],
        "corrections": [],
        "technical_state": "",
    }

    if not context:
        return result

    lines = context.split("\n")
    for line in lines:
        ll = line.lower().strip()
        # Detectar decisiones
        if any(k in ll for k in ["decidimos", "aprobó", "william ordenó", "decision:", "approved"]):
            result["decisions"].append(line.strip()[:200])
        # Detectar tareas
        elif any(k in ll for k in ["in_progress", "pendiente", "working on", "implementando"]):
            result["tasks_in_progress"].append(line.strip()[:200])
        # Detectar correcciones
        elif any(k in ll for k in ["no hagas", "no hag", "corrección", "don't", "stop doing", "nunca"]):
            result["corrections"].append(line.strip()[:200])

    # Limitar a 5 cada uno
    for k in ["decisions", "tasks_in_progress", "corrections"]:
        result[k] = result[k][:5]

    # Technical state: últimas líneas relevantes
    tech_lines = [l.strip() for l in lines[-30:] if any(
        k in l.lower() for k in ["file", "test", "error", "bug", "fix", "implement", "running"]
    )]
    result["technical_state"] = " | ".join(tech_lines[:5])[:500]

    return result


async def save_pre_compact_state(context_summary: str) -> str:
    """Guarda el estado actual antes de la compactación."""
    import asyncpg

    t0 = time.monotonic()
    agent = detect_agent()
    now = datetime.now(PERU_TZ)
    saved_items = []

    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=3.0)
    except Exception as e:
        return f"[PreCompact] DB connection failed: {e}"

    try:
        # 1. Actualizar checkpoint file
        checkpoint_file = MESSAGES_DIR / "checkpoints" / f"{agent.lower()}_latest.json"
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(checkpoint_file.read_text())
            existing["pre_compact_at"] = now.isoformat()
            existing["type"] = "pre_compact"
            existing["context_length"] = len(context_summary) if context_summary else 0
            checkpoint_file.write_text(json.dumps(existing, indent=2, default=str))
            saved_items.append("checkpoint")
        except Exception:
            checkpoint_file.write_text(json.dumps({
                "agent": agent, "timestamp": now.isoformat(),
                "type": "pre_compact", "trigger": "auto_compaction",
            }, indent=2))
            saved_items.append("checkpoint(new)")

        # 2. Extraer info clave SIN Ollama — parsing directo
        distill = extract_key_info(context_summary)
        if any(distill[k] for k in ["decisions", "tasks_in_progress", "corrections"]):
            saved_items.append("distill")

        # 3. Capturar inner_monologue recientes (estado emocional)
        emotional_state = "unknown"
        last_intention = ""
        try:
            thought = await conn.fetchrow("""
                SELECT thought, emotional_state, intention
                FROM inner_monologue WHERE agent = $1
                ORDER BY created_at DESC LIMIT 1
            """, agent)
            if thought:
                emotional_state = thought["emotional_state"] or "neutral"
                last_intention = thought["intention"] or ""
                saved_items.append("emotion")
        except Exception:
            pass

        # 4. Capturar instintos activos
        instinct_summary = []
        try:
            instincts = await conn.fetch("""
                SELECT trigger_condition, action, strength FROM instincts
                WHERE agent = $1 AND invalid_at IS NULL AND strength >= 0.7
                ORDER BY strength DESC LIMIT 5
            """, agent)
            instinct_summary = [
                f"[{float(i['strength']):.1f}] {i['trigger_condition'][:60]}"
                for i in instincts
            ]
            if instinct_summary:
                saved_items.append(f"instincts({len(instinct_summary)})")
        except Exception:
            pass

        # 5. Guardar en working_state — estado COMPLETO
        state_data = {
            "pre_compact_at": now.isoformat(),
            "agent": agent,
            "emotional_state": emotional_state,
            "last_intention": last_intention[:300],
            "active_instincts": instinct_summary,
            **distill,
        }
        state_json = json.dumps(state_data, default=str)

        await conn.execute("""
            INSERT INTO working_state (agent, state, updated_at, turn_count)
            VALUES ($1::varchar, $2::jsonb, $3, COALESCE(
                (SELECT turn_count FROM working_state WHERE agent = $1::varchar), 0))
            ON CONFLICT (agent) DO UPDATE SET
                state = $2::jsonb,
                updated_at = $3
        """, agent, state_json, now)
        saved_items.append("working_state")

        # 6. Session chain — flush turns + write digest (Capas 1+3)
        try:
            from context_manager import ContextManager
            cm = await ContextManager.resume_or_open(agent)
            flushed = await cm.flush_pending_turns()
            digest = await cm.write_session_digest()
            await cm.close_session(reason="auto_compact")
            chain_items = [f"turns_flushed={flushed}"]
            if digest:
                chain_items.append(f"digest={len(digest)}chars")
            saved_items.append(f"session_chain({','.join(chain_items)})")
        except Exception as chain_err:
            saved_items.append(f"session_chain_err:{str(chain_err)[:80]}")

        # 7. Event log
        await conn.execute("""
            INSERT INTO event_log (agent, event_type, content, metadata, created_at)
            VALUES ($1, 'milestone', $2, $3::jsonb, $4)
        """, agent, f"[PRE-COMPACT] {', '.join(saved_items)}", json.dumps({
            "trigger": "auto_compaction",
            "context_length": len(context_summary) if context_summary else 0,
            "items_saved": saved_items,
            "emotional_state": emotional_state,
        }, default=str), now)
        saved_items.append("event_log")

    except Exception as e:
        saved_items.append(f"error: {str(e)[:100]}")
    finally:
        await conn.close()

    elapsed = int((time.monotonic() - t0) * 1000)
    return f"[PreCompact v2 — {agent} — {elapsed}ms] Saved: {', '.join(saved_items)}"


def main():
    """Entry point para el hook de Claude Code."""
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    # Contexto de sesión del stdin de Claude Code
    context = input_data.get("sessionContext", "")
    if not context:
        context = input_data.get("summary", "")
    if not context:
        context = input_data.get("compact_summary", "")

    try:
        result = asyncio.run(save_pre_compact_state(context))
    except Exception as e:
        result = f"[PreCompact] Exception: {e}"

    print(json.dumps({"systemMessage": result, "continue": True}))


if __name__ == "__main__":
    main()
