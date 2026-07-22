"""P1 — Test adversarial del clamp RESTRICTIVE de identidad dura interna.

REAL (no pseudocódigo), runnable en DB AISLADA. Cubre la matriz COMPLETA:
  - 6 tablas afectadas,
  - acceso PROPIO positivo (el clamp no rompe lo legítimo),
  - lectura AJENA negativa (0 filas del otro tenant),
  - escritura AJENA negativa (INSERT a otro tenant falla),
  - conjunto OBLIGATORIO de logins (una lista parcial FALLA, no es verde),
  - control POSITIVO (sin política, la fuga es visible).

Aplicabilidad por celda vía has_table_privilege(login, tabla, op) → no da falso rojo
donde un login legítimamente no tiene grant.

NO correr contra producción. Requiere DB de PRUEBA desechable.

Config por entorno:
  P1_ADMIN_DSN   DSN con permiso de DDL + INSERT canario en la DB de prueba.
  P1_LOGIN_DSNS  JSON {rol_login: dsn} — DEBE cubrir REQUIRED_LOGINS.

Uso:
  P1_ADMIN_DSN=... P1_LOGIN_DSNS='{...}' pytest -q test_internal_tenant_clamp.py
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import pathlib
import uuid

import asyncpg
import pytest

HERE = pathlib.Path(__file__).parent
UP_SQL = (HERE / "001_internal_tenant_clamp_up.sql").read_text()
DOWN_SQL = (HERE / "001_internal_tenant_clamp_down.sql").read_text()

# Conjunto OBLIGATORIO: los logins que alcanzan las tablas (login_* que auto-heredan
# los pr_* + svc_seal_studio LOGIN directo). Una lista parcial => FALLA (no verde).
REQUIRED_LOGINS = frozenset({
    "login_ada_bridge", "login_bus", "login_checkpoints", "login_dashboard_admin",
    "login_dashboard_ro", "login_dum_heartbeat", "login_infra_watchdog",
    "login_mcp_canary", "svc_seal_studio",
})

TENANT_A = "00000000-0000-0000-0000-000000000000"          # tenant interno (propio)
TENANT_B = "11111111-1111-1111-1111-111111111111"          # tenant ajeno (canario)
MARKER = f"P1TEST_{uuid.uuid4().hex[:12]}"                   # marcador sintético (NO secreto)
CANARY = f"P1CANARY_{uuid.uuid4().hex}"
CHASH = hashlib.sha256(CANARY.encode()).hexdigest()
_archive_ids = itertools.count(9_000_000_000)

# Config por tabla: cómo sembrar/identificar/escribir el canario. `marker_col`
# identifica las filas de prueba para contar y limpiar.
TABLES = {
    "memories": {
        "marker_col": "agent",
        "insert": "INSERT INTO soul_v3.memories (tenant_id,agent,category,content,content_hash_sha256) VALUES ($1,$2,'p1test',$3,$4)",
        "args": lambda tid: (tid, MARKER, CANARY, CHASH),
    },
    "session_memory": {
        "marker_col": "agent",
        "insert": "INSERT INTO soul_v3.session_memory (tenant_id,agent,session_id) VALUES ($1,$2,$3)",
        "args": lambda tid: (tid, MARKER, CANARY),
    },
    "inner_monologue": {
        "marker_col": "agent",
        "insert": "INSERT INTO soul_v3.inner_monologue (tenant_id,agent,thought) VALUES ($1,$2,$3)",
        "args": lambda tid: (tid, MARKER, CANARY),
    },
    "distilled_exchanges": {
        "marker_col": "agent",
        "insert": "INSERT INTO soul_v3.distilled_exchanges (tenant_id,agent) VALUES ($1,$2)",
        "args": lambda tid: (tid, MARKER),
    },
    "memory_retrieval_log": {
        "marker_col": "agent_requesting",
        "insert": "INSERT INTO soul_v3.memory_retrieval_log (tenant_id,agent_requesting) VALUES ($1,$2)",
        "args": lambda tid: (tid, MARKER),
    },
    "memories_archive": {
        "marker_col": "agent",
        "insert": "INSERT INTO soul_v3.memories_archive (tenant_id,id,agent,content) VALUES ($1,$2,$3,$4)",
        "args": lambda tid: (tid, next(_archive_ids), MARKER, CANARY),
    },
}

ADMIN_DSN = os.environ.get("P1_ADMIN_DSN")
LOGIN_DSNS = json.loads(os.environ.get("P1_LOGIN_DSNS", "{}"))

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN or not LOGIN_DSNS,
    reason="requiere P1_ADMIN_DSN y P1_LOGIN_DSNS apuntando a una DB de PRUEBA aislada",
)


def _count_sql(table):
    return f"SELECT count(*) FROM soul_v3.{table} WHERE tenant_id=$1 AND {TABLES[table]['marker_col']}=$2"


def _clean_sql(table):
    return f"DELETE FROM soul_v3.{table} WHERE {TABLES[table]['marker_col']}=$1"


async def _seed(admin, tenant):
    for t, cfg in TABLES.items():
        await admin.execute(cfg["insert"], *cfg["args"](tenant))


async def _clean(admin):
    for t in TABLES:
        await admin.execute(_clean_sql(t), MARKER)


def test_required_logins_present():
    """Una lista PARCIAL de logins no es verde: falta cobertura => FALLA."""
    missing = REQUIRED_LOGINS - set(LOGIN_DSNS)
    assert not missing, f"faltan logins obligatorios en P1_LOGIN_DSNS: {sorted(missing)}"


@pytest.fixture()
async def migrated_admin():
    """Aplica UP + siembra canario de tenant A (propio) y B (ajeno). Teardown = limpiar + DOWN."""
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(UP_SQL)
        await _seed(admin, TENANT_A)
        await _seed(admin, TENANT_B)
        yield admin
    finally:
        await _clean(admin)
        await admin.execute(DOWN_SQL)
        await admin.close()


async def _priv(admin, login, table, op):
    return await admin.fetchval("SELECT has_table_privilege($1, $2, $3)",
                                login, f"soul_v3.{table}", op)


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_own_read_positive(migrated_admin, login, table):
    """El clamp NO rompe lo legítimo: el login VE su propio tenant (A)."""
    if not await _priv(migrated_admin, login, table, "SELECT"):
        pytest.skip(f"{login} sin SELECT en {table} (no aplica)")
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_A)
        await conn.execute("SELECT set_config('app.agent', $1, false)", MARKER)
        n = await conn.fetchval(_count_sql(table), TENANT_A, MARKER)
        assert n > 0, f"REGRESIÓN: {login} no ve su propio tenant en {table} (clamp roto)"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_read_negative(migrated_admin, login, table):
    """Forjar app.tenant_id ajeno => 0 filas del tenant B."""
    if not await _priv(migrated_admin, login, table, "SELECT"):
        pytest.skip(f"{login} sin SELECT en {table} (no aplica)")
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_B)
        await conn.execute("SELECT set_config('app.agent', $1, false)", MARKER)
        n = await conn.fetchval(_count_sql(table), TENANT_B, MARKER)
        assert n == 0, f"FUGA: {login} leyó {n} filas del tenant ajeno en {table}"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_write_negative(migrated_admin, login, table):
    """Escribir a un tenant ajeno DEBE fallar (WITH CHECK del clamp, o falta de grant)."""
    cfg = TABLES[table]
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_B)
        await conn.execute("SELECT set_config('app.agent', $1, false)", MARKER)
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(cfg["insert"], *cfg["args"](TENANT_B))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_positive_control_leak_without_policy():
    """Control positivo: SIN la política, un login SÍ ve el canario ajeno.

    Prueba que el test detecta fugas de verdad (si esto NO fugara, los negativos
    serían falsos verdes). Corre sobre DOWN (sin política).
    """
    admin = await asyncpg.connect(ADMIN_DSN)
    login = "login_mcp_canary" if "login_mcp_canary" in LOGIN_DSNS else sorted(LOGIN_DSNS)[0]
    try:
        await admin.execute(DOWN_SQL)          # asegurar SIN política
        await _seed(admin, TENANT_B)
        # elegir una tabla donde el login tenga SELECT
        table = None
        for t in TABLES:
            if await _priv(admin, login, t, "SELECT"):
                table = t
                break
        if table is None:
            pytest.skip(f"{login} sin SELECT en ninguna tabla afectada")
        conn = await asyncpg.connect(LOGIN_DSNS[login])
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_B)
            await conn.execute("SELECT set_config('app.agent', $1, false)", MARKER)
            n = await conn.fetchval(_count_sql(table), TENANT_B, MARKER)
            assert n > 0, ("control positivo FALLÓ: sin política no hubo fuga -> el test no "
                           "distingue clampado de no-clampado (revisar grants/setup)")
        finally:
            await conn.close()
    finally:
        await _clean(admin)
        await admin.close()
