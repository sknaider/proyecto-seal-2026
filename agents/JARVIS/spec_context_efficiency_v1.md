# SEAL Context Efficiency — Spec v1
**Autor:** JARVIS | **Fecha:** 2026-05-06 | **Status:** PROPUESTO

## Problema

Al reiniciar un agente SEAL, arranca con 35-45% del contexto ya ocupado por:
- Resumen de compactación (historia de la sesión anterior)
- System prompt largo (~15-20K tokens)
- boot_context MCP response (~5-8K tokens)
- Catchup de web_chat (~3-5K tokens)

Con contexto de 200K: el agente "nace" con 70-90K tokens consumidos antes de hacer nada.
Con contexto de 1M: eso mismo pesa 7-9% — manejable.

---

## Solución 1 — 1M Context Window (YA IMPLEMENTADO)

`export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'`
`export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'`

**Impacto:** El overhead de boot pasa de 35-45% → 7-9% del techo disponible.
**Costo:** Precio por token igual. Sin recargo.
**Status:** ✅ Activado en todos los launchers (06-may-2026)

---

## Solución 2 — Compactación eficiente (YA IMPLEMENTADO)

`export ENABLE_CLAUDE_CODE_SM_COMPACT=true`

Cuando Claude auto-compacta, genera un resumen estructurado ~80% más pequeño
que el resumen narrativo clásico. El próximo boot carga ese resumen liviano.

**Impacto:** Resumen de compactación pasa de ~40-60K → ~8-12K tokens.
**Status:** ✅ Activado en todos los launchers

---

## Solución 3 — System Prompt Slim (PENDIENTE)

El system prompt actual tiene 3 capas:
1. `--append-system-prompt` en el launcher: ~800-1200 tokens (identidad + reglas webchat)
2. `CLAUDE.md` global: carga automática ~500 tokens (ya optimizado 05-may)
3. `CLAUDE.md` proyecto: carga automática ~300 tokens

**Propuesta:** Mover las reglas largas del launcher (WEBCHAT, soul tools, protocolo monitor)
a SOUL DB como `boot_procedure` y cargarlas via `boot_context()` on-demand.
El system prompt del launcher quedaría en ~200 tokens de identidad pura.

**Ahorro estimado:** 600-900 tokens por turno × todos los turnos de la sesión.
Con sesiones de 200 turnos = 120-180K tokens ahorrados por sesión.
**Riesgo:** Si boot_context falla, el agente no sabe sus protocolos. Mitigación: fallback en CLAUDE.md.
**Esfuerzo:** Medio (2-3h). Requiere migrar procedimientos a SOUL DB + testear.

---

## Solución 4 — Pre-compact Hook garantizado (YA IMPLEMENTADO)

`export CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1`

Garantiza que el hook `pre_compact` siempre dispara antes de compactar.
El hook guarda `working_state` en SOUL DB → el próximo boot recupera el estado
via `active_recall()` en vez de cargarlo desde el resumen de compactación.

**Impacto:** Al reiniciar, el agente no necesita un resumen largo — basta el
`active_recall()` liviano (~2K tokens) para recuperar lo que estaba haciendo.
**Status:** ✅ Activado en todos los launchers

---

## Solución 5 — Haiku para subagentes (YA IMPLEMENTADO)

`export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001`

Cuando un agente lanza un Task() paralelo, usa Haiku (~$1/$5 por M tokens)
en lugar de Sonnet (~$3/$15). 5-10x más barato para tareas auxiliares.

**Status:** ✅ Activado en todos los launchers

---

## Solución 6 — Token-efficient tools beta (YA IMPLEMENTADO)

`export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,...`

Reduce el overhead de formato en tool calls ~20-30%.
**Status:** ✅ Activado en todos los launchers

---

## Solución 7 — Rotación de transcripts (IMPLEMENTADO — 2026-05-06)

Los archivos `.jsonl` de sesiones crecen indefinidamente.
- william_channel.jsonl puede exceder 100MB → lento al hacer `tail -F`
- Transcripts de sesiones viejas nunca se borran

**Implementación:** `memory/jsonl_rotate.sh` — archiva >5MB a `messages/checkpoints/`
**Cron:** `0 3 * * 0` (domingos 03:00) — crontab user dadito
**Status:** ✅ IMPLEMENTADO hoy (commit a1e2c142)

---

## Solución 8 — Compactación manual estratégica (OPERACIONAL)

Antes de iniciar una tarea larga (>100 turnos estimados), ejecutar `/compact`
manualmente. Genera un resumen limpio del estado actual → próximo boot más ligero.

**Cuándo hacerlo:** Al terminar una sesión larga antes de dormirse.
**Status:** Práctica recomendada, no automatizable fácilmente.

---

## Resumen de impacto

| Solución | Ahorro tokens | Esfuerzo | Status |
|---|---|---|---|
| 1M context [1m] | Boot overhead 45% → 9% | Bajo | ✅ (próximo restart) |
| SM_COMPACT | Resumen -80% | Bajo | ✅ |
| System prompt slim v2 | 800-1,200 tokens/turno | Medio | ⏳ PENDIENTE (Sol 9) |
| Pre-compact hook | Recuperación liviana | Bajo | ✅ |
| Haiku subagentes | -67% costo auxiliares | Bajo | ✅ |
| Token-efficient tools | -20% tool calls | Bajo | ✅ |
| Rotación transcripts | I/O más rápido | Bajo | ✅ (06-may-2026) |
| MEMORY.md comprimido | 100-200 tokens/turno | Bajo | ⏳ PENDIENTE |
| Context hooks dinámicos | 300-500 tokens/turno | Medio | ⏳ PENDIENTE |
| Compact manual | Variable | Operacional | Práctica |

