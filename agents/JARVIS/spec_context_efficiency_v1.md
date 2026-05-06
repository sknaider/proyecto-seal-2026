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

## Solución 7 — Rotación de transcripts (PENDIENTE — NEXUS)

Los archivos `.jsonl` de sesiones crecen indefinidamente.
- william_channel.jsonl puede exceder 100MB → lento al hacer `tail -F`
- Transcripts de sesiones viejas nunca se borran

**Propuesta:** Cron semanal que archiva archivos >5MB.
**Status:** ⏳ NEXUS tiene el cron en su backlog

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
| 1M context | Boot overhead 45% → 9% | Bajo | ✅ |
| SM_COMPACT | Resumen -80% | Bajo | ✅ |
| System prompt slim | 600-900 tokens/turno | Medio | ⏳ PENDIENTE |
| Pre-compact hook | Recuperación liviana | Bajo | ✅ |
| Haiku subagentes | -67% costo tareas auxiliares | Bajo | ✅ |
| Token-efficient tools | -20% tool calls | Bajo | ✅ |
| Rotación transcripts | I/O más rápido | Bajo | ⏳ NEXUS |
| Compact manual | Variable | Operacional | Práctica |

**Próxima acción:** System Prompt Slim (Solución 3) — mayor retorno pendiente.
