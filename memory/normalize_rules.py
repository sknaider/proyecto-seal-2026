#!/usr/bin/env python3
"""Normalize noisy auto-escalated Matrix rules.

Default mode is dry-run. Use --apply to:
- demote raw ``william_auto_*`` Matrix captures from priority >=8 to priority 5
- insert/update a small set of canonical William rules at priority 10

No rows are deleted and no rules are deactivated.
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import json

import asyncpg


DB_URL = pg_dsn(required=True)


CANONICAL_RULES = [
    (
        "william_canonical_test_before_done",
        "Siempre testear y verificar antes de declarar una tarea terminada. La victoria requiere evidencia auditable: comando ejecutado, salida relevante y archivo/ruta concreta cuando aplique.",
    ),
    (
        "william_canonical_post_compact_restart",
        "Después de compactación, reinicio o caída de daemon, verificar continuidad: boot/active_recall, working_state, session digest, servicios activos y que el agente vuelva a escuchar.",
    ),
    (
        "william_canonical_save_memory",
        "Guardar en SOUL DB decisiones, correcciones, hitos y pendientes importantes. No guardar ruido conversacional como regla crítica.",
    ),
    (
        "william_canonical_dm_attention",
        "Mantener atención a mensajes dirigidos al agente y DM autorizados. Responder solo cuando el mensaje esté dirigido al agente o aporte valor claro.",
    ),
    (
        "william_canonical_team_coordination",
        "Cuando una tarea involucra varios agentes, coordinar estado, pendientes y handoff verificable; no asumir que otro agente recordó el contexto.",
    ),
]


async def main_async(apply: bool) -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        noisy = await conn.fetch("""
            SELECT id, rule_key, priority, left(content, 160) AS content
            FROM soul_v3.rules
            WHERE active = true
              AND priority >= 8
              AND rule_key LIKE 'william_auto_%'
              AND content LIKE '[Matrix]%'
            ORDER BY priority DESC, created_at DESC
        """)
        canonical_existing = await conn.fetchval("""
            SELECT count(*)
            FROM soul_v3.rules
            WHERE rule_key = ANY($1::varchar[])
        """, [key for key, _ in CANONICAL_RULES])

        print(f"normalize_rules mode={'APPLY' if apply else 'DRY RUN'}")
        print(f"scope.noisy_matrix_rules={len(noisy)}")
        print(f"scope.canonical_existing={canonical_existing}")
        for row in noisy[:20]:
            print(f"candidate.id={row['id']} priority={row['priority']} key={row['rule_key']} content={row['content']}")
        if len(noisy) > 20:
            print(f"candidate.more={len(noisy) - 20}")

        if not apply:
            print("No changes made. Re-run with --apply to demote noisy rules and upsert canonical rules.")
            return

        updated = await conn.execute("""
            UPDATE soul_v3.rules
            SET priority = 5,
                metadata = COALESCE(metadata, '{}'::jsonb)
                           || $1::jsonb,
                updated_at = now()
            WHERE active = true
              AND priority >= 8
              AND rule_key LIKE 'william_auto_%'
              AND content LIKE '[Matrix]%'
        """, json.dumps({"normalized_by": "ADA", "reason": "raw_matrix_capture_demoted"}))

        upserted = 0
        for key, content in CANONICAL_RULES:
            existing_id = await conn.fetchval(
                "SELECT id FROM soul_v3.rules WHERE rule_key = $1 ORDER BY id LIMIT 1",
                key,
            )
            if existing_id:
                await conn.execute("""
                    UPDATE soul_v3.rules
                    SET content = $2,
                        priority = 10,
                        tier = 1,
                        active = true,
                        updated_at = now(),
                        metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb
                    WHERE id = $1
                """, existing_id, content, json.dumps({"canonical": True, "source": "normalize_rules"}))
            else:
                await conn.execute("""
                    INSERT INTO soul_v3.rules
                        (agent, rule_key, content, priority, tier, active, set_by, metadata, created_at, updated_at)
                    VALUES
                        ('TEAM', $1, $2, 10, 1, true, 'ADA', $3::jsonb, now(), now())
                """, key, content, json.dumps({"canonical": True, "source": "normalize_rules"}))
            upserted += 1

        print(f"updated.noisy_matrix_rules={updated}")
        print(f"upserted.canonical_rules={upserted}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    main()
