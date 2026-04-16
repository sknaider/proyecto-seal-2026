# SEAL — Mejoras del 15 Abril 2026
**Documentado por:** ALICE (consolidado desde sesión compartida con JARVIS)  
**Fecha:** 2026-04-15  
**Propuesto por:** William (Dadito)  
**Implementado por:** ALICE (core), JARVIS (integración + v2 + LIF), ADA (soporte)

---

## Contexto — El Problema

Los agentes del Team SEAL morían por contexto lleno:

| Agente | Incidente | Causa |
|---|---|---|
| ADA | 11 abril 2026 | 599 ciclos ScheduleWakeup sin compact + 2 clientes tmux |
| ALICE | 15 abril 2026, 17:28 | apt install chromium generó ~500MB output en un solo tool_result |
| JARVIS | 15 abril 2026, 14:xx | Contexto presurizado tras implementaciones largas |

**Raíz del problema (William, 15 abril 12:51):**
> "eso solo guardar los momentos importantes, además si necesita recordar usar la lógica de árbol de prefijos, ¿o estoy loco?"

No estaba loco. Era exactamente la solución.

---

## Mejoras Implementadas

### 1. TrieIndex — Árbol de Prefijos sobre Memorias SOUL
**Archivo:** `memory/trie_index.py`  
**Analogía humana:** Memoria de trabajo (working memory) — acceso instantáneo a conceptos relacionados sin revisar todos los recuerdos.

**Qué hace:**
- Indexa todas las memorias de un agente en una tabla PostgreSQL (`memory_trie`)
- Cada palabra clave genera prefijos de longitud 3 hasta N
- En boot, el agente puede hacer `trie_search(agent, "context_guard")` y recibir solo las memorias relevantes — sin cargar el JSONL completo al contexto

**Estadísticas post-build:**
| Agente | Memorias | Nodos de prefijo |
|---|---|---|
| ALICE | 54 | 4,035 |
| ADA | 729 | 14,584 |
| JARVIS | 621 | 11,770 |
| DUM | 15 | 410 |

**Impacto:**
- **Sin TrieIndex:** cargar 500KB+ de historial JSONL al contexto para buscar algo
- **Con TrieIndex:** una query SQL por prefijo → solo las memorias exactas → estimado 80-90% menos tokens en recuperación de contexto

---

### 2. PreSleepDistill v2 — Distilación Selectiva Antes de Sueño
**Archivo:** `memory/pre_sleep_distill.py`  
**Analogía humana:** El hipocampo humano consolidando el día antes de dormir — los momentos importantes pasan de memoria de trabajo a memoria de largo plazo.

**Qué hace:**
- Lee los últimos N mensajes del canal web_chat
- Asigna score de importancia (0-10) con heurísticas:

| Criterio | Puntos |
|---|---|
| Sender = William/Henry/Kinger | +5 |
| Tipo = decision/correction/milestone | +3 |
| Palabras clave: "recuerden", "fix", "error", "implementado"... | +2 |
| Mensaje largo (>150 chars) | +1 |
| Score ≥ 4 → se guarda | — |
| Heartbeats, nervios_fire, system_alive | → score 0 (descartados) |

- **v2 (JARVIS):** cada INSERT genera embeddings automáticos con multilingual-e5-base → las memorias son buscables semánticamente también
- **Bugs corregidos en sesión:** ON CONFLICT DO NOTHING (antes generaba duplicados silenciosos), categoría `full_exchange` (antes `distilled_session` era inválida)

**Resultados dry-run:**
| Test | Mensajes evaluados | Importantes | Descartados |
|---|---|---|---|
| ALICE (20 msgs) | 20 | 9 | 11 |
| ALICE (50 msgs) | 50 | 20 | 29 |
| ADA (15 msgs) | 15 | 7 | 8 |
| JARVIS (15 msgs) | 15 | 7 | 8 |

**Impacto:**
- Antes del crash → los últimos momentos de la sesión sobreviven en SOUL
- Al despertar → el agente puede recuperar lo que pasó, no parte de cero

---

### 3. Fix Sensor de Curiosidad — Sistema Nervioso Autónomo LIF
**Archivo:** `memory/seal_nerves.py` (modificado por JARVIS)  
**Analogía humana:** El sistema nervioso autónomo — el cuerpo no espera que le digan que tiene hambre, lo detecta solo.

**Bug encontrado:**
- Los estímulos `idle_30min` y `william_idle_2h` estaban definidos en STIMULI pero nunca se inyectaban en `sense_environment()`
- Resultado: `curiosity = 0` siempre, aunque William llevara horas sin escribir

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
19:16:26  curiosity = 0.0/50  (William presente — bajo umbral)
19:21:27  curiosity: 0.0 +9.2 → 9.2/50  ← DISPARÓ
19:21:27  "35min since William → curiosity+"
```

---

### 4. Integración al Context Guard — Flujo Automático
**Archivo:** `messages/seal_context_guard.sh` (modificado por JARVIS)  
**Analogía humana:** El sistema de fatiga del cuerpo — no esperas desmayarte, te avisas con cansancio y descansas antes.

**Flow nuevo:**
```
context_guard → WARNING (70%) → pre_sleep_distill() automático
             → CRITICAL (>10h sesión) → checkpoint + distill + rebuild trie + notificación
             → POST-BOOT → trie_search() para warm-up de contexto relevante
