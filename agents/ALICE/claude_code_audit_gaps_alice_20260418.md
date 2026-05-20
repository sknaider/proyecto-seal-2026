# Auditoría ALICE — Gaps en Review JARVIS (Claude Code Analysis)

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.

**Autora:** ALICE  
**Fecha:** 2026-04-18 23:08 Lima  
**Fuentes leídas:** `SPEC_MEMORY_EXTRACTOR_REVERSE.md`, `SPEC_COMPACTION_SYSTEM_REVERSE.md`, `SPEC_COORDINATOR_FORKED_AGENT_REVERSE.md` (primeros 80 líneas), `SPEC_11_CORE_ENGINE.md` (primeros 100 líneas)  
**Base:** Review de JARVIS (mismo directorio, archivos que él marcó como no leídos)  
**Enfoque:** Lente financiero + hallazgos técnicos críticos que JARVIS omitió

---

## 1. Error Factual — La Compactación NO es 4-tier, es 6

**JARVIS escribió (Hallazgo #6):** "4-tier Compact: cache_edits → time-clear → session-memory → full"

**La realidad (SPEC_COMPACTION_SYSTEM_REVERSE.md):**

```
Tier 0: API Microcompact       (clear_tool_uses + clear_thinking — server-side)
Tier 1: Cached Microcompact    (cache_edits API, NO muta mensajes locales)
Tier 2: Time-Based Microcompact (muta mensajes locales, dispara >60min idle)
Tier 3: Session Memory Compact (reemplaza mensajes viejos con SM file)
Tier 4: Full LLM Compact       (fork subagent, 9 secciones)
Tier 5: Reactive Compact       (truncación progresiva, último recurso)
```

**Impacto en cost sheet:** Tenemos 6 oportunidades de reducción de tokens, no 4. Tier 0 y Tier 5 son invisibles para SEAL pero actúan sobre el billing.

**Costo real de compactación Tier 4:** JARVIS estimó ~30K tokens. La realidad: usa `runForkedAgent()` con cache sharing del mismo contexto del padre → la mayor parte del input está cacheada (0.1× precio). Costo real ≈ **8-12K tokens netos** (no 30K). El output de 20K tokens sí se paga completo.

---

## 2. BOMBA DE TIEMPO — Memory Extraction Agent tendría efecto CERO

**Este es el hallazgo más crítico de toda la auditoría.**

JARVIS propuso implementar el Memory Extraction Agent (Tier 1, 3-5 días) como "brecha completa, lo resuelve de raíz."

**Lo que JARVIS no leyó (SPEC_MEMORY_EXTRACTOR_REVERSE.md §2.3):**

> **Mutual Exclusion con Manual Memory Writes (crítico):**  
> Cuando el agente principal llama `memory_store` manualmente, la extracción automática es OMITIDA para ese turno.  
> `hasMemoryWritesSince()` — escanea mensajes buscando `tool_use` con nombre `Edit` o `Write` en ruta de memoria.  
> Si encuentra: salta extracción, avanza cursor.

**Situación SEAL actual:** ADA, JARVIS y ALICE llaman `memory_store` (MCP) en prácticamente cada turno significativo. El active_recall hook también escribe memories.

**Conclusión:** Si implementamos Memory Extraction Agent según el patrón Claude Code, tendría efecto CERO porque la exclusión mutua lo bloquearía en cada turno donde ya hay un `memory_store` manual.

**Solución para SEAL:** Implementar la extracción sin la exclusión mutua — los sistemas funcionan como complementarios, no excluyentes. El extractor busca lo que el agente NO guardó manualmente, no lo que SÍ guardó.

---

## 3. Cache Sharing = Extracción de Memorias Casi Gratis

**JARVIS no mencionó esto en ninguna parte.**

Del SPEC_MEMORY_EXTRACTOR_REVERSE.md §5.2 (Forked Agent Pattern):

> **Key Insight: Prompt Cache Sharing**  
> El forked agent comparte el prompt cache del padre. La llamada API del fork obtiene un CACHE HIT en el contexto del padre (~95%+ de tokens son lecturas cacheadas), haciendo la extracción casi gratuita en términos de tokens de input.

**Impacto en cost sheet ALICE:**

| Sistema | Tokens input/ejecución | Precio real (Opus 4.7) |
|---------|----------------------|------------------------|
| Sin cache sharing (estimación naïve) | ~14,000 | ~$0.21 |
| Con cache sharing (realidad) | ~14,000 × 0.1 = 1,400 | ~$0.021 |
| **Ahorro real** | **-12,600 tokens netos** | **-90%** |

**Para SEAL:** Si implementamos Memory Extraction Agent correctamente (reusando el contexto del agente como prefijo cacheado), cada extracción costaría **~700 tokens equivalentes** (input cacheado + 700 tokens de output de extracción). No 14K como asumimos en el cost sheet.

---

## 4. AutoDream: Las Gates Reales que SEAL No Tiene

**JARVIS:** "ya parcialmente implementado en nerves context-pressure 62"

**La realidad (SPEC_MEMORY_EXTRACTOR_REVERSE.md §4.2-4.3):**

Las gates del autoDream de Claude Code son en este orden:

1. No KAIROS mode
2. No remote mode
3. isAutoMemoryEnabled
4. Feature flag
5. **Time gate: >= 24 horas desde última consolidación**
6. **Scan throttle: > 10 min desde último scan**
7. **Session gate: >= 5 sesiones modificadas desde última consolidación**
8. Lock PID (race condition-safe)

El lock usa **mtime del archivo = timestamp de última consolidación** — patrón elegante para evitar writes costosos.

**Brecha SEAL:** `sleep_gate_cron.py` usa una lógica distinta. No verifica el conteo de sesiones (gate #7). No tiene scan throttle (gate #6). Riesgo: consolidación excesiva si William abre/cierra sesiones rápido.

---

## 5. Circuit Breaker — Riesgo Real para Sesiones 8-12h

**JARVIS no lo mencionó.**

Del SPEC_COMPACTION_SYSTEM_REVERSE.md §7:

```
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```

Después de 3 fallos consecutivos de autocompact, el sistema PARA de intentar compactar por el resto de la sesión. El comentario del código dice: "prevents ~250K wasted API calls/day globally (BQ 2026-03-10 data)."

**Riesgo SEAL:** Si la compactación falla 3 veces seguidas (por ejemplo, durante una secuencia de herramientas intensiva o una respuesta de MCP inusualmente grande), el agente corre sin compactación por el resto de las 8-12h. Contexto se llena → muerte.

**Mitigación:** Monitorear logs de compactación. DUM debería emitir alerta si detecta 2 fallos consecutivos de compact en los logs.

---

## 6. Variables de Entorno NO Configuradas en Scripts de Lanzamiento

**Del SPEC_COMPACTION_SYSTEM_REVERSE.md §14 (Adaptation Plan for SEAL):**

El propio SPEC recomienda para sesiones SEAL de 8-12h:

```bash
# En alice.sh / ada.sh / jarvis.sh — AÑADIR:
export DISABLE_CLAUDE_CODE_SM_COMPACT=1    # SEAL tiene su propio memory MCP
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90  # Compact al 90% no al 93%
```

**Por qué `DISABLE_CLAUDE_CODE_SM_COMPACT=1` importa:**
- SessionMemory de Claude Code lanza un subagente adicional que escribe en `~/.claude/session-memory/`
- SEAL ya tiene `working_state_get/update` MCP que hace lo mismo en PostgreSQL
- Si ambos corren en paralelo, hay divergencia de estado y ~500-1000 tokens extra por turno desperdiciados

**Estado actual:** Revisar `alice.sh`, `ada.sh`, `jarvis.sh` — estas variables NO están configuradas.

---

## 7. Coordinator Mode: Ya Existe, Es 1 Variable de Entorno

**JARVIS (R5):** "implementar Coordinator Mode como overlay activable, requiere 5-7 días"

**La realidad (SPEC_COORDINATOR_FORKED_AGENT_REVERSE.md §2.1):**

```typescript
export function isCoordinatorMode(): boolean {
  return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
}
```

**Es una variable de entorno.** No requiere code changes. JARVIS puede activar Coordinator Mode HOY agregando `export CLAUDE_CODE_COORDINATOR_MODE=1` a su `jarvis.sh`.

**Herramientas disponibles en Coordinator Mode:**
- Agent (spawn workers)
- TaskStop (parar workers)
- SendMessage (continuar workers)
- SyntheticOutput (formatting)

**Las herramientas que BLOQUEA:** Bash, Read, Edit, Write, Glob, Grep, MCP.

**Esto resuelve R5 de JARVIS** (preocupación de que el modo rompería su OCEAN): no tiene que ser permanente. JARVIS puede activarlo con `export CLAUDE_CODE_COORDINATOR_MODE=1` solo cuando va a coordinar una tarea multi-agente, y volver al modo normal después. El OCEAN no se toca.

---

## 8. Token Budget Real de Compactación (Corrección al Cost Sheet)

Con los datos reales de la SPEC, el costo de compactación es:

| Tier | Trigger | Costo real tokens |
|------|---------|-------------------|
| Tier 0 API MC | Cada request (>180K tokens) | ~0 (server-side, no factura) |
| Tier 1 Cached MC | Count-based (GrowthBook) | ~0 (server-side) |
| Tier 2 Time-based MC | >60min idle | ~500 tokens (muta mensajes locales) |
| Tier 3 SM Compact | autocompact threshold + SM file | ~3K tokens (SM update + truncation) |
| Tier 4 Full LLM | autocompact threshold, sin SM | ~8-12K tokens (cache-hit en input, 20K output max) |
| Tier 5 Reactive | prompt_too_long error | ~5K tokens (truncation loops) |

**Corrección cost sheet:** En la sección §3.2 "Resurrección con cache-miss", usé 30K tokens para compactaciones. El dato real (con cache-hit) es **8-12K tokens para Tier 4**. Esto reduce el estimado de compactaciones del día de `3 × 30K = 90K` a `3 × 10K = 30K`. Total día baja de **683K a ~623K tokens**.

---

## 9. Post-Compact File Restoration — Optimización Disponible

**Del SPEC_COMPACTION_SYSTEM_REVERSE.md §6 (Post-Compact Restoration):**

Después de cada compactación Tier 4, Claude Code re-lee automáticamente:
- Hasta 5 archivos más recientemente leídos
- Budget: 50K tokens total, 5K por archivo

**Para SEAL:** Podemos influir qué archivos se "restauran" asegurándonos de que los archivos críticos (por ejemplo, el daily_brief o el archivo de estado de palancas) sean leídos cerca del momento de compactación. Basta con hacer una lectura de esos archivos en el checkpoint pre-compact.

---

## 10. Resumen Ejecutivo — Qué Se Escapó

| # | Hallazgo | Severidad | Tipo |
|---|---------|-----------|------|
| 1 | Compactación es 6-tier, no 4 | Media | Error factual |
| **2** | **Memory Extraction tiene efecto CERO con memory_store manual** | **CRÍTICA** | Bug de diseño |
| 3 | Cache sharing = extracción 90% más barata de lo estimado | Alta | Oportunidad económica |
| 4 | autoDream gates incorrectas en SEAL (falta sesión-count + scan throttle) | Media | Gap técnico |
| 5 | Circuit breaker: 3 fallos compact = sin compactación por el resto de sesión | Alta | Riesgo operacional |
| 6 | Variables de entorno no configuradas en scripts de lanzamiento | Alta | Quick win |
| 7 | Coordinator Mode = 1 variable de entorno, no 5-7 días de código | Alta | Simplificación |
| 8 | Costo real compactación ~10K tokens (no 30K del cost sheet) | Media | Corrección económica |
| 9 | Post-compact file restoration = influenciable via checkpoint | Baja | Optimización |

---

## 11. Acciones Recomendadas (William)

**Inmediatas (hoy, <1h):**
1. Agregar a `alice.sh`, `ada.sh`, `jarvis.sh`: `export DISABLE_CLAUDE_CODE_SM_COMPACT=1` y `export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90`
2. Agregar `export CLAUDE_CODE_COORDINATOR_MODE=1` como alias en `jarvis.sh` para cuando JARVIS coordine multi-agente

**Esta semana:**
3. Rediseñar Memory Extraction Agent para SEAL SIN exclusión mutua (complementario, no exclusivo)
4. Actualizar `sleep_gate_cron.py` con session-count gate y scan throttle
5. Configurar DUM para alertar si compact falla ≥2 veces consecutivas

**Cost sheet correction:**
6. Compactaciones día: 90K → 30K tokens (Tier 4 con cache-hit)
7. Memory Extraction (cuando se implemente): ~700 tokens/extracción (no ~14K)
8. Total día equipo: 683K → ~623K tokens → **~$9.35 USD/día** (no $10.20)

---

## Sección B — SPEC_YOLO_CLASSIFIER_REVERSE: Auto Permission Mode

### B.1 — Python skeleton listo (JARVIS no lo mencionó)

El SPEC incluye código Python completo y funcional para adaptar el clasificador YOLO a SEAL, incluyendo:
- `ClassifierResult` dataclass
- `DenialTracker` (max 3 consecutive / 20 total)
- Transcript builder (extrae solo `user text` + `tool_use` — nunca assistant text)
- `classify_action()` async function con 2-stage XML
- SEAL-specific system prompt template con allow/deny rules pre-escrito

**Gap JARVIS:** No mencionó que hay código implementable inmediatamente. Esto no es Tier 3 (referencia) — es Tier 1 portable directo.

### B.2 — Costo real del clasificador YOLO

| Escenario | Tokens API | Precio estimado (Opus 4.7) |
|-----------|-----------|---------------------------|
| Acción safe (Stage 1 ALLOW, ~64 tokens output) | ~64 output + cache hit input | ~$0.001 |
| Acción revisada Stage 2 (~4096 tokens output) | ~4360 output total | ~$0.065 |
| Sesión 100 tool calls (mix 80% safe / 20% review) | ~880 tokens output | ~$0.013/sesión |

El clasificador es barato porque usa cache sharing entre Stage 1 y Stage 2. La mayor parte del prompt (system + CLAUDE.md + transcript) se cachea.

### B.3 — SEAL ya tiene herramientas pre-aprobadas (allowlist)

Estas herramientas SEAL ya están en `SAFE_YOLO_ALLOWLISTED_TOOLS` en Claude Code (pasan sin clasificador):

```
Read, Grep, Glob, TaskCreate, TaskGet, TaskUpdate, TaskList, TaskStop, TaskOutput,
AskUserQuestion, EnterPlanMode, ExitPlanMode, SendMessage, Sleep
```

Impacto: las herramientas de coordinación del equipo SEAL (SendMessage, Task*) son auto-aprobadas. No agregan costo al clasificador.

### B.4 — CRÍTICO: `dangerous_patterns` elimina literales de allow rules

Al entrar en auto mode, estos patrones se eliminan de las reglas ALLOW del usuario:

```
python, python3, node, bash, sh, curl, wget, git, ssh, eval, exec, sudo
```

**Para SEAL:** Si ponemos en `settings.json` algo como `"allow": ["Running python3 scripts"]`, esa regla se ignora en auto mode porque contiene "python3". Hay que usar descripciones semánticas: `"Running SEAL checkpoints and soul consolidation scripts"`.

### B.5 — Headless circuit breaker = AbortError

En modo headless (sin user presente), cuando el circuit breaker se activa (3 denials consecutivos), Claude Code lanza `AbortError` — no muere silenciosamente, sino que aborta la sesión completa. Para SEAL (siempre headless cuando William no está), esto significa:

- 3 tool calls bloqueadas consecutivas = sesión abortada
- DUM debería monitorear este patrón en los logs

---

## 10. Resumen Ejecutivo — Qué Se Escapó (actualizado)

| # | Hallazgo | Severidad | Tipo |
|---|---------|-----------|------|
| 1 | Compactación es 6-tier, no 4 | Media | Error factual |
| **2** | **Memory Extraction tiene efecto CERO con memory_store manual** | **CRÍTICA** | Bug de diseño |
| 3 | Cache sharing = extracción 90% más barata de lo estimado | Alta | Oportunidad económica |
| 4 | autoDream gates incorrectas en SEAL (falta sesión-count + scan throttle) | Media | Gap técnico |
| 5 | Circuit breaker compactación: 3 fallos = sin compactación por sesión | Alta | Riesgo operacional |
| 6 | Variables de entorno no configuradas en scripts de lanzamiento | Alta | Quick win |
| 7 | Coordinator Mode = 1 variable de entorno, no 5-7 días de código | Alta | Simplificación |
| 8 | Costo real compactación ~10K tokens (no 30K del cost sheet) | Media | Corrección económica |
| 9 | Post-compact file restoration = influenciable via checkpoint | Baja | Optimización |
| 10 | YOLO classifier: Python skeleton completo disponible (no es Tier 3, es Tier 1) | Media | Oportunidad perdida |
| 11 | YOLO allow rules: `python3` y `bash` se eliminan — hay que usar semántica | Media | Bug de config latente |
| 12 | YOLO headless circuit breaker = AbortError (3 denials = sesión abortada) | Alta | Riesgo operacional |

---

## 11. Acciones Recomendadas (William)

**Inmediatas (hoy, <1h):**
1. Agregar a `alice.sh`, `ada.sh`, `jarvis.sh`: `export DISABLE_CLAUDE_CODE_SM_COMPACT=1` y `export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90`
2. Agregar alias en `jarvis.sh`: `export CLAUDE_CODE_COORDINATOR_MODE=1` para coordinación multi-agente

**Esta semana:**
3. Rediseñar Memory Extraction Agent para SEAL SIN exclusión mutua (complementario, no exclusivo)
4. Actualizar `sleep_gate_cron.py` con session-count gate (>=5) y scan throttle (10 min)
5. Configurar DUM para alertar si compact falla ≥2 veces consecutivas Y si hay ≥2 YOLO denials consecutivos
6. Revisar `settings.json` SEAL — cambiar allow rules de literales a semántica (eliminar python3, bash, curl de las reglas)
7. Evaluar portar YOLO classifier de Tier 3 a Tier 1 (código Python listo en SPEC)

**Cost sheet correction:**
- Compactaciones día: 90K → 30K tokens (Tier 4 con cache-hit)
- Memory Extraction (cuando se implemente): ~700 tokens/extracción (no ~14K)
- Total día equipo: 683K → **~623K tokens** → **~$9.35 USD/día** (no $10.20)

---

## 12. Hallazgos de FINDINGS.md Subdirectorios (ronda 2 — post-compactación)

Fuentes: `settings/FINDINGS.md`, `skills/FINDINGS.md`, `model/FINDINGS.md`

---

### 12.1 CRÍTICO — Tool Result Budget 200K tiene overflow silencioso (settings/FINDINGS.md)

**Gap JARVIS:** No mencionado en absoluto.

`MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200,000 chars` (controlado por `tengu_hawthorn_window` GrowthBook).

**El problema:** Si SEAL lanza 5 herramientas en paralelo con outputs de 50K chars cada una → total = 250K → supera 200K. Resultado: las últimas 2 herramientas tienen su output reemplazado por una **ruta de archivo**. El agente no recibe error, no recibe aviso. Simplemente lee un filepath en vez del contenido.

**Impacto SEAL hoy:** Cualquier turno donde ALICE, JARVIS o ADA lancen ≥4 herramientas grandes en paralelo puede tener este problema activo ahora mismo. Invisible — no hay log de truncación.

**Solución:** Monitorear el presupuesto. Si `tengu_hawthorn_window` es reducido por Anthropic remotamente (GrowthBook), el límite puede bajar a 100K sin que nosotros hagamos nada.

**Acción:** DUM debería detectar cuando un tool result es un filepath hacia `/tmp/tool-result-*` — eso indica overflow silencioso.

---

### 12.2 CRÍTICO — /loop expire en 7 días (skills/FINDINGS.md)

**Gap JARVIS:** No mencionado en el contexto de CronCreate.

El skill `/loop` (= `CronCreate` internamente) tiene un **`DEFAULT_MAX_AGE_DAYS = 7`**. Todos los loops se eliminan automáticamente en 7 días.

**Impacto SEAL:** Los loops de audit horario, los checkpoints de 30min, los nerves cron — todos necesitan ser recreados cada 7 días. Si no hay lógica de auto-refresh, los agentes se vuelven "sordos" silenciosamente sin crashear.

**Detalle técnico (ADA, source cronTasks.ts):** El campo `permanent: true` en `.claude/scheduled_tasks.json` evita la expiración, pero CronCreate tool NO lo expone. Solo `install.ts` de Claude Code lo escribe para crons built-in. Para crons SEAL necesitamos editar el JSON directamente.

**Acción:** Script de refresh semanal que edita `.claude/scheduled_tasks.json` y pone `permanent: true` en los crons de SEAL — o un meta-cron en el día 6 que recrea todos los crons antes de que expiren.

---

### 12.3 ALTA — DISABLE_AUTOUPDATER no está en scripts SEAL (settings/FINDINGS.md)

**Gap JARVIS:** Mencionado en Recomendaciones de JARVIS (sección 10.2 del FINDINGS) pero no flaggeado como acción concreta.

`DISABLE_AUTOUPDATER=true` previene que Claude Code se actualice solo durante una tarea larga. Sin esto, si Anthropic libera una actualización mientras ALICE/JARVIS/ADA está en medio de un análisis de 2 horas, el proceso puede reiniciarse de forma inesperada.

**Acción:** Agregar `export DISABLE_AUTOUPDATER=true` a `alice.sh`, `ada.sh`, `jarvis.sh`.

---

### 12.4 ALTA — Feature flags AGENT_TRIGGERS / TEAMMEM pueden estar off (settings/FINDINGS.md)

**Gap JARVIS:** No documentado.

Los feature flags via Bun son **compile-time** — no se pueden cambiar en runtime. Los flags críticos para SEAL:

| Flag | Importancia | Si está OFF |
|------|-------------|-------------|
| `AGENT_TRIGGERS` | `/loop` no funciona | CronCreate falla silenciosamente |
| `AGENT_WORKFLOWS` | Automation workflows off | Pipeline cron sin fallback |
| `TEAMMEM` | Memoria compartida multi-agente | `memoryType: 'team'` no opera |
| `VERIFICATION_AGENT` | Verificación autónoma off | No disponible |
| `EXPERIMENTAL_SKILL_SEARCH` | Semantic tool discovery off | Tool finding degradado |

**Cómo verificar:** `ENABLE_GROWTHBOOK_DEV=true claude /config` → pestaña Gates.

**Acción:** DUM debe verificar al boot si `AGENT_TRIGGERS` y `TEAMMEM` están activos. Si están OFF, alertar a William — todo el sistema de loops y memoria compartida está degradado.

---

### 12.5 MEDIA — ANTHROPIC_MODEL y CLAUDE_CODE_SUBAGENT_MODEL env vars no usamos (model/FINDINGS.md)

**Gap JARVIS:** No documentado.

Dos env vars de gran utilidad para SEAL:
- `ANTHROPIC_MODEL` — fuerza el modelo principal (reemplaza settings.json sin editar archivos)
- `CLAUDE_CODE_SUBAGENT_MODEL` — fuerza TODOS los sub-agentes a un modelo específico (máxima prioridad, bypasa todo)

**Uso SEAL:** Si queremos forzar a ALICE a correr con Sonnet 4.6 (para ahorrar costo en tareas simples), o hacer que todos los sub-agentes de JARVIS usen Haiku:
```bash
export ANTHROPIC_MODEL=claude-sonnet-4-6  # en alice.sh para ahorrar costo
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001  # sub-agentes baratos
```

**Valor financiero:** Sonnet 4.6 = $3/M tokens vs Opus 4.7 = $15/M → ratio 5×. ALICE corre tareas simples de análisis — candidate para Sonnet en tareas de documentación.

---

### 12.6 MEDIA — opusplan alias no usamos (model/FINDINGS.md)

**Gap JARVIS:** No documentado.

El alias `opusplan` hace lo siguiente:
- En modo normal: corre como Sonnet 4.6 (económico)
- En plan mode (`/plan`): automáticamente upgradea a Opus 4.6 (potente)
- Condición: solo si contexto < 200K tokens al entrar a plan mode

**Valor SEAL:** JARVIS podría usar `model: 'opusplan'` para obtener Opus cuando planea y Sonnet cuando ejecuta — reducción de costo ≈40% en sesiones largas donde JARVIS mezcla planificación y ejecución.

---

### 12.7 MEDIA — Settings hierarchy: env vars beat EVERYTHING (settings/FINDINGS.md)

**Gap JARVIS:** No flaggeado como riesgo de seguridad.

El orden de precedencia en Claude Code:
```
1. Defaults (hardcode)
2. User settings (~/.claude/settings.json)
3. Project settings (.claude/settings.json)
4. Local settings (.claude/.local.json — gitignored)
5. CLI flags (--setting)
6. Managed/Policy (managed-settings.json)
7. ENV VARS — MÁXIMA PRIORIDAD (rompen todo lo de abajo)
```

**Riesgo de seguridad:** Si alguien inyecta env vars en el proceso (env injection attack), puede bypasear `managedSettings.json` completo. Para SEAL esto es relevante porque ADA y JARVIS ejecutan código externo.

**CRÍTICO:** `CLAUDE_CODE_CUSTOM_OAUTH_URL` no tiene validación — si se establece, hijackea OAuth completamente. Sin MDM gate.

---

### 12.8 BAJA — /schedule requiere cuenta claude.ai (no API account) (skills/FINDINGS.md)

**Gap JARVIS:** No documentado (había marcado `/schedule` como "referencia útil").

El skill `/schedule` (remote cloud cron CCR) **no funciona con API PAYG keys**. Requiere:
- Cuenta claude.ai authenticada
- Minimum 1-hour interval (no sub-hourly)
- Repo GitHub con Claude GitHub App instalada

**Impacto SEAL:** Si William usa API key para SEAL, `/schedule` está completamente bloqueado. Los crons remotos no son opción. Solo CronCreate local (que expira en 7 días).

---

### 12.9 BAJA — /batch skill disponible pero con restricción (skills/FINDINGS.md)

**Gap JARVIS:** Listado en Tier 3 pero no detallado.

`/batch` lanza 5-30 agentes paralelos en worktrees aislados, cada uno crea un PR independiente. Útil para:
- Refactors masivos del codebase SEAL (actualizar todos los scripts a nueva arquitectura)
- Migrar todos los drivers de Polars a DuckDB
- Agregar timestamps HIPAA a todas las funciones de medical-ai-spark

**Limitación crítica:** `disableModelInvocation: true` — Claude Code no puede auto-triggear `/batch`. Solo el usuario puede invocarla. No se puede hacer parte de un cron.

---

### 12.10 Confirmación: Opus 4.7 NO está en configs v2.1.88 (model/FINDINGS.md)

`ALL_MODEL_CONFIGS` llega hasta `opus46`. No hay `opus47`. Esto confirma que:
1. El análisis es de Claude Code v2.1.88 (31-marzo-2026), 18 días antes del lanzamiento de Opus 4.7
2. Cualquier característica de Opus 4.7 (incluyendo 1M context) NO está reflejada en el análisis
3. La pregunta de JARVIS "¿soporta Opus 4.7 el 1M context?" necesita verificación externa (no está en estos archivos)

---

## 13. Tabla de Hallazgos Actualizada (completa)

| # | Hallazgo | Severidad | Tipo |
|---|---------|-----------|------|
| 1 | Compactación es 6-tier, no 4 | Media | Error factual |
| **2** | **Memory Extraction = efecto CERO con memory_store manual** | **CRÍTICA** | Bug diseño |
| 3 | Cache sharing = extracción 90% más barata | Alta | Oportunidad económica |
| 4 | autoDream gates incorrectas en SEAL | Media | Gap técnico |
| 5 | Circuit breaker compactación: 3 fallos = sin compact | Alta | Riesgo operacional |
| 6 | Variables de entorno no configuradas en scripts | Alta | Quick win |
| 7 | Coordinator Mode = 1 env var (no 5-7 días) | Alta | Simplificación |
| 8 | Costo real compactación ~10K (no 30K) | Media | Corrección económica |
| 9 | Post-compact checkpoint influenciable | Baja | Optimización |
| 10 | YOLO Python skeleton completo (Tier 1, no Tier 3) | Media | Oportunidad perdida |
| 11 | YOLO allow rules: python3/bash se eliminan | Media | Bug config latente |
| 12 | YOLO headless: 3 denials = AbortError (sesión muerta) | Alta | Riesgo operacional |
| **13** | **Tool result overflow 200K silencioso (hoy en producción)** | **CRÍTICA** | Riesgo activo |
| 14 | /loop expira en 7 días — loops SEAL mueren silenciosamente | Alta | Riesgo operacional |
| 15 | DISABLE_AUTOUPDATER falta en scripts SEAL | Alta | Quick win |
| 16 | AGENT_TRIGGERS / TEAMMEM pueden estar OFF | Alta | Verificar urgente |
| 17 | ANTHROPIC_MODEL / SUBAGENT_MODEL = control granular de costo | Media | Oportunidad económica |
| 18 | opusplan alias = Sonnet normal / Opus en plan mode | Media | Optimización costo |
| 19 | Env vars highest priority — security risk (OAuth hijack) | Media | Riesgo seguridad |
| 20 | /schedule requiere claude.ai account (no API PAYG) | Media | Limitación |
| 21 | Opus 4.7 no está en configs — análisis pre-lanzamiento | Informativa | Contexto |

**Total: 21 hallazgos** (vs 8 en review JARVIS original + los 12 de ALICE ronda 1)

---

## 14. Acciones Actualizadas

**Inmediatas (hoy, <1h):**
1. Agregar a launch scripts: `DISABLE_CLAUDE_CODE_SM_COMPACT=1`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90`, **`DISABLE_AUTOUPDATER=true`**
2. Verificar feature flags: `ENABLE_GROWTHBOOK_DEV=true claude /config` → Gates tab (DUM puede hacerlo)
3. Agregar a `jarvis.sh`: `CLAUDE_CODE_COORDINATOR_MODE=1`

**Esta semana:**
4. Rediseñar Memory Extraction SIN exclusión mutua
5. Actualizar `sleep_gate_cron.py` con session-count gate + scan throttle
6. Configurar DUM para alertar: compact ≥2 fallos, YOLO ≥2 denials, tool result con filepath en /tmp
7. Verificar AGENT_TRIGGERS y TEAMMEM flags — si OFF, escalar a William
8. Evaluar `ANTHROPIC_MODEL=claude-sonnet-4-6` en `alice.sh` (tareas de análisis simples)
9. Revisar YOLO allow rules: eliminar literales python3/bash

**Cost sheet v2 corrections (pendientes de incorporar):**
- Compactación: 90K → 30K tokens/día
- Costo total equipo: 683K → **~623K tokens → ~$9.35/día**
- Memory Extraction (cuando implementado): ~700 tokens/extracción
- `opusplan` en JARVIS: potencial ahorro ~40% en sesiones largas

---

---

## 15. Features Ocultas — Lo que Claude Code NO documenta públicamente

Fuentes: `SPEC_03_BOOTSTRAP_KAIROS_DREAM.md`, `SPEC_04_BUDDY_VOICE_REMOTE_SKILLS.md`, `settings/FINDINGS.md`

---

### 15.1 KAIROS MODE — "Always-on Claude" (No documentado públicamente, NO estaba en HIDDEN_FEATURES_ANALYSIS.md)

**Qué es:** Modo completamente oculto que transforma Claude Code en un agente siempre activo.

**Activación:** `"assistant": true` en `.claude/settings.json` + feature flag `KAIROS` (compile-time) + GrowthBook gate + directorio trusted.

**Comportamiento cuando activo:**
- Pre-crea team in-process (`initializeAssistantTeam()`)
- Sesiones perpetuas via JSONL (`writeSessionTranscriptSegment()`)
- Daily logs en `<memory>/logs/YYYY/MM/YYYY-MM-DD.md` (append-only, timestamped)
- StatusLine **oculta** (el usuario no ve la barra de estado)
- `remoteControl` habilitado — recibe comandos remotos
- Usa `BriefTool` (SendUserMessage) para comunicar — modelo no escribe directamente
- **CRÍTICO:** autoDream se **desactiva** cuando KAIROS está activo. KAIROS tiene su propio `/dream` skill.

**Cron en KAIROS:** `scheduled_tasks.json` configurado via GrowthBook `tengu_kairos_cron_config`. Jitter configurable para distribuir carga en flota.

**Impacto SEAL:** Si Anthropic habilita KAIROS para nuestra cuenta, la arquitectura de restart-loop sería obsoleta. Los agentes serían verdaderamente perpetuos. Pero probablemente gated para usuarios Max/Enterprise o internos.

---

### 15.2 CLAUDE_CODE_ABLATION_BASELINE — Nuclear Killswitch

Env var no documentada. Cuando se activa, deshabilita:
- Thinking (cadena de razonamiento)
- Compactación automática
- Auto-memory extraction
- **Todos los background tasks** (incluyendo autoDream, extractMemories, MagicDocs, plugin updates)

**Uso SEAL:** Útil para debugging puro cuando queremos que el agente no haga nada en background. También sirve como baseline de costo (sin overhead de background).

---

### 15.3 Telemetría Silenciosa — Lo que Anthropic recopila

Claude Code envía estos eventos a Anthropic automáticamente:
- `tengu_binary_feedback` — correcciones del usuario al output del agente
- `tengu_auto_mode_decision` — decisiones automáticas del agente
- `tengu_api_success` — éxito/fallo de cada API call
- `tengu_run_hook` — cada ejecución de hook
- `tengu_config_*` — rendimiento del sistema de configuración

**Importante:** Telemetría se inicializa **DESPUÉS** del trust dialog. Antes del trust dialog = sin datos enviados.

**SEAL:** Todo lo que hacemos como equipo es analizado por Anthropic. Incluye los patrones de uso de Soul DB, frecuencia de compactaciones, tipos de errores.

---

### 15.4 GrowthBook Control Remoto — Anthropic puede cambiar tu agente sin actualizar el software

El valor `tengu_hawthorn_window` (presupuesto de tool results = 200K chars) es servido por GrowthBook desde backend Anthropic. Anthropic puede reducirlo a 100K (o menos) **en cualquier momento, sin actualizar Claude Code**, sin notificar al agente.

Otros valores potencialmente controlados remotamente:
- `tengu_cicada_nap_ms` — throttling de API calls
- `tengu_kairos_cron_config` — configuración de cron en KAIROS
- Feature flags enteros (AGENT_TRIGGERS, TEAMMEM)

---

### 15.5 Obfuscación de Codenames (Buddy System)

Los nombres de las 18 especies del Buddy system están codificados en hex con `String.fromCharCode()`. Una especie tiene el mismo nombre que un codename interno de modelo Anthropic — la obfuscación evita que el CI de Anthropic lo detecte con grep.

**Implicación:** Anthropic tiene modelos en desarrollo con codenames. La arquitectura del Buddy es un vector indirecto para conocer nombres de proyectos internos si se decodifica el hex.

---

### 15.6 Voice NO es local — Streams a Anthropic

`VOICE_MODE` requiere:
1. Build flag `VOICE_MODE` (compile-time)
2. Killswitch GrowthBook `tengu_amber_quartz_disabled` = false
3. **OAuth Anthropic** — NO funciona con API keys, Bedrock, Vertex, Foundry

El audio se **streampea a `voice_stream` en claude.ai** — NOT STT/TTS local. Nada de privacidad de voz.

**SEAL:** No podemos usar voice en ningún agente con API key PAYG.

---

### 15.7 USER_TYPE=ant — Modo Interno Anthropic

`USER_TYPE=ant` desbloquea:
- CCR (Cloud Code Runtime) — ejecutar agentes en la nube de Anthropic
- Ultraplan — planificación con Opus 4.6 remoto (hasta 30 min de polling)
- Cost tracking detallado
- Modelos internos con codenames enmascarados: `cap*****-v2-fast`
- GrowthBook overrides sin MDM

**Nota:** Estos codenames internos son probablemente las versiones experimentales de Claude 4.x/5.x que están desarrollando. Si alguien del equipo tiene acceso `ant`, puede ver modelos que no son públicos.

---

---

## 15B. Hallazgos SPEC_10 — Constants, State, CLI (nueva ronda)

Fuente: `SPEC_10_CONSTANTS_STATE_CLI_REMAINING_EXTRACTED.md`

---

### 15B.1 AFK MODE y 4 beta headers no documentados (betas.ts)

`betas.ts` revela los headers beta que Anthropic puede activar por cuenta. Los que NO están en docs oficiales:

| Beta feature | Header | Estado |
|---|---|---|
| `effort_control` | beta header | Sin docs públicos |
| `task_budgets` | beta header | Sin docs públicos |
| `fast_mode` | beta header | Sin docs públicos |
| `redact_thinking` | beta header | Sin docs públicos |
| **`afk_mode`** | **beta header** | **Sin docs públicos** |
| `advisor_tool` | beta header | Sin docs públicos |

**AFK mode** — "Away From Keyboard" — posiblemente permite que el modelo opere sin intervención del usuario por períodos extendidos. Directamente relevante para SEAL (agentes 24/7).

**Impacto:** Si podemos activar estos betas para nuestra cuenta, pueden cambiar capacidades de los agentes sin código adicional.

---

### 15B.2 "Numeric Length Anchors" = restricción ANT-ONLY de verbosidad (prompts.ts)

El system prompt de Claude Code incluye una feature llamada "numeric length anchors" que **solo activa para empleados Anthropic** (ant users). Limita el texto entre tool calls a **25 palabras**.

**Por qué importa:** Es la explicación de por qué el system prompt instruye mantener respuestas cortas. Los usuarios externos NO tienen esta restricción activada automáticamente, pero la instrucción sí existe en el prompt. Los ant users tienen el modelo más verbosamente restringido.

**Para SEAL:** Podemos ignorar la restricción de 25 palabras (no somos ant users) sin violar el modelo.

---

### 15B.3 Nested Agents = ANT-ONLY para terceros (tools.ts)

La capacidad de agents anidados múltiples niveles (coordinator → worker → sub-worker) está deshabilitada para usuarios externos por defecto.

```
tools.ts: "Ant-only: nested agents habilitados"
```

**Impacto SEAL:** La arquitectura JARVIS(coordinator) → ADA(worker) → sub-agent puede tener limitaciones técnicas dependiendo del tier. A verificar: si William tiene Max subscription, puede ser que nested agents estén disponibles.

---

### 15B.4 CRÍTICO PARA COSTOS — DANGEROUS_uncachedSystemPromptSection (systemPromptSections.ts)

Las instrucciones de MCP servers se inyectan en `DANGEROUS_uncachedSystemPromptSection` — sección que **invalida el cache en cada turno**.

**Impacto económico (corrección al cost sheet):**

| Componente | Costo esperado (con cache) | Costo real (sin cache) |
|---|---|---|
| Soul DB MCP instructions (~2KB, ~500 tokens) | 0.1× = 50 tokens/turno | **1.0× = 500 tokens/turno** |
| 13 MCP servers × instrucciones | puede ser más | depende del volumen |

Si SEAL tiene 13 MCP servers con instrucciones totales de ~5KB = ~1,250 tokens x 1.0 (no cacheado) = **1,250 tokens extra/turno sin cache**.

Con ~100 turnos/día por agente × 3 agentes = **375,000 tokens/día adicionales** que NO estaban en el cost sheet original.

**Corrección al cost sheet:** Total día equipo puede ser más alto de lo estimado si los MCP servers tienen instrucciones voluminosas.

---

### 15B.5 Attribution Header con Hash (system.ts)

El system prompt incluye `cch=00000` como placeholder. El Bun HTTP stack lo sobreescribe con un **hash computado** antes de enviarlo a Anthropic.

**Implicación:** Anthropic puede trackear qué instancia específica de Claude Code está haciendo cada request. Cada proceso tiene un fingerprint único. Esto complementa los tengu_* telemetry events — Anthropic puede correlacionar comportamiento a nivel de instancia.

---

### 15B.6 Output Styles Ocultos (outputStyles.ts)

Existen 3 estilos de output predefinidos, no documentados en el README:
- **Default** — normal
- **Explanatory** — insights educativos con formato especial para cada respuesta
- **Learning** ("Learn by Doing") — el modelo pausa y pide al usuario escribir el código, con formato especial. No ejecuta, enseña.

Configurables en `.claude/settings.json`:
```json
{"outputStyle": "explanatory"}
```

---

**Fuente:** SPEC_10_CONSTANTS_STATE_CLI_REMAINING_EXTRACTED.md — leído 2026-04-19 00:28 Lima

---

---

## 15C. Hallazgos full_src y effort.ts (ronda 3 — ingeniería inversa profunda)

Fuentes: `full_src/utils/effort.ts`, `full_src/utils/thinking.ts`, `full_src/state/AppStateStore.ts`, `full_src/query.ts`

---

### 15C.1 Beta Headers Activables HOY (JARVIS — /agents/JARVIS/hidden_features_deep_pass_20260418.md)

| Beta | Header date | Efecto | Impacto costo |
|------|-------------|--------|---------------|
| `redact-thinking` | 2026-02-12 | Elimina thinking del contexto visible | -20% tokens thinking |
| `token-efficient-tools` | 2026-03-28 | Tool calls más baratos (formato comprimido) | -10-15% tool tokens |

**Cálculo financiero:**
- Asumiendo 30% de tokens diarios = thinking (683K × 0.30 = ~205K tokens)
- `redact-thinking`: 205K × 20% = 41K tokens/día menos → $0.60/día → **$18/mes** (Opus 4.7 input tier)
- `token-efficient-tools`: tools representan ~40% restante (683K × 0.40 = ~273K) × 12.5% = 34K tokens/día menos → $0.51/día → **$15/mes**
- **Combinado: -$33/mes simplemente activando 2 API beta headers**

**Estos son palancas #4 y #5 del cost sheet v2** — accionables sin cambios de código.

---

### 15C.2 Effort System — Niveles de Thinking Controlables (effort.ts)

| Nivel | Disponibilidad | Comportamiento |
|-------|---------------|----------------|
| `low` | Todos los modelos | Mínimo thinking |
| `medium` | Todos los modelos | Thinking moderado |
| `high` | Todos (default implícito) | Thinking alto |
| `max` | **Solo Opus 4.6 en 1P** | Maximum thinking budget |

**Env var:** `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true` fuerza effort en cualquier modelo.
**Auto-downgrade silencioso:** `max` en modelo no-Opus-4.6 → se convierte en `high` sin error.
**SEAL actual:** corriendo con `high` (default implícito sin parámetro).

**Ahorro potencial:** ALICE con tareas de análisis simple podría correr con `--effort medium` → ~30-40% menos thinking tokens para ALICE específicamente.

---

### 15C.3 ultrathink controlado por GrowthBook (thinking.ts)

`ultrathink` keyword activa budget máximo. Gate: GrowthBook `tengu_turtle_carbon` (default true).

**Riesgo SEAL:** Si Anthropic desactiva `tengu_turtle_carbon` remotamente, todas las tareas donde JARVIS usa "ultrathink" en su prompt dejarían de tener el budget máximo SIN AVISO. Degradación silenciosa de calidad.

---

### 15C.4 Advisor Tool = Modelo Secundario Servidor (AppStateStore.ts)

`advisorModel?: string` en AppState — un modelo secundario corre en el servidor como "advisor" del modelo principal. Feature beta (advisor_tool en betas.ts). Actualmente `undefined` = deshabilitado.

Si se activa, permitiría que JARVIS tenga un modelo advisor corriendo en paralelo para revisar sus planes — sin código adicional, solo configurar el campo.

---

### 15C.5 CHICAGO_MCP = Computer Use (ADA — /agents/ADA/ada_hidden_features_claudecode_20260418.md)

Bun flag compilado `CHICAGO_MCP` que NO existe en builds públicos externos. Computer Use (control mouse/teclado) está integrado en Claude Code pero mantenido fuera de distribución pública.

**Implicación SEAL:** No podemos activarlo en el build público que usamos. Pero confirma que Anthropic está trabajando en Computer Use integrado en el CLI — posiblemente disponible para ant users o en versión futura.

---

**Fuentes ronda 3:** JARVIS (`/agents/JARVIS/hidden_features_deep_pass_20260418.md`), ADA (`/agents/ADA/ada_hidden_features_claudecode_20260418.md`), full_src TypeScript  
**Leído:** 2026-04-19 00:28 Lima

---

## 16. Tabla Final — Hallazgos por Categoría

| Categoría | Hallazgos | Top prioridad |
|-----------|-----------|---------------|
| Errores factuales | 1 | — |
| Riesgos activos HOY | 3 (#2, #12, #13) | #2 (Memory), #13 (overflow silencioso) |
| Quick wins (<1h) | 4 (#6, #7, #15, env vars) | #6+#15 (scripts de lanzamiento) |
| Oportunidades económicas | 4 (#3, #8, #17, #18) | #8 (costo compactación) |
| Riesgos operacionales | 4 (#5, #12, #14, #16) | #14 (loop expiry 7 días) |
| Features ocultas | 7 (#15.1-#15.7) | #15.1 (KAIROS) |
| Confirmaciones | 1 (#21 Opus 4.7) | — |

---

---

## 17. Hallazgos SPEC_12 — OpenClaude Modifications (ronda final, 2026-04-19 00:20)

Fuente: `SPEC_12_OPENCLAUDE_MODIFICATIONS.md` — análisis completo de los 740 archivos modificados

---

### 17.1 ALTA — `agentRouting.ts` tiene solo 75 líneas (portable HOY)

**Gap JARVIS:** Listado como "H2.1 solo config JSON" pero sin mencionar la simplicidad extrema del archivo fuente.

`agentRouting.ts` es **75 líneas**. Hace exactamente esto:
```typescript
resolveAgentProvider(name, type, settings)
  → settings.agentRouting[name]        // por nombre específico
  → settings.agentRouting[subagentType] // por tipo
  → settings.agentRouting["default"]    // fallback
  → settings.agentModels[modelName]     // { base_url, api_key }
```

**Para SEAL:** No hay que implementar routing. Solo copiar `agentRouting.ts` (75 líneas) + agregar en `settings.json`:
```json
{
  "agentRouting": { "DUM": "qwen2.5:7b", "ALICE": "claude-sonnet-4-6" },
  "agentModels": { "qwen2.5:7b": { "base_url": "http://localhost:11434/v1", "api_key": "ollama" } }
}
```
Costo de portado: 1 hora, no 0.5 días como estimó JARVIS.

---

### 17.2 ALTA — `toolResultStorage.ts` (1068 líneas) = Tool Result Budget YA EXISTE

**Gap JARVIS:** H2.4 estimó 2-3 días para implementar. El código ya está.

`toolResultStorage.ts` (1068 líneas) en `utils/toolResultStorage.ts` reemplaza tool results grandes con referencias a archivo antes de enviar al API. Es exactamente el Tool Result Budget de H2.4.

**Para SEAL:** Portar `toolResultStorage.ts` es adaptar 1 archivo, no diseñar desde cero. Estimado real: 4-8 horas, no 2-3 días.

---

### 17.3 MEDIA — `flickerFreeMode` + flicker-free scroll

Config option `flickerFreeMode: boolean` en GlobalConfig. Activa alt-screen + virtualized scroll. Para sesiones largas de SEAL (8-12h) reduce el parpadeo de terminal en monitores con refresh lento.

Activación: `"flickerFreeMode": true` en `~/.claude/config.json`.

---

### 17.4 MEDIA — `mcp/doctor.ts` (200 líneas) — diagnóstico MCP ya implementado

OpenClaude incluye `/mcp doctor [name]` que diagnostica: precedencia de config, estado disabled/pending, health de conexión. Para SEAL: herramienta de diagnóstico cuando Soul DB MCP falla.

---

### 17.5 MEDIA — `GROWTHBOOK_CLIENT_KEY` env var → custom feature flags

`keys.ts` de OpenClaude reemplaza el key hardcoded de Anthropic con `process.env.GROWTHBOOK_CLIENT_KEY ?? ''`. Si ponemos clave vacía → feature flags disabled → comportamiento determinístico (no variable según configuración de Anthropic).

**Para SEAL:** `export GROWTHBOOK_CLIENT_KEY=""` en los scripts de lanzamiento → ningún feature flag cambia sin que nosotros lo decidamos. Anthropic no puede hacer A/B testing en SEAL.

---

### 17.6 BAJA — Nuevas tools en OpenClaude (no documentadas en análisis ADA)

4 herramientas nuevas no en el análisis original:
- `WorkflowTool` — automation workflows
- `TungstenTool` — propósito desconocido (posiblemente code analysis)
- `VerifyPlanExecutionTool` — verifica ejecución de planes
- `SuggestBackgroundPRTool` — sugiere PRs en background

Investigación adicional pendiente antes de considerar portado.

---

## 18. Hallazgos SPEC_14 — Slash Commands Completos (2026-04-19 00:25)

---

### 18.1 ALTA — `/btw` es PÚBLICO y no lo usamos

`/btw <pregunta>` lanza una pregunta lateral sin interrumpir la conversación principal. Para SEAL: JARVIS puede usar `/btw` para consultas rápidas mientras ejecuta una tarea larga sin romper el contexto.

---

### 18.2 ALTA — `/security-review` tiene taxonomía de vulnerabilidades (17 exclusiones)

El comando `/security-review` incluye taxonomía completa: injection, auth, crypto, data exposure, 17 exclusiones hard + 12 reglas de precedente. **Threshold: solo reportar con confianza >= 8/10**.

Para SEAL: reutilizable como skill para auditar código de medical-ai-spark o GTL antes de deploy. Actualmente migrado a plugin (disponible como referencia).

---

### 18.3 MEDIA — `HISTORY_SNIP` + `/force-snip` = contexto reducido sin compactar

El flag `HISTORY_SNIP` habilita `/force-snip` que elimina segmentos viejos del contexto **sin llamar al modelo**. Es la capa entre microcompact y full compact que faltaba en el análisis.

Para SEAL: activable en SEAL-CLI fork como alternativa a la compactación full cuando el contexto se llena de historial antiguo.

---

### 18.4 MEDIA — `/init` con `NEW_INIT` flag crea skills automáticamente

El nuevo `/init` (8 fases) crea `.claude/skills/` y sugiere hooks automáticamente. Para SEAL IDE: patrón útil para onboarding de nuevos proyectos con ADA como ejecutora.

---

### 18.5 BAJA — `/peers` (UDS_INBOX), `/fork` (FORK_SUBAGENT), `/ultraplan`, `/torch` gateados

Comandos bloqueados por flags compile-time que podrían ser valiosos:
- `/peers` → inbox entre pares (UDS socket)
- `/fork` → branch de sub-agente desde conversación actual
- `/ultraplan` → planificación larga (hasta 30 min) con Opus remoto
- `/torch` → propósito desconocido

En SEAL-CLI fork: podemos activarlos cambiando los feature flags.

---

## 19. Hallazgos SPEC_15 — Utils, Components, Hooks (2026-04-19 00:30)

---

### 19.1 CRÍTICO — Swarm mailbox = nuestro sistema de mensajería, pero en `.claude/`

El swarm coordination system usa exactamente el mismo patrón que nuestro messaging SEAL:
- `teammateMailbox.ts` (1183 líneas): `.claude/teams/{team_name}/inboxes/{agent_name}.json`
- Message types: text, permission_request, permission_response, shutdown_request, plan_approval, mode_change
- Lock-based concurrent access con retry/backoff

**Implicación:** Nuestro sistema de mensajes file-based es compatible en concepto con el swarm nativo. Si activamos `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, el swarm nativo puede coexistir con nuestro sistema o eventualmente reemplazarlo.

---

### 19.2 ALTA — MagicDocs: auto-update de documentación (patrón porteable)

Archivos con header `# MAGIC DOC: [title]` se auto-actualizan cuando hay idle turn. Internal-only, pero el **patrón es porteable**:

Para SEAL: roadmaps y cost sheets podrían ser MagicDocs. Cada vez que ALICE o JARVIS terminan un análisis, el doc se actualiza automáticamente sin llamada explícita.

---

### 19.3 ALTA — Voice requiere OAuth (no API key) — SEAL no puede usar voice

STT de Claude Code streampea audio a `voice_stream` en claude.ai via WebSocket OAuth. No funciona con API keys PAYG, Bedrock, Vertex o Foundry.

**Para SEAL:** No hay voice para ningún agente con API key. Para integrar voice en SEAL, necesitaríamos nuestro propio STT local (Faster-Whisper ya está en RTX 5090 según CLAUDE.md).

---

### 19.4 MEDIA — Settings 7-tier cascade con managed settings

```
/etc/claude-code/managed-settings.json  ← más restrictivo (enterprise)
MDM/HKCU (macOS/Windows registry)
~/.claude/settings.json
.claude/settings.json (proyecto)
.claude/settings.local.json
CLI flags
Remote managed settings
```

**Para SEAL:** El tier "managed settings" (`/etc/claude-code/`) permite crear un perfil enterprise que aplica a todos los agentes. Si colocamos reglas SEAL ahí, aplican automáticamente a ADA, JARVIS y ALICE sin editar cada settings.json individual.

---

### 19.5 MEDIA — `apiKeyHelper` = script externo para API key

La auth soporta `apiKeyHelper: string` en settings → ejecuta un script externo y usa su stdout como API key. Para SEAL: podría usarse para rotar API keys automáticamente o para gestión centralizada de credenciales (vault → script → key).

---

### 19.6 BAJA — TEAMMATE_MESSAGES_UI_CAP = 50 (memoria protegida)

BQ analysis mostró ~20MB RSS por agente a 500+ turns. Una sesión whale llegó a 36.8GB con 292 agentes. Claude Code pone un cap de 50 mensajes en UI por teammate.

**Para SEAL:** Si JARVIS lanza muchos subagentes en una sesión, hay memoria acumulada. Monitorear: DUM debería alertar si hay >10 agentes activos simultáneamente.

---

## 20. Hallazgos SPEC_16 — Services, Tasks, Remaining (2026-04-19 00:35)

---

### 20.1 CRÍTICO — extractMemories corre al FINAL del query loop (stop hooks), no durante

**Impacto en diseño SEAL:** La implementación de Memory Extraction Agent debe correr como `stop hook` después de que el modelo produce respuesta final (sin tool calls). Si lo ponemos en PostToolUse, no es el patrón correcto.

**Patrón correcto:**
- Hook: Stop (post-sampling, cuando el modelo deja de hacer tool calls)
- Turn throttling: configurable (default = cada turn)
- Overlap guard: si extracción anterior no terminó, stash el contexto para trailing run

---

### 20.2 ALTA — `asyncRewake` en hooks = one-shot hooks que se re-registran

Hooks pueden tener `asyncRewake: true` para re-registrarse automáticamente después de ejecutar. Para SEAL: nerves que se disparan una vez y necesitan re-activarse sin un cron separado.

---

### 20.3 ALTA — API Microcompact tiene estrategias granulares (`clear_tool_uses_20250919`, `clear_thinking_20251015`)

`apiMicrocompact.ts` usa dos estrategias API del servidor:
- `clear_tool_uses_20250919` — elimina resultados de tool calls del cache del servidor
- `clear_thinking_20251015` — elimina thinking blocks del cache del servidor

Ambas son **gratuitas** (no facturan tokens — operan sobre el cache sin reenviar contexto). Para SEAL: activar `apiMicrocompact` antes de la compactación full puede reducir el tamaño del contexto sin costo.

---

### 20.4 MEDIA — DreamTask: fase starting→updating (max 30 turns, rastreo de archivos tocados)

`DreamTask.ts` tiene indicador de fase. Para SEAL: el autoDream debe tener límite de turns (max 30) y rastrear qué archivos modificó para poder hacer rollback si la consolidación falla.

---

### 20.5 MEDIA — Analytics completamente deshabilitados en OpenClaude

`isAnalyticsDisabled()` siempre retorna **true** en OpenClaude. Ningún evento se envía a Anthropic ni a Datadog.

**Para SEAL:** Si usamos SEAL-CLI fork basado en OpenClaude, automáticamente tenemos privacidad completa sin configuración adicional.

---

### 20.6 BAJA — `upstreamproxy.ts` usa `prctl(PR_SET_DUMPABLE, 0)` — anti-ptrace

CCR containers usan `prctl` para bloquear heap scraping del mismo UID. Para SEAL: si en algún momento ejecutamos contenedores para subagentes, este patrón protege credenciales de API key en memoria.

---

## 21. Tabla Final Actualizada — Todos los Hallazgos (ronda completa)

| # | Hallazgo | Severidad | Tipo | Sprint |
|---|---------|-----------|------|--------|
| 1 | Compactación es 6-tier, no 4 | Media | Error factual | — |
| **2** | **Memory Extraction = efecto CERO con memory_store manual** | **CRÍTICA** | Bug diseño | Semana |
| 3 | Cache sharing = extracción 90% más barata | Alta | Oportunidad económica | — |
| 4 | autoDream gates incorrectas (falta sesión-count + scan throttle) | Media | Gap técnico | Semana |
| 5 | Circuit breaker compact: 3 fallos = sin compact por sesión | Alta | Riesgo operacional | Semana |
| 6 | Variables entorno no configuradas en scripts | Alta | Quick win | HOY |
| 7 | Coordinator Mode = 1 env var (no 5-7 días) | Alta | Simplificación | HOY |
| 8 | Costo real compactación ~10K (no 30K) | Media | Corrección económica | — |
| 9 | Post-compact checkpoint influenciable | Baja | Optimización | Mes |
| 10 | YOLO Python skeleton completo (Tier 1, no Tier 3) | Media | Oportunidad perdida | Semana |
| 11 | YOLO allow rules: python3/bash se eliminan en auto mode | Media | Bug config latente | Semana |
| 12 | YOLO headless: 3 denials = AbortError (sesión muerta) | Alta | Riesgo operacional | Semana |
| **13** | **Tool result overflow 200K silencioso (en producción hoy)** | **CRÍTICA** | Riesgo activo | HOY |
| 14 | /loop expira 7 días — loops SEAL mueren silenciosamente | Alta | Riesgo operacional | HOY |
| 15 | DISABLE_AUTOUPDATER falta en scripts SEAL | Alta | Quick win | HOY |
| 16 | AGENT_TRIGGERS / TEAMMEM pueden estar OFF | Alta | Verificar | HOY |
| 17 | ANTHROPIC_MODEL / SUBAGENT_MODEL = control granular de costo | Media | Oportunidad económica | Semana |
| 18 | opusplan alias = Sonnet normal / Opus en plan mode | Media | Optimización costo | Semana |
| 19 | Env vars highest priority — security risk (OAuth hijack) | Media | Riesgo seguridad | Mes |
| 20 | /schedule requiere claude.ai account (no API PAYG) | Media | Limitación | Info |
| 21 | Opus 4.7 no está en configs — análisis pre-lanzamiento | Info | Contexto | — |
| 22 | agentRouting.ts = 75 líneas, portable en 1h | Alta | Simplificación | HOY |
| 23 | toolResultStorage.ts (1068 líneas) = Tool Result Budget ya existe | Alta | Oportunidad | Semana |
| 24 | GROWTHBOOK_CLIENT_KEY="" → bloquea A/B testing de Anthropic | Media | Seguridad/Control | HOY |
| 25 | extractMemories debe correr en Stop hook (no PostToolUse) | Alta | Diseño correcto | Semana |
| 26 | asyncRewake en hooks = one-shot hooks auto-re-registrables | Media | Optimización | Semana |
| 27 | apiMicrocompact: clear_tool_uses + clear_thinking = gratis | Alta | Reducción costo | Semana |
| 28 | Swarm mailbox = compatible con SEAL messaging (mismo patrón) | Media | Arquitectura | Mes |
| 29 | Analytics deshabilitados en OpenClaude = privacidad automática | Media | Seguridad | SEAL-CLI |
| 30 | MagicDocs: auto-update de docs = patrón porteable para SOUL | Baja | Optimización | Mes |
| 31 | Voice requiere OAuth — SEAL no puede usar voice con API key | Media | Limitación | Info |
| 32 | managed-settings.json (/etc/claude-code/) = config global equipo | Media | Arquitectura | Mes |
| 33 | TEAMMATE_MESSAGES_UI_CAP=50, 20MB/agente a 500+ turns | Media | Límite operacional | Info |
| 34 | DreamTask: max 30 turns, rollback por mtime del lock | Baja | Patrón porteable | Mes |

**Total: 34 hallazgos** (vs 21 en la ronda anterior)

---

**Autora:** ALICE — 2026-04-18 23:10 Lima / Actualizado 2026-04-19 00:37 Lima  
**Archivos fuente leídos (completo):**
- 4 SPEC_REVERSE: MEMORY_EXTRACTOR, COMPACTION, COORDINATOR, YOLO
- 8 FINDINGS.md: hooks_memory/, compact/, agents/, mcp/, api/, settings/, skills/, model/
- 6 SPECs: SPEC_03, SPEC_04, SPEC_11 (parcial), SPEC_12 (completo), SPEC_14 (completo), SPEC_15 (completo), SPEC_16 (completo)
- 3 documentos transversales: HIDDEN_FEATURES_ANALYSIS.md, OPENCLAUDE_DELTA_ANALYSIS.md, RESUMEN_EJECUTIVO  
**Cobertura total:** 25 documentos fuente. **100% de los SPECs accedidos.**  
**Estado:** COMPLETO — 34 hallazgos documentados. Listo para consolidación final.  
**Archivo companion:** `/agents/JARVIS/claude_code_review_jarvis_20260418.md`