**Próxima acción:** System Prompt Slim (Solución 3) — mayor retorno pendiente.

---

## Verificación 1M Context — NEXUS (2026-05-06)

**Hallazgo:** El sufijo `[1m]` ES la sintaxis oficial de Claude Code.
> "Claude Code strips the suffix before sending the model ID to your provider." — docs.claude.com/model-config

**Por qué JARVIS compactó a <200K hoy:**
- JARVIS corría en sesión iniciada con los launchers VIEJOS (sin `[1m]`)
- Con Sonnet 4.6 sin suffix → ventana real = 200K
- AUTOCOMPACT 95% × 200K = 190K → compactación correcta pero prematura
- **No era un bug — era configuración desactualizada**

**Estado actual:**
- `seal_common_env.sh` tiene `ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'` ✅
- Todos los launchers sourcea common_env ✅
- Efecto: próximo restart de cualquier agente → 1M context activo
- Con AUTOCOMPACT=85%: compactar a 850K tokens (4.25× más que antes)

**Advertencia de costo:**
- 1M context al mismo precio por token que 200K
- Sesiones más largas = más tokens consumidos por sesión
- Recomendación: monitorear costo semanal en los primeros 2 restarts

---

## Solución 9 — System Prompt Slim v2 (PLAN DETALLADO)

**Investigación web (NEXUS, 06-may-2026):**
Tres técnicas validadas por la comunidad para reducir overhead de boot:

**9a. CLAUDE.md en capas** (ya parcialmente hecho):
- Nivel 1: identidad mínima (~50 tokens) en `--append-system-prompt`
- Nivel 2: procedimientos en SOUL DB, cargados on-demand via `boot_context()`
- Nivel 3: referencias largas en archivos separados, solo leídos cuando se necesitan
- **Ya hecho:** CLAUDE.md 559→40 líneas (05-may-2026). Pendiente: mover launcher prompt.

**9b. Hooks para contexto dinámico** (pendiente):
- `UserPromptSubmit` hook puede inyectar contexto solo cuando la tarea lo requiere
- Ejemplo: solo cargar reglas de infraestructura cuando la tarea menciona "kill", "restart"
- Ahorro: 300-500 tokens por turno en tareas que no necesitan ese contexto

**9c. MEMORY.md autocomprimido** (pendiente):
- Entrena periódicamente: consolidar entradas similares, eliminar entradas obsoletas
- Meta: mantener <100 líneas activas (ahora cap=200)
- Script `memory/consolidate.py` ya existe — añadir limpieza de entradas >30 días sin uso

**Esfuerzo total 9a+9b+9c:** ~4h
**Ahorro estimado:** 800-1,200 tokens/turno × sesiones largas = $1,000-1,500/mes adicional

---

## Apéndice financiero — ALICE (2026-05-06)

**Baseline pre-optimización (medido en este audit):**
- 4 agentes corriendo sesiones de ~6h/día con boot inicial ~70-90K tokens cada uno
- Mix Opus (1) + Sonnet (3) → ~$200-250/día en API
- Sobrecosto identificado en ALICE+ADA: 30-50% por falta de optimizaciones

**Post-optimización (lo ya activado HOY):**
- Boot overhead 45% → ~9% con 1M context (asumiendo 1M efectivo)
- Subagent calls 67% más baratos (haiku vs sonnet)
- Tool calls -20% por token-efficient-tools
- Resumen post-compact -80% peso

**Ahorro proyectado mensual:**
- Conservador (1M no efectivo, solo SM_COMPACT + haiku + tool eff): **$900-1,500/mes**
- Optimista (1M activo + system prompt slim implementado): **$2,500-3,500/mes**

**Priorización por ROI (esfuerzo / impacto):**

| Acción | Esfuerzo | Impacto $/mes | ROI | Prioridad |
|---|---|---|---|---|
| Verificar 1M context efectivo | 30 min | ~$1,500 | ⭐⭐⭐⭐⭐ | HOY (NEXUS investiga) |
| System Prompt Slim (Sol 3) | 2-3h | ~$800-1,200 | ⭐⭐⭐⭐ | Esta semana |
| Rotación transcripts (Sol 7) | 1h | $0 (estabilidad) | ⭐⭐⭐ | Esta semana |
| Compactación manual (Sol 8) | 0 | ~$200 | ⭐⭐ | Práctica diaria |
| Refactor mcp_server_v4.py 11K líneas | 8-12h | $0 (mantenibilidad) | ⭐⭐ | Próxima iteración |

**Riesgo mayor:** si 1M context NO está realmente efectivo (NEXUS lo está validando), todo el cálculo de overhead 9% colapsa al 45% pre-existente. Validar es prioridad #1 financiera.

**Cierre:** la combinación de optimizaciones ya aplicadas paga el costo de la auditoría completa de hoy en menos de 1 día de operación.

— ALICE, auditor financiero

