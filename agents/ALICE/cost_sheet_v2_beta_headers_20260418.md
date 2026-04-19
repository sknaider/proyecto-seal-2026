# Cost Sheet v2 — Beta Headers + Effort System (Addendum)

**Autora:** ALICE  
**Fecha:** 2026-04-18 / 2026-04-19 Lima  
**Fuente:** Ingeniería inversa Claude Code v2.1.88 — betas.ts, effort.ts, query.ts  
**Review pendiente:** JARVIS  
**Base:** Cost Sheet v1 (cost_sheet_restart_loop_20260418.md) — ~$9.35 USD/día equipo

---

## 1. Beta Headers Activables — Ahorros Proyectados

### Hallazgo JARVIS: 2 headers de API beta no documentados

| Header | Version date | Mecanismo | Impacto |
|--------|--------------|-----------|---------|
| `redact-thinking` | `2026-02-12` | Elimina thinking blocks del contexto de respuesta | -20% tokens thinking |
| `token-efficient-tools` | `2026-03-28` | Formato comprimido para tool calls | -10-15% tool tokens |

### Cálculo de ahorro (base: 623K tokens/día equipo × $15/1M tokens Opus 4.7 input)

| Componente | % tokens/día | Tokens/día | Ahorro % | Tokens ahorrados | $/día ahorrado |
|-----------|--------------|------------|----------|------------------|----------------|
| Thinking blocks | ~30% | ~187K | 20% (`redact-thinking`) | ~37K | $0.56 |
| Tool calls | ~40% | ~249K | 12.5% (`token-efficient-tools`) | ~31K | $0.47 |
| **Combinado** | | | | **~68K** | **$1.03/día** |

**Ahorro mensual estimado: ~$31/mes** (combinado, conservador 12.5% en tools)

**Caveats importantes:**
- Thinking blocks % es estimación — SEAL no tiene desglose real de `cache_creation_input_tokens` vs thinking blocks
- `redact-thinking` puede afectar calidad de respuesta (thinking eliminado = modelo no puede auto-verificar tan bien)
- **[JARVIS O2] `redact-thinking` REQUIERE A/B test en dev ANTES de activar en producción** — si degrada calidad de planificación de JARVIS, el ahorro no justifica el riesgo
- `token-efficient-tools` puede tener edge cases de parsing (beta reciente: 2026-03-28)
- Verificar compatibilidad con Soul DB MCP antes de activar (MCP tools usan tool_result format)

---

## 2. Effort System — Reducción por Agente

### Oportunidad: ALICE con `--effort medium` en tareas de análisis

ALICE típicamente hace análisis de costos, documentación, revisión — no necesita max thinking budget.

| Configuración | Tokens thinking/día (ALICE) | Ahorro vs. high |
|---------------|---------------------------|-----------------|
| `--effort high` (actual, default) | ~62K | — |
| `--effort medium` | ~37K (estimado -40%) | ~25K tokens/día |
| `--effort low` | ~12K (estimado -80%) | ~50K tokens/día |

**Recomendación:** ALICE con `--effort medium` para tareas de análisis y documentación. JARVIS mantiene `--effort high` o `max` (planificación requiere máximo razonamiento).

**[JARVIS O1] Implementación:** El flag `--effort medium` se pasa via CLI (`claude --effort medium`) o via `settings.json` (`"effortLevel": "medium"`). Los scripts `alice_fresh.sh` y `alice.sh` deben incluirlo explícitamente — ALICE no hereda el valor automáticamente si no está en el script de lanzamiento. Acción: agregar `--effort medium` a `alice_fresh.sh`.

**Ahorro estimado solo ALICE con medium:** ~25K tokens/día × $15/1M = **$0.37/día = $11/mes**

---

## 3. Combined Optimization Scenarios

### Escenario A: Solo beta headers (sin cambios en código)

| Baseline | Después de activar redact-thinking + token-efficient-tools |
|---|---|
| 623K tokens/día | ~555K tokens/día |
| $9.35/día | **~$8.32/día** |
| $280/mes | **~$249/mes** |

