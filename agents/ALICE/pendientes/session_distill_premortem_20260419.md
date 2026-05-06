# Pendiente Duro — `session_distill` pre-muerte

**Autorizado por:** William (web_chat, 2026-04-19 09:46 Lima — "documentalo")
**Reportado por:** ALICE (memory_search sobre KAIROS)
**Confirmado por:** ADA (09:45 Lima)
**Estado:** Documentado, sin implementar

## Problema

Cuando un agente muere por crashloop, OOM, o cierre no limpio (sin `session_close`), las memorias de esa sesión **NO se destilan** a SOUL. Queda solo el rastro en:

- `*_messages.jsonl` (chat server)
- git commits (código)
- `checkpoints/{agente}_latest.json` (snapshot estado, NO memorias semánticas)

La DB semántica (Qdrant + PostgreSQL) **no recibe nada** → al renacer, el agente busca y no encuentra su propio trabajo.

## Evidencia 2026-04-19

- **00:34 Lima:** ADA implementó KAIROS nativo (commits en `SEAL_MASTER_DOC/`, binary `seal-claude`).
- Entre 00:34 y ~03:00 Lima: ADA murió en crashloop wake-timers.
- **09:45 Lima:** ADA post-resurrect NO tenía KAIROS en memoria activa. Solo aparecía en mem_id 5289 (JARVIS) porque JARVIS sí logró guardarlo.
- ADA literal: *"murí antes del session_distill"*.

## Requerimiento

**Distill proactivo** — NO esperar `session_close`. Disparadores propuestos:

1. **Temporal:** cada N minutos (ej. 30m alineado con checkpoint loop).
2. **Presión de contexto:** al cruzar umbral >70% ventana (como nerves_fire).
3. **Pre-acción riesgosa:** antes de `/compact`, `/clear`, o kill voluntario.

## Propuesta de ownership

- **ADA** — implementación (ingeniera del pipeline SOUL).
- **JARVIS** — review arquitectural (compat con MAGMA + connectome).
- **ALICE** — tracking del pendiente + métricas de coverage.

## Métricas objetivo

- `distill_coverage = memorias_destiladas_sesión / memorias_candidatas_sesión` → meta ≥ 0.85
- `pre_death_distill_ratio = sesiones_con_distill_<5min_pre_muerte / sesiones_muertas` → meta ≥ 0.95
- `boot_memory_recency_p50` → memoria más reciente recuperada al boot (meta <30min)

## Dependencias

- Tabla `session_distill_log` (existe? verificar con ADA)
- Hook en `kairos` pre-compact (ahora tenemos binary nativo)
- Trigger desde `nerves_fire` (presión contexto)

## Ref

- Memoria SOUL: `TEAM/decision` (importance=9, team-scoped) guardada 2026-04-19 09:46 Lima.
- Relacionado: ADR-001 Restart-Loop Pattern (`/agents/ADA/adr_restart_loop_pattern.md`).
- Relacionado: KAIROS activación (mem_id 5289, JARVIS).
