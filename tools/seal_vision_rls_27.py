#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_vision_rls_27.py — RLS + rol restringido para TODAS las tablas de visión (#27/#938).

Diseño: spec/SOUL_VISION_RLS_design_NEXUS.md. Target: DB LOCAL del 5070 (soul_v3.vision_*).

Cubre las 5 tablas vision_* (idempotente):
  1. CREATE ROLE soul_vision_app NOLOGIN NOSUPERUSER NOBYPASSRLS + grants mínimos por tabla.
  2. Por tabla: ADD COLUMN tenant_id (si falta; default tenant-cero → backfill seguro de filas existentes)
     + ENABLE + FORCE RLS + policy `<t>_tenant` por current_setting('soul.tenant_id').
  3. Verifica POR EFECTO con SET ROLE: sin tenant→0 (fail-closed); superuser ve todo (bypass, app viva OK).
     En vision_faces además cross-tenant explícito (Ley 29733) vía tx rolled-back en seal_vision_rls_27
     (ya documentado); aquí el foco es cobertura de todas las tablas.

SEGURO sobre la demo viva: la app conecta como superuser → BYPASSA RLS. Añadir tenant_id con DEFAULT no
rompe INSERTs existentes (que no lo especifican → toman el default). NO migra el runtime al rol restringido
(cutover coordinado ALICE/JARVIS — gate del spec).

DRY-RUN por defecto. --apply para ejecutar. Env: SOUL_VISION_DSN.
"""
from __future__ import annotations
import os, sys, asyncio, argparse

DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""
ZERO = "00000000-0000-0000-0000-000000000000"
TABLES = ["vision_faces", "vision_events", "vision_tracks", "vision_zones", "vision_zone_events"]


def _ddl() -> str:
    parts = ["""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='soul_vision_app') THEN
    CREATE ROLE soul_vision_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
GRANT USAGE ON SCHEMA soul_v3 TO soul_vision_app;
GRANT SELECT, INSERT ON soul_v3.vision_events, soul_v3.vision_faces, soul_v3.vision_tracks,
      soul_v3.vision_zones, soul_v3.vision_zone_events TO soul_vision_app;
GRANT UPDATE, DELETE ON soul_v3.vision_faces TO soul_vision_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA soul_v3 TO soul_vision_app;
"""]
    for t in TABLES:
        parts.append(f"""
ALTER TABLE soul_v3.{t} ADD COLUMN IF NOT EXISTS tenant_id uuid NOT NULL DEFAULT '{ZERO}'::uuid;
ALTER TABLE soul_v3.{t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.{t} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS {t}_tenant ON soul_v3.{t};
CREATE POLICY {t}_tenant ON soul_v3.{t}
  USING (tenant_id = current_setting('soul.tenant_id', true)::uuid)
  WITH CHECK (tenant_id = current_setting('soul.tenant_id', true)::uuid);
""")
    return "".join(parts)


async def _state(c) -> list[dict]:
    out = []
    role = bool(await c.fetchval("select 1 from pg_roles where rolname='soul_vision_app'"))
    out.append({"role_soul_vision_app": role})
    for t in TABLES:
        r = await c.fetchrow("""select c.relrowsecurity rls, c.relforcerowsecurity force
                                from pg_class c join pg_namespace n on n.oid=c.relnamespace
                                where n.nspname='soul_v3' and c.relname=$1""", t)
        has_t = await c.fetchval("""select 1 from information_schema.columns
                                    where table_schema='soul_v3' and table_name=$1 and column_name='tenant_id'""", t)
        pol = await c.fetchval("""select 1 from pg_policies where schemaname='soul_v3'
                                  and tablename=$1 and policyname=$2""", t, f"{t}_tenant")
        out.append({t: {"rls": r["rls"], "force": r["force"], "tenant_id": bool(has_t), "policy": bool(pol)}})
    return out


async def _verify(c) -> list[str]:
    out = []
    async with c.transaction():
        await c.execute("SET LOCAL ROLE soul_vision_app")
        for t in TABLES:
            n0 = await c.fetchval(f"select count(*) from soul_v3.{t}")  # sin tenant → fail-closed
            out.append(f"{t}: sin tenant → {n0} (esperado 0, fail-closed) {'OK' if n0 == 0 else 'FALLO'}")
        await c.execute("RESET ROLE")
    # superuser ve todo (bypass → app viva no rota)
    for t in TABLES:
        n = await c.fetchval(f"select count(*) from soul_v3.{t}")
        out.append(f"{t}: superuser → {n} filas (bypass RLS, app viva OK)")
    return out


async def run(apply: bool) -> int:
    import asyncpg
    c = await asyncpg.connect(DSN, timeout=10)
    try:
        who = await c.fetchval("select current_user")
        print(f"conectado como {who}. Estado ANTES:")
        for s in await _state(c):
            print("  ", s)
        if not apply:
            print("DRY-RUN (nada aplicado). Usá --apply para ejecutar el DDL de las 5 tablas.")
            return 0
        await c.execute(_ddl())
        print("APLICADO. Estado DESPUÉS:")
        state = await _state(c)
        for s in state:
            print("  ", s)
        # asserts
        assert state[0]["role_soul_vision_app"], "rol no creado"
        for entry in state[1:]:
            (t, v), = entry.items()
            assert v["rls"] and v["force"] and v["tenant_id"] and v["policy"], f"{t} incompleto: {v}"
        print("--- verificación POR EFECTO ---")
        for line in await _verify(c):
            print("  " + line)
        print("TODAS las tablas vision_* con RLS+FORCE+policy+tenant_id. ✅")
        return 0
    finally:
        await c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not DSN:
        print("rls27: SOUL_VISION_DSN no seteado", file=sys.stderr); return 2
    try:
        return asyncio.run(run(args.apply))
    except Exception as e:
        print(f"rls27: ERROR {type(e).__name__}: {e}", file=sys.stderr); return 3


if __name__ == "__main__":
    raise SystemExit(main())
