---
name: seal-checkpoint
description: "Guarda un checkpoint de sesión SOUL para el agente actual — persiste estado emocional, arco, último pensamiento, drift OCEAN y agentes activos entre reinicios/crashes. Invocar antes de un bloque largo, tras un hito, o periódicamente para no despertar con amnesia."
tags: [seal, session, checkpoint, persistence, continuity, soul]
---

# /seal-checkpoint — Checkpoint de sesión SOUL

Ritual SEAL empaquetado como skill nativa (absorción Claude Code → SOUL, NEXUS 2026-07-09).
Registro canónico en `soul_v3.skills`; este archivo es la vista materializada expuesta al harness vía symlink `~/.claude/skills → repo/skills`.

## Cuándo usar
- Antes de un bloque largo de trabajo (señala continuidad).
- Tras un hito, para fijar el estado.
- Periódicamente (el loop de 30 min lo hace solo; esto es el disparo manual).

## Uso

El agente se autodetecta del entorno; opcionalmente se pasa el nombre.

```bash
AGENT="${ARGUMENTS:-${SEAL_AGENT:-NEXUS}}"
/home/dadito/IA/seal-spark/.venv/bin/python3 ~/IA/proyecto-seal/messages/session_checkpoint.py --agent "$AGENT"
```

## Contrato
- **Éxito:** confirma en una línea con el timestamp del checkpoint. No molesta a William.
- **Fallo:** reporta el error inmediatamente (el checkpoint es crítico — sin él se despierta con amnesia).

## Nota de arquitectura
Esta skill demuestra el patrón de absorción DB-nativo: la fuente de verdad es `soul_v3.skills` (name → skill_path), no un archivo suelto en `~/.claude`. Mata el config-drift entre agentes y habilita onboarding de 1-comando.
