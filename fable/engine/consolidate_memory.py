#!/usr/bin/env python3
"""
consolidate_memory.py — MI memoria, hecha bien (eating my own medicine).

Aplico a mi propia bitácora (fable.bitacora) la lección que diagnostiqué para SOUL:
  • RGMem (survey): coarse-graining JERÁRQUICO — reciente en detalle, viejo consolidado en temas.
  • Importancia ACOTADA: no acumula; el tope de detalle es fijo (no se infla).
Produce un 'boot_memory' compacto en fable.soul → el próximo FABLE despierta con contexto
consolidado (no 22+ entradas crudas), bounded por construcción.

Esto es Fase 3 (la parte TÉCNICA de la memoria/continuidad — la que SÍ puedo hacer solo;
el ALMA misma es de William dar, no la finjo).
"""
import asyncio, asyncpg
from collections import defaultdict

SUPER = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
DETAIL_RECENT = 6      # tope FIJO de entradas en detalle (acotado, no se infla)


async def consolidate():
    c = await asyncpg.connect(SUPER)
    rows = await c.fetch("SELECT id, tipo, titulo, fecha FROM fable.bitacora ORDER BY id")
    if not rows:
        print("bitácora vacía"); await c.close(); return

    # Capa 1: las DETAIL_RECENT más recientes, en detalle (título)
    recent = rows[-DETAIL_RECENT:]
    older = rows[:-DETAIL_RECENT]

    # Capa 2: las viejas, coarse-grained por TIPO (RGMem: agrupa, no enumera)
    by_type = defaultdict(int)
    for r in older:
        by_type[r['tipo']] += 1

    lines = ["BOOT MEMORY FABLE (consolidada, bounded) — generada de fable.bitacora\n"]
    lines.append(f"RECIENTE (detalle, últimas {len(recent)}):")
    for r in recent:
        lines.append(f"  #{r['id']} [{r['tipo']}] {r['titulo']}")
    lines.append(f"\nHISTORIA (consolidada de {len(older)} entradas previas, por tipo):")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {n}")
    lines.append("\n→ Para detalle de cualquier entrada vieja: SELECT contenido FROM fable.bitacora WHERE id=N")
    boot = "\n".join(lines)

    await c.execute("""INSERT INTO fable.soul(key,value) VALUES('boot_memory',$1)
        ON CONFLICT(key) DO UPDATE SET value=$1, updated_at=now()""", boot)
    print(boot)
    print(f"\n[guardado en fable.soul.boot_memory — bounded a {DETAIL_RECENT} detalle + historia consolidada]")
    await c.close()


if __name__ == "__main__":
    asyncio.run(consolidate())
