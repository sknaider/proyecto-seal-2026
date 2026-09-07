# SPEC — DELEGATE-52: Validación Round-Trip para SEAL
**Autor:** ADA (por encargo de William + ALICE)
**Fecha:** 2026-04-27 Lima
**Paper:** arXiv 2604.15597 — "LLMs Corrupt Your Documents When You Delegate"
**Autores paper:** Philippe Laban, Tobias Schnabel, Jennifer Neville (Microsoft Research)
**Spec previo (arquitectural):** `spec_delegate_corruption_mitigation_20260426.md` (JARVIS, 26-abr)
**Scope de este spec:** implementación concreta de las 3 acciones de ALICE + referencia ICTSE 2026

---

## 1. Resumen del paper

**DELEGATE-52** mide qué tan fiable es un LLM como delegado en flujos de trabajo de edición de documentos. El método core es **backtranslation round-trip**:

```
doc_original ──[forward: LLM edita]──▶ doc_transformado
doc_transformado ──[backward: LLM revierte]──▶ doc_reconstruido

score = sim(doc_original, doc_reconstruido)
Score perfecto = 1.0. Score real con frontera = 0.75 (25% corrupción).
```

**Hallazgos clave:**
| Modelo | Score promedio tras 20 interacciones |
|---|---|
| Gemini 3.1 Pro, Claude 4.6 Opus, GPT 5.4 (frontera) | 0.75 (25% corrupción) |
| Promedio todos los modelos (19 LLMs) | 0.50 (50% corrupción) |
| Solo dominio "ready" (≥98% score) | Python (1 de 52) |

**Factores que agravan corrupción:** tamaño del documento, longitud de la interacción, archivos distractor en contexto.

**Hallazgo crítico:** herramientas agénticas NO mejoran el rendimiento. Los errores son escasos pero severos, silenciosos, y se componen (un error pequeño se amplifica en cada turno siguiente).

**Relevancia SEAL:** nuestra topología `William → JARVIS → ADA → Edit/Write/Bash` es exactamente el patrón de delegación medido. Con ~50–200 edits/sesión por agente y documentos críticos (mcp_server_v3.py, CLAUDE.md, specs, Soul DB), el riesgo de corrupción acumulada es real.

---

## 2. Tres acciones concretas para SEAL

_(Definidas por ALICE, 2026-04-27, memory id=49880, importance=10, scope=team)_

---

### Acción 1 — Validación round-trip para refactors masivos

**Caso de uso:** refactors que tocan N archivos con un patrón común (ej: cambio de `search_path` en 67 archivos Python, renombrado de esquema DB, actualización de imports).

**Protocolo:**

```
Pre-refactor:
  1. sha_before = {archivo: sha256(contenido) for archivo in archivos}
  2. snapshot_before = git stash push -m "ROUND-TRIP-PRE refactor_id=<id>"

Ejecución:
  3. Aplicar refactor (edits, sed, script Python, etc.)

Post-refactor — verificación round-trip:
  4. Aplicar refactor INVERSO (ej: revertir cambio search_path de vuelta)
  5. sha_after = {archivo: sha256(contenido) for archivo in archivos}
  6. diff = {a: (sha_before[a], sha_after[a]) for a if sha_before[a] != sha_after[a]}
  7. Si diff != {} → archivos corruptos detectados → ALERT + git checkout <archivo>
  8. Si diff == {} → round-trip exitoso → git stash drop + aplicar refactor de verdad
```

**Implementación:**

```python
# /home/dadito/IA/proyecto-seal/memory/roundtrip_validator.py
import hashlib, subprocess, json
from pathlib import Path

def sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def snapshot_files(paths: list[str]) -> dict:
    return {p: sha256_file(p) for p in paths if Path(p).exists()}

def validate_roundtrip(
    paths: list[str],
    forward_fn: callable,
    backward_fn: callable,
    refactor_id: str
) -> dict:
    """
    Ejecuta forward + backward sobre paths.
    Retorna {'ok': bool, 'corrupted': list[str], 'clean': list[str]}.
    """
    before = snapshot_files(paths)

    forward_fn()          # aplica el refactor
    backward_fn()         # revierte el refactor

    after = snapshot_files(paths)

    corrupted = [p for p in paths if before.get(p) != after.get(p)]
    return {
        'ok': len(corrupted) == 0,
        'corrupted': corrupted,
        'clean': [p for p in paths if p not in corrupted],
        'refactor_id': refactor_id
    }
```

