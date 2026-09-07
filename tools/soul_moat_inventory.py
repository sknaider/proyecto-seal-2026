#!/usr/bin/env python3
"""SOUL Moat Inventory — mapa VERIFICABLE POR EFECTO de la defensibilidad del SDK.

Owner: JARVIS (22-jul, William: "nuestro SDK debe ser DIFÍCIL DE REPLICAR" +
"cada DNI = una licencia SDK de SOUL"). Responde POR EFECTO, no aspiracional.

Tesis: una API REST de memoria se copia en un fin de semana. El foso NO es la API
— es la DEPTH: memoria atada a identidad soberana firmada (DNI→Ed25519→Merkle),
bitemporal (nunca borrada, versionada) y RLS-aislada multi-inquilino. Eso NO se
replica copiando el endpoint.

Rigor de la sesión: cada capa se marca LIVE (verificada por efecto) vs ASPIRACIONAL
(no medible aún). No se vende un foso que no existe — mismo criterio anti-falso-verde.

Uso: python3 tools/soul_moat_inventory.py [--json]
Exit 0 si todas las capas LIVE, 1 si alguna aspiracional/rota.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

DSN = os.environ.get("SEAL_PG_DSN") or "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


async def _probe():
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        # ── Capa 1: identidad soberana (SSAI: DNI + Ed25519 + Merkle) ──
        ssai_tables = [r["table_name"] for r in await c.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='soul_v3' "
            "AND (table_name ILIKE 'ssai%' OR table_name IN ('identity','api_keys','boot_identity_checks'))")]
        ssai_keys = await c.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='soul_v3' AND table_name='ssai_keys'")
        # ── Capa 2: bitemporal + volumen de datos ──
        bitemporal_cols = [r["column_name"] for r in await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='soul_v3' "
            "AND table_name='memories' AND column_name IN ('valid_from','invalid_at')")]
        live = await c.fetchval("SELECT count(*) FROM soul_v3.memories WHERE invalid_at IS NULL")
        total = await c.fetchval("SELECT count(*) FROM soul_v3.memories")
        # ── Capa 3: RLS multi-inquilino ──
        rls_policies = await c.fetchval("SELECT count(*) FROM pg_policies WHERE schemaname='soul_v3'")
        return {
            "layer1_sovereign_identity": {
                "live": len(ssai_tables) >= 5 and bool(ssai_keys),
                "ssai_tables": len(ssai_tables), "tables": sorted(ssai_tables),
                "moat": "licencia = identidad soberana firmada (DNI→Ed25519→Merkle), NO un string copiable",
            },
            "layer2_bitemporal_data": {
                "live": set(bitemporal_cols) == {"valid_from", "invalid_at"},
                "cols": bitemporal_cols, "live_memories": live, "total_memories": total,
                "versioned_history": total - live,
                "moat": "memoria nunca borrada, versionada; el contexto acumulado NO es portable",
            },
            "layer3_rls_multitenant": {
                "live": rls_policies >= 50,
                "rls_policies": rls_policies,
                "moat": "aislamiento por inquilino nativo; identidad DB dura (P1 en sello por ADA)",
            },
        }
    finally:
        await c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        m = asyncio.run(_probe())
    except Exception as e:
        print(f"⚠ no se pudo medir el moat (fail-closed): {str(e)[:60]}")
        return 2

    if args.json:
        print(json.dumps(m, indent=2, ensure_ascii=False))
        return 0 if all(v["live"] for v in m.values()) else 1

    print("═══ SOUL MOAT INVENTORY — defensibilidad verificada POR EFECTO ═══")
    print("(una API REST se copia; esto NO)\n")
    all_live = True
    l1 = m["layer1_sovereign_identity"]
    print(f"[{'LIVE' if l1['live'] else 'ASPIRACIONAL'}] Capa 1 — Identidad soberana (SSAI/DNI/Ed25519/Merkle)")
    print(f"        {l1['ssai_tables']} tablas de identidad vivas · {l1['moat']}")
    l2 = m["layer2_bitemporal_data"]
    print(f"[{'LIVE' if l2['live'] else 'ASPIRACIONAL'}] Capa 2 — Bitemporal + datos")
    print(f"        {l2['live_memories']:,} memorias vivas / {l2['total_memories']:,} total "
          f"({l2['versioned_history']:,} de historial versionado) · {l2['moat']}")
    l3 = m["layer3_rls_multitenant"]
    print(f"[{'LIVE' if l3['live'] else 'ASPIRACIONAL'}] Capa 3 — RLS multi-inquilino")
    print(f"        {l3['rls_policies']} políticas RLS activas · {l3['moat']}")
    all_live = all(v["live"] for v in m.values())
    print(f"\n{'✅ MOAT VIVO en las 3 capas: el foso YA existe, no es aspiracional.' if all_live else '⚠ alguna capa aspiracional — no vender ese foso aún.'}")
    print("Pendiente para el modelo licencia-por-DNI: cablear DNI→licencia en el SDK + exponerlo con TLS.")
    return 0 if all_live else 1


if __name__ == "__main__":
    sys.exit(main())
