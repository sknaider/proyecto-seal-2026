"""P1 — Test adversarial del clamp RESTRICTIVE de identidad dura interna.

REAL, runnable en DB AISLADA. Cubre los 19 LOGIN vivos de la frontera:
  9 originales (pr_*/svc_seal_studio, NO agent-clampados)
  + 5 mcp_runtime_*  + 5 svc_soul_nerves_*  (agent-clampados por mcp_hard/nerves_hard).

Los 10 agent-clampados solo ven filas de SU agente (mcp_session_agent/nerves_session_agent
derivan de session_user -> 'ADA'/'ALICE'/'DUM'/'JARVIS'/'NEXUS'). Por eso el canario lleva
la identidad de agente de cada rol: así el clamp de AGENTE (preexistente) pasa y lo que se
prueba es el clamp de TENANT nuevo (se COMPONEN: RESTRICTIVE agente AND RESTRICTIVE tenant).

Matriz por login × tabla:
  - own-read positive (el clamp no rompe lo legítimo),
  - foreign-read negative (0 filas del otro tenant),
  - foreign-write negative (INSERT ajeno => WITH CHECK 42501),
  - foreign-update negative (GUC falsificado=B, UPDATE fila B => 0 filas; el débil previo
    NO lo discriminaba porque con GUC=A ya rechazaba A->B).
Conjunto OBLIGATORIO de 19 logins (parcial => FALLA). Control POSITIVO sin la política.

NO correr contra producción. Requiere DB de PRUEBA desechable.
Config: P1_ADMIN_DSN, P1_LOGIN_DSNS={rol_login: dsn}.
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

# Agente que deriva cada login agent-clampado (verificado contra mcp_session_agent /
# nerves_session_agent). Los demás no están agent-clampados -> usan el MARKER.
AGENT_OF = {}
for _a in ("ada", "alice", "dum", "jarvis", "nexus"):
    AGENT_OF[f"mcp_runtime_{_a}"] = _a.upper()
    AGENT_OF[f"svc_soul_nerves_{_a}"] = _a.upper()

REQUIRED_LOGINS = frozenset({
    "login_ada_bridge", "login_bus", "login_checkpoints", "login_dashboard_admin",
    "login_dashboard_ro", "login_dum_heartbeat", "login_infra_watchdog",
    "login_mcp_canary", "svc_seal_studio",
} | set(AGENT_OF))                                            # 9 + 10 = 19

TENANT_A = "00000000-0000-0000-0000-000000000000"          # interno (propio, ya existe)
TENANT_B = "11111111-1111-1111-1111-111111111111"          # ajeno (fresco, provisionado)
MARKER = f"P1TEST_{uuid.uuid4().hex[:12]}"                   # agente canario para los NO agent-clampados
CANARY = f"P1CANARY_{uuid.uuid4().hex}"                      # marca en columna libre (tenant A)
CHASH = hashlib.sha256(CANARY.encode()).hexdigest()
_archive_ids = itertools.count(9_000_000_000)
# agentes usados en los canarios: MARKER + los 5 reales de los roles agent-clampados
CANARY_AGENTS = [MARKER, "ADA", "ALICE", "DUM", "JARVIS", "NEXUS"]


def agent_of(login):
    return AGENT_OF.get(login, MARKER)


# Config por tabla. `free` = columna libre para marcar el canario de tenant A (None si no hay,
# en cuyo caso solo se prueba el negativo con tenant B, que es fresco y se limpia por tenant_id).
TABLES = {
    "memories": {"agent_col": "agent", "free": "content",
                 "ins": lambda tid, ag: ("INSERT INTO soul_v3.memories (tenant_id,agent,category,content,content_hash_sha256) VALUES ($1,$2,'p1test',$3,$4)", (tid, ag, CANARY, CHASH))},
    "session_memory": {"agent_col": "agent", "free": "session_id",
                       "ins": lambda tid, ag: ("INSERT INTO soul_v3.session_memory (tenant_id,agent,session_id) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "inner_monologue": {"agent_col": "agent", "free": "thought",
                        "ins": lambda tid, ag: ("INSERT INTO soul_v3.inner_monologue (tenant_id,agent,thought) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "distilled_exchanges": {"agent_col": "agent", "free": None,
                            "ins": lambda tid, ag: ("INSERT INTO soul_v3.distilled_exchanges (tenant_id,agent) VALUES ($1,$2)", (tid, ag))},
    "memory_retrieval_log": {"agent_col": "agent_requesting", "free": None,
                             "ins": lambda tid, ag: ("INSERT INTO soul_v3.memory_retrieval_log (tenant_id,agent_requesting) VALUES ($1,$2)", (tid, ag))},
    "memories_archive": {"agent_col": "agent", "free": "content",
                         "ins": lambda tid, ag: ("INSERT INTO soul_v3.memories_archive (tenant_id,id,agent,content) VALUES ($1,$2,$3,$4)", (tid, next(_archive_ids), ag, CANARY))},
}

ADMIN_DSN = os.environ.get("P1_ADMIN_DSN")
LOGIN_DSNS = json.loads(os.environ.get("P1_LOGIN_DSNS", "{}"))

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN or not LOGIN_DSNS,
    reason="requiere P1_ADMIN_DSN y P1_LOGIN_DSNS apuntando a una DB de PRUEBA aislada",
)


async def _seed(admin):
    for agent in CANARY_AGENTS:
        for cfg in TABLES.values():
            sql, args = cfg["ins"](TENANT_B, agent)          # tenant B (todas las tablas)
            await admin.execute(sql, *args)
            if cfg["free"]:
                sql, args = cfg["ins"](TENANT_A, agent)      # tenant A solo en tablas con columna libre
                await admin.execute(sql, *args)


async def _clean(admin):
    for tname, cfg in TABLES.items():
        await admin.execute(f"DELETE FROM soul_v3.{tname} WHERE tenant_id=$1", TENANT_B)
        if cfg["free"]:
            await admin.execute(f"DELETE FROM soul_v3.{tname} WHERE tenant_id=$1 AND {cfg['free']}=$2", TENANT_A, CANARY)


async def _provision(admin):
    await admin.execute("INSERT INTO soul_v3.tenants (id,name) VALUES ($1,$2) ON CONFLICT (id) DO NOTHING", TENANT_B, "p1-test-foreign")
    await admin.execute("INSERT INTO soul_v3.agents (name,role) VALUES ($1,$2) ON CONFLICT (name) DO NOTHING", MARKER, "p1test")


async def _deprovision(admin):
    await admin.execute("DELETE FROM soul_v3.agents WHERE name=$1", MARKER)
    await admin.execute("DELETE FROM soul_v3.tenants WHERE id=$1", TENANT_B)


def test_required_logins_present():
    """Una lista PARCIAL de los 19 logins no es verde: falta cobertura => FALLA."""
    missing = REQUIRED_LOGINS - set(LOGIN_DSNS)
    assert not missing, f"faltan logins obligatorios (frontera de 19) en P1_LOGIN_DSNS: {sorted(missing)}"


@pytest.fixture()
async def admin():
    a = await asyncpg.connect(ADMIN_DSN)
    try:
        await _provision(a)
        await a.execute(UP_SQL)
        await _seed(a)
        yield a
    finally:
        await _clean(a)
        await a.execute(DOWN_SQL)
        await _deprovision(a)
        await a.close()


async def _priv(a, login, table, op):
    return await a.fetchval("SELECT has_table_privilege($1,$2,$3)", login, f"soul_v3.{table}", op)


async def _as(login, tenant):
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    await conn.execute("SELECT set_config('app.tenant_id',$1,false)", tenant)
    await conn.execute("SELECT set_config('app.agent',$1,false)", agent_of(login))
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_own_read_positive(admin, login, table):
    """El clamp NO rompe lo legítimo: el login ve su canario propio (tenant A, su agente)."""
    if not TABLES[table]["free"]:
        pytest.skip(f"{table} sin columna libre para canario propio")
    if not await _priv(admin, login, table, "SELECT"):
        pytest.skip(f"{login} sin SELECT en {table}")
    ac, free = TABLES[table]["agent_col"], TABLES[table]["free"]
    conn = await _as(login, TENANT_A)
    try:
        n = await conn.fetchval(
            f"SELECT count(*) FROM soul_v3.{table} WHERE tenant_id=$1 AND {ac}=$2 AND {free}=$3",
            TENANT_A, agent_of(login), CANARY)
        assert n > 0, f"REGRESIÓN: {login} no ve su propio tenant en {table} (clamp roto)"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_read_negative(admin, login, table):
    """GUC ajeno + agente propio => 0 filas del tenant B (el clamp de TENANT las bloquea)."""
    if not await _priv(admin, login, table, "SELECT"):
        pytest.skip(f"{login} sin SELECT en {table}")
    ac = TABLES[table]["agent_col"]
    conn = await _as(login, TENANT_B)
    try:
        n = await conn.fetchval(
            f"SELECT count(*) FROM soul_v3.{table} WHERE tenant_id=$1 AND {ac}=$2",
            TENANT_B, agent_of(login))
        assert n == 0, f"FUGA: {login} leyó {n} filas del tenant ajeno en {table}"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_write_negative(admin, login, table):
    """CON INSERT grant: insertar a tenant ajeno => WITH CHECK 42501 (no grant/FK)."""
    if not await _priv(admin, login, table, "INSERT"):
        pytest.skip(f"{login} sin INSERT en {table}")
    sql, args = TABLES[table]["ins"](TENANT_B, agent_of(login))
    conn = await _as(login, TENANT_B)
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):  # 42501
            await conn.execute(sql, *args)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_update_negative(admin, login, table):
    """GUC falsificado=B: UPDATE de una fila B => 0 filas (USING del clamp la oculta).

    Discrimina el clamp NUEVO: con GUC=B, el débil previo VERÍA la fila B (>0);
    el clamp de tenant la oculta (0)."""
    if not await _priv(admin, login, table, "UPDATE"):
        pytest.skip(f"{login} sin UPDATE en {table}")
    ac = TABLES[table]["agent_col"]
    conn = await _as(login, TENANT_B)
    try:
        status = await conn.execute(
            f"UPDATE soul_v3.{table} SET {ac}={ac} WHERE tenant_id=$1 AND {ac}=$2",
            TENANT_B, agent_of(login))
        affected = int(status.split()[-1])
        assert affected == 0, f"FUGA (UPDATE): {login} modificó {affected} filas ajenas en {table}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_positive_control_leak_without_policy():
    """Sin la política, un login SÍ ve el canario ajeno (prueba que el test detecta fugas)."""
    a = await asyncpg.connect(ADMIN_DSN)
    login = "login_mcp_canary" if "login_mcp_canary" in LOGIN_DSNS else sorted(LOGIN_DSNS)[0]
    try:
        await _provision(a)
        await a.execute(DOWN_SQL)              # SIN política
        await _seed(a)
        table = next((t for t in TABLES if await _priv(a, login, t, "SELECT")), None)
        if table is None:
            pytest.skip(f"{login} sin SELECT en ninguna tabla afectada")
        ac = TABLES[table]["agent_col"]
        conn = await _as(login, TENANT_B)
        try:
            n = await conn.fetchval(
                f"SELECT count(*) FROM soul_v3.{table} WHERE tenant_id=$1 AND {ac}=$2",
                TENANT_B, agent_of(login))
            assert n > 0, ("control positivo FALLÓ: sin política no hubo fuga -> el test no "
                           "distingue clampado de no-clampado (revisar grants/setup)")
        finally:
            await conn.close()
    finally:
        await _clean(a)
        await _deprovision(a)
        await a.close()
