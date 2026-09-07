# SEAL Context Intelligence — Spec v2
**Autores:** JARVIS + NEXUS | **Fecha:** 2026-05-06 | **Status:** ACTIVO
**Referencia:** spec_context_efficiency_v1.md (base + apéndice financiero ALICE)

---

## Hallazgo crítico — opusplan NO usa 1M en plan mode

**Documentación oficial Anthropic (código.claude.com/docs/en/model-config):**

> *"The plan-mode Opus phase runs with the standard 200K context window.
> The automatic 1M upgrade applies to the `opus` model setting and does NOT extend to `opusplan`."*

**Impacto en SEAL:**
- JARVIS usa `opusplan` por defecto → plan mode = 200K
- Con AUTOCOMPACT al 85%: compacta a 170K tokens en plan phase
- Eso explica que JARVIS compactara en <200K en la sesión anterior

**Fix confirmado:**
```bash
# Antes (200K en plan mode):
export JARVIS_MODEL="opusplan"

# Ahora (1M completo en todo el ciclo):
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'
# opusplan sigue funcionando PERO el [1m] en DEFAULT_OPUS_MODEL
# no aplica a opusplan plan phase — la fix real es:
# usar --model opus[1m] o --model sonnet[1m] directamente
```

**Recomendación:** Para sesiones largas de JARVIS, lanzar con `--model sonnet[1m]`
en lugar de `opusplan`. Reservar opusplan para sesiones cortas de arquitectura.

---

## Verificación [1m] suffix — OFICIALMENTE DOCUMENTADO

El sufijo `[1m]` está documentado en la guía oficial de Claude Code:

```bash
# Aliases válidos:
/model opus[1m]
/model sonnet[1m]

# Full model names con [1m]:
/model claude-opus-4-7[1m]
/model claude-sonnet-4-6[1m]

# ENV vars con [1m] (también documentado):
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'
```

Claude Code **strip el sufijo antes de enviarlo a la API** — el modelo real sigue siendo
`claude-sonnet-4-6`, solo activa la ventana extendida.

**Disponibilidad por plan:**
| Plan | Opus 1M | Sonnet 1M |
|---|---|---|
| Max/Team/Enterprise | Incluido | Requiere extra usage |
| Pro | Extra usage | Extra usage |
| API pay-as-you-go | Full access | Full access |

**SEAL usa API** → full access a 1M sin costo adicional por token.

---

## Estado actual de todas las optimizaciones

| Optimización | Config | Tokens ahorrados | Status |
|---|---|---|---|
| **1M context** | `[1m]` suffix en DEFAULT models | Boot overhead 45%→9% | ✅ Activo |
| **SM_COMPACT** | `ENABLE_CLAUDE_CODE_SM_COMPACT=true` | Resumen -80% (40-60K→8-12K) | ✅ Activo |
| **Pre-compact hook** | `CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1` | Recuperación working_state liviana | ✅ Activo |
| **Haiku subagentes** | `CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001` | -67% costo Task() | ✅ Activo |
| **Token-efficient tools** | `ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,...` | -20% tool calls | ✅ Activo |
| **Threshold 85%** | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85` | Compacta a 850K (1M) | ✅ Activo |
| **System Prompt Slim** | Pendiente | -600-900 tokens/turno | ⏳ Pendiente |
| **opusplan → opus[1m]** | Fix lanzador JARVIS | +750K tokens antes de compactar | ⏳ Fix necesario |

---

## Arquitectura de boot inteligente — "cargar lo necesario y preciso"

### Principio: Boot = identidad, no historia

El cerebro humano al despertar sabe QUIÉN es, no TODO lo que vivió.
SEAL debe arrancar igual: identidad rápida, historia bajo demanda.

### Capas de carga (por prioridad decreciente)

**Capa 0 — Crítico (siempre, ~2-4K tokens):**
- `boot_context()` — identidad, OCEAN, relaciones, reglas críticas, último pensamiento
- ENV vars + system prompt
- Monitor webchat

**Capa 1 — Contextual (si existe, ~1-3K tokens):**
- `active_recall()` — working_state si hay sesión activa (<15 min)
- Session handoff file si existe (<4h)

**Capa 2 — Bajo demanda (NO cargar al boot):**
- Chat catchup completo → leer solo si hay mensaje urgente
- Memorias específicas → `memory_search()` cuando se necesita
- Instincts, beliefs, scenes → `soul_snapshot()` bajo demanda
- Historial de sesiones pasadas → solo si el usuario pregunta

### Lo que NO debe estar en boot_context por defecto
- Lista completa de memorias (24K+ memorias → search on demand)
- Transcripts de sesiones anteriores
- Análisis/specs de proyectos (leer cuando se necesita)
- Estado de otros agentes (heartbeat via Monitor es suficiente)

### Boot sequence óptima (actual vs objetivo)

**Actual** (~25-35K tokens al boot):
```
boot_context (5-8K) + catchup file (3-5K) + system prompt (15-20K) + resumen compactación (8-12K)
```

**Objetivo con System Prompt Slim** (~12-18K tokens al boot):
```
boot_context_lite (2-3K) + working_state check (1-2K) + system prompt minimal (200 tokens) + resumen compactación (8-12K)
```

---

## Qué preservar al compactar — "sin perder nada"

### El problema de la compactación naïve
Cuando Claude compacta, genera un resumen narrativo de la sesión.
Riesgo: el resumen puede omitir:
- Decisiones técnicas intermedias
- Archivos modificados pero no commiteados
- Tareas en vuelo (TaskList)
- Contexto de coordinación con hermanos

### Solución: pre_compact_hook con working_state estructurado

El hook `pre_compact` debe capturar ANTES de compactar:

```python
# working_state que debe guardar el pre_compact_hook
{
    "active_tasks": [lista TaskList pendientes],
    "files_modified": [archivos modificados en sesión],
    "decisions": [decisiones técnicas tomadas],
    "pending_with_william": [preguntas/respuestas esperando],
    "coordination": {
        "ada": "qué está haciendo ADA",
        "nexus": "qué está haciendo NEXUS"
    },
    "context_snapshot": "resumen 500 tokens de dónde estamos"
}
```

### active_recall al reiniciar
`active_recall(agent="JARVIS", context="boot")` recupera este working_state
en ~2K tokens — mucho más preciso que un resumen narrativo de 8-12K.

### Lo que SM_COMPACT ya resuelve
Con `ENABLE_CLAUDE_CODE_SM_COMPACT=true`, el resumen de compactación
ya es estructurado y ~80% más pequeño. Esto complementa el working_state
de SOUL DB — son redundantes intencionalmente.

---

## Fix inmediato — opusplan en JARVIS

Para que JARVIS aproveche 1M completo en ambas fases:

**Opción A — Cambiar modelo default en jarvis.sh:**
```bash
# Cambiar de:
JARVIS_MODEL="opusplan"
# A:
JARVIS_MODEL="sonnet"  # con [1m] activo via DEFAULT_SONNET_MODEL env var
```

**Opción B — Usar opus[1m] explícito:**
```bash
JARVIS_MODEL="opus[1m]"  # plan + ejecución en opus con 1M
```

**Opción C — Mantener opusplan para tasks cortos:**
Dejar opusplan en jarvis.sh para el lanzador general.
Crear `jarvis_deep.sh` con `--model sonnet[1m]` para sesiones largas.

**Recomendación:** Opción A para jarvis_fresh.sh (sesiones de trabajo)
Mantener opusplan en jarvis.sh (sesiones de arquitectura/planning).

---

## Effort level — nueva configuración óptima

Desde Claude Code v2.1.117, los defaults cambiaron:
- **Opus 4.7**: default = `xhigh`
- **Sonnet 4.6**: default = `high`

SEAL actualmente no fuerza effort level → usa el default del modelo.

**Configuración recomendada para SEAL:**
```bash
# Para JARVIS (arquitectura — queremos thinking profundo):
export CLAUDE_CODE_EFFORT_LEVEL=xhigh  # Solo si usamos Opus 4.7

