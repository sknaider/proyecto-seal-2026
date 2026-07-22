"""P1 — Test adversarial DIFERENCIAL del clamp RESTRICTIVE de identidad dura interna.

REAL, runnable en DB AISLADA. La seguridad se prueba por COMPARACIÓN por celda
(login × tabla × operación), no por un 0 aislado:

    baseline SIN clamp  ->  acceso ajeno PERMITIDO   (control positivo por operación)
    CON clamp           ->  acceso ajeno CERRADO

Si el baseline ya deniega (p.ej. el rol tiene GRANT pero NINGUNA policy PERMISSIVE que
lo haga visible), la celda es `not_applicable`: un 0 ahí NO prueba el clamp -> se salta,
no se cuenta como verde. (`has_table_privilege` NO basta: grant != visibilidad RLS.)

Cobertura: los 19 LOGIN vivos de la frontera (9 originales + 5 mcp_runtime_* + 5
svc_soul_nerves_*; estos 10 están agent-clampados por mcp_hard/nerves_hard pero NO
tenant-clampados -> el clamp de tenant SE COMPONE con el de agente). El canario lleva
la identidad de agente de cada rol para aislar el efecto TENANT.

Además test_binding_resolves_own_tenant prueba, por mecanismo, que el clamp no cierra el
acceso PROPIO (internal_role_tenant_id() resuelve el tenant interno para cada login).

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
import pytest_asyncio

HERE = pathlib.Path(__file__).parent
UP_SQL = (HERE / "001_internal_tenant_clamp_up.sql").read_text()
DOWN_SQL = (HERE / "001_internal_tenant_clamp_down.sql").read_text()

AGENT_OF = {}
for _a in ("ada", "alice", "dum", "jarvis", "nexus"):
    AGENT_OF[f"mcp_runtime_{_a}"] = _a.upper()
    AGENT_OF[f"svc_soul_nerves_{_a}"] = _a.upper()

REQUIRED_LOGINS = frozenset({
    "login_ada_bridge", "login_bus", "login_checkpoints", "login_dashboard_admin",
    "login_dashboard_ro", "login_dum_heartbeat", "login_infra_watchdog",
    "login_mcp_canary", "svc_seal_studio",
} | set(AGENT_OF))                                            # 9 + 10 = 19

TENANT_A = "00000000-0000-0000-0000-000000000000"
TENANT_B = "11111111-1111-1111-1111-111111111111"
MARKER = f"P1TEST_{uuid.uuid4().hex[:12]}"
CANARY = f"P1CANARY_{uuid.uuid4().hex}"
CHASH = hashlib.sha256(CANARY.encode()).hexdigest()
_archive_ids = itertools.count(9_000_000_000)
CANARY_AGENTS = [MARKER, "ADA", "ALICE", "DUM", "JARVIS", "NEXUS"]

TABLES = {
    "memories": {"agent_col": "agent",
                 "ins": lambda tid, ag: ("INSERT INTO soul_v3.memories (tenant_id,agent,category,content,content_hash_sha256) VALUES ($1,$2,'p1test',$3,$4)", (tid, ag, CANARY, CHASH))},
    "session_memory": {"agent_col": "agent",
                       "ins": lambda tid, ag: ("INSERT INTO soul_v3.session_memory (tenant_id,agent,session_id) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "inner_monologue": {"agent_col": "agent",
                        "ins": lambda tid, ag: ("INSERT INTO soul_v3.inner_monologue (tenant_id,agent,thought) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "distilled_exchanges": {"agent_col": "agent",
                            "ins": lambda tid, ag: ("INSERT INTO soul_v3.distilled_exchanges (tenant_id,agent,summary) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "memory_retrieval_log": {"agent_col": "agent_requesting",
                             "ins": lambda tid, ag: ("INSERT INTO soul_v3.memory_retrieval_log (tenant_id,agent_requesting,query_text) VALUES ($1,$2,$3)", (tid, ag, CANARY))},
    "memories_archive": {"agent_col": "agent",
                         "ins": lambda tid, ag: ("INSERT INTO soul_v3.memories_archive (tenant_id,id,agent,content) VALUES ($1,$2,$3,$4)", (tid, next(_archive_ids), ag, CANARY))},
}

ADMIN_DSN = os.environ.get("P1_ADMIN_DSN")
LOGIN_DSNS = json.loads(os.environ.get("P1_LOGIN_DSNS", "{}"))

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN or not LOGIN_DSNS,
    reason="requiere P1_ADMIN_DSN y P1_LOGIN_DSNS apuntando a una DB de PRUEBA aislada",
)


def agent_of(login):
    return AGENT_OF.get(login, MARKER)


async def _priv(a, login, table, op):
    return await a.fetchval("SELECT has_table_privilege($1,$2,$3)", login, f"soul_v3.{table}", op)


async def _as(login, tenant):
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    await conn.execute("SELECT set_config('app.tenant_id',$1,false)", tenant)
    await conn.execute("SELECT set_config('app.agent',$1,false)", agent_of(login))
    return conn


async def _foreign_read_count(login, table):
    ac = TABLES[table]["agent_col"]
    conn = await _as(login, TENANT_B)
    try:
        return await conn.fetchval(
            f"SELECT count(*) FROM soul_v3.{table} WHERE tenant_id=$1 AND {ac}=$2",
            TENANT_B, agent_of(login))
    finally:
        await conn.close()


async def _foreign_insert_ok(login, table):
    """¿Una inserción a tenant ajeno SUCEDE? Se hace en tx y se revierte (no persiste)."""
    sql, args = TABLES[table]["ins"](TENANT_B, agent_of(login))
    conn = await _as(login, TENANT_B)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(sql, *args)
            await tr.rollback()
            return True
        except asyncpg.PostgresError:
            await tr.rollback()
            return False
    finally:
        await conn.close()


async def _foreign_update_rows(login, table):
    """Filas ajenas afectadas por un UPDATE (en tx revertida)."""
    ac = TABLES[table]["agent_col"]
    conn = await _as(login, TENANT_B)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            status = await conn.execute(
                f"UPDATE soul_v3.{table} SET {ac}={ac} WHERE tenant_id=$1 AND {ac}=$2",
                TENANT_B, agent_of(login))
            return int(status.split()[-1])
        finally:
            await tr.rollback()
    finally:
        await conn.close()


async def _provision(a):
    await a.execute("INSERT INTO soul_v3.tenants (id,name) VALUES ($1,$2) ON CONFLICT (id) DO NOTHING", TENANT_B, "p1-test-foreign")
    await a.execute("INSERT INTO soul_v3.agents (name,role) VALUES ($1,$2) ON CONFLICT (name) DO NOTHING", MARKER, "p1test")


async def _deprovision(a):
    await a.execute("DELETE FROM soul_v3.agents WHERE name=$1", MARKER)
    await a.execute("DELETE FROM soul_v3.tenants WHERE id=$1", TENANT_B)


async def _seed(a):
    for agent in CANARY_AGENTS:
        for cfg in TABLES.values():
            sql, args = cfg["ins"](TENANT_B, agent)
            await a.execute(sql, *args)


async def _clean(a):
    for tname in TABLES:
        await a.execute(f"DELETE FROM soul_v3.{tname} WHERE tenant_id=$1", TENANT_B)


def test_required_logins_present():
    """Una lista PARCIAL de los 19 logins no es verde: falta cobertura => FALLA."""
    missing = REQUIRED_LOGINS - set(LOGIN_DSNS)
    assert not missing, f"faltan logins obligatorios (frontera de 19) en P1_LOGIN_DSNS: {sorted(missing)}"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def ctx():
    """Fase 0 (SIN clamp): provisiona, siembra tenant B, mide baseline por celda.
       Fase 1: aplica el clamp. Teardown revierte todo."""
    a = await asyncpg.connect(ADMIN_DSN)
    try:
        await _provision(a)
        await a.execute(DOWN_SQL)                 # garantizar SIN clamp
        await _seed(a)
        base = {"read": {}, "write": {}, "update": {}}
        for login in sorted(REQUIRED_LOGINS):
            if login not in LOGIN_DSNS:
                continue
            for table in sorted(TABLES):
                if await _priv(a, login, table, "SELECT"):
                    base["read"][(login, table)] = await _foreign_read_count(login, table)
                if await _priv(a, login, table, "INSERT"):
                    base["write"][(login, table)] = await _foreign_insert_ok(login, table)
                if await _priv(a, login, table, "UPDATE"):
                    base["update"][(login, table)] = await _foreign_update_rows(login, table)
        await a.execute(UP_SQL)                    # aplicar clamp
        yield {"admin": a, "base": base}
    finally:
        await _clean(a)
        await a.execute(DOWN_SQL)
        await _deprovision(a)
        await a.close()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
async def test_binding_resolves_own_tenant(ctx, login):
    """El clamp NO cierra el acceso propio: internal_role_tenant_id() resuelve el tenant
    interno (A) para cada login (mecanismo; no asume permissives)."""
    conn = await asyncpg.connect(LOGIN_DSNS[login])
    try:
        tid = await conn.fetchval("SELECT soul_v3.internal_role_tenant_id()")
        assert str(tid) == TENANT_A, (
            f"{login} resuelve tenant={tid}, no el interno {TENANT_A} -> el clamp cerraría su acceso propio")
    finally:
        await conn.close()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_read_blocked(ctx, login, table):
    """Diferencial: baseline sin clamp PERMITE leer ajeno (>0) y con clamp lo CIERRA (0)."""
    base = ctx["base"]["read"].get((login, table))
    if base is None:
        pytest.skip(f"{login} sin SELECT en {table}")
    if base == 0:
        pytest.skip(f"not_applicable: sin clamp {login} ya lee 0 en {table} (sin permissive) — el 0 no prueba el clamp")
    n = await _foreign_read_count(login, table)
    assert n == 0, f"FUGA: {login} leyó {n} filas ajenas en {table} pese al clamp (baseline sin clamp={base})"


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_write_blocked(ctx, login, table):
    """Diferencial: baseline sin clamp la escritura ajena SUCEDE y con clamp FALLA (42501)."""
    base = ctx["base"]["write"].get((login, table))
    if base is None:
        pytest.skip(f"{login} sin INSERT en {table}")
    if not base:
        pytest.skip(f"not_applicable: sin clamp la escritura ajena de {login} en {table} ya falla — no prueba el clamp")
    sql, args = TABLES[table]["ins"](TENANT_B, agent_of(login))
    conn = await _as(login, TENANT_B)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):  # 42501 = WITH CHECK
                await conn.execute(sql, *args)
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("login", sorted(REQUIRED_LOGINS))
@pytest.mark.parametrize("table", sorted(TABLES))
async def test_foreign_update_blocked(ctx, login, table):
    """Diferencial: baseline sin clamp el UPDATE ajeno afecta >0 y con clamp 0 (USING lo oculta)."""
    base = ctx["base"]["update"].get((login, table))
    if base is None:
        pytest.skip(f"{login} sin UPDATE en {table}")
    if base == 0:
        pytest.skip(f"not_applicable: sin clamp el UPDATE ajeno de {login} en {table} ya afecta 0 — no prueba el clamp")
    affected = await _foreign_update_rows(login, table)
    assert affected == 0, f"FUGA (UPDATE): {login} modificó {affected} filas ajenas en {table} pese al clamp (baseline={base})"
