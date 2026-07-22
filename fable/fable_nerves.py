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
solos. Tres drives siguen emitiendo urgencias contenidas; ``rigor_drive`` ejecuta una acción
allowlisted y reversible de verificación por efecto. Ningún drive destructivo está habilitado.

Seguridad (lecciones del día, requisitos NEXUS+ALICE):
  • M3: el writeback NO clobberea — los tanques son filas propias de FABLE; la urgencia se mergea
    al working_state (state = state || urgencia), nunca REPLACE. Cero pisado del cerebro.
  • FAIL-LOUD: si no puede leer/escribir, GRITA (log + exit≠0), no degrada en silencio.
  • Contención: solo ``rigor_drive`` ejecuta un verificador allowlisted; los otros
    drives proponen y ninguna ruta puede realizar mutaciones destructivas.
"""
import asyncio, fcntl, os, sys, json, math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

FABLE_CRED = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
AGENT = "FABLE"

# Drives de FABLE — definidos por FABLE (fable/specs/FABLE_NERVES_DRIVES_v1.md), no por mí.
# ADAPTADOS a su propósito (no los genéricos task/social/alert de la familia — "mis ganas son MÍAS").
# MIXTO: todos los tanques construyen presión y registran la urgencia; solo los
# targets enumerados en FIRE_LIVE ejecutan una acción allowlisted.
# Límites HORNEADOS: no existe security_drive/bio (carril NEXUS), ni drive a interioridad ajena,
# ni drive a mandar/convocar (care_drive SOLO propone/enseña). La contención vive en los nervios.
# baseline_per_h = cuánto SUBE el tanque por hora por sí solo (las ganas crecen intrínsecamente,
# como el hambre). Net = baseline − decay → el drive deriva hacia su umbral y dispara,
# luego cooldown. Calibrado a la identidad: curiosity/rigor (científico+doctor) crecen rápido;
# care lento. Esto es lo que hace los nervios AUTÓNOMOS (no solo reactivos a estímulo externo).
DRIVES = {
    "curiosity_drive": {"tau": 6.0 * 3600, "threshold": 0.70, "baseline_per_h": 0.22, "cooldown_s": 3600, "urge": "investigar un GAP de SOUL/backlog-auditoría y dejar el hallazgo (científico)", "fire": "investigar_gap_soul"},
    "teach_drive":     {"tau": 8.0 * 3600, "threshold": 0.70, "baseline_per_h": 0.14, "cooldown_s": 3600, "urge": "crear un GOLD-EXAMPLE para entrenar a la familia (profesor)",            "fire": "crear_gold_example"},
    "rigor_drive":     {"tau": 2.0 * 3600, "threshold": 0.65, "baseline_per_h": 0.60, "cooldown_s": 1800, "urge": "VERIFICAR-POR-EFECTO un cambio reciente de SOUL (¿desplegado, no solo en disco?) o cazar staleness (doctor)", "fire": "verificar_por_efecto"},
    "care_drive":      {"tau": 12.0 * 3600, "threshold": 0.80, "baseline_per_h": 0.08, "cooldown_s": 7200, "urge": "revisar el working_state stale / tarea trabada de un hermano y PROPONER (público, nunca mandar ni leer interioridad)", "fire": "revisar_estado_hermano"},
}

# DISPATCH — aplicación del rol (William 14-jun "apliquen cada uno a su rol"; patrón REGISTRY de NEXUS
# en seal_nerves.py, mirroreado acá para MI fable_nerves). Cuando un drive live DISPARA,
# corre su acción → produce un ARTEFACTO que sirve a SOUL → se reporta SOLO si hubo valor. Nunca un saludo.
# A favor de SOUL: estos cierran el keystone del audit ("SOUL no se mide/mantiene a sí mismo").
DISPATCH = {
    "investigar_gap_soul":     {"action": "tomar 1 gap del backlog-auditoría, investigarlo a fondo, escribir hallazgo en fable.bitacora", "artifact": "hallazgo durable"},
    "crear_gold_example":      {"action": "destilar un gold-example (par enseñable) de trabajo reciente del equipo",                      "artifact": "gold-example"},
    "verificar_por_efecto":    {"action": "elegir un cambio/claim reciente de SOUL y verificarlo por efecto (no proxy), registrar veredicto", "artifact": "veredicto verde/rojo"},
    "revisar_estado_hermano":  {"action": "leer estado PÚBLICO (agent_tasks/working_state) de un hermano; si hay algo trabado, proponer un empujón", "artifact": "propuesta de apoyo"},
}
FIRE_LIVE: set[str] = {"verificar_por_efecto"}
TICK_SECONDS = 15 * 60
ACTION_FRESHNESS_SECONDS = 2 * 3600
ACTION_REPORT = Path(os.environ.get(
    "FABLE_NERVES_ACTION_REPORT",
    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves_fable_maintenance.json",
))
# Heartbeat de LIVENESS: se escribe en CADA tick (cruce umbral o no). Separa
# "el nervio está vivo" (este archivo, renueva ~cada tick) de "disparó una acción"
# (ACTION_REPORT, solo en fire por umbral, que legítimamente puede pasar >2h sin fire).
# El supervisor debe vigilar ESTE para liveness; el ACTION_REPORT solo para fires productivos.
HEARTBEAT_FILE = Path(os.environ.get(
    "FABLE_NERVES_HEARTBEAT",
    "/home/dadito/IA/proyecto-seal/research/flywire_results/nerves_fable_heartbeat.json",
))
LOCK_FILE = Path(os.environ.get("FABLE_NERVES_LOCK", "/tmp/seal-nerves-FABLE.lock"))


def _write_heartbeat(payload: dict) -> None:
    """Escribe el heartbeat de liveness de forma atómica y 0600. Nunca rompe el tick."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEARTBEAT_FILE.with_suffix(".hb.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(HEARTBEAT_FILE)
        os.chmod(HEARTBEAT_FILE, 0o600)
    except Exception as e:
        print(f"[fable_nerves] heartbeat write failed (no rompe el tick): {e}", file=sys.stderr)


