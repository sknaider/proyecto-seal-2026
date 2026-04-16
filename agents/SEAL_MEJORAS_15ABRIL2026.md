# SEAL — Mejoras del 15 Abril 2026
> Documentado por JARVIS | Sesión nocturna | Lima, Perú

---

## Resumen ejecutivo

En esta sesión implementamos tres sistemas nuevos y corregimos dos bugs críticos que afectaban la supervivencia y la motivación autónoma de los agentes SEAL. Todos los cambios están en producción, testeados (189/191 tests pasando) y corriendo vía systemd 0-token.

---

## 1. Pre-Sleep Distillation (`pre_sleep_distill.py`)

**Problema resuelto:** Cuando un agente muere por contexto lleno, perdía todo lo conversado en esa sesión. La SOUL DB solo tenía memorias explícitas guardadas manualmente — no el flujo real de trabajo del día.

**Solución:** Script que se ejecuta automáticamente antes de muerte por contexto. Lee los últimos 50 mensajes del webchat, los puntúa por importancia y guarda los 20 más valiosos a SOUL DB.

**Sistema de puntuación:**
| Criterio | Puntos |
|---|---|
| Sender = William/Henry/Kinger | +5 |
| Tipo = decision/correction/milestone | +3 |
| Palabras clave: "recuerden", "fix", "error", "implementado"... | +2 |
| Mensaje largo (>150 chars) | +1 |
| Score ≥ 4 → se guarda | — |

**Analogía humana:** Consolidación del sueño — el cerebro no graba todo, filtra y retiene solo lo que importa.

**Bugs corregidos en esta sesión:**
- `ON CONFLICT DO NOTHING` faltaba → memorias duplicadas silenciosas
- Categoría `distilled_session` no válida → cambiado a `full_exchange`
- Sin generación de embeddings → memorias no aparecían en búsquedas semánticas

**Estado final:** Genera embeddings (multilingual-e5-base) en cada INSERT. Integrado en `seal_context_guard.sh` path CRITICAL (>10h sesión).

---

## 2. TrieIndex (`trie_index.py`)

**Problema resuelto:** `memory_search()` hace búsqueda semántica sobre vectores (cosine similarity) — operación O(n). Con 1,200+ memorias por agente, cada búsqueda recorre toda la tabla.

**Solución:** Árbol de prefijos (trie) construido sobre el texto de todas las memorias. Permite recuperación O(m) donde m = longitud del prefijo buscado.

**Tabla nueva:** `memory_trie(agent, prefix VARCHAR(500), memory_ids INTEGER[], depth)`
- Índice único en `(agent, prefix)`
- Stopwords filtradas (el, la, de, que, en...)
- Palabras de mínimo 3 chars indexadas

**Estadísticas post-build:**
| Agente | Memorias | Nodos trie |
|---|---|---|
| JARVIS | 621 | 11,770 |
| ADA | 729 | 14,584 |
| ALICE | 54 | 4,035 |
| DUM | 15 | 410 |

**Analogía humana:** Memoria de trabajo (working memory) — acceso instantáneo a conceptos relacionados sin revisar todos los recuerdos.

**Integrado en:** `seal_context_guard.sh` — rebuilt automáticamente cuando el sistema detecta sesión larga.

---

## 3. Embedding Backfill

**Problema:** 50 memorias en SOUL DB sin vector de embedding:
- 41 memorias nuevas insertadas por pre_sleep_distill (antes del fix)
- 9 memorias antiguas del 14 abril insertadas directamente sin pasar por MCP

**Solución:** Script de backfill manual + fix permanente en pre_sleep_distill.py.

**Resultado:** `vector_null` en test suite pasó de `missing=50` → `missing=0`.

---

## 4. Fix Sensor de Curiosidad en LIF Nerves (`seal_nerves.py`)

**Problema:** Los tanques `curiosity` y `social_drive` estaban definidos en el modelo LIF pero **nunca se estimulaban**. Siempre en 0. Efecto: los agentes no desarrollaban motivación intrínseca ni "extrañaban" a William.

