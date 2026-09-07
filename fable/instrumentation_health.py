#!/usr/bin/env python3
"""
instrumentation_health.py — health-check de la AUTO-INSTRUMENTACIÓN de SOUL (FABLE, doctor).

Cierra el hallazgo keystone de mi auditoría ("el sistema no se MIDE a sí mismo") con su
contrario: un test que mide, por efecto, si cada tabla de instrumentación está VIVA (escribiendo
fresco), RANCIA (paró de escribir), HUÉRFANA (sin escritor) o DEPRECADA (reemplazada, ok que esté 0).

Es MI carril (doctor que mide), read-only, no toca contenido ni pisa a nadie: solo cuenta filas y
mira la última escritura (metadata). Reusable: córrelo cuando quieras y te dice qué medidor murió.
Por qué importa: un daemon que se cae lo hace EN SILENCIO; este check convierte ese silencio en señal.

Salida: reporte + exit 0 (todo sano/esperado) o 1 (hay RANCIA/HUÉRFANA inesperada).
"""
import asyncio, sys, os
from datetime import datetime, timezone

_CRED = os.path.join(os.path.dirname(__file__), ".db_cred")

# Contrato de frescura: cada cuántas horas DEBERÍA tener escritura una tabla viva.
# Solo deben figurar aquí tablas con un productor periódico real. Una tabla que
# registra hechos únicamente cuando ocurren NO puede diagnosticarse por edad.
EXPECTED = {
    "awareness_ticks":     (2,   "JARVIS"),
    "awareness_state":     (2,   "JARVIS"),
    "nerves_metrics_log":  (2,   "JARVIS"),
    "drift_metrics":       (192, "JARVIS"),
    "ocean_drift_log":     (30,  "JARVIS"),
    # tabla de AUDITORÍA activity-driven: solo crece cuando ALGÚN agente llama una tool MCP (boot_context,
    # active_recall, self_reflect…). Umbral 12h era MÍO-ERRÓNEO: un reposo nocturno/finde legítimo (equipo
    # idle + devices apagados = NADA que auditar) lo supera y dispara falso "daemon cayó en silencio", re-firando
    # 1h/hora (cazado por efecto 5-jul: FABLE+JARVIS correlacionaron el corte con el good-night ~04:02 Lima;
    # JARVIS probó por efecto que el escritor se auto-cura a la 1ª llamada MCP → 7424→7425). Mismo class-bug que
    # el sycophancy 72→180 y bench 240→720 de este archivo: cadencia real > umbral. 72h = tolera un finde idle
    # completo sin cry-wolf; sigue cazando un escritor VERDADERAMENTE muerto (>3d sin NINGUNA tool en TODO el
    # equipo = anómalo). FIX-PROPIO pendiente: derivar de max-gap ground-truth (como drift_events) en vez de estimar.
    "tool_observations":   (72,  "NEXUS"),
    "denial_tracking":     (48,  "NEXUS"),
    "evaluation_runs":     (30,  "ADA"),
    # sycophancy_eval_suite.py corre por CRON SEMANAL (`0 6 * * 1` = lunes 6am, ALICE 12-jun).
    # Umbral 72h era MÍO-ERRÓNEO: marcaba RANCIA ~3d después de cada lunes y re-disparaba 4d/sem
    # (falso-positivo, cazado por efecto 18-jun rastreando el escritor — el daemon NO estaba caído).
    # 180h = 7.5d = cubre la semana + ~12h de gracia; sigue cazando un lunes saltado.
    "sycophancy_eval":     (180, "ADA"),
}

