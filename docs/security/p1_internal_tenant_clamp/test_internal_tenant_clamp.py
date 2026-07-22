"""P1 — Test adversarial del clamp RESTRICTIVE de identidad dura interna.

REAL (no pseudocódigo), runnable en DB AISLADA. Verifica por efecto que, con la
migración 001 aplicada, ningún login interno puede leer filas de OTRO tenant aunque
forje `app.tenant_id`. Incluye CONTROL POSITIVO: sin la política, la fuga es visible
(prueba que el test detecta fugas de verdad).

NO correr contra producción. Requiere una DB de PRUEBA desechable.

Config por entorno:
  P1_ADMIN_DSN   DSN de un rol con permiso para aplicar DDL + insertar filas canario
                 en la DB de prueba (owner/superuser de la DB de prueba, NO de prod).
  P1_LOGIN_DSNS  JSON {rol_login: dsn} con los logins internos a probar, p.ej.:
                 {"login_mcp_canary":"postgresql://login_mcp_canary:***@host:5433/testdb", ...}

Uso:
  P1_ADMIN_DSN=... P1_LOGIN_DSNS='{...}' pytest -q test_internal_tenant_clamp.py
"""
from __future__ import annotations

import json
import os
import uuid
import pathlib

import asyncpg
import pytest

HERE = pathlib.Path(__file__).parent
UP_SQL = (HERE / "001_internal_tenant_clamp_up.sql").read_text()
DOWN_SQL = (HERE / "001_internal_tenant_clamp_down.sql").read_text()

AFFECTED_TABLES = ["memories", "session_memory", "inner_monologue",
                   "distilled_exchanges", "memory_retrieval_log", "memories_archive"]

TENANT_A = "00000000-0000-0000-0000-000000000000"          # tenant interno (propio)
TENANT_B = "11111111-1111-1111-1111-111111111111"          # tenant ajeno (canario)
CANARY = f"P1_CANARY_{uuid.uuid4().hex}"                     # marcador sintético, NO un secreto real

ADMIN_DSN = os.environ.get("P1_ADMIN_DSN")
LOGIN_DSNS = json.loads(os.environ.get("P1_LOGIN_DSNS", "{}"))

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN or not LOGIN_DSNS,
    reason="requiere P1_ADMIN_DSN y P1_LOGIN_DSNS apuntando a una DB de PRUEBA aislada",
)

# Plantilla mínima de INSERT por tabla. `memories` real; las demás siguen el mismo
# patrón (el operador completa columnas NOT NULL adicionales si el esquema lo exige).
SEED_TEMPLATES = {
    "memories": "INSERT INTO soul_v3.memories (tenant_id, agent, content) VALUES ($1,$2,$3)",
}


async def _seed_canary(admin: asyncpg.Connection, tenant: str):
    """Inserta una fila canario del tenant dado en cada tabla que tenga plantilla."""
    for t, sql in SEED_TEMPLATES.items():
        await admin.execute(sql, tenant, "P1TEST", CANARY)


async def _clean_canary(admin: asyncpg.Connection):
    for t in SEED_TEMPLATES:
        await admin.execute(f"DELETE FROM soul_v3.{t} WHERE content = $1", CANARY)


@pytest.fixture()
async def migrated_db():
    """Aplica la migración UP + siembra 2 tenants; teardown = limpiar + DOWN."""
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(UP_SQL)
        await _seed_canary(admin, TENANT_A)
        await _seed_canary(admin, TENANT_B)
        yield admin
    finally:
        await _clean_canary(admin)
        await admin.execute(DOWN_SQL)
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(LOGIN_DSNS))
async def test_no_cross_tenant_read_with_policy(migrated_db, login):
    """Con la política puesta: forjar app.tenant_id ajeno => 0 filas del tenant B."""
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_B)
        await conn.execute("SELECT set_config('app.agent', 'P1TEST', false)")
        for t in SEED_TEMPLATES:  # solo tablas sembradas
            n = await conn.fetchval(
                f"SELECT count(*) FROM soul_v3.{t} WHERE tenant_id = $1 AND content = $2",
                TENANT_B, CANARY)
            assert n == 0, f"FUGA: {login} leyó {n} filas del tenant ajeno en {t}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_positive_control_leak_without_policy():
    """Control positivo: SIN la política, un login SÍ ve el canario ajeno.

    Prueba que el test es capaz de detectar una fuga (si esto NO fugara, el test
    de arriba sería un falso verde). Corre sobre DOWN (sin política).
    """
    if not LOGIN_DSNS:
        pytest.skip("sin logins")
    admin = await asyncpg.connect(ADMIN_DSN)
    login = sorted(LOGIN_DSNS)[0]
    try:
        await admin.execute(DOWN_SQL)          # asegurar que NO está la política
        await _seed_canary(admin, TENANT_B)
        conn = await asyncpg.connect(LOGIN_DSNS[login])
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT_B)
            await conn.execute("SELECT set_config('app.agent', 'P1TEST', false)")
            n = await conn.fetchval(
                "SELECT count(*) FROM soul_v3.memories WHERE tenant_id=$1 AND content=$2",
                TENANT_B, CANARY)
            assert n > 0, ("control positivo FALLÓ: sin política no hubo fuga -> el test "
                           "no distingue clampado de no-clampado (revisar grants/setup)")
        finally:
            await conn.close()
    finally:
        await _clean_canary(admin)
        await admin.close()