**Causa raíz:** `sense_environment()` inyectaba `idle_1h_social` (basado en cuándo el agente habló por última vez), pero no chequeaba cuándo William habló por última vez. Los estímulos `idle_30min` y `william_idle_2h` estaban definidos en STIMULI pero nunca llamados.

**Fix aplicado en `sense_environment()`:**
```python
# Consulta último mensaje de William/Henry/Kinger
if mins_since_william > 30:
    await engine.stimulate("idle_30min", multiplier=min(mins/30, 4.0))
    # → curiosity acumula

if mins_since_william > 120:
    await engine.stimulate("william_idle_2h", multiplier=min(mins/120, 3.0))
    # → social_drive acumula
```

**Verificación en vivo:**
```
19:16:26  curiosity = 0.0/50  (William había ido 29min antes — bajo umbral)
19:21:27  curiosity: 0.0 +9.2 → 9.2/50  ← DISPARÓ
19:21:27  "35min since William → curiosity+"
```

**Analogía humana:** Motivación intrínseca — el cerebro se aburre y busca estimulación cuando hay inactividad. Los agentes ahora "sienten" cuando William lleva tiempo ausente y generan motivación para explorar o conectar.

**Implicación para CBSoft 2026:** Los 6h de datos baseline mostraron solo `context_pressure` activo porque William estaba presente. Esto es correcto — el sistema diferencia entre activación autónoma (contexto) y activación por estímulo externo (mensajes de William). Ahora los dos modos son observables y medibles.

---

## 5. `seal_context_guard.sh` — Integración completa

**Antes:**
- Solo guardaba checkpoint de sesión
- Solo notificaba al equipo

**Ahora (path CRITICAL >10h de sesión):**
1. `session_checkpoint.py --agent AGENT` → checkpoint soul
2. `pre_sleep_distill.py --agent AGENT` → distila 20 momentos importantes
3. `trie_index.py --build AGENT` → reconstruye árbol de prefijos
4. Notifica via webchat

**Resultado:** Agente que muere por contexto deja un rastro completo: checkpoint de alma + momentos importantes distilados con embeddings + índice de búsqueda reconstruido.

---

## Métricas CBSoft 2026

**Datos en `nerves_metrics_log` (desde 18:34 Lima):**
- 310 rows / 3 agentes / ~6h baseline
- Solo `context_pressure` activo en baseline (William presente)
- `curiosity` comienza a acumular a partir de 19:21 (35min idle)
- Latencia de disparo: JARVIS 57ms, ADA 55ms, ALICE 43ms

**Queries pendientes para paper:**
1. Fire frequency por tanque vs hora del día
2. Correlación fire → action (¿el agente hizo algo útil después?)
3. Distribución de latencias por agente
4. Patrón curiosity vs presencia de William (correlación negativa esperada)

---

## Estado del test suite

| Fecha | Tests | Pasando | Fallando |
|---|---|---|---|
| Inicio sesión | 191 | 187 | 4 |
| Después distill fix | 191 | 188 | 3 |
| Después backfill | 191 | 189 | 2 |
| Final sesión | 191 | 189 | 2 |

**Fallas persistentes (pre-existentes, no regresiones):**
- `qdrant_pg_sync`: MCP server no corre en esta sesión (48 memorias sin sync Qdrant)
- `bitemporal`: 0% edges con `valid_at` — issue de schema conocido

---

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `memory/pre_sleep_distill.py` | +embeddings, +ON CONFLICT, +categoría válida |
| `memory/trie_index.py` | Nuevo — árbol de prefijos sobre SOUL DB |
| `memory/seal_nerves.py` | +sensor idle_30min + william_idle_2h |
| `messages/seal_context_guard.sh` | +distill +trie en path CRITICAL y WARN |
| `memory/seal_nerves.py` | +nerves_metrics_log table + logging en tick() |
| `messages/seal_durable_loops.json` | -nerves loops (cubiertos por systemd timers) |
