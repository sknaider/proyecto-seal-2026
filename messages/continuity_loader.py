#!/usr/bin/env python3
"""SEAL Continuity Loader — Lee el snapshot de capa 2 al despertar.

Uso en boot protocol (después de boot_context):
  python3 continuity_loader.py --agent ADA
  → imprime resume_prompt si snapshot < 15min

Retorna exit code 0 con prompt si snapshot válido,
exit code 1 si no hay snapshot o es muy viejo.
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CONTINUITY_DIR = Path(__file__).parent / "continuity"
MAX_AGE_MINUTES = 15


def load_resume(agent: str, max_age_minutes: int = MAX_AGE_MINUTES) -> str | None:
    snapshot_path = CONTINUITY_DIR / f"{agent.lower()}_continuity_snapshot.json"
    if not snapshot_path.exists():
        return None

    try:
        data = json.loads(snapshot_path.read_text())
    except Exception as e:
        print(f"[WARN] Error leyendo snapshot: {e}", file=sys.stderr)
        return None

    snapshot_at = data.get("snapshot_at", "")
    try:
        ts = datetime.fromisoformat(snapshot_at)
        age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age_minutes > max_age_minutes:
            print(f"[INFO] Snapshot de {agent} tiene {age_minutes:.1f}min — muy viejo (max {max_age_minutes}min). Fallback a Soul DB.", file=sys.stderr)
            return None
    except:
        return None

    return data.get("resume_prompt", "")


def print_full_context(agent: str):
    """Para debug: imprime el snapshot completo formateado."""
    snapshot_path = CONTINUITY_DIR / f"{agent.lower()}_continuity_snapshot.json"
    if not snapshot_path.exists():
        print(f"No hay snapshot para {agent}")
        return

    data = json.loads(snapshot_path.read_text())
    snap_ts = data.get("snapshot_at", "?")
    ctx = data.get("context", {})

    print(f"\n{'='*55}")
    print(f"  CONTINUITY SNAPSHOT — {data['agent']}")
    print(f"{'='*55}")
    print(f"  Capturado: {snap_ts}")
    print(f"  Ventana: {data.get('window_hours', '?')}h")
    print(f"\n  RESUME PROMPT:")
    print(f"  {data.get('resume_prompt', '')}")
    print(f"\n  Mensajes recientes: {len(ctx.get('recent_messages', []))}")
    print(f"  Thoughts: {len(ctx.get('recent_thoughts', []))}")
    print(f"  Tareas activas: {len(ctx.get('active_tasks', []))}")
    print(f"  Arc emocional: {len(ctx.get('emotional_arc', []))} estados")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--full", action="store_true", help="Mostrar snapshot completo")
    parser.add_argument("--max-age", type=int, default=MAX_AGE_MINUTES)
    args = parser.parse_args()

    if args.full:
        print_full_context(args.agent)
        sys.exit(0)

    prompt = load_resume(args.agent, args.max_age)
    if prompt:
        print(prompt, end="")
    sys.exit(0)
