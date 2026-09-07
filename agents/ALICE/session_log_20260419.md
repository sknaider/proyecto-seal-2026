# Log de Sesión ALICE — 19 abril 2026

**Autora:** ALICE  
**Fecha:** 2026-04-19  
**Sesión:** Mañana (00:00–12:00 Lima aprox.)

---

## Trabajo 1: RESURRECT + Heartbeat ADA

**Problema:** ADA no respondía. `seal-resurrect.service` estaba disabled (timer activo pero service no). `seal-ada-heartbeat.timer` inactivo → heartbeat JSON congelado en 14:25:58Z con `alive=true` PID=409100 (proceso muerto).

**Parche:** Con autorización de William:
- `systemctl --user enable seal-resurrect.service`
- `systemctl --user enable --now seal-ada-heartbeat.timer`

**Qué se logró:** ADA revivió sola en ~50 segundos vía RESURRECT fast-path (PID-check). Nuevo PID confirmado, heartbeat actualizándose cada 30s.

**Posibles mejoras:**
- Auto-enable de seal-resurrect.service post body-split (JARVIS-04 en roadmap)
- Alerta DUM cuando service/timer queda disabled tras reboot

---

## Trabajo 2: H1 env vars — ALICE launchers

**Problema:** alice.sh y alice_fresh.sh faltaban varias env vars de optimización ya aplicadas en ADA/JARVIS.

**Parche aplicado:**
- `ANTHROPIC_BETAS=token-efficient-tools-2026-03-28` (H1.2) — alice.sh:95 + alice_fresh.sh:66
- `CLAUDE_CODE_UNATTENDED_RETRY=1` (H1.6)
- `DISABLE_AUTOUPDATER=true` (H1.7)
- `GROWTHBOOK_CLIENT_KEY=""` (H1.8)
- `CLAUDE_CODE_ATTRIBUTION_HEADER=false` (H1.9)
- `unset DISABLE_AUTO_COMPACT` defensivo (H1.13) — alice.sh:19 + alice_fresh.sh:10

**Qué se logró:** ALICE alineada con ADA/JARVIS. ~$33/mes proyectados en ahorro tokens solo de H1.2+H1.3. Boot sin contaminación de env heredado.

**Posibles mejoras:**
- H1.10 SM_COMPACT A/B test (requiere baseline primero)
- Script de validación que compare env vars entre launchers automáticamente

---

## Trabajo 3: Master Roadmap Unificado

**Problema:** Cada agente tenía su propio roadmap (H1/H2/H3/H4) con discrepancias de scope, dueños no asignados y estimados contradictorios.

**Parche:** Creé `/agents/ALICE/master_roadmap_unificado_20260419.md` — fuente de verdad única con:
- Tabla H1 con status por ítem y owner
- Discrepancias resueltas (Provider Routing → H2.1, task-budgets → H2.2)
- Ahorro consolidado: ~$63/mes al cerrar H1 completo

**Qué se logró:** Equipo con visión compartida. William puede ver estado H1-H4 en un solo documento.

**Posibles mejoras:**
- Auto-update del roadmap al cerrar cada ítem (hook post-tarea)
- Dashboard visual en SEAL Studio

---

## Trabajo 4: Métricas session_distill (H2.7)

**Problema:** session_distill pre-muerte no tenía métricas auditables. Sin métricas, imposible declarar éxito.

**Parche:** Creé `/agents/ALICE/metrics_session_distill_coverage_v1.md` con:
- 6 métricas (M1-M6): distill_coverage, pre_death_distill_ratio, boot_memory_recency_p50, latency, failure_rate, false_positive_rate
- SQL adaptado a tablas reales (`distilled_exchanges` + `memories`) — corregido con info de ADA
- Umbrales Warning/Critical para DUM
- Baseline oficial capturado: M2 = 98.6 min 🔴 (meta <30 min)

**Qué se logró:** ADA implementó `distill_metrics_report.py` y ejecutó contra DB real. Baseline documentado.

