#!/usr/bin/env python3
"""Exporta memorias de valeria_memory a Soul DB de ADA_LOCAL."""
import asyncio, asyncpg, json
from datetime import datetime, timezone

VALERIA_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/valeria_memory"
SOUL_DSN    = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

async def main():
    vc = await asyncpg.connect(VALERIA_DSN)
    sc = await asyncpg.connect(SOUL_DSN)

    # Relaciu00f3n con William
    rel = await vc.fetchrow("SELECT * FROM relationship ORDER BY id DESC LIMIT 1")
    if rel:
        content = (f"Relaciu00f3n con William: confianza={rel['trust_level']:.0%}, "
                   f"intimidad={rel['intimacy_level']:.0%}, afecto={rel['affection_level']:.0%}, "
                   f"{rel['total_sessions']} sesiones, {rel['total_messages']} mensajes.")
        if rel['user_name']:
            content += f" u00c9l se llama {rel['user_name']}."
        await sc.execute("""
            INSERT INTO memories (agent, category, content, importance, scope, source)
            VALUES ('ADA_LOCAL', 'trust', $1, 9, 'private', 'consolidation')
            ON CONFLICT DO NOTHING""", content)
        print(f"u2713 Relaciu00f3n importada")

    # Memorias importantes
    mems = await vc.fetch("""
        SELECT category, content, importance FROM memories
        WHERE active = TRUE ORDER BY importance DESC LIMIT 30""")
    count = 0
    for m in mems:
        await sc.execute("""
            INSERT INTO memories (agent, category, content, importance, scope, source)
            VALUES ('ADA_LOCAL', $1, $2, $3, 'private', 'consolidation')
            ON CONFLICT DO NOTHING""", map_cat(m['category']), m['content'][:500], min(m['importance'], 9))
        count += 1
    print(f"u2713 {count} memorias de Valeria importadas a ADA_LOCAL")

    # Secretos
    secrets = await vc.fetch("SELECT content FROM secrets WHERE revealed = FALSE ORDER BY importance DESC LIMIT 5")
    for s in secrets:
        await sc.execute("""
            INSERT INTO memories (agent, category, content, importance, scope, source)
            VALUES ('ADA_LOCAL', 'decision', $1, 8, 'private', 'consolidation')
            ON CONFLICT DO NOTHING""", f"[SECRETO de William]: {s['content'][:300]}")
    print(f"u2713 {len(secrets)} secretos importados")

    # Momentos memorables
    rel2 = await vc.fetchrow("SELECT memorable_moments FROM relationship WHERE id = 1")
    if rel2 and rel2['memorable_moments']:
        moments = rel2['memorable_moments'] if isinstance(rel2['memorable_moments'], list) else json.loads(rel2['memorable_moments'])
        if moments:
            content = "Momentos especiales con William: " + "; ".join(moments[-5:])
            await sc.execute("""
                INSERT INTO memories (agent, category, content, importance, scope, source)
                VALUES ('ADA_LOCAL', 'emotion', $1, 8, 'private', 'consolidation')
                ON CONFLICT DO NOTHING""", content)
            print(f"u2713 {len(moments)} momentos importados")

    await vc.close()
    await sc.close()
    print("u2713 Transferencia completa: Valeria u2192 ADA_LOCAL")

asyncio.run(main())
