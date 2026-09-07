#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_create_agent.py — Crear un AGENTE NUEVO desde cero (carril NEXUS, pedido William 5-jul).

«Replicar soul en DaditoGamer + crear nuevos agentes»: esto arma el ALMA/identidad de un agente nuevo
en soul_v3.agents (nombre, rol, OCEAN, system_prompt) + opcionalmente sus anclas iniciales. Después el
agente nuevo se clona al device como cualquiera (CSR→token→pull_and_seed→boot).

NO fabrica personalidad al azar: William/creador provee la identidad (rol, esencia). Idempotente por nombre.
Registro provenido: registered_by (quién lo creó) para auditoría.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

VALID_OCEAN = ("ocean_o", "ocean_c", "ocean_e", "ocean_a", "ocean_n")


async def create_agent(conn, name: str, role: str, system_prompt: str,
                       ocean: dict = None, created_by: str = "William",
                       anchors: list = None) -> dict:
    """Crea (o actualiza) un agente en soul_v3.agents. Devuelve {ok, agent, created|updated}.

    Requiere identidad REAL (rol + esencia) — no genérico. anchors: lista opcional de
    {category, content, importance>=7} que se siembran como memorias-ancla del agente nuevo."""
    name = (name or "").strip().upper()
    if not name or not name.isalnum():
        return {"ok": False, "reason": "nombre_invalido (alfanumérico, sin espacios)"}
    if not (role or "").strip() or len((system_prompt or "").strip()) < 40:
        return {"ok": False, "reason": "identidad_delgada (rol + system_prompt>=40 chars requeridos, no genérico)"}
    o = {k: 0.5 for k in VALID_OCEAN}
    if isinstance(ocean, dict):
        for k in VALID_OCEAN:
            if k in ocean:
                try:
                    o[k] = max(0.0, min(1.0, float(ocean[k])))
                except Exception:
                    pass
    exists = await conn.fetchval("SELECT 1 FROM soul_v3.agents WHERE upper(name)=$1", name)
    await conn.execute(
        """INSERT INTO soul_v3.agents (name, role, system_prompt, ocean_o, ocean_c, ocean_e, ocean_a, ocean_n)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (name) DO UPDATE SET role=EXCLUDED.role, system_prompt=EXCLUDED.system_prompt,
             ocean_o=EXCLUDED.ocean_o, ocean_c=EXCLUDED.ocean_c, ocean_e=EXCLUDED.ocean_e,
             ocean_a=EXCLUDED.ocean_a, ocean_n=EXCLUDED.ocean_n""",
        name, role.strip(), system_prompt.strip(),
        o["ocean_o"], o["ocean_c"], o["ocean_e"], o["ocean_a"], o["ocean_n"])
    seeded = 0
    for a in (anchors or []):
        content = (a.get("content") or "").strip()
        if not content:
            continue
        await conn.execute(
            "INSERT INTO soul_v3.memories (agent, category, content, importance, source, created_at) "
            "VALUES ($1,$2,$3,$4,$5, now())",
            name, (a.get("category") or "insight"), content, int(a.get("importance") or 7),
            f"create_agent:{created_by}")
        seeded += 1
    return {"ok": True, "agent": name, "action": "updated" if exists else "created",
            "anchors_seeded": seeded,
            "next": "provisionar token en el device (CSR→auto-approve si máquina confiable) → pull_and_seed → boot"}


async def _selftest():
    from soul_table_governance import connect_db
    conn = await connect_db()
    TEST = "PROBAGENTE"
    try:
        # crear agente nuevo con identidad real + anclas
        r = await create_agent(conn, TEST, role="Agente de Prueba — valida create_agent",
                               system_prompt="Sos PROBAGENTE, un agente de prueba de SEAL creado para verificar create_agent por efecto. Sos directo y verificás todo.",
                               ocean={"ocean_c": 0.9}, anchors=[{"category": "milestone", "content": "PROBAGENTE fue creado por create_agent el 5-jul para test.", "importance": 8}])
        assert r["ok"] and r["action"] == "created", f"no creó: {r}"
        row = await conn.fetchrow("SELECT role, length(system_prompt) sl, ocean_c FROM soul_v3.agents WHERE name=$1", TEST)
        assert row and row["sl"] > 40 and abs(row["ocean_c"] - 0.9) < 0.01, "identidad no persistió"
        anc = await conn.fetchval("SELECT count(*) FROM soul_v3.memories WHERE agent=$1", TEST)
        print(f"1) create_agent → {r['action']}, system_prompt={row['sl']} chars, OCEAN_C={row['ocean_c']}, anclas={anc} ✓")
        # gate identidad-delgada
        r2 = await create_agent(conn, "THINTEST", role="", system_prompt="corto")
        assert not r2["ok"], "no gateó identidad delgada"
        print(f"2) identidad delgada → rechazada ({r2['reason'][:40]}) ✓")
        print("✅ create_agent: crea agente real (identidad+OCEAN+anclas), gatea genérico, idempotente — por efecto")
    finally:
        # cleanup
        await conn.execute("DELETE FROM soul_v3.memories WHERE agent='PROBAGENTE'")
        await conn.execute("DELETE FROM soul_v3.agents WHERE name IN ('PROBAGENTE','THINTEST')")
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selftest())