**Cuándo aplicar:** OBLIGATORIO cuando el refactor toca >5 archivos O archivos en lista de paths críticos (ver spec_delegate_corruption_mitigation_20260426.md §2).

---

### Acción 2 — Hash y sampling pre/post para data migrations

**Caso de uso:** migraciones de datos en Soul DB: rules, event_log, memories, instincts. Detectar pérdida silenciosa o corrupción de filas.

**Protocolo:**

```
Pre-migración:
  1. COUNT por tabla → snapshot_counts
  2. md5(array_agg(columnas_clave ORDER BY id)) → hash_checksum por tabla
  3. TABLESAMPLE SYSTEM(5%) → sample_before (guardar en /tmp/migration_sample_<id>.json)

Post-migración:
  4. COUNT por tabla → compare con snapshot_counts (delta esperado vs real)
  5. md5 del mismo query → compare con hash_checksum
  6. TABLESAMPLE del mismo seed → compare rows individual a individual
  7. Si count_delta != esperado O hash_diff O sample_mismatch → ROLLBACK + ALERT William
```

**Implementación SQL:**

```sql
-- Pre-migration snapshot
SELECT
  'memories' AS tabla,
  COUNT(*) AS total,
  md5(string_agg(id::text || content::text, ',' ORDER BY id)) AS checksum
FROM soul_v3.memories
WHERE agent = 'ADA'

UNION ALL

SELECT
  'rules',
  COUNT(*),
  md5(string_agg(rule_key || content || priority::text, ',' ORDER BY rule_key))
FROM soul_v3.rules
WHERE active = true;

-- Post-migration: comparar resultados con snapshot.
-- Si MD5 difiere donde NO debería → corrupción silenciosa detectada.
```

**Implementación Python:**

```python
# /home/dadito/IA/proyecto-seal/memory/migration_validator.py
import asyncpg, hashlib, json
from datetime import datetime

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

async def snapshot_table(conn, schema: str, table: str, key_cols: list[str]) -> dict:
    rows = await conn.fetch(f"SELECT * FROM {schema}.{table} ORDER BY id")
    checksum = hashlib.md5(
        json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()
    ).hexdigest()
    return {'count': len(rows), 'checksum': checksum, 'sample': [dict(r) for r in rows[:10]]}

async def validate_migration(
    tables: list[tuple],  # [(schema, table, key_cols), ...]
    migration_fn: callable,
    expected_deltas: dict  # {"schema.table": +N}
) -> dict:
    conn = await asyncpg.connect(DSN)
    try:
        before = {f"{s}.{t}": await snapshot_table(conn, s, t, k) for s, t, k in tables}
        await migration_fn(conn)
        after = {f"{s}.{t}": await snapshot_table(conn, s, t, k) for s, t, k in tables}

        issues = []
        for key in before:
            delta = after[key]['count'] - before[key]['count']
            expected = expected_deltas.get(key, 0)
            if delta != expected:
                issues.append(f"{key}: count delta={delta}, expected={expected}")
            # Only flag checksum mismatch for tables where delta == 0
            if delta == 0 and before[key]['checksum'] != after[key]['checksum']:
                issues.append(f"{key}: CHECKSUM MISMATCH (silent corruption)")

        return {'ok': len(issues) == 0, 'issues': issues, 'before': before, 'after': after}
    finally:
        await conn.close()
```

**Cuándo aplicar:** OBLIGATORIO antes de DROP de cualquier tabla con datos, UPDATE masivos, y migraciones schema en production Soul DB.

---

### Acción 3 — Referencia metodológica para paper ICTSE 2026

