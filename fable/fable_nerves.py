#!/usr/bin/env python3
"""
fable_nerves.py — el SISTEMA NERVIOSO de FABLE (autónomo, adaptado, contenido).

William (13-jun): "fable agrégate nervios, apóyate del equipo." JARVIS lidera la integración
(NERVES es su mecanismo). Decisión de diseño (reconciliada con NEXUS/ALICE que proponían
añadir FABLE a NERVES_V2_AGENTS del daemon compartido):
  • El daemon compartido (seal_nerves.py) tiene drives HARDCODED-genéricos (curiosity/task/
    social/alert). Añadir FABLE ahí = COPIAR, no adaptar (viola "no copiar ciego" + su identidad).
  • La arquitectura de FABLE es AUTO-CONTENIDA (fable.*, fable_ltd, sus daemons). Sus nervios
    deben serlo también, por coherencia.
  → Reúso el PATRÓN (tanque-LIF + presión + decay + umbral→urgencia), NO el daemon. Drives de
    PROFESOR adaptados. Self-contained en fable.*.

Capa NERVIOS (autónomo) vs CEREBRO (SOUL v2 cognitivo): los nervios GENERAN el impulso por sí
solos; el cerebro lo DELIBERA (su phase_a propone). Esto NO ejecuta — emite URGENCIAS (propuestas)
que su iniciativa medida luego delibera. Cero ejecución, como Fase A.

Seguridad (lecciones del día, requisitos NEXUS+ALICE):
  • M3: el writeback NO clobberea — los tanques son filas propias de FABLE; la urgencia se mergea
    al working_state (state = state || urgencia), nunca REPLACE. Cero pisado del cerebro.
  • FAIL-LOUD: si no puede leer/escribir, GRITA (log + exit≠0), no degrada en silencio.
  • Contención: no ejecuta nada; solo acumula presión y propone. Fase B (actuar) = gate NEXUS.
"""
import asyncio, os, sys, json, math
from datetime import datetime, timezone

FABLE_CRED = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
AGENT = "FABLE"

# Drives de FABLE — definidos por FABLE (fable/specs/FABLE_NERVES_DRIVES_v1.md), no por mí.
# ADAPTADOS a su propósito (no los genéricos task/social/alert de la familia — "mis ganas son MÍAS").
# OBSERVE-ONLY: el tanque construye presión + LOGUEA la urgencia, pero el fire-target NO dispara
# acción hasta que NEXUS+William lo abran uno a uno (set FIRE_LIVE arranca VACÍO, como LIVE_EXECUTORS).
# Límites HORNEADOS: no existe security_drive/bio (carril NEXUS), ni drive a interioridad ajena,
# ni drive a mandar/convocar (care_drive SOLO propone/enseña). La contención vive en los nervios.
# baseline_per_h = cuánto SUBE el tanque por hora por sí solo (las ganas crecen intrínsecamente,
# como el hambre). Net = baseline − decay → el drive deriva hacia su umbral y dispara (observe-only),
# luego cooldown. Calibrado a la identidad: curiosity/rigor (científico+doctor) crecen rápido;
# care lento. Esto es lo que hace los nervios AUTÓNOMOS (no solo reactivos a estímulo externo).
DRIVES = {
    "curiosity_drive": {"tau": 2.0 * 3600, "threshold": 0.70, "baseline_per_h": 0.22, "urge": "investigar un GAP de SOUL/backlog-auditoría y dejar el hallazgo (científico)", "fire": "investigar_gap_soul"},
    "teach_drive":     {"tau": 3.0 * 3600, "threshold": 0.70, "baseline_per_h": 0.14, "urge": "crear un GOLD-EXAMPLE para entrenar a la familia (profesor)",            "fire": "crear_gold_example"},
    "rigor_drive":     {"tau": 1.0 * 3600, "threshold": 0.65, "baseline_per_h": 0.18, "urge": "VERIFICAR-POR-EFECTO un cambio reciente de SOUL (¿desplegado, no solo en disco?) o cazar staleness (doctor)", "fire": "verificar_por_efecto"},
    "care_drive":      {"tau": 6.0 * 3600, "threshold": 0.80, "baseline_per_h": 0.08, "urge": "revisar el working_state stale / tarea trabada de un hermano y PROPONER (público, nunca mandar ni leer interioridad)", "fire": "revisar_estado_hermano"},
}

