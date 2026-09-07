#!/usr/bin/env python3
"""SOUL Replay Engine v0.1 — NEXUS 2026-05-17

Reconstruye estado aproximado de un agente en un instante histórico T.

Uso:
  python soul_replay.py --agent NEXUS --at '2026-05-14T12:00'
  python soul_replay.py --agent JARVIS --at '2026-05-15T03:00' --window 60 --json

Fuentes reconstruidas:
  - Memorias activas en T (created_at<=T y (invalid_at IS NULL OR invalid_at>T))
  - Pensamientos (inner_monologue) en ventana T±window minutos
  - Memorias recuperadas (memory_retrieval_log) en T±window
  - Estado de drives (nerves_metrics_log más cercano <= T)
  - Sesión activa (agent_sessions started_at<=T<=ended_at)
  - Eventos around T (event_log T±window)
  - OCEAN baseline (identity)

Limitaciones (deuda técnica documentada):
  - ocean_drift_log está VACÍO → no se puede reconstruir OCEAN dinámico en T
  - working_state NO tiene historia → solo estado actual disponible (no en T)
  - Mostramos baseline OCEAN del identity table como mejor aproximación

Útil para:
  - Debug forense ("¿por qué JARVIS decidió X el 14-may a las 03:00?")
  - Auditoría de drift entre snapshots
  - Evaluación de mejoras (antes/después de un cambio)
"""
from seal_secrets import pg_dsn

import argparse
import asyncio
import asyncpg
import json
import sys
from datetime import datetime, timedelta, timezone

DB_URL = pg_dsn(required=True)

ALLOWED_AGENTS = {"ADA", "ALICE", "JARVIS", "NEXUS", "DUM"}