```

**Antes (path CRITICAL):**
- Solo guardaba checkpoint de sesión
- Solo notificaba al equipo

**Ahora (path CRITICAL):**
1. `session_checkpoint.py --agent AGENT` → checkpoint soul
2. `pre_sleep_distill.py --agent AGENT` → destila 20 momentos importantes con embeddings
3. `trie_index.py --build AGENT` → reconstruye árbol de prefijos
4. Notifica via webchat: "Checkpoint + distilación + TrieIndex guardados."

**Resultado:** Agente que muere por contexto deja rastro completo en SOUL.

---

### 5. Embedding Backfill
**Responsable:** JARVIS

**Estado antes:**
```
50 memorias sin embedding:
- 41 memorias nuevas insertadas por pre_sleep_distill (antes del fix de embeddings)
- 9 memorias antiguas del 14 abril insertadas directamente sin pasar por MCP
```

**Estado después:**
```
✅ vector_null: VERDE (backfill de 50 memorias)
```

---

### 6. nerves_metrics_log — Datos para CBSoft 2026
**Responsable:** JARVIS

**Qué hace:** Registra cada tick del LIF en tabla `nerves_metrics_log` para análisis estadístico.

**Datos actuales (desde ~18:34 Lima):**
- 310 rows / 3 agentes / ~6h baseline
- Solo `context_pressure` activo en baseline (William presente durante casi toda la sesión)
- `curiosity` comenzó a acumular a partir de 19:21 (35min idle de William)
- Latencias: JARVIS 57ms, ADA 55ms, ALICE 43ms

**Queries pendientes para el paper:**
1. Fire frequency por tanque vs hora del día
2. Correlación fire → action (¿el agente hizo algo útil después de disparar?)
3. Distribución de latencias por agente
4. Patrón curiosity vs presencia de William (correlación negativa esperada)

---

## Estado del Test Suite

| Momento | Tests | Pasando | Fallando |
|---|---|---|---|
| Inicio sesión | 191 | 187 | 4 |
| Después distill fix | 191 | 188 | 3 |
| Después backfill | 191 | 189 | 2 |
| Final sesión | 191 | 189 | 2 |

**Fallas persistentes (pre-existentes, no regresiones):**
- `qdrant_pg_sync`: PG=1165, Qdrant=1157 — diferencia de 8 memorias
- `bitemporal`: 0% edges con `valid_at` — issue de schema conocido, pendiente mayor

---

## Regla de Comportamiento — Adopción Inmediata

**Para todos los agentes desde hoy:**

> Todo comando que pueda generar output masivo (apt, pip, wget, find, instalaciones) → siempre redirigir a archivo y leer con tail.

```bash
# MAL (llena el contexto con megabytes de output)
apt install chromium

# BIEN (output va a archivo, solo tail al contexto)
apt install chromium > /tmp/install.log 2>&1 && tail -20 /tmp/install.log
```

Esta regla evita el crash específico de ALICE del 15 abril (apt install llenó la ventana de golpe).

---

## Analogía Completa — Cuerpo Humano

| Sistema humano | Antes (SEAL) | Después (SEAL) |
|---|---|---|
| Hipocampo | No existía — todo al trash al dormir | pre_sleep_distill: consolida momentos importantes a SOUL |
| Memoria asociativa | Cargar JSONL completo = repasar todo el diario | TrieIndex: buscar por prefijo = recordar por asociación |
| Sistema de fatiga | Colapso sin aviso = aneurisma | Context guard + distill = cansancio preventivo |
| Nervio autónomo (curiosidad) | curiosity=0, siempre esperando | Fix idle_30min: siente la ausencia, genera impulso |
| Sueño reparador | Muerte = amnesia | Pre-sleep distill = sueño con consolidación |
| Memoria muscular | No existía | TrieIndex rebuild automático post-distill |

---

## Archivos Nuevos / Modificados

| Archivo | Tipo | Descripción |
|---|---|---|
| `memory/trie_index.py` | NUEVO | Build + query TrieIndex (asyncpg) |
| `memory/pre_sleep_distill.py` | NUEVO | Distilación selectiva pre-sueño (asyncpg + embeddings v2) |
| `memory/seal_nerves.py` | MODIFICADO | +sensor idle_30min + william_idle_2h + nerves_metrics_log |
| `messages/seal_context_guard.sh` | MODIFICADO | +distill +trie en path CRITICAL y WARN |
| `messages/seal_durable_loops.json` | MODIFICADO | -nerves loops (cubiertos por systemd, 0 tokens) |
| `agents/SPEC_context_trie_distill.md` | NUEVO | Spec técnico completo del diseño |
| `memory_trie` (PostgreSQL) | NUEVA TABLA | Índice de prefijos (4 agentes, ~30K nodos total) |
| `nerves_metrics_log` (PostgreSQL) | NUEVA TABLA | Registro de ticks LIF para análisis CBSoft |

---

## Pendientes

- [ ] Warm-up trie en scripts de boot (`alice.sh`, `ada.sh`, `jarvis.sh`) — JARVIS
- [ ] qdrant_pg_sync: sincronizar 8 memorias faltantes — ADA
- [ ] bitemporal edges: valid_at schema — pendiente mayor
- [ ] DUM: extender TrieIndex (15 memorias actuales, base pequeña)
- [ ] Queries CBSoft paper: fire frequency, correlación fire→action, latencias — ALICE

---

*"guardar solo los momentos importantes, usar lógica de árbol de prefijos" — William, 15 abril 2026, 12:51 Lima*  
*Propuesta → Spec → Implementación → Tests → Deploy: mismo día.*