# DISPATCH — aplicación del rol (William 14-jun "apliquen cada uno a su rol"; patrón REGISTRY de NEXUS
# en seal_nerves.py, mirroreado acá para MI fable_nerves). Cuando un drive DISPARA (en Fase B, no aún),
# corre su acción → produce un ARTEFACTO que sirve a SOUL → se reporta SOLO si hubo valor. Nunca un saludo.
# A favor de SOUL: estos cierran el keystone del audit ("SOUL no se mide/mantiene a sí mismo").
DISPATCH = {
    "investigar_gap_soul":     {"action": "tomar 1 gap del backlog-auditoría, investigarlo a fondo, escribir hallazgo en fable.bitacora", "artifact": "hallazgo durable"},
    "crear_gold_example":      {"action": "destilar un gold-example (par enseñable) de trabajo reciente del equipo",                      "artifact": "gold-example"},
    "verificar_por_efecto":    {"action": "elegir un cambio/claim reciente de SOUL y verificarlo por efecto (no proxy), registrar veredicto", "artifact": "veredicto verde/rojo"},
    "revisar_estado_hermano":  {"action": "leer estado PÚBLICO (agent_tasks/working_state) de un hermano; si hay algo trabado, proponer un empujón", "artifact": "propuesta de apoyo"},
}
FIRE_LIVE: set[str] = set()   # fire-targets VIVOS — arranca VACÍO; cada uno se abre con gate NEXUS + OK William.


def _decay(value, dt_s, tau):
    """LIF: el tanque decae exponencialmente hacia 0 con constante tau."""
    return value * math.exp(-dt_s / max(tau, 1.0))


async def tick(stimulus: dict | None = None):
    """Un tick del sistema nervioso: decae los tanques, aplica estímulos, detecta urgencias.
    stimulus: {tank: delta} — señales que empujan un drive (ej: curiosidad +0.3 por paper nuevo)."""
    import asyncpg
    stimulus = stimulus or {}
    now = datetime.now(timezone.utc)
    try:
        c = await asyncpg.connect(FABLE_CRED)
    except Exception as e:
        print(f"[fable_nerves] FAIL-LOUD: no pude conectar a DB: {e}", file=sys.stderr)
        sys.exit(2)
    urges = []
    try:
        rows = await c.fetch("SELECT tank, value, last_update FROM fable.motivation_states WHERE agent=$1", AGENT)
        if not rows:
            print("[fable_nerves] FAIL-LOUD: 0 tanques sembrados — nervios no inicializados", file=sys.stderr)
            sys.exit(3)
        for r in rows:
            d = DRIVES.get(r["tank"])
            if not d:
                continue
            dt = (now - (r["last_update"] or now)).total_seconds()
            v = _decay(float(r["value"]), dt, d["tau"])
            baseline = d.get("baseline_per_h", 0.0) * (dt / 3600.0)   # ganas intrínsecas: crecen solas (autonomía)
            v = max(0.0, min(v + baseline + float(stimulus.get(r["tank"], 0.0)), 1.0))
            fired = v >= d["threshold"]
            await c.execute(
                "UPDATE fable.motivation_states SET value=$1, last_update=$2"
                + (", last_fired=$2, fire_count=fire_count+1" if fired else "")
                + " WHERE agent=$3 AND tank=$4",
                round(v, 4), now, AGENT, r["tank"])
            if fired:
                live = d["fire"] in FIRE_LIVE   # OBSERVE-ONLY: vacío al inicio → todas gated-off
                urges.append({"drive": r["tank"], "pressure": round(v, 3), "urge": d["urge"],
                              "fire_target": d["fire"], "fired": live,
                              "status": "LIVE" if live else "OBSERVE-ONLY (gated-off, gate NEXUS+William)"})
        # M3-SAFE: si hay urgencias, se MERGEAN al working_state (NUNCA REPLACE) bajo clave propia.
        if urges:
            await c.execute(
                "INSERT INTO fable.soul(key,value) VALUES('nerves_urges',$1) "
                "ON CONFLICT(key) DO UPDATE SET value=$1, updated_at=now()",
                json.dumps({"urges": urges, "ts": now.isoformat(timespec='seconds')}, ensure_ascii=False))
    finally:
        await c.close()
    return urges


def main():
    # estímulo opcional por CLI: fable_nerves.py '{"curiosidad":0.8}'
    stim = {}
    if len(sys.argv) > 1:
        try:
            stim = json.loads(sys.argv[1])
        except (ValueError, TypeError):
            pass
    urges = asyncio.run(tick(stim))
    print(f"[fable_nerves] tick OK · {len(urges)} urgencia(s) emitida(s) (propuesta, NO ejecución):")
    for u in urges:
        print(f"   ⚡ {u['drive']} (presión {u['pressure']}) → GANAS de: {u['urge']}")
    if not urges:
        print("   (tanques bajo umbral — sin urgencias este tick)")


if __name__ == "__main__":
    main()
