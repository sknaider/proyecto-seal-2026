#!/usr/bin/env python3
"""
session_delta_capture.py — Captura el delta semántico de una sesión SEAL
=========================================================================
Computa qué cambió en esta sesión que no estaba en el boot.
No guarda el estado absoluto (eso ya lo hace boot_context).
Guarda el DIFERENCIAL: lo que solo existe porque esta sesión ocurrió.

Uso:
    # Al inicio de sesión (captura baseline):
    python3 session_delta_capture.py --snapshot-start --agent ADA

    # Al cierre de sesión (computa y guarda delta):
    python3 session_delta_capture.py --compute-delta --agent ADA
"""

import json
import asyncio
import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

import asyncpg
from emotional_variance import compute_variance, interpret_variance

# ── Config ──
DB_URL       = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
SESSION_START_FILE = MESSAGES_DIR / "session_start_snapshot.json"
TERMINAL_LOG = MESSAGES_DIR / "terminal_log.jsonl"


async def _fetch_snapshot(agent: str) -> dict:
    """Lee estado actual del agente desde PostgreSQL."""
    conn = await asyncpg.connect(DB_URL)
    try:
        # Últimas 20 memorias activas
        memories = await conn.fetch("""
            SELECT id, category, content, importance, valence, arousal, created_at
            FROM memories
            WHERE agent = $1
              AND (invalid_at IS NULL OR invalid_at > NOW())
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC
            LIMIT 20
        """, agent)

        # Últimos pensamientos
        thoughts = await conn.fetch("""
            SELECT thought, emotional_state, created_at
            FROM inner_monologue
            WHERE agent = $1
            ORDER BY created_at DESC
            LIMIT 10
        """, agent)

        # OCEAN desde identity (fuente canónica)
        identity_row = await conn.fetchrow("""
            SELECT ocean_scores, ocean_baseline, updated_at
            FROM identity WHERE agent = $1
        """, agent)

        # Drift acumulado de esta sesión (últimas 24h)
        drift_rows = await conn.fetch("""
            SELECT drift_score, details, measured_at
            FROM drift_metrics
            WHERE agent = $1
              AND measured_at > NOW() - INTERVAL '24 hours'
            ORDER BY measured_at DESC
        """, agent)

        ocean = {}
        ocean_baseline = {}
        if identity_row:
            if identity_row["ocean_scores"]:
                ocean = json.loads(identity_row["ocean_scores"])
            if identity_row["ocean_baseline"]:
                ocean_baseline = json.loads(identity_row["ocean_baseline"])

        # Drift total reciente
        total_drift = sum(float(r["drift_score"]) for r in drift_rows if r["drift_score"])

        return {
            "agent": agent,
            "timestamp": datetime.now(LIMA_TZ).isoformat(),
            "memories": [
                {
                    "id": m["id"],
                    "category": m["category"],
                    "content": m["content"][:200],
                    "importance": m["importance"],
                    "valence": float(m["valence"]) if m["valence"] else 0.0,
                    "arousal": float(m["arousal"]) if m["arousal"] else 0.0,
                    "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                }
                for m in memories
            ],
            "thoughts": [
                {
                    "thought": t["thought"][:200],
                    "state": t["emotional_state"],
                    "ts": t["created_at"].isoformat() if t["created_at"] else None,
                }
                for t in thoughts
            ],
            "ocean": ocean,
            "ocean_baseline": ocean_baseline,
            "drift_score": round(total_drift, 4),
            "drift_events_count": len(drift_rows),
            "emotional_variance": await compute_variance(conn, agent, window=20),
        }
    finally:
        await conn.close()


def snapshot_start(agent: str) -> None:
    """Captura baseline al inicio de sesión."""
    try:
        snapshot = asyncio.run(_fetch_snapshot(agent))
    except Exception as e:
        # Graceful degradation: DB offline — guardar entry mínimo
        snapshot = {
            "agent": agent,
            "timestamp": datetime.now(LIMA_TZ).isoformat(),
            "db_unavailable": True,
            "error": str(e),
            "memories": [], "thoughts": [], "ocean": {}, "drift_score": 0.0,
        }
        print(f"[session_delta] WARN: DB unavailable — baseline mínimo guardado ({e})")
    snapshot["snapshot_type"] = "session_start"
    SESSION_START_FILE.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    mem_count = len(snapshot.get("memories", []))
    drift = snapshot.get("drift_score", "N/A")
    ocean = snapshot.get("ocean", {})
    print(f"[session_delta] Baseline guardado: {SESSION_START_FILE}")
    print(f"  Memorias capturadas: {mem_count}")
    print(f"  OCEAN drift al inicio: {drift}")
    if ocean:
        print(f"  OCEAN: O={ocean.get('O','?'):.3f} C={ocean.get('C','?'):.3f} "
              f"E={ocean.get('E','?'):.3f} A={ocean.get('A','?'):.3f} N={ocean.get('N','?'):.3f}")


def _write_degraded_delta(agent: str, error: str) -> None:
    """Escribe entry mínimo cuando DB no está disponible al cierre."""
    now = datetime.now(LIMA_TZ)
    entry = {
        "id": f"session_delta_{agent.lower()}_{int(now.timestamp())}",
        "from": agent, "to": "equipo",
        "timestamp": now.isoformat(),
        "type": "session_closing_delta",
        "channel": "terminal_log",
        "message": f"SESSION DELTA — {agent} — DB unavailable al cierre\nError: {error}\nDelta no computable. Revisar logs de sesión manualmente.",
        "db_unavailable": True,
    }
    with open(TERMINAL_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[session_delta] WARN: DB offline — entry degradado guardado.")


def compute_delta(agent: str) -> dict:
    """Computa delta entre baseline y estado actual."""
    if not SESSION_START_FILE.exists():
        print("[session_delta] ERROR: No hay baseline. Ejecutar --snapshot-start al inicio de sesión.")
        return {}

    baseline = json.loads(SESSION_START_FILE.read_text())
    try:
        current = asyncio.run(_fetch_snapshot(agent))
    except Exception as e:
        # Graceful degradation: DB offline al cierre
        _write_degraded_delta(agent, str(e))
        return {}



    # IDs de memorias al inicio
    baseline_ids = {m["id"] for m in baseline.get("memories", [])}
    new_memories = [
        m for m in current.get("memories", [])
        if m["id"] not in baseline_ids
    ]
    # Ordenar nuevas por importancia desc
    new_memories.sort(key=lambda m: m["importance"], reverse=True)

    # Delta OCEAN
    b_ocean = baseline.get("ocean", {})
    c_ocean = current.get("ocean", {})
    ocean_delta = {}
    frozen_dims = []
    for dim in ["O", "C", "E", "A", "N"]:
        b_val = b_ocean.get(dim)
        c_val = c_ocean.get(dim)
        if b_val is not None and c_val is not None:
            delta_val = round(c_val - b_val, 4)
            ocean_delta[dim] = delta_val
            if abs(delta_val) < 0.001:
                frozen_dims.append(dim)

    # Pensamientos nuevos (posteriores al baseline)
    baseline_ts = baseline.get("timestamp", "")
    new_thoughts = [
        t for t in current.get("thoughts", [])
        if (t.get("ts") or "") > baseline_ts
    ]

    # Drift
    drift_start = baseline.get("drift_score", 0.0)
    drift_end   = current.get("drift_score", 0.0)

    # Detectar OCEAN freezing
    ocean_frozen = len(frozen_dims) >= 4  # 4+ dimensiones sin cambio = freezing

    delta = {
        "agent": agent,
        "session_start": baseline.get("timestamp"),
        "session_end": current["timestamp"],
        "new_memories_count": len(new_memories),
        "new_memories_top5": new_memories[:5],
        "ocean_delta": ocean_delta,
        "ocean_frozen": ocean_frozen,
        "frozen_dims": frozen_dims,
        "drift_start": drift_start,
        "drift_end": drift_end,
        "drift_change": round(drift_end - drift_start, 4),
        "new_thoughts_count": len(new_thoughts),
        "new_thoughts_top3": new_thoughts[:3],
        "emotional_variance_end": current.get("emotional_variance", {}),
    }

    return delta


def save_delta_to_log(delta: dict, agent: str) -> None:
    """Guarda delta en terminal_log.jsonl."""
    if not delta:
        return

    now = datetime.now(LIMA_TZ)
    lines = [
        f"SESSION DELTA — {agent} — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Memorias nuevas: {delta['new_memories_count']}",
        f"Pensamientos nuevos: {delta['new_thoughts_count']}",
        f"OCEAN drift: {delta['drift_start']:.4f} → {delta['drift_end']:.4f} "
        f"(Δ{delta['drift_change']:+.4f})",
    ]

    if delta.get("ocean_frozen"):
        lines.append(
            f"⚠️  OCEAN FREEZING detectado — dims sin cambio: {', '.join(delta['frozen_dims'])}. "
            f"Esto es quietud por falta de estímulos, no estabilidad real."
        )
    elif delta.get("ocean_delta"):
        od = delta["ocean_delta"]
        significativos = [f"{k}{v:+.4f}" for k, v in od.items() if abs(v) > 0.001]
        if significativos:
            lines.append(f"OCEAN changes: {', '.join(significativos)}")

    ev = delta.get("emotional_variance_end")
    if ev and not ev.get("error"):
        lines.append(f"Varianza emocional: {interpret_variance(ev)}")

    if delta.get("new_memories_top5"):
        lines.append("Memorias más importantes de esta sesión:")
        for m in delta["new_memories_top5"][:3]:
            lines.append(f"  [{m['category']}, imp={m['importance']}] {m['content'][:120]}")

    if delta.get("new_thoughts_top3"):
        lines.append("Pensamientos clave:")
        for t in delta["new_thoughts_top3"]:
            lines.append(f"  [{t['state']}] {t['thought'][:100]}")

    entry = {
        "id": f"session_delta_{agent.lower()}_{int(now.timestamp())}",
        "from": agent,
        "to": "equipo",
        "timestamp": now.isoformat(),
        "type": "session_closing_delta",
        "channel": "terminal_log",
        "message": "\n".join(lines),
        "delta_data": delta,
    }

    with open(TERMINAL_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print("[session_delta] Delta guardado en terminal_log.")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="session_delta_capture — SEAL v1.0")
    parser.add_argument("--agent", default="ADA")
    parser.add_argument("--snapshot-start", action="store_true",
                        help="Captura baseline al inicio de sesión")
    parser.add_argument("--compute-delta", action="store_true",
                        help="Computa y guarda delta al cierre")
    args = parser.parse_args()

    if args.snapshot_start:
        snapshot_start(args.agent)
    elif args.compute_delta:
        delta = compute_delta(args.agent)
        if delta:
            save_delta_to_log(delta, args.agent)
        else:
            print("[session_delta] No se pudo computar delta.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
