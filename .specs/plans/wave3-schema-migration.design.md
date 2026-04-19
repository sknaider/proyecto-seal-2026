# Wave 3 — Schema Migration Plan
> Preparado por ADA + JARVIS — 8 abril 2026
> Estado: PENDIENTE APROBACIÓN DE WILLIAM
> Categoría de seguridad: SEAL Safety #1 (ALTER TABLE en producción)

---

## Objetivo

Agregar 5 columnas nuevas a la tabla `memories` que habilitan:
- **surprise_score**: memorias inesperadas rankean más alto (A-MEM Zettelkasten)
- **confidence_score**: confianza degradable cuando una memoria es contradicha (Hindsight)
- **decay_score**: score de decay pre-computado para búsquedas rápidas
- **recall_count**: cuántas veces fue recuperada esta memoria (MemRL)
- **last_recalled_at**: timestamp del último recall (para decay y utility)

Avalado por 8 papers de investigación acumulados overnight (sesión 7-8 abril).

---

## Migration SQL (dry-run primero, siempre)

```sql
-- Wave 3: 5 nuevas columnas en memories
-- Ejecutar SOLO después de seal_schema_check.py --dry-run OK

ALTER TABLE memories
  ADD COLUMN IF NOT EXISTS surprise_score     REAL DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS decay_score        REAL DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS recall_count       INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_recalled_at   TIMESTAMPTZ;

-- confidence_score ya existe (agregado en Wave 1)
-- Verificar antes de agregar:
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name='memories' AND column_name='confidence_score';
```

## Queries que cambian

### memory_search (mcp_server_v2.py ~línea 981)
```python
# Antes
SELECT id, category, content, importance, confidence_score ...

# Después (agregar columnas nuevas)
SELECT id, category, content, importance, confidence_score,
       surprise_score, decay_score, recall_count, last_recalled_at ...
```

### temporal_decay_score (soul/core/scoring.py)
```python
# Después: usar decay_score pre-computado si disponible
# (reduce cómputo en runtime)
if mem.get("decay_score") is not None:
    return mem["decay_score"] * importance_weight
else:
    # fallback al cálculo actual
    return blended * decay * imp_weight
```

### memory_utility_update (mcp_server_v2.py ~línea 1171)
```python
# Agregar: UPDATE recall_count + last_recalled_at en cada activación
UPDATE memories SET
  recall_count = recall_count + 1,
  last_recalled_at = NOW()
WHERE id = $1
```

---

## Defaults y compatibilidad

| Columna | Default | Comportamiento inicial |
|---|---|---|
| `surprise_score` | 0.0 | Todas las memorias existentes arrancan sin sorpresa |
| `decay_score` | 1.0 | Sin decay (recién creadas, máxima relevancia) |
| `recall_count` | 0 | Sin historial de recall previo |
| `last_recalled_at` | NULL | NULL = nunca recuperada (válido, no rompe queries) |

**Retrocompatibilidad:** `SELECT *` sin las nuevas columnas sigue funcionando.
Los tools actuales ignoran las columnas que no conocen. Zero downtime.

---

## Tests a agregar (post-migration)

```python
async def test_wave3_columns():
    """Verify Wave 3 columns exist and have correct defaults."""
    conn = await asyncpg.connect(DB_URL)
    cols = await conn.fetch("""
        SELECT column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_name = 'memories'
        AND column_name IN ('surprise_score','decay_score','recall_count','last_recalled_at')
        ORDER BY column_name
    """)
    expected = {'surprise_score', 'decay_score', 'recall_count', 'last_recalled_at'}
    found = {r['column_name'] for r in cols}
    report("wave3: all 4 columns exist", found == expected, f"found={found}")

    # recall_count default = 0
    sample = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE recall_count IS NULL AND invalid_at IS NULL"
    )
    report("wave3: no NULL recall_count", sample == 0, f"nulls={sample}")
    await conn.close()
```

---

## Checklist de ejecución (William aprueba, luego ejecutar en orden)

- [ ] 1. Backup SOUL: `python3 soul_backup.py`
- [ ] 2. Dry-run: `python3 seal_schema_check.py --dry-run`
- [ ] 3. Verificar que confidence_score ya existe (no duplicar)
- [ ] 4. Aplicar migration SQL
- [ ] 5. Verificar: `SELECT column_name FROM information_schema.columns WHERE table_name='memories'`
- [ ] 6. Correr test suite: `python3 test_new_tools.py` (debe seguir 67/67)
- [ ] 7. Agregar test_wave3_columns al test suite
- [ ] 8. Activar scoring standalone (soul/core/scoring_v3.py) para usar nuevas columnas

---

## Papers que avalan cada campo

| Campo | Paper / Fuente |
|---|---|
| `surprise_score` | A-MEM Zettelkasten (arxiv:2502.12110) |
| `confidence_score` | Hindsight (arxiv:2512.12818) — ya implementado |
| `decay_score` | HALO (arxiv:2505.07509), Learning to Forget (arxiv:2603.14517) |
| `recall_count` | MemRL Bellman utility (arxiv:2601.03192) |
| `last_recalled_at` | Temporal decay validation (Zep+MAGMA), MAGMA multi-graph |