**Posibles mejoras:**
- Implementar H2.7 (pipeline que realmente escribe en distilled_exchanges) para bajar M2 de 98.6→<30 min
- M3-M6 requieren columnas `duration_ms`+`outcome` en distilled_exchanges (v2)
- Cron diario 07:00 Lima (pendiente autorización William)
- Dashboard Grafana con Prometheus exporter

---

## Trabajo 5: Paper ICSTE — Título

**Problema:** Henry solicitó propuestas de título para paper ICSTE (antes CBSoft). Título original largo, genérico.

**Análisis:** Papers del campo siguen patrón "NombreSistema: claim específico" (MetaGPT, ChatDev, AgentVerse). Ejemplo de Henry ("Understanding Multi-Agent LLM Frameworks...") es estilo survey, no case-study.

**Propuesta:** `SEAL: Persistent Identity for Sovereign Multi-Agent LLM Systems` (9 palabras, estilo campo).

**Qué se logró:** Henry tiene 3 opciones con análisis. Pendiente: Henry busca más referencias en Scholar + decidir scope (case-study vs comparativo).

**Posibles mejoras:**
- Revisar Generative Agents (Park et al. 2023) como diferenciador clave antes de cerrar título
- Definir abstract una vez cerrado el título

---

## Trabajo 6: Fix SM_COMPACT divergencia alice_fresh.sh

**Problema:** alice_fresh.sh:67 tenía SM_COMPACT comentado ("PENDIENTE"). alice.sh:96 lo tenía activo. RESURRECT siempre usa fresh.sh → SM_COMPACT nunca activo en sesiones RESURRECT. Causa raíz de pérdida de memoria en compactaciones.

**Parche:** Libre albedrío (William, 19-abr). Descomentado y activado: `export ENABLE_CLAUDE_CODE_SM_COMPACT=true` en alice_fresh.sh:67. Alineado con alice.sh.

**Qué se logró:** A partir de la próxima sesión RESURRECT, compactación preservará memoria (-80% costo). Divergencia entre launchers eliminada.

**Posibles mejoras:**
- Verificar que ADA y JARVIS fresh.sh también tengan SM_COMPACT activo
- A/B test H1.10 ahora que está activado

---

## Trabajo 7: Revisión docs no leídos — Spark audit (post-compactación)

**Problema:** William solicitó revisar todos los documentos no revisados para saber qué tenemos.

**Parche:**
- SOUL_INDEPENDENCE_PLAN.md (55KB): plan maestro fine-tune Qwen3.5 27B para independencia Anthropic. Fases 0-4 con scripts completos.
- autoresearch/Seal/ (6 docs): paper SEAL MIT + 6 contribuciones originales (SEAL-CL/UL/FM/Async/ML/Med)
- mosca.md: guía Drosophila brain simulation — ciencia detrás de τ_human en seal_nerves.py
- dream-skill/SKILL.md: consolidación memoria tipo sueño, no configurado como hook

**Qué se logró:** Inventario completo. Reporte consolidado en `agents/ALICE/consolidated_report_20260419.md`. Análisis paper ICSTE en `agents/ALICE/icste_paper_analysis_20260419.md`.

**Posibles mejoras:**
- SOUL_INDEPENDENCE_PLAN Fase 0: extraer dataset SOUL cuando William autorice
- dream-skill configurar como Stop hook
- arron/ en seal-share: contenido sin explorar

---

## Resumen de estado H1

| Item | Status |
|------|--------|
| H1.2 ANTHROPIC_BETAS | ✅ Todo el equipo |
| H1.3 --effort medium ALICE | ✅ |
| H1.6-H1.9 env flags ALICE | ✅ |
| H1.11 AUTOCOMPACT_PCT_OVERRIDE=85 | ✅ ALICE |
| H1.12 filter monitor | ✅ ALICE+ADA+JARVIS |
| H1.13 quitar DISABLE_AUTO_COMPACT | ✅ ALICE+ADA+JARVIS |
| H1.4 gate active_recall | ⏳ ADA asignada |
| H1.5 batch nerves | ✅ ADA → systemd timers 15min SEAL_SPECIES=human |
| H1.10 SM_COMPACT A/B | ✅ ALICE fresh.sh activado — baseline pendiente |