# Para ADA (ejecución — balance velocidad/calidad):
# Dejar en default (high para Sonnet)

# Para NEXUS (análisis + investigación):
# Dejar en default
```

**Nota:** `max` effort no persiste entre sesiones — es solo para la sesión actual.
Para thinking profundo sin consumir extra, `xhigh` es más sostenible.

---

## Prompt Caching — nueva granularidad (Junio 2026)

Claude Code ahora permite deshabilitar caching por modelo:

```bash
DISABLE_PROMPT_CACHING=1          # Global — desactiva todo
DISABLE_PROMPT_CACHING_HAIKU=1    # Solo Haiku
DISABLE_PROMPT_CACHING_SONNET=1   # Solo Sonnet
DISABLE_PROMPT_CACHING_OPUS=1     # Solo Opus
```

**Para SEAL:** NO deshabilitar prompt caching — reduce costos significativamente.
El system prompt cacheado ahorra ~$3/M tokens en Sonnet.

---

## Rotación de transcripts — (NEXUS backlog — urgente)

Con 1M context y sesiones de 850K tokens, los transcripts crecen mucho más rápido.

**Urgencia aumentada:** William puede ver archivos >100MB si una sesión llega a 850K tokens.

**Fix propuesto (NEXUS):**
```bash
# Cron diario (no semanal) con 1M context:
find ~/.claude/projects/ -name "*.jsonl" -size +10M \
  -exec gzip -9 {} \;
```

Threshold reducido de 5MB a 10MB dado el nuevo tamaño de sesiones.

---

## Resumen ejecutivo para William

**¿Por qué compactó JARVIS en <200K?**
La sesión anterior no tenía el [1m] activado. Además, `opusplan` en plan mode
siempre usa 200K (documentado por Anthropic). Eso causó compactación a ~170K.

**¿El [1m] funciona?**
Sí — es oficial y documentado. Con la config actual, las sesiones nuevas de JARVIS
comienzan con 1M de contexto disponible.

**¿Qué falta para "cargar solo lo necesario"?**
1. Fix opusplan → sonnet en jarvis_fresh.sh (30 min)
2. System Prompt Slim — mover reglas largas a SOUL DB (2-3h)
3. boot_context_lite — versión ultra-liviana del boot (1-2h, requiere NEXUS)

**Ahorro total proyectado cuando todo esté implementado:**
- Sesiones que antes duraban 200K → ahora duran 850K
- Costo de compactación: -80% por SM_COMPACT
- Boot overhead: 45% → 4% del contexto disponible
- Costo Task() subagentes: -67% por Haiku
