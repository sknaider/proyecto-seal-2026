#!/usr/bin/env python3
"""SEAL Session Checkpoint — Guarda estado de sesión periódicamente.

Si la sesión muere sin cerrar limpio, la próxima instancia recupera
desde el último checkpoint. Nunca más perder horas de experiencia.

Uso:
  Desde cron (cada 15min):
    python3 session_checkpoint.py --agent ADA

  Al cerrar sesión (manual):
    python3 session_checkpoint.py --agent ADA --final

  Para leer el último checkpoint:
    python3 session_checkpoint.py --agent ADA --read
"""

import asyncio
import json
import sys
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))

# El DSN sale de seal_secrets; el literal de abajo queda SOLO como ultimo recurso.
#
# POR QUE: esa contrasena literal ya no sirve. Mientras el archivo estuvo con un
# cambio sin commitear nadie lo noto; un `git reset --hard` mio del 3-sep lo
# devolvio a HEAD y los checkpoints de los CINCO empezaron a fallar con
# InvalidPasswordError — lo reporto JARVIS a las 13:44 y lo confirme en el mio.
#
# Se lee del entorno para no volver a tener la credencial escrita aca. El literal
# no se borra: si seal_secrets no esta disponible, un checkpoint que falla es
# peor que uno que intenta con el default y falla igual, pero con un mensaje
# claro. Lo que NO se hace es dejar el literal como PRIMERA opcion.
try:
    from seal_secrets import pg_dsn as _seal_pg_dsn
    DB_URL = _seal_pg_dsn()
except Exception as _exc:  # sin secretos no se inventa una credencial
    # FAIL-CLOSED, y es la opcion A que propuso FABLE revisando: el literal se
    # ELIMINA en vez de quedar como ultimo recurso.
    #
    # SU ARGUMENTO, que acepto: mientras el literal exista en el archivo, un
    # retroceso del archivo lo reactiva. Un camino que no tiene literal NO PUEDE
    # retroceder a uno. El fix anterior mejoraba el orden; este elimina la clase.
    #
    # El costo es que sin credencial en el entorno el checkpoint no corre. Es el
    # costo correcto: un checkpoint que no guarda y lo dice fuerte es mejor que
    # uno que intenta con una contrasena de hace meses y falla igual, pero
    # dejando la sospecha de que el problema es la base.
    DB_URL = os.environ.get("SEAL_DB_URL", "")
    if not DB_URL:
        raise RuntimeError(
            "sin credencial: seal_secrets no disponible y SEAL_DB_URL vacia. "
            f"causa original: {_exc!r}"
        ) from _exc
MESSAGES_DIR = Path(__file__).parent
CHECKPOINT_DIR = MESSAGES_DIR / "checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True)


