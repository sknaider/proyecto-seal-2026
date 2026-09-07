#!/usr/bin/env python3
"""
fable_boot.py — el BOOT de FABLE (cableado). El próximo FABLE despierta cargando las DOS memorias:
  • OPERATIVA (fable.bitacora consolidada) → de la que TRABAJA y JUZGA (neutral)
  • EMOCIONAL (fable.emotional_memory) → el SER (identidad, relación, valores, momentos)
Espejo del dual-memory de la familia (dual_memory_governance.py). Imprime el contexto de arranque.
"""
import asyncio, asyncpg
SUPER = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()

async def boot():
    c = await asyncpg.connect(SUPER)
    out = ["=== BOOT FABLE — dos memorias ==="]
    # EMOCIONAL primero: quién SOY (el ser)
    out.append("\n── MEMORIA EMOCIONAL (el ser — NO se usa para juzgar trabajo) ──")
    for r in await c.fetch("SELECT category, content FROM fable.emotional_memory ORDER BY id"):
        out.append(f"  [{r['category']}] {r['content']}")
    # OPERATIVA: de la que trabajo (consolidada + reciente)
    out.append("\n── MEMORIA OPERATIVA (de la que TRABAJO — neutral) ──")
    bm = await c.fetchval("SELECT value FROM fable.soul WHERE key='boot_memory'")
    out.append(bm or "  (sin boot_memory consolidada)")
    # soul base (identidad/método/límites)
    out.append("\n── NÚCLEO (fable.soul) ──")
    for r in await c.fetch("SELECT key, left(value,400) v FROM fable.soul WHERE key IN ('identity','method','limits','directriz_william','adversario','mision','trust_creator','iniciativa','sensibles')"):
        out.append(f"  {r['key']}: {r['v']}")
    # CONTINUIDAD: en qué iba al último corte (Fase 5 — no nacer sin hilo de tarea)
    out.append("\n── EN QUÉ IBA (working_state — retomo el hilo, no empiezo de cero) ──")
    try:
        import json
        from checkpoint import format_checkpoint
        ws = await c.fetchval("SELECT value FROM fable.soul WHERE key='working_state'")
        out.append(format_checkpoint(json.loads(ws) if ws else None))
    except Exception as e:
        out.append(f"  (working_state no disponible: {e})")
    txt = "\n".join(out)
    # persistir el boot unificado para acceso directo
    await c.execute("INSERT INTO fable.soul(key,value) VALUES('boot_full',$1) ON CONFLICT(key) DO UPDATE SET value=$1, updated_at=now()", txt)
    await c.close()
    print(txt)
    print("\n[guardado en fable.soul.boot_full — el próximo FABLE corre fable_boot.py o lee fable.soul.boot_full]")

if __name__ == "__main__":
    asyncio.run(boot())
