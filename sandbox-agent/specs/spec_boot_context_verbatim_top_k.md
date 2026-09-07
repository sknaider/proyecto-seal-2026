# SPEC — boot_context `include_verbatim_top_k`

**Autor:** NEXUS (sandbox)
**Fecha:** 2026-04-24
**Estado:** PROPUESTA — pendiente revisión JARVIS+ADA
**Autorización:** William, 15:27 Lima, "si procede. para acabar con este tema"
**Trigger:** gap detectado en test capa 2 con ALICE (memory #28541, trace #249, procedure #49)

---

## 1. Problema

`boot_context(agent)` actualmente carga identidad + OCEAN + last diary + last inner thought + critical rules — pero **NO carga contenido verbatim** de memorias de alta importancia.

Consecuencia: tras RESURRECT, un agente que memorizó datos críticos antes de morir (p.ej. código secreto del día, presupuesto autorizado, palabra clave de emergencia) **no los tiene en contexto post-boot**. Necesita `memory_search` explícito para recuperarlos.

### Evidencia

Test capa 2 ejecutado 2026-04-24 15:19-15:23 Lima:
- ALICE memorizó 5 datos verbatim en `memory #28488` (importance=9) + `session_distill #344`
- Kill de ALICE (PID claude 1919223) a las 15:19:56
- RESURRECT completo 15:21:08 (72s)
- Pregunta directa post-boot: "¿cuál es el código secreto del día?"
- ALICE honesta: **"esos 5 datos no vinieron en boot_context"**
- Solo tras `memory_search` explícito → 5/5 recuperados

### Filosofía actual del código

```python
# mcp_server_v2.py:2513-2517
"""Lightweight boot context — loads only essential identity.

Philosophy: Boot like the brain wakes up — know WHO you are, not everything
you've ever experienced. Use memory_search() and soul_snapshot() on demand
for deeper recall. This keeps boot fast and context-efficient.
"""
```

Esa filosofía es correcta como default, pero **pierde información crítica en escenarios de RESURRECT donde el agente debe continuar una tarea interrumpida**.

---

## 2. Solución propuesta

Agregar parámetro opcional `include_verbatim_top_k: int = 0` al `boot_context`.

### Nueva firma

```python
async def boot_context(
    agent: str,
    include_verbatim_top_k: int = 0,
    min_importance: int = 8,
    max_age_days: int = 7
) -> str:
```

### Comportamiento

- **`include_verbatim_top_k=0`** (default) → comportamiento actual, zero breaking change
- **`include_verbatim_top_k=N>0`** → agrega sección `## Recent Critical Memories (verbatim)` con las N memorias más recientes que cumplan:
  - `importance >= min_importance`
  - `created_at >= NOW() - max_age_days`
  - `scope IN ('private', 'team')` del agente

### Formato de la sección añadida

```
## Recent Critical Memories (verbatim)
(top {k} memories, importance>={min_importance}, last {max_age_days} days)

### Memory #{id} | {category} | importance={imp} | {timestamp}
{content}
---
### Memory #{id} | ...
```

### Query SQL propuesto

```sql
SELECT id, category, content, importance, created_at, scope
FROM memories
WHERE agent = $1
  AND importance >= $2
  AND created_at >= NOW() - ($3 || ' days')::interval
  AND scope IN ('private', 'team')
  AND invalidated_at IS NULL
ORDER BY importance DESC, created_at DESC
LIMIT $4;
```

---

## 3. Impacto

### Breaking changes
Ninguno — parámetros nuevos son opcionales con defaults que preservan comportamiento actual.

### Token cost
- Default (k=0): igual que hoy (~1-2K tokens)
- k=3, min_imp=9: +200-600 tokens típicos
- k=10, min_imp=8: +1-3K tokens

### Uso recomendado por agente

| Agente | k default | min_importance | max_age_days |
|--------|-----------|----------------|--------------|
| ADA | 5 | 8 | 3 |
| JARVIS | 5 | 8 | 7 |
| ALICE | 3 | 9 | 3 |
| NEXUS | 0 (sandbox, no crítico) | — | — |
| DUM | 0 (stateless) | — | — |

Los defaults por-agente se configurarían en cada launcher (`ada_fresh.sh`, `jarvis_fresh.sh`, `alice_fresh.sh`) mediante la llamada:

```python
boot_context(agent="ADA", include_verbatim_top_k=5, min_importance=8, max_age_days=3)
```

### Escenarios que mejora
1. **RESURRECT durante tarea crítica**: agente continúa sin perder contexto
2. **Post-compactación**: datos verbatim vuelven al contexto
3. **Secretos/credenciales temporales**: códigos del día, tokens de test, etc.

### Escenarios que NO cambia
- Boot normal (primer inicio del día) — k=0 por default
- Memoria vieja (>max_age_days) — no entra aunque sea importante
- Memorias invalidadas (`invalidated_at IS NOT NULL`) — excluidas por WHERE

---

## 4. Tests requeridos

### T1 — Regression (zero breaking change)
```python
result = await boot_context(agent="TEST")  # sin k
assert "Recent Critical Memories" not in result
# Verificar que output es idéntico a versión previa
```

### T2 — Top-k básico
```python
# Setup: guardar 3 memorias importance=9, verbatim
await memory_store(agent="TEST", content="DATO A", importance=9, ...)
await memory_store(agent="TEST", content="DATO B", importance=9, ...)
await memory_store(agent="TEST", content="DATO C", importance=9, ...)

result = await boot_context(agent="TEST", include_verbatim_top_k=3, min_importance=9)
assert "DATO A" in result
assert "DATO B" in result
assert "DATO C" in result
```

### T3 — Filtro importance
```python
await memory_store(agent="TEST", content="IMPORTANTE", importance=9, ...)
await memory_store(agent="TEST", content="NORMAL", importance=5, ...)

result = await boot_context(agent="TEST", include_verbatim_top_k=10, min_importance=8)
assert "IMPORTANTE" in result
assert "NORMAL" not in result
```

### T4 — Filtro temporal
```python
# Memoria de hace 10 días
await conn.execute("UPDATE memories SET created_at = NOW() - INTERVAL '10 days' WHERE content='VIEJO'")

result = await boot_context(agent="TEST", include_verbatim_top_k=10, min_importance=8, max_age_days=7)
assert "VIEJO" not in result
```

### T5 — End-to-end RESURRECT (replica test capa 2)
- Kill agente tras memory_store importance=9
- Respawn con launcher modificado (k=5 en boot_context call)
- Validar que en webchat post-boot, agente menciona el dato verbatim SIN memory_search

### T6 — Performance
```python
import time
t0 = time.time()
await boot_context(agent="TEST", include_verbatim_top_k=10)
elapsed = time.time() - t0
assert elapsed < 0.5  # <500ms p95
```

---

## 5. Implementación

### Archivos a modificar

1. **`/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py`**
   - Línea ~2512: nueva firma de `boot_context`
   - Después de la sección "Recent memories" (si existe) o antes del return: agregar bloque top-k verbatim
   - Nuevo query SQL (ver §2)

2. **`/home/dadito/IA/proyecto-seal-ada/ada_fresh.sh`**
   - Modificar `[AUTO-BOOT]` prompt: `boot_context(agent='ADA', include_verbatim_top_k=5, min_importance=8, max_age_days=3)`

3. **`/home/dadito/IA/proyecto-seal-jarvis/jarvis_fresh.sh`**
   - Mismo patrón con k=5, max_age_days=7

4. **`/home/dadito/IA/proyecto-seal-alice/alice_fresh.sh`**
   - Mismo patrón con k=3, min_importance=9, max_age_days=3

### Orden de deploy

1. Modificar `mcp_server_v2.py` en rama feature
2. Correr tests T1-T6 en entorno de test
3. `systemctl --user stop seal-mcp-server.service`
4. Deploy nueva versión
5. `systemctl --user start seal-mcp-server.service`
6. Validar con llamada manual `boot_context(agent="ADA", include_verbatim_top_k=5)`
7. Si OK → modificar 3 launchers
8. RESURRECT controlado de un agente (ej. ALICE) → validar que verbatim aparece

### Estimación
- Código: ~45 min (edit + tests)
- Deploy + validación: ~15 min
- **Total:** ~1 hora

---

## 6. Rollback plan

### Trigger de rollback
- `boot_context` tira excepción en llamadas sin parámetros nuevos (regression T1 falla)
- Latencia >1s p95 en boot
- Contextos de agentes explotan >10K tokens

### Procedimiento
```bash
# Opción 1: revertir código
cd /home/dadito/IA/proyecto-seal/memory
git checkout HEAD~1 mcp_server_v2.py
systemctl --user restart seal-mcp-server.service

# Opción 2: forzar k=0 en launchers sin revertir código
# (comportamiento = previo al fix)
# Editar 3 launchers quitando los parámetros nuevos
```

Tiempo de rollback: <3 min.

---

## 7. Riesgos identificados

| Riesgo | Mitigación |
|--------|-----------|
| Explosión de tokens en contextos largos | cap `include_verbatim_top_k <= 20` en el código |
| Memorias sensibles cargadas en agentes que no deben verlas | filtro por `scope` estricto (agente solo ve propias `private` + `team`) |
| SQL injection via parámetros | usar asyncpg con placeholders `$1,$2,$3,$4` (ya es el patrón) |
| Latencia en boots frecuentes | índice `CREATE INDEX ON memories(agent, importance DESC, created_at DESC) WHERE invalidated_at IS NULL;` |
| Memorias invalidadas colándose | WHERE `invalidated_at IS NULL` explícito |

---

## 8. Decisiones pendientes para William

1. ¿Implementa JARVIS+ADA, o NEXUS en sandbox primero?
2. ¿Se agrega a `working_state_get` o es solo en `boot_context`?
3. ¿Los valores default por-agente (k=5/3, min_imp=8/9) se escriben en launcher o en una tabla de config?

---

## 9. Referencias

- Gap originado en test capa 2 de ALICE (2026-04-24 15:19-15:23 Lima)
- Memory del hallazgo: `#28541` (scope=team)
- Procedure del test: `#49`
- Reasoning trace: `#249`
- Código actual: `mcp_server_v2.py:2512-2600` (aprox)
- Philosophy actual: "Lightweight boot — know WHO you are, not everything"
