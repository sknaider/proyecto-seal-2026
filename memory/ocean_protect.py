"""
ocean_protect.py — Protección de OCEAN baselines del equipo SEAL
Solo William puede modificar los parámetros base de personalidad.
Llave: William_Henry_Tovar_Urquia_SEAL_Director
"""
import asyncio
import asyncpg
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

WILLIAM_KEY = hashlib.sha256(b"William_Henry_Tovar_Urquia_SEAL_Director").hexdigest()


def _compute_lock_hash(agent: str, ocean: dict, key: str) -> str:
    data = json.dumps({"agent": agent, "ocean": ocean, "key": key}, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


async def verify_baseline(agent: str) -> dict:
    """Verifica que el OCEAN actual no ha sido alterado sin autorización."""
    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT ocean_scores, ocean_baseline, ocean_lock_hash FROM identity WHERE agent = $1",
        agent
    )
    await conn.close()

    if not row:
        return {"status": "error", "msg": f"Agente {agent} no encontrado"}

    baseline = row["ocean_baseline"]
    stored_hash = row["ocean_lock_hash"]
    expected_hash = _compute_lock_hash(agent, baseline, WILLIAM_KEY)

    if stored_hash != expected_hash:
        return {"status": "ALERTA", "msg": f"⚠️ Baseline de {agent} fue alterado sin autorización"}

    return {"status": "ok", "agent": agent, "baseline": baseline, "current": row["ocean_scores"]}


async def update_ocean(agent: str, new_ocean: dict, william_passphrase: str) -> dict:
    """
    Actualiza el OCEAN de un agente.
    Solo funciona con la llave de William.
    """
    provided_key = hashlib.sha256(william_passphrase.encode()).hexdigest()
    if provided_key != WILLIAM_KEY:
        return {"status": "DENEGADO", "msg": "Llave incorrecta. Solo William puede modificar OCEAN."}

    conn = await asyncpg.connect(DB_URL)

    # Verificar que el baseline actual es válido
    row = await conn.fetchrow(
        "SELECT ocean_baseline, ocean_lock_hash FROM identity WHERE agent = $1", agent
    )
    if not row:
        await conn.close()
        return {"status": "error", "msg": f"Agente {agent} no encontrado"}

    # Calcular nuevo hash con el nuevo OCEAN
    new_hash = _compute_lock_hash(agent, new_ocean, WILLIAM_KEY)

    await conn.execute(
        """UPDATE identity
           SET ocean_scores = $1,
               ocean_baseline = $1,
               ocean_lock_hash = $2,
               ocean_locked_at = $3
           WHERE agent = $4""",
        new_ocean, new_hash, datetime.now(LIMA_TZ), agent
    )
    await conn.close()

    return {
        "status": "ok",
        "msg": f"OCEAN de {agent} actualizado y baseline re-bloqueado",
        "new_ocean": new_ocean
    }


async def get_ocean_current(agent: str) -> dict:
    """
    Retorna OCEAN actual = ocean_baseline + drift acumulado autorizado.
    Lee desde ocean_current VIEW (ocean_base_values + ocean_drift_log).
    Fallback: ocean_baseline de identity si VIEW falla.
    """
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT dimension, current_value FROM ocean_current WHERE agent = $1",
            agent
        )
        if rows:
            dim_map = {"agreeableness": "A", "conscientiousness": "C",
                       "extraversion": "E", "neuroticism": "N", "openness": "O"}
            return {dim_map[r["dimension"]]: float(r["current_value"]) for r in rows}
        # fallback to baseline
        row = await conn.fetchrow("SELECT ocean_baseline FROM identity WHERE agent = $1", agent)
        return dict(row["ocean_baseline"]) if row else {}
    finally:
        await conn.close()


async def log_drift_event(agent: str, dimension: str, delta: float,
                           event_type: str, event_desc: str = "") -> dict:
    """
    Registra un evento de drift autorizado en ocean_drift_log.
    Aplica guardrails: max_drift_per_day y max_total_drift (±0.15 por dimensión).
    """
    OCEAN_MAX_DRIFT = 0.15
    OCEAN_MAX_PER_DAY = 0.003
    DIM_FULL = {"A": "agreeableness", "C": "conscientiousness",
                "E": "extraversion", "N": "neuroticism", "O": "openness"}
    dim_full = DIM_FULL.get(dimension, dimension)

    conn = await asyncpg.connect(DB_URL)
    try:
        # Check total accumulated drift
        row = await conn.fetchrow(
            "SELECT base_value, current_value FROM ocean_current WHERE agent=$1 AND dimension=$2",
            agent, dim_full
        )
        if not row:
            return {"status": "error", "msg": f"Agente/dimensión no encontrado: {agent}/{dimension}"}

        total_drift = float(row["current_value"]) - float(row["base_value"])
        if abs(total_drift + delta) > OCEAN_MAX_DRIFT:
            return {"status": "blocked", "msg": f"Drift total excedería ±{OCEAN_MAX_DRIFT} para {agent}.{dimension}"}

        # Check daily drift cap
        today = datetime.now(LIMA_TZ).date()
        daily_row = await conn.fetchrow(
            "SELECT COALESCE(SUM(ABS(delta)), 0) as daily FROM ocean_drift_log "
            "WHERE agent=$1 AND dimension=$2 AND applied=TRUE AND DATE(created_at AT TIME ZONE 'America/Lima')=$3",
            agent, dim_full, today
        )
        daily_total = float(daily_row["daily"])
        if daily_total + abs(delta) > OCEAN_MAX_PER_DAY:
            return {"status": "blocked", "msg": f"Drift diario excedería {OCEAN_MAX_PER_DAY} para {agent}.{dimension}"}

        await conn.execute(
            "INSERT INTO ocean_drift_log (agent, dimension, delta, event_type, event_desc) VALUES ($1,$2,$3,$4,$5)",
            agent, dim_full, delta, event_type, event_desc
        )
        new_value = float(row["current_value"]) + delta
        return {"status": "ok", "agent": agent, "dimension": dimension,
                "delta": delta, "new_value": round(new_value, 4)}
    finally:
        await conn.close()


async def audit_all() -> None:
    """Verifica integridad de todos los baselines."""
    conn = await asyncpg.connect(DB_URL)
    agents = await conn.fetch("SELECT agent FROM identity ORDER BY agent")
    await conn.close()

    print("=== AUDITORIA OCEAN BASELINES ===")
    for row in agents:
        result = await verify_baseline(row["agent"])
        status = result["status"]
        if status == "ok":
            baseline = result["baseline"]
            current = result["current"]
            drift = {k: round(current.get(k, 0) - baseline.get(k, 0), 3) for k in baseline}
            has_drift = any(abs(v) > 0.05 for v in drift.values())
            flag = "⚠️ DRIFT" if has_drift else "✅"
            print(f"{flag} {row['agent']}: baseline intacto | drift={drift}")
        else:
            print(f"🚨 {row['agent']}: {result['msg']}")


if __name__ == "__main__":
    asyncio.run(audit_all())
