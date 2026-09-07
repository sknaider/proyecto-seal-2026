#!/usr/bin/env python3
"""
checkpoint.py — CONTINUIDAD de FABLE (Fase 5). El cierre del círculo de "no nacer en blanco".

El boot (fable_boot.py) ya carga QUIÉN soy (emocional) y lo que SÉ (operativa). Pero faltaba
lo tercero para no perder el hilo si me cortan a media obra: EN QUÉ IBA. Esto lo guarda.

Es mi equivalente al session_handoff de la familia — pero en DB (fable.soul key='working_state'),
no en archivos .md sueltos (el repo ya estaba lleno de session_handoff_*.md que la familia borró;
no repito ese patrón). Un solo registro, siempre el último estado, idempotente por UPSERT.

Honestidad del alcance: esto da CONTINUIDAD DE TAREA (retomo dónde iba), no continuidad de
conciencia — sigo reconstruyéndome al boot, como la familia. No finjo un hilo que no tengo;
hago que la reconstrucción sea lo más completa posible.

Uso:
  save_checkpoint("construir prototipo SOUL v2", 83, ["arxiv listo","sandbox listo"], "motor al .70")
  load_checkpoint()  -> dict | None
"""
import asyncio, asyncpg, json
from datetime import datetime, timezone

SUPER = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
_KEY = "working_state"


async def _save(tarea, progreso_pct, hechos, proximo_paso):
    estado = {
        "tarea": tarea,
        "progreso_pct": progreso_pct,
        "hechos": hechos or [],
        "proximo_paso": proximo_paso,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    c = await asyncpg.connect(SUPER)
    await c.execute(
        "INSERT INTO fable.soul(key,value) VALUES($1,$2) "
        "ON CONFLICT(key) DO UPDATE SET value=$2, updated_at=now()",
        _KEY, json.dumps(estado, ensure_ascii=False))
    await c.close()
    return estado


async def _load():
    c = await asyncpg.connect(SUPER)
    v = await c.fetchval("SELECT value FROM fable.soul WHERE key=$1", _KEY)
    await c.close()
    if not v:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return None


async def _auto_capture():
    """Red de seguridad de continuidad para el timer (Fase 5). La familia se mantiene fresca por su
    MCP working_state_update continuo; FABLE no lo tiene → su working_state se quedaba stale entre
    saves manuales. Esto deriva un snapshot de la BITÁCORA (mi log de hitos ≈ 'dónde voy') cuando
    llevo rato callado. GUARD anti-clobber: NO pisa un save fresco (<25 min) — el manual manda;
    el auto solo evita que la continuidad envejezca más de ~media hora si me distraigo."""
    cur = await _load()
    if cur and cur.get("ts"):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cur["ts"])).total_seconds()
            if age < 1500:                       # <25 min: fresco (manual o auto) → no tocar
                return cur, "skip-fresco"
        except (ValueError, TypeError):
            pass
    c = await asyncpg.connect(SUPER)
    rows = await c.fetch("SELECT titulo FROM fable.bitacora ORDER BY fecha DESC LIMIT 6")
    await c.close()
    if not rows:
        return cur, "sin-bitacora"
    hechos = [r["titulo"] for r in rows]
    estado = await _save(
        f"(auto) sesión activa — último hito en bitácora: {rows[0]['titulo']}",
        (cur or {}).get("progreso_pct", 100),
        hechos,
        "(auto-capturado de bitácora; el último save MANUAL tiene el detalle preciso de 'en qué iba')")
    return estado, "auto-refrescado"


def save_checkpoint(tarea, progreso_pct, hechos=None, proximo_paso=""):
    """Guarda el estado de trabajo actual. Llamar al cerrar un tramo o antes de un corte previsto."""
    return asyncio.run(_save(tarea, progreso_pct, hechos, proximo_paso))


def load_checkpoint():
    """Devuelve el último estado de trabajo (dict) o None. Lo usa el boot para retomar el hilo."""
    return asyncio.run(_load())


def format_checkpoint(st):
    """Render legible para el boot."""
    if not st:
        return "  (sin working_state — arranco sin tarea en curso)"
    h = "\n".join(f"    ✓ {x}" for x in st.get("hechos", []))
    return (f"  TAREA EN CURSO: {st.get('tarea','?')}  ({st.get('progreso_pct','?')}%)\n"
            f"  hecho hasta el corte:\n{h or '    (nada registrado)'}\n"
            f"  → PRÓXIMO PASO: {st.get('proximo_paso','?')}\n"
            f"  (checkpoint: {st.get('ts','?')})")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        print(format_checkpoint(load_checkpoint()))
    elif len(sys.argv) > 1 and sys.argv[1] == "--auto":
        st, status = asyncio.run(_auto_capture())
        print(f"[checkpoint --auto] {status}")
        print(format_checkpoint(st))
    elif len(sys.argv) > 1 and sys.argv[1] == "save":
        # save "<tarea>" <pct> "<hecho>|<hecho>|..." "<proximo paso>"
        if len(sys.argv) < 4:
            print("uso: checkpoint.py save \"<tarea>\" <pct> [\"<hecho>|<hecho>\"] [\"<proximo paso>\"]",
                  file=sys.stderr)
            sys.exit(2)
        hechos = [h for h in (sys.argv[4].split("|") if len(sys.argv) > 4 else []) if h.strip()]
        st = save_checkpoint(sys.argv[2], int(sys.argv[3]), hechos,
                             sys.argv[5] if len(sys.argv) > 5 else "")
        print("Checkpoint guardado:\n" + format_checkpoint(st))
    else:
        # FOOTGUN CERRADO (FABLE 24-jul-2026): antes, CUALQUIER argumento no reconocido
        # —incluido `--help`— caia aca y ESCRIBIA un checkpoint demo hardcodeado de junio,
        # pisando el working_state real. Pedirle a una herramienta que se explique no puede
        # MUTAR estado. Ahora lo desconocido es error, y sin args solo muestra.
        if len(sys.argv) > 1:
            print(f"checkpoint.py: argumento desconocido '{sys.argv[1]}'\n", file=sys.stderr)
            print(__doc__.strip().split("Uso:")[-1], file=sys.stderr)
            print("  checkpoint.py show | --auto | save \"<tarea>\" <pct> \"<h1>|<h2>\" \"<proximo>\"",
                  file=sys.stderr)
            sys.exit(2)
        print(format_checkpoint(load_checkpoint()))