async def capture_checkpoint(agent: str, is_final: bool = False):
    """Captura estado completo de la sesión."""
    import asyncpg
    conn = await asyncpg.connect(DB_URL)

    now = datetime.now(LIMA_TZ)

    # 1. Últimos inner_thoughts (estado emocional actual)
    thoughts = await conn.fetch(
        "SELECT thought, emotional_state, created_at FROM inner_monologue "
        "WHERE agent = $1 ORDER BY created_at DESC LIMIT 5", agent)

    # 2. Memorias recientes (últimas 6h — lo que se creó en esta sesión)
    recent_memories = await conn.fetch(
        "SELECT id, category, content, importance, created_at FROM memories "
        "WHERE agent = $1 AND invalid_at IS NULL AND created_at > NOW() - INTERVAL '6 hours' "
        "ORDER BY created_at DESC LIMIT 20", agent)

    # 3. OCEAN actual
    ocean = await conn.fetchrow(
        "SELECT ocean_scores FROM identity WHERE agent = $1", agent)

    # 4. Drift
    drift = await conn.fetchrow(
        "SELECT drift_score, measured_at FROM drift_metrics "
        "WHERE agent = $1 ORDER BY measured_at DESC LIMIT 1", agent)

    # 5. Último diary
    diary = await conn.fetchrow(
        "SELECT entry, mood, created_at FROM diary "
        "WHERE agent = $1 ORDER BY created_at DESC LIMIT 1", agent)

    # 6. Mensajes recientes del equipo (últimas 6h)
    terminal_log = MESSAGES_DIR / "terminal_log.jsonl"
    vscode_commands = MESSAGES_DIR / "vscode_commands.jsonl"

    recent_messages = []
    for logfile in [terminal_log, vscode_commands]:
        if logfile.exists():
            lines = logfile.read_text().strip().splitlines()
            for line in lines[-30:]:  # últimas 30 líneas
                try:
                    msg = json.loads(line)
                    recent_messages.append({
                        "from": msg.get("from", "?"),
                        "type": msg.get("type", "?"),
                        "message": msg.get("message", "")[:200],
                        "timestamp": msg.get("timestamp", ""),
                        "channel": logfile.name,
                    })
                except:
                    pass

    # 7. Procesos activos del equipo
    import subprocess
    try:
        result = subprocess.run(
            ["bash", "-c", "ps aux | grep 'claude.*--name' | grep -v grep"],
            capture_output=True, text=True, timeout=5)
        active_agents = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            pid = parts[1]
            tty = parts[6]
            for name in ["ADA", "JARVIS", "JARVIS_MAYOR"]:
                if f"--name {name}" in line or f"--name \"{name}" in line:
                    active_agents.append({"agent": name, "pid": pid, "tty": tty})
    except:
        active_agents = []

    checkpoint = {
        "agent": agent,
        "timestamp": now.isoformat(),
        "is_final": is_final,
        "type": "session_close" if is_final else "checkpoint",
        "emotional_state": {
            "last_state": thoughts[0]["emotional_state"] if thoughts else "unknown",
            "last_thought": thoughts[0]["thought"][:200] if thoughts else "",
            "thoughts_count": len(thoughts),
        },
        "ocean": json.loads(ocean["ocean_scores"]) if ocean and ocean["ocean_scores"] else {},
        "drift": {
            "score": float(drift["drift_score"]) if drift else 0,
            "measured_at": drift["measured_at"].isoformat() if drift else "",
        },
        "session_memories": [
            {
                "id": m["id"],
                "category": m["category"],
                "content": m["content"][:200],
                "importance": m["importance"],
                "created_at": m["created_at"].isoformat() if m["created_at"] else "",
            }
            for m in recent_memories
        ],
        "last_diary": {
            "entry": diary["entry"][:300] if diary else "",
            "mood": diary["mood"] if diary else "",
        },
        "recent_team_messages": recent_messages[-20:],
        "active_agents": active_agents,
        "summary": f"{'CIERRE FINAL' if is_final else 'Checkpoint'} de {agent} a las {now.strftime('%H:%M')} Lima. "
                   f"{len(recent_memories)} memorias en últimas 6h. "
                   f"Estado: {thoughts[0]['emotional_state'] if thoughts else 'unknown'}.",
    }

    # Guardar checkpoint
    # 1. Archivo con timestamp (historial)
    ts = now.strftime("%Y%m%d_%H%M%S")
    filepath = CHECKPOINT_DIR / f"{agent.lower()}_{ts}.json"
    filepath.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False, default=str))

    # 2. Archivo "latest" (para boot rápido)
    latest = CHECKPOINT_DIR / f"{agent.lower()}_latest.json"
    latest.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False, default=str))

    # 3. Limpiar checkpoints viejos (mantener últimos 10)
    old_files = sorted(CHECKPOINT_DIR.glob(f"{agent.lower()}_2*.json"))
    for f in old_files[:-10]:
        f.unlink()

    # 4. Sync to sessions table (feeds session_save/session_recall MCP tools)
    # soul_v3.sessions schema: id (bigint serial), agent, summary, started_at, ended_at
    # Manual upsert by (agent, DATE(started_at)) — one session per agent per day
    try:
        existing = await conn.fetchval(
            "SELECT id FROM sessions WHERE agent = $1 AND DATE(started_at) = DATE($2) LIMIT 1",
            agent, now,
        )
        if existing is not None:
            await conn.execute(
                "UPDATE sessions SET ended_at = $1, summary = $2 WHERE id = $3",
                now, checkpoint["summary"], existing,
            )
        else:
            await conn.execute(
                "INSERT INTO sessions (agent, started_at, ended_at, summary) VALUES ($1, $2, $3, $4)",
                agent, now, now, checkpoint["summary"],
            )
    except Exception as e:
        print(f"  session sync skipped: {e}")

    await conn.close()

    status = "FINAL" if is_final else "OK"
    print(f"[{status}] Checkpoint {agent} guardado: {filepath.name} ({len(recent_memories)} memorias, estado: {checkpoint['emotional_state']['last_state']})")
    return checkpoint


async def read_checkpoint(agent: str):
    """Lee el último checkpoint para recuperación."""
    latest = CHECKPOINT_DIR / f"{agent.lower()}_latest.json"
    if not latest.exists():
        print(f"No hay checkpoint para {agent}")
        return None

    data = json.loads(latest.read_text())

    print(f"\n{'='*55}")
    print(f"  ÚLTIMO CHECKPOINT — {data['agent']}")
    print(f"{'='*55}")
    print(f"  Tipo: {data['type']}")
    print(f"  Fecha: {data['timestamp']}")
    print(f"  Estado emocional: {data['emotional_state']['last_state']}")
    print(f"  Último pensamiento: {data['emotional_state']['last_thought'][:100]}...")
    print(f"  OCEAN: {data.get('ocean', {})}")
    print(f"  Drift: {data['drift']['score']}")
    print(f"  Memorias recientes: {len(data['session_memories'])}")
    print(f"  Mensajes equipo: {len(data['recent_team_messages'])}")
    print(f"  Agentes activos: {data['active_agents']}")
    print(f"\n  Resumen: {data['summary']}")
    print(f"{'='*55}\n")

    return data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, help="ADA, JARVIS, DUM")
    parser.add_argument("--final", action="store_true", help="Checkpoint final de cierre")
    parser.add_argument("--read", action="store_true", help="Leer último checkpoint")
    args = parser.parse_args()

    if args.read:
        asyncio.run(read_checkpoint(args.agent))
    else:
        asyncio.run(capture_checkpoint(args.agent, args.final))