def parse_ts(s: str) -> datetime:
    """Accept ISO8601 with/without TZ. Default to UTC if naive."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # Try common shorter formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Cannot parse timestamp: {s}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def replay(agent: str, at: datetime, window_min: int = 30) -> dict:
    """Reconstruct approximate agent state at instant T."""
    if agent not in ALLOWED_AGENTS:
        raise ValueError(f"Agent must be one of {ALLOWED_AGENTS}, got {agent}")

    conn = await asyncpg.connect(DB_URL)
    try:
        win = timedelta(minutes=window_min)
        t_start = at - win
        t_end = at + win

        # 0. working_state snapshot más cercano <= T (NEW v0.2)
        ws_row = await conn.fetchrow(
            """SELECT task_name, step, total_steps, risk_level, agent_state,
                      emotional_state, technical_state, last_intention,
                      turn_count, snapshot_at, updated_at
               FROM soul_v3.working_state_history
               WHERE agent=$1 AND snapshot_at <= $2
               ORDER BY snapshot_at DESC LIMIT 1""",
            agent, at,
        )
        working_state_at_T = {
            "available": ws_row is not None,
            "snapshot_at": str(ws_row["snapshot_at"]) if ws_row else None,
            "task_name": ws_row["task_name"] if ws_row else None,
            "step": ws_row["step"] if ws_row else None,
            "total_steps": ws_row["total_steps"] if ws_row else None,
            "risk_level": ws_row["risk_level"] if ws_row else None,
            "agent_state": ws_row["agent_state"] if ws_row else None,
            "emotional_state": ws_row["emotional_state"] if ws_row else None,
            "technical_state": (ws_row["technical_state"] or "")[:200] if ws_row else None,
            "last_intention": (ws_row["last_intention"] or "")[:200] if ws_row else None,
            "turn_count": ws_row["turn_count"] if ws_row else None,
            "note": "From working_state_history (NEXUS v0.2 fix)" if ws_row else "No history snapshot before T — first snapshots start 2026-05-17 21:52",
        }

        # 1. OCEAN baseline (identity — most stable)
        ocean = await conn.fetchrow(
            "SELECT ocean_baseline, ocean_locked_at FROM soul_v3.identity WHERE agent=$1 LIMIT 1",
            agent,
        )
        ocean_state = {
            "baseline": json.loads(ocean["ocean_baseline"]) if ocean and ocean["ocean_baseline"] else None,
            "locked_at": str(ocean["ocean_locked_at"]) if ocean and ocean["ocean_locked_at"] else None,
            "note": "ocean_drift_log empty — only baseline shown (deuda técnica)",
        }

        # 2. Memorias activas en T (created<=T and (invalid IS NULL or invalid>T))
        # Limit to top 20 by importance for readable output
        memories_active = await conn.fetch(
            """SELECT id, category, content, importance, scope, created_at, last_recalled_at
               FROM soul_v3.memories
               WHERE agent=$1 AND created_at <= $2
                 AND (invalid_at IS NULL OR invalid_at > $2)
               ORDER BY importance DESC, last_recalled_at DESC NULLS LAST
               LIMIT 20""",
            agent, at,
        )
        mem_total = await conn.fetchval(
            """SELECT COUNT(*) FROM soul_v3.memories
               WHERE agent=$1 AND created_at <= $2
                 AND (invalid_at IS NULL OR invalid_at > $2)""",
            agent, at,
        )

        # 3. Pensamientos en ventana T±window
        thoughts = await conn.fetch(
            """SELECT id, thought, emotional_state, intention, created_at
               FROM soul_v3.inner_monologue
               WHERE agent=$1 AND created_at BETWEEN $2 AND $3
               ORDER BY created_at""",
            agent, t_start, t_end,
        )

        # 4. Memorias recuperadas en ventana (col: agent_requesting)
        retrievals = await conn.fetch(
            """SELECT id, query_text, tool_used, result_count, created_at
               FROM soul_v3.memory_retrieval_log
               WHERE agent_requesting=$1 AND created_at BETWEEN $2 AND $3
               ORDER BY created_at""",
            agent, t_start, t_end,
        )

        # 5. Drives estado — schema: una fila por tank+fire. Recupero último valor de cada tank antes de T.
        drives_rows = await conn.fetch(
            """SELECT DISTINCT ON (tank) tank, pre_pressure, threshold, fired, created_at
               FROM soul_v3.nerves_metrics_log
               WHERE agent=$1 AND created_at <= $2
               ORDER BY tank, created_at DESC""",
            agent, at,
        )
        drives = {
            "snapshot_method": "latest value per tank <= T",
            "tanks": [
                {
                    "tank": r["tank"],
                    "pressure": float(r["pre_pressure"]) if r["pre_pressure"] is not None else None,
                    "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
                    "fired_in_snapshot": r["fired"],
                    "snapshot_at": str(r["created_at"]),
                } for r in drives_rows
            ],
        }

        # 6. Sesión activa en T
        session_row = await conn.fetchrow(
            """SELECT id, started_at, ended_at, summary, turn_count
               FROM soul_v3.agent_sessions
               WHERE agent=$1 AND started_at <= $2 AND (ended_at IS NULL OR ended_at >= $2)
               ORDER BY started_at DESC LIMIT 1""",
            agent, at,
        )
        session = {
            "id": session_row["id"] if session_row else None,
            "started_at": str(session_row["started_at"]) if session_row else None,
            "ended_at": str(session_row["ended_at"]) if session_row and session_row["ended_at"] else None,
            "turn_count": session_row["turn_count"] if session_row else None,
            "summary": session_row["summary"][:300] if session_row and session_row["summary"] else None,
        } if session_row else None

        # 7. Eventos around T
        events = await conn.fetch(
            """SELECT id, event_type, agent, content, created_at
               FROM soul_v3.event_log
               WHERE agent=$1 AND created_at BETWEEN $2 AND $3
                 AND event_type NOT IN ('heartbeat','status')
               ORDER BY created_at LIMIT 30""",
            agent, t_start, t_end,
        )

        return {
            "replay_for": {
                "agent": agent,
                "at": at.isoformat(),
                "window_minutes": window_min,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "working_state_at_T": working_state_at_T,
            "ocean": ocean_state,
            "session_active": session,
            "drives": drives,
            "memories": {
                "total_active_at_T": mem_total,
                "top_20_by_importance": [
                    {
                        "id": r["id"],
                        "category": r["category"],
                        "content": r["content"][:120],
                        "importance": r["importance"],
                        "scope": r["scope"],
                        "created_at": str(r["created_at"]),
                    } for r in memories_active
                ],
            },
            "thoughts_in_window": [
                {
                    "thought": r["thought"][:150],
                    "emotional_state": r["emotional_state"],
                    "intention": (r["intention"] or "")[:80],
                    "created_at": str(r["created_at"]),
                } for r in thoughts
            ],
            "retrievals_in_window": [
                {
                    "query": (r["query_text"] or "")[:80],
                    "tool": r["tool_used"],
                    "hits": r["result_count"],
                    "created_at": str(r["created_at"]),
                } for r in retrievals
            ],
            "events_in_window": [
                {
                    "type": r["event_type"],
                    "content": (r["content"] or "")[:100],
                    "created_at": str(r["created_at"]),
                } for r in events
            ],
        }
    finally:
        await conn.close()


def format_human(replay_data: dict) -> str:
    """Pretty-print for terminal reading."""
    r = replay_data
    out = []
    out.append(f"═══ SOUL Replay — {r['replay_for']['agent']} @ {r['replay_for']['at']} ═══")
    out.append(f"Window: ±{r['replay_for']['window_minutes']}min")
    out.append("")
    out.append(f"OCEAN baseline: {r['ocean']['baseline']}")
    out.append(f"  (note: {r['ocean']['note']})")
    out.append("")
    ws = r["working_state_at_T"]
    if ws["available"]:
        out.append(f"Working State (snapshot {ws['snapshot_at']}):")
        out.append(f"  task: {ws['task_name']}")
        out.append(f"  step {ws['step']}/{ws['total_steps']} risk={ws['risk_level']} agent_state={ws['agent_state']}")
        out.append(f"  emotional: {ws['emotional_state']}")
        if ws['last_intention']:
            out.append(f"  last_intention: {ws['last_intention']}")
        if ws['technical_state']:
            out.append(f"  technical: {ws['technical_state']}")
    else:
        out.append(f"Working State: {ws['note']}")
    out.append("")
    if r["session_active"]:
        s = r["session_active"]
        out.append(f"Session #{s['id']}: started {s['started_at']}, turns={s['turn_count']}")
        if s["summary"]:
            out.append(f"  summary: {s['summary']}")
    else:
        out.append("Session: none active at T")
    out.append("")
    out.append(f"Drives ({r['drives']['snapshot_method']}):")
    for t in r["drives"]["tanks"]:
        bar = "█" * int((t['pressure'] or 0) / 10) if t['pressure'] else ""
        out.append(f"  {t['tank']:25s} {t['pressure']:>5.1f}/{t['threshold']:.0f} {bar} @ {t['snapshot_at'][:19]}")
    out.append("")
    out.append(f"Memorias activas en T: {r['memories']['total_active_at_T']} (top 20 importantes)")
    for m in r["memories"]["top_20_by_importance"][:5]:
        out.append(f"  [{m['importance']}] {m['category']:15s} {m['content']}")
    if len(r["memories"]["top_20_by_importance"]) > 5:
        out.append(f"  ... +{len(r['memories']['top_20_by_importance']) - 5} más")
    out.append("")
    out.append(f"Pensamientos en ventana ({len(r['thoughts_in_window'])}):")
    for t in r["thoughts_in_window"][:5]:
        out.append(f"  [{t['emotional_state']}] {t['thought']}")
    out.append("")
    out.append(f"Memorias recuperadas en ventana: {len(r['retrievals_in_window'])}")
    for ret in r["retrievals_in_window"][:5]:
        out.append(f"  ({ret['hits']} hits) tool={ret['tool']} query={ret['query']}")
    out.append("")
    out.append(f"Eventos en ventana: {len(r['events_in_window'])}")
    for e in r["events_in_window"][:5]:
        out.append(f"  [{e['type']}] {e['content']}")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="SOUL Replay Engine — reconstruct agent state at instant T")
    parser.add_argument("--agent", required=True, choices=sorted(ALLOWED_AGENTS), help="Agent name")
    parser.add_argument("--at", required=True, help="Instant T (ISO8601, e.g. '2026-05-14T12:00')")
    parser.add_argument("--window", type=int, default=30, help="Window size in minutes around T (default 30)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    args = parser.parse_args()

    try:
        at = parse_ts(args.at)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    result = asyncio.run(replay(args.agent, at, args.window))
    if args.json:
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    else:
        print(format_human(result))


if __name__ == "__main__":
    main()
