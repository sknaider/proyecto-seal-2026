#!/usr/bin/env python3
"""Seed soul_v3.capability_grants para los agentes SEAL — fix del outage de memoria
(NEXUS, 26-ago-2026). William: "fix Correcta maxima capacidad con test".

CONTEXTO: el tool-broker del MCP corre en `enforce` pero capability_scope/grants
estaban VACÍAS → todo write/comm de agente denegado ("no capability entry"). Este
script llena los grants con la capacidad operativa por agente.

SEGURIDAD (verificado en policy_decision, tool_broker.py:236): DESTRUCTIVE se gatea
ANTES que el scope → un grant NO bypasea la confirmación destructiva. Y "external"
(sin grant) sigue DENY_BY_DEFAULT → el control negativo se preserva.

TRANSACCIONAL + AUTO-TEST: siembra dentro de una transacción, prueba por-efecto con
tool_broker.check (positivo: agente→allow memory_store; negativo: external→deny;
seguridad: agente→destructive NO se auto-permite), y COMMITEA sólo si los 3 pasan;
si algo falla, ROLLBACK (no deja el core a medias). Idempotente (ON CONFLICT-like).

Uso:  SEAL_DB_URL=<dsn-admin> python3 tools/seal_seed_agent_capabilities.py [--commit]
Sin --commit corre en DRY-RUN (siembra, testea, ROLLBACK siempre) para revisión.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

AGENTS = ("ADA", "ALICE", "JARVIS", "NEXUS", "DUM")  # SPECTRE excluido (cuarentena)
SERVER = "seal-memory"
GRANTED_BY = "NEXUS-capability-fix-20260826"
NOTES = "Seed operativo tras outage: broker enforce + tablas vacias. William autorizo (fix con test). Destructive sigue gateado aparte."


async def _seed_and_test(conn, commit: bool) -> int:
    from tool_broker import check as broker_check

    tx = conn.transaction()
    await tx.start()
    try:
        # ── Seed idempotente: un grant wildcard activo por agente ──
        for a in AGENTS:
            exists = await conn.fetchval(
                """SELECT 1 FROM soul_v3.capability_grants
                   WHERE caller_id=$1 AND tool='*' AND active IS TRUE
                   AND (expires_at IS NULL OR expires_at > NOW()) LIMIT 1""", a)
            if exists:
                print(f"   = {a}: ya tiene grant '*' activo (idempotente, no duplico)")
                continue
            await conn.execute(
                """INSERT INTO soul_v3.capability_grants
                   (caller_id, caller_type, server, tool, granted_by, expires_at, active, notes)
                   VALUES ($1,'agent',$2,'*',$3,NULL,TRUE,$4)""",
                a, SERVER, GRANTED_BY, NOTES)
            print(f"   + {a}: grant '*' insertado")

        # ── Test por-efecto (con la MISMA conn/transacción) ──
        print("\n   -- controles (por-efecto, dentro de la tx) --")
        pos = await broker_check("NEXUS", "seed-test", "memory_store",
                                 {"content": "x", "agent": "NEXUS"}, conn=conn, audit=False)
        neg = await broker_check("external", "seed-test", "memory_store",
                                 {"content": "x"}, conn=conn, audit=False)
        safe = await broker_check("NEXUS", "seed-test", "memory_invalidate",
                                  {"memory_id": 1, "agent": "NEXUS"}, conn=conn, audit=False)
        print(f"   [+] NEXUS memory_store     -> allow={pos.allow} decision={pos.decision} ({pos.rule_id})")
        print(f"   [-] external memory_store  -> allow={neg.allow} decision={neg.decision} ({neg.rule_id})")
        print(f"   [!] NEXUS memory_invalidate-> allow={safe.allow} needs_confirm={safe.needs_confirmation} ({safe.rule_id})")

        ok_pos = pos.allow is True
        ok_neg = neg.allow is False
        ok_safe = safe.allow is False  # destructivo NO se auto-permite
        all_ok = ok_pos and ok_neg and ok_safe
        print(f"\n   RESULTADO: positivo={ok_pos} negativo={ok_neg} seguridad-destructiva={ok_safe} => {'VERDE' if all_ok else 'FALLA'}")

        if commit and all_ok:
            await tx.commit()
            print("   COMMIT — grants sembrados en vivo.")
            return 0
        await tx.rollback()
        print("   ROLLBACK — " + ("DRY-RUN (sin --commit)" if not commit else "los controles FALLARON, no toco el core"))
        return 0 if all_ok else 1
    except BaseException:
        await tx.rollback()
        raise


async def main() -> int:
    dsn = os.environ.get("SEAL_DB_URL")
    if not dsn:
        print("ERROR: exportá SEAL_DB_URL con un DSN que tenga INSERT en soul_v3.capability_grants", file=sys.stderr)
        return 2
    os.environ.setdefault("SEAL_DB_URL", dsn)  # tool_broker.db lo necesita
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        priv = await conn.fetchval("SELECT has_table_privilege(current_user,'soul_v3.capability_grants','INSERT')")
        role = await conn.fetchval("SELECT current_user")
        print(f"rol={role} INSERT_priv={priv}")
        if not priv:
            print("ERROR: el rol no puede INSERT en capability_grants (usá el DSN admin).", file=sys.stderr)
            return 2
        return await _seed_and_test(conn, commit="--commit" in sys.argv)
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
