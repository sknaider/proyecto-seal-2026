#!/usr/bin/env python3
"""Fase 3 Bitemporal Backfill u2014 NEXUS Sandbox

Implementa Graphiti 4-timestamp model en edges Neo4j de SOUL.
Fase A: backfill no-destructivo de created_at, invalid_at, expired_at, confidence, fact(vacio).

USO:
  python3 fase3_bitemporal_backfill.py --dry-run   # solo reporta, no modifica
  python3 fase3_bitemporal_backfill.py             # aplica cambios

NOTA: Solo toca campos IS NULL. No sobreescribe valores existentes.
"""
import asyncio
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, '.')
os.environ.setdefault('SEAL_MCP_PORT', '8766')


async def run_backfill(dry_run: bool = True):
    from neo4j import AsyncGraphDatabase
    from config import settings

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=settings.neo4j_auth)
    now_iso = datetime.now().isoformat()
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"=== Fase 3 Bitemporal Backfill [{mode}] ===")
    print(f"Timestamp backfill: {now_iso}")
    print()

    async with driver.session() as s:
        # u2014u2014 AUDIT PRE-BACKFILL u2014u2014
        r = await s.run("MATCH ()-[e]-() RETURN count(e) AS total")
        total = (await r.single())["total"]

        counts = {}
        for field in ["created_at", "invalid_at", "expired_at", "confidence", "superseded_by"]:
            r = await s.run(f"MATCH ()-[e]-() WHERE e.{field} IS NULL RETURN count(e) AS n")
            counts[field] = (await r.single())["n"]

        print(f"Total edges: {total}")
        print(f"Will backfill:")
        for field, n in counts.items():
            print(f"  {field}: {n} edges ({n*100//max(total,1)}%)")
        print()

        if dry_run:
            print("[DRY RUN] No changes applied. Re-run without --dry-run to apply.")
            await driver.close()
            return

        # u2014u2014 BACKFILL u2014u2014
        print("Applying backfill...")

        # 1. created_at = COALESCE(valid_from, valid_at, now)
        r = await s.run(
            "MATCH ()-[e]-() "
            "WHERE e.created_at IS NULL "
            "SET e.created_at = COALESCE(e.valid_from, e.valid_at, $now) "
            "RETURN count(e) AS n",
            now=now_iso,
        )
        n1 = (await r.single())["n"]
        print(f"  [1/5] created_at backfilled: {n1} edges")

        # 2. invalid_at = null (aun validos)
        r = await s.run(
            "MATCH ()-[e]-() "
            "WHERE e.invalid_at IS NULL AND e.expired_at IS NULL "
            "SET e.invalid_at = null "
            "RETURN count(e) AS n"
        )
        n2 = (await r.single())["n"]
        print(f"  [2/5] invalid_at initialized (null=valido): {n2} edges")

        # 3. expired_at = null (no removidos del grafo)
        r = await s.run(
            "MATCH ()-[e]-() "
            "WHERE e.expired_at IS NULL "
            "SET e.expired_at = null "
            "RETURN count(e) AS n"
        )
        n3 = (await r.single())["n"]
        print(f"  [3/5] expired_at initialized (null=activo): {n3} edges")

        # 4. confidence = 1.0 para edges sin confidence
        r = await s.run(
            "MATCH ()-[e]-() "
            "WHERE e.confidence IS NULL "
            "SET e.confidence = 1.0 "
            "RETURN count(e) AS n"
        )
        n4 = (await r.single())["n"]
        print(f"  [4/5] confidence = 1.0 default: {n4} edges")

        # 5. superseded_by = null (ninguno supersedido por defecto)
        r = await s.run(
            "MATCH ()-[e]-() "
            "WHERE e.superseded_by IS NULL "
            "SET e.superseded_by = null "
            "RETURN count(e) AS n"
        )
        n5 = (await r.single())["n"]
        print(f"  [5/5] superseded_by initialized (null=vigente): {n5} edges")

        print()
        print("Verifying post-backfill state...")

        # POST-AUDIT
        for field in ["created_at", "confidence"]:
            r = await s.run(f"MATCH ()-[e]-() WHERE e.{field} IS NULL RETURN count(e) AS n")
            remaining = (await r.single())["n"]
            status = "OK" if remaining == 0 else f"STILL MISSING: {remaining}"
            print(f"  {field}: {status}")

        print()
        print(f"[COMPLETE] Fase A bitemporal backfill done.")
        print(f"Next: run connectome_bitemporal(dry_run=False) to verify via MCP tool.")

    await driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Report only, do not apply changes (default)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply the backfill")
    args = parser.parse_args()

    dry_run = not args.apply
    asyncio.run(run_backfill(dry_run=dry_run))