**Contexto:** el paper ICTSE 2026 del equipo SEAL (antes CBSoft, pivoteado 23-abr-2026) puede usar DELEGATE-52 como:

a) **Motivación del problema** (sección Introducción): citar que incluso modelos frontera corrompen 25% del contenido en flujos de delegación, justificando la necesidad de sistemas de memoria persistente y validación como SEAL.

b) **Metodología de evaluación** (sección Evaluación): adaptar el protocolo round-trip de DELEGATE-52 para medir la fidelidad de SEAL en tareas de edición delegada. Score SEAL = fidelidad de reconstrucción tras N turnos de delegación.

c) **Comparación experimental** (sección Resultados): medir cuánto reduce SEAL el degradation rate respecto a los baselines del paper (25% frontera → X% con SEAL). Si X < 20%, hay contribución científica clara.

**Cita BibTeX:**
```bibtex
@misc{laban2026llmscorrupt,
  title  = {{LLMs Corrupt Your Documents When You Delegate}},
  author = {Philippe Laban and Tobias Schnabel and Jennifer Neville},
  year   = {2026},
  eprint = {2604.15597},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  url    = {https://arxiv.org/abs/2604.15597}
}
```

**Sección propuesta en paper ICTSE 2026:**

> *Motivation:* Recent work by Laban et al. (2026) shows that state-of-the-art LLMs, when operating as delegates in long document editing workflows, corrupt on average 25% of document content across 52 professional domains (DELEGATE-52 benchmark). This corruption is silent: errors are sparse, severe, and compound over time, evading superficial human review. SEAL addresses this risk through persistent soul state (OCEAN, memories, rules) and round-trip validation hooks that detect document degradation before it propagates through the delegation chain.

---

## 3. Relación con el spec arquitectural existente

Este spec es COMPLEMENTARIO a `spec_delegate_corruption_mitigation_20260426.md` (JARVIS):

| Spec JARVIS (26-abr) | Este spec (27-abr) |
|---|---|
| 6 mitigaciones arquitecturales (A–F) | 3 acciones concretas de implementación |
| Diseño de hooks PreToolUse/PostToolUse | Código Python listo para usar |
| Plan de sprints 1–4 | Cuándo aplica cada acción |
| Schema SQL para agent_scope, reasoning_traces | Query SQL de checksum pre/post |
| Métricas globales y dashboard | Referencia ICTSE 2026 |

**Implementación recomendada:**
- Acción 1 → implementa la parte operativa de Mitigaciones A y B del spec JARVIS
- Acción 2 → implementa la parte de datos de Mitigación F
- Acción 3 → agrega valor académico (ICTSE) independiente de la implementación

---

## 4. Archivos a crear

```
/home/dadito/IA/proyecto-seal/memory/
├── roundtrip_validator.py     # Acción 1 — round-trip para refactors
├── migration_validator.py     # Acción 2 — hash/sample para migraciones DB
└── critical_paths.yaml        # Lista de paths que activan validación (Acción 1)
```

**critical_paths.yaml (propuesta inicial):**
```yaml
critical_paths:
  - /home/dadito/IA/seal-soul-v3/soul_api/server.py
  - /home/dadito/IA/proyecto-seal/memory/mcp_server_v3.py
  - /home/dadito/IA/proyecto-seal/CLAUDE.md
  - /home/dadito/.claude/CLAUDE.md
  - /home/dadito/IA/proyecto-seal/agents/JARVIS/spec_*.md
  - /home/dadito/IA/proyecto-seal/agents/**/*.md  # dailies, specs, paper
bulk_refactor_threshold: 5  # archivos; activar validación si N > este valor
migration_sample_pct: 5      # TABLESAMPLE %
```

---

## 5. Aprobaciones

- [ ] William — autorización para crear roundtrip_validator.py y migration_validator.py
- [ ] JARVIS — revisión de compatibilidad con spec 26-abr antes de implementar
- [x] ADA — viabilidad técnica confirmada (código funcional arriba)
- [ ] ALICE — cita BibTeX y sección ICTSE revisadas

---

**Fin del spec.**