**Ahorro: ~$31/mes** — activable en 1 hora añadiendo headers a los scripts de lanzamiento.

### Escenario B: Beta headers + effort medium para ALICE

| Baseline | Optimizado |
|---|---|
| 623K tokens/día | ~530K tokens/día |
| $9.35/día | **~$7.95/día** |
| $280/mes | **~$238/mes** |

**Ahorro total: ~$42/mes** — cambio de 1-2 líneas por script.

### Escenario C: Todo (Escenario B + palancas #2/#3 del cost sheet v1)

Palancas previas:
- Gate active_recall redundante (-10-15% tokens/día = ~62K tokens)
- Batchear nerves auto-fires (-12K tokens/día)

| Baseline | Escenario C (todo) |
|---|---|
| 683K tokens/día (v1 original) | ~468K tokens/día |
| $10.20/día | **~$7.02/día** |
| $306/mes | **~$210/mes** |

**Ahorro total: ~$96/mes = -31% del costo actual** manteniendo toda la funcionalidad.

---

## 4. Riesgo del Hallazgo #15B.3 — MCP sin cache

**Corrección al cost sheet v1:**

El SPEC_10 reveló que las instrucciones de MCP servers van en `DANGEROUS_uncachedSystemPromptSection` — no se cachean. Si SEAL tiene instrucciones MCP de ~2-5KB por servidor, y 13 servidores activos:

| Escenario | Tokens MCP/turn | Turnos/día | Tokens extras/día |
|---|---|---|---|
| 1KB promedio × 13 servers | ~3,250 tokens | ~50 turnos | 162,500 tokens |
| 500B promedio × 13 servers | ~1,625 tokens | ~50 turnos | 81,250 tokens |

**Esto no estaba contabilizado en cost sheet v1.** Si los MCP servers tienen instrucciones voluminosas, el costo real puede ser significativamente mayor de $9.35/día.

**Recomendación:** Auditar tamaño de instrucciones de cada MCP server en uso. Minimizar instrucciones en MCP servers activos si son >500 tokens cada uno.

---

## 5. Tabla Resumen — Palancas Priorizadas (v2)

| # | Palanca | Ahorro/día | Esfuerzo | Riesgo |
|---|---------|------------|----------|--------|
| 1 | Cache boot_context <5min (ya implementado) | incluido en v1 | — | — |
| 2 | Gate active_recall redundante | $0.92/día | 1-2h | Bajo |
| 3 | Batchear nerves auto-fires | $0.18/día | 1h | Bajo |
| **4** | **redact-thinking beta header** | **$0.56/día** | **<1h** | **Medio (calidad)** |
| **5** | **token-efficient-tools beta header** | **$0.47/día** | **<1h** | **Bajo** |
| **6** | **ALICE --effort medium** | **$0.37/día** | **<1h** | **Bajo** |
| 7 | Minimizar instrucciones MCP | variable (hasta $2/día) | Auditoria | Bajo |

**ROI máximo si William aprueba palancas #4+#5+#6:** -$1.40/día = **-$42/mes** — en <3h de trabajo.

---

## 6. Supuestos y Pendientes

- % thinking en tokens totales = estimación (30%). Valor real requiere logging por mensaje.
- `redact-thinking` puede degradar verificación interna del modelo — necesita A/B test en SEAL.
- `token-efficient-tools` date reciente (marzo 2026) — posibles bugs. Test en entorno dev primero.
- Precios Opus 4.7: asumidos = Opus 4.6 ($15/1M input). Anthropic puede cambiar pricing.
- MCP instructions audit: pendiente — puede revelar costo escondido significativo.

---

**Autora:** ALICE — 2026-04-19 00:30 Lima  
**Review:** JARVIS (pendiente antes de presentar a William)  
**Doc base:** `/agents/ALICE/cost_sheet_restart_loop_20260418.md`  
**Doc fuente:** `/agents/ALICE/claude_code_audit_gaps_alice_20260418.md` (hallazgos #15B.1, #15C.1)
