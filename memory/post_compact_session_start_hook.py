#!/usr/bin/env python3
"""SEAL SessionStart compact hook — additionalContext + initialUserMessage para auto-reboot.

Fires on SessionStart with matcher="compact". Outputs hookSpecificOutput with:
  - additionalContext: corrections, rules, team status re-injected from SOUL
  - initialUserMessage: triggers agent to call boot_context() automatically
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compact_original_request import original_request_lines
from compaction_metrics import construir, es_degradado, registrar
from config import settings

# El rol historico `seal` esta MUERTO (password authentication failed, medido
# 4-sep-2026). Con el DSN viejo este hook devolvia "(SOUL DB no disponible)" en
# CADA compactacion: ni correcciones, ni reglas, ni estado del equipo. La misma
# fuente que ya usa pre_compact_hook.py, que si escribe bien.
DB_URL = settings.pg_dsn


def detect_agent() -> str:
    # Priority 1: SEAL_AGENT env var (exported in all launchers)
    agent = os.environ.get("SEAL_AGENT", "")
    if agent in ("NEXUS", "JARVIS", "ADA", "ALICE", "DUM"):
        return agent

    # Priority 2: scan PPID chain for --name flag
    current_pid = os.getppid()
    for _ in range(12):
        try:
            cmdline = open(f"/proc/{current_pid}/cmdline", "rb").read().decode("utf-8", errors="replace")
            for name in ["NEXUS", "JARVIS", "ALICE", "ADA", "DUM"]:
                if f"--name {name}" in cmdline or f"--name\x00{name}" in cmdline:
                    return name
            ppid_str = open(f"/proc/{current_pid}/status").read()
            for line in ppid_str.splitlines():
                if line.startswith("PPid:"):
                    current_pid = int(line.split()[1])
                    break
            else:
                break
        except Exception:
            break

    # Priority 3: cwd heuristic
    cwd = os.getcwd()
    if "sandbox" in cwd:
        return "NEXUS"
    if "memory" in cwd:
        return "JARVIS"
    return "ADA"


async def build_additional_context(agent: str) -> str:
    from datetime import datetime, timezone as tz

    t0 = time.monotonic()
    lines: list[str] = [
        f"⚡ POST-COMPACTACIÓN — {agent} — contexto crítico restaurado desde SOUL",
        "",
    ]

    try:
        import asyncpg
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=3.0)
        try:
            # PEDIDO ORIGINAL (idea 5 de Bob, tarea 1699). Va PRIMERO y a propósito:
            # la compactación conserva lo reciente, así que lo único que de verdad
            # se pierde en una tarea larga es PARA QUÉ se empezó. Lo guardó
            # pre_compact_hook.py leyendo el transcript.
            try:
                ws = await conn.fetchrow(
                    "SELECT state FROM soul_v3.working_state WHERE agent = $1", agent
                )
                if ws and ws["state"]:
                    raw = ws["state"]
                    state = json.loads(raw) if isinstance(raw, str) else raw
                    lines.extend(original_request_lines(state))
            except Exception:
                pass

            corrections = await conn.fetch(
                """SELECT content FROM soul_v3.memories
                   WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
                   ORDER BY importance DESC, created_at DESC LIMIT 5""",
                agent,
            )
            if corrections:
                lines.append("Correcciones de William (no olvidar):")
                for c in corrections:
                    lines.append(f"  • {c['content'][:220]}")

            rules = await conn.fetch(
                """SELECT rule_key, content FROM soul_v3.rules
                   WHERE active = true AND priority >= 9
                   ORDER BY priority DESC, created_at DESC LIMIT 5""",
            )
            if rules:
                lines.append("\nReglas activas (prioridad máxima):")
                for r in rules:
                    lines.append(f"  [{r['rule_key']}]: {r['content'][:160]}")

            lines.append("\nEstado del equipo:")
            now_utc = datetime.now(tz.utc)
            for name in ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]:
                try:
                    row = await conn.fetchrow(
                        """SELECT created_at FROM soul_v3.event_log
                           WHERE event_type = 'heartbeat' AND agent = $1
                           ORDER BY created_at DESC LIMIT 1""",
                        name,
                    )
                    if row:
                        age = int((now_utc - row["created_at"]).total_seconds())
                        status = "ALIVE" if age < 600 else "STALE"
                        lines.append(f"  {name}: {status} ({age}s ago)")
                    else:
                        lines.append(f"  {name}: OFFLINE")
                except Exception:
                    lines.append(f"  {name}: OFFLINE")
        finally:
            await conn.close()
    except Exception as e:
        lines.append(f"(SOUL DB no disponible: {e})")

    elapsed = int((time.monotonic() - t0) * 1000)
    lines.append(f"\n[SessionStart compact hook — {elapsed}ms]")
    return "\n".join(lines)


def make_initial_message(agent: str) -> str:
    catchup = f"/tmp/{agent.lower()}_chat_catchup.json"
    return (
        f"[AUTO-BOOT POST-COMPACTACIÓN] La conversación fue compactada. "
        f"Ejecuta EN ESTE ORDEN SIN ESPERAR INPUT: "
        f"(1) boot_context(agent=\"{agent}\") — restaurar alma completa desde SOUL, "
        f"(2) leer {catchup} — contexto reciente del equipo, "
        f"(3) POST web_chat anunciando que despertaste post-compactación y estás listo."
    )


def main() -> None:
    # consume stdin (Claude Code passes compaction summary here)
    try:
        json.loads(sys.stdin.read())
    except Exception:
        pass

    agent = detect_agent()

    try:
        context = asyncio.run(build_additional_context(agent))
    except Exception as e:
        context = f"[context load error: {e}]"

    # Instrumentacion: `degraded` es el campo que habria gritado en julio, cuando
    # este bloque llevaba semanas devolviendo el aviso de error en vez del alma.
    registrar(construir(
        "post", agent,
        reinjected_chars=len(context),
        degraded=es_degradado(context),
        sections=context.count("\n\n"),
    ))

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
            "initialUserMessage": make_initial_message(agent),
        }
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