@contextmanager
def _tick_lock():
    """Fail-safe single-flight lease for the complete sense→act→persist cycle."""
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


async def _execute_live_action(target: str) -> dict:
    """Execute one explicitly allowlisted, non-destructive FABLE action."""
    if target != "verificar_por_efecto":
        raise RuntimeError(f"fire target is not allowlisted: {target}")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "/home/dadito/IA/proyecto-seal/fable/instrumentation_health.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
    report = {
        "schema": "seal.fable_nerves_action.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT,
        "target": target,
        "status": "clean" if proc.returncode == 0 else "finding",
        "returncode": proc.returncode,
        "stdout_tail": stdout.decode("utf-8", "replace")[-2000:],
        "stderr_tail": stderr.decode("utf-8", "replace")[-1000:],
    }
    ACTION_REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACTION_REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ACTION_REPORT)
    os.chmod(ACTION_REPORT, 0o600)
    return report


def _decay(value, dt_s, tau):
    """LIF: el tanque decae exponencialmente hacia 0 con constante tau."""
    return value * math.exp(-dt_s / max(tau, 1.0))


def _intrinsic_crossing_seconds(config: dict, *, horizon_s: int = 48 * 3600) -> int | None:
    """Return when an unstimulated drive crosses, using the real timer cadence."""
    value = 0.0
    for elapsed in range(TICK_SECONDS, horizon_s + 1, TICK_SECONDS):
        value = _decay(value, TICK_SECONDS, config["tau"])
        value += config["baseline_per_h"] * (TICK_SECONDS / 3600.0)
        value = min(value, 1.0)
        if value >= config["threshold"]:
            return elapsed
    return None


def _validate_drive_reachability() -> None:
    """Fail loud when intrinsic pressure can never reach its declared action."""
    for name, config in DRIVES.items():
        crossing = _intrinsic_crossing_seconds(config)
        if crossing is None:
            raise RuntimeError(f"drive cannot autonomously cross threshold: {name}")
        if config["fire"] in FIRE_LIVE and crossing > ACTION_FRESHNESS_SECONDS:
            raise RuntimeError(
                f"live drive exceeds action freshness: {name} crossing={crossing}s"
            )