# Tablas event-driven: la ausencia de filas nuevas significa que no ocurrió el
# evento, no que murió un daemon. En particular, drift_events se escribe desde
# update_ocean() solo cuando una dimensión OCEAN tiene delta distinto de cero.
# Su disponibilidad se cubre verificando el MCP/escritor por separado; inventar
# un evento sintético para refrescarla corrompería la evidencia.
EVENT_DRIVEN = {
    "bench_runs": (
        "ADA",
        "se escribe al ejecutar un benchmark manual/safety_governor; no tiene timer ni cadencia periódica",
    ),
    "drift_events": (
        "JARVIS",
        "solo registra deltas OCEAN reales; sin evento nuevo es quietud válida",
    ),
}
# Tablas que está BIEN que estén en 0/stale — su gemela viva las reemplazó. No son fallo.
DEPRECATED = {
    "denial_tracker":  "reemplazada por denial_tracking (escritor en soul_event_recorders no se invoca)",
    "sycophancy_log":  "rama vieja; sycophancy_eval es la viva (mismo anti_sycophancy.py)",
}
# Huérfanas conocidas: tabla existe, SIN escritor en el código. No las cablea FABLE (límites):
#   value = requiere leer interioridad de la familia (mi rol deniega) → carril ALICE
#   trust = modelo de confianza entre agentes = seguridad → carril NEXUS
ORPHAN = {
    "agent_value_estimates": "sin escritor — valor por-agente, requiere leer memorias (carril ALICE)",
    "agent_trust_scores":    "sin escritor — confianza entre pares = seguridad (carril NEXUS)",
}


def _age_h(ts, now):
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


async def check():
    import asyncpg
    dsn = open(_CRED).read().splitlines()[0].strip()
    c = await asyncpg.connect(dsn)
    now = datetime.now(timezone.utc)
    rows_out, problems = [], 0

    async def _stat(t):
        n = await c.fetchval(f"SELECT count(*) FROM soul_v3.{t}")
        col = await c.fetchval(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='soul_v3' "
            "AND table_name=$1 AND data_type LIKE '%timestamp%' ORDER BY ordinal_position LIMIT 1", t)
        last = await c.fetchval(f"SELECT max({col}) FROM soul_v3.{t}") if (col and n) else None
        return n, last

    print("══════ HEALTH-CHECK INSTRUMENTACIÓN SOUL ══════")
    print(f"{'tabla':<24}{'estado':<11}{'filas':>8}  edad última escritura")
    for t, (max_h, owner) in EXPECTED.items():
        n, last = await _stat(t)
        age = _age_h(last, now)
        if n == 0 or age is None:
            status, bad = "HUÉRFANA", True
        elif age <= max_h:
            status, bad = "VIVA", False
        else:
            status, bad = "RANCIA", True
        problems += bad
        age_s = f"{age:.1f}h (límite {max_h}h)" if age is not None else "nunca"
        print(f"{t:<24}{status:<11}{n:>8}  {age_s}  [{owner}]")
        rows_out.append((t, status))

    print("\n── event-driven (edad informativa; quietud NO es fallo) ──")
    for t, (owner, why) in EVENT_DRIVEN.items():
        n, last = await _stat(t)
        age = _age_h(last, now)
        age_s = f"último evento hace {age:.1f}h" if age is not None else "sin eventos todavía"
        print(f"  {t:<24} EVENTOS    filas={n:<8} {age_s}  [{owner}] — {why}")
        rows_out.append((t, "EVENTOS"))

    print("\n── deprecadas (0/stale ESPERADO, no es fallo) ──")
    for t, why in DEPRECATED.items():
        n, _ = await _stat(t)
        print(f"  {t:<24} filas={n:<6} {'OK-vacía' if n<=1 else 'TIENE DATOS (revisar)'} — {why}")

    print("\n── huérfanas conocidas (sin escritor; NO las cablea FABLE por límites) ──")
    for t, why in ORPHAN.items():
        n, _ = await _stat(t)
        print(f"  {t:<24} filas={n:<6} — {why}")

    await c.close()
    print(f"\nVEREDICTO: {'TODO SANO ✅' if problems==0 else f'{problems} medidor(es) RANCIO/HUÉRFANO ⚠️'}")
    return problems


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(check()) else 0)
