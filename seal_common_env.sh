#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  seal_common_env.sh — ENV vars compartidas por TODOS los agentes SEAL
#  Owner: ALICE (auditor / propuesta de optimización 2026-05-06)
#  Aplicar: cada launcher debe hacer `source seal_common_env.sh` antes
#           de invocar seal-claude.
#
#  Mantenimiento: cualquier optimización Anthropic nueva va aquí PRIMERO,
#  luego cada launcher la hereda automáticamente. Excepciones específicas
#  por agente se sobreescriben DESPUÉS del source en el launcher propio.
# ═══════════════════════════════════════════════════════════

# ── Identidad SEAL ──
# SEAL_AGENT debe ser exportado por el launcher antes de source este archivo
[ -z "$SEAL_AGENT" ] && echo "[seal_common_env] WARN: SEAL_AGENT no definido — definir en launcher" >&2

# ── Anthropic features experimentales (todos los agentes los aprovechan) ──
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

# Beta features Anthropic — token efficient tools, task budgets, fine-grained streaming, compact
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12

# Subagents Task() corren en haiku (~5x más barato que sonnet/opus)
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001

# Router Anthropic elige modelo por costo automáticamente
export CLAUDE_CODE_AGENT_COST_STEER=1

# Compactación session_memory interna (-80% costo del summary)
export ENABLE_CLAUDE_CODE_SM_COMPACT=true

# Garantiza que pre_compact_hook siempre dispara — preserva working_state
export CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1

# ── Determinismo / control SEAL ──
# Bloquea A/B testing Anthropic — comportamiento determinista
export GROWTHBOOK_CLIENT_KEY=""

# Desactiva tracking de instalación a Anthropic
export CLAUDE_CODE_ATTRIBUTION_HEADER=false

# Sin updates forzados — control de versión en SEAL
export DISABLE_AUTOUPDATER=true

# Retry indefinido en headless
export CLAUDE_CODE_UNATTENDED_RETRY=1

# ── KAIROS daily logs (todos los agentes contribuyen) ──
export SEAL_KAIROS=true

# ── 1M context window (Sonnet 4.6 + Opus 4.7 — GA sin beta header) ──
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'

# ── Excepciones por agente — se sobrescriben en el launcher ──
# CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: NO se exporta aquí — cada launcher decide
#   • Todos los agentes: 85% (1M context — William 06-may-2026)