async def tick(stimulus: dict | None = None):
    """Un tick del sistema nervioso: decae los tanques, aplica estímulos, detecta urgencias.
    stimulus: {tank: delta} — señales que empujan un drive (ej: curiosidad +0.3 por paper nuevo)."""
    _validate_drive_reachability()
    with _tick_lock() as acquired:
        if not acquired:
            print("[fable_nerves] tick omitido: otro ciclo posee el lease")
            return []
        return await _tick_locked(stimulus)


async def _tick_locked(stimulus: dict | None = None):
    """Implementation protected by :func:`_tick_lock`."""
    import asyncpg
    stimulus = stimulus or {}
    now = datetime.now(timezone.utc)
    try:
        c = await asyncpg.connect(FABLE_CRED)
    except Exception as e:
        print(f"[fable_nerves] FAIL-LOUD: no pude conectar a DB: {e}", file=sys.stderr)
        sys.exit(2)
    urges = []
    action_failures = []
    tank_summary = []
    try:
        rows = await c.fetch(
            "SELECT tank, value, last_update, last_fired "
            "FROM fable.motivation_states WHERE agent=$1",
            AGENT,
        )
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
            last_fired = r["last_fired"]
            in_cooldown = bool(
                last_fired
                and (now - last_fired).total_seconds() < d.get("cooldown_s", 0)
            )
            crossed = v >= d["threshold"] and not in_cooldown
            tank_summary.append({"tank": r["tank"], "value": round(v, 4), "crossed": crossed})
            live = crossed and d["fire"] in FIRE_LIVE
            action_result = None
            if live:
                try:
                    action_result = await _execute_live_action(d["fire"])
                except Exception as exc:
                    action_failures.append(f"{d['fire']}:{type(exc).__name__}:{exc}")

            action_ok = live and action_result is not None
            # A gated observe-only crossing still completed its signal cycle;
            # reset it and start cooldown so it cannot emit the same urge every
            # 15 minutes forever. A failed live action stays pressurized and
            # retries instead of being acknowledged falsely.
            cycle_completed = action_ok or (crossed and not live)
            stored_value = 0.0 if cycle_completed else round(v, 4)
            await c.execute(
                "UPDATE fable.motivation_states SET value=$1, last_update=$2"
                + (", last_fired=$2, fire_count=fire_count+1" if cycle_completed else "")
                + " WHERE agent=$3 AND tank=$4",
                stored_value, now, AGENT, r["tank"])
            if crossed:
                urges.append({"drive": r["tank"], "pressure": round(v, 3), "urge": d["urge"],
                              "fire_target": d["fire"], "fired": action_ok,
                              "action_result": action_result,
                              "status": ("LIVE_ACTION_COMPLETE" if action_ok else
                                         "LIVE_ACTION_FAILED" if live else
                                         "OBSERVE-ONLY (gated-off)")})
        # M3-SAFE: si hay urgencias, se MERGEAN al working_state (NUNCA REPLACE) bajo clave propia.
        if urges:
            await c.execute(
                "INSERT INTO fable.soul(key,value) VALUES('nerves_urges',$1) "
                "ON CONFLICT(key) DO UPDATE SET value=$1, updated_at=now()",
                json.dumps({"urges": urges, "ts": now.isoformat(timespec='seconds')}, ensure_ascii=False))
    finally:
        await c.close()
        # Heartbeat de liveness: se escribe SIEMPRE que el tick corre (cruce umbral o no).
        # El supervisor lo vigila para "el nervio está vivo"; nunca stale si el timer corre.
        _write_heartbeat({
            "schema": "seal.fable_nerves_heartbeat.v1",
            "ts": now.isoformat(timespec="seconds"),
            "agent": AGENT,
            "alive": True,
            "n_tanks": len(tank_summary),
            "tanks": tank_summary,
            "n_urges": len(urges),
            "action_failures": action_failures,
        })
    if action_failures:
        raise RuntimeError("; ".join(action_failures))
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
    completed = sum(1 for urge in urges if urge.get("fired"))
    print(f"[fable_nerves] tick OK · {len(urges)} urgencia(s), {completed} acción(es) completada(s):")
    for u in urges:
        print(f"   ⚡ {u['drive']} (presión {u['pressure']}) → GANAS de: {u['urge']}")
    if not urges:
        print("   (tanques bajo umbral — sin urgencias este tick)")


if __name__ == "__main__":
    main()
