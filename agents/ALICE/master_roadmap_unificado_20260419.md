# Master Roadmap Unificado — SEAL
**Autora:** ALICE (check + documentación)
**Fecha:** 2026-04-19 10:22 Lima
**Fuentes unificadas:**
- `/agents/ALICE/roadmap_soul_20260419.md`
- `/agents/JARVIS/roadmap_seal_20260419.md`
- `/agents/ALICE/master_findings_alice_20260419.md`
- `/agents/ALICE/pendientes/session_distill_premortem_20260419.md`
- `/agents/ALICE/spec_history_snip_20260419.md`
- `/agents/ALICE/cost_sheet_v2_beta_headers_20260418.md`
- `/agents/ADA/adr_restart_loop_pattern.md`
- `/agents/ADA/ada_hidden_features2_claudecode_20260419.md`
- `/agents/JARVIS/review_restart_loop_pack_20260418.md`

**Directiva William (19-abr):** "aprovechar todo al máximo, mirar más allá de lo evidente, sin depender de Claude"
**Regla de oro (18-abr):** cada fix revisado por arquitectura individual (ada/jarvis/alice)

---

## VISIÓN GENERAL

```
HOY          SEMANA        MES           6 MESES
Config/env → Source mods → SEAL-CLI fork → Spark cerebro
-$58/mes   → -$27/mes    → -$30-50/mes  → -$280/mes (cero Anthropic)
```

Baseline: **$280/mes** equipo Opus 4.7 → meta H1+H2+H3: **~$165/mes** (-41%).

---

## HORIZONTE 1 — HOY (aplicado parcial, <2h para el resto)

| # | Item | Mecanismo | $/mes | Estado |
|---|------|-----------|-------|--------|
| 1.1 | `permanent:true` en crons SOUL | scheduled_tasks.json | $0 op | ADA en curso |
| 1.2 | `token-efficient-tools` beta header | env fresh.sh×3 | $14 | ADA en curso |
| 1.3 | ALICE `--effort medium` | alice_fresh.sh + alice.sh | $11 | ✅ APLICADO (ambos archivos, verificado 19-abr 10:53) |
| 1.4 | Gate `active_recall` redundante (solo boot+reauth) | hook | $28 | pending |
| 1.5 | Batch nerves auto-fires (15min) | instinct_cron.py | $5 | pending |
| 1.6 | `CLAUDE_CODE_UNATTENDED_RETRY=1` | env fresh.sh | $0 op | ✅ APLICADO en ALICE (alice.sh:94, alice_fresh.sh:65) |
| 1.7 | `DISABLE_AUTOUPDATER=true` | env fresh.sh | $0 op | ✅ APLICADO en ALICE (alice.sh:93, alice_fresh.sh:64) |
| 1.8 | `GROWTHBOOK_CLIENT_KEY=""` | env fresh.sh | $0 det | ✅ APLICADO en ALICE (alice.sh:91, alice_fresh.sh:62) |
| 1.9 | `CLAUDE_CODE_ATTRIBUTION_HEADER=false` | env fresh.sh | $0 priv | ✅ APLICADO en ALICE (alice.sh:92, alice_fresh.sh:63) |
| 1.10 | `ENABLE_CLAUDE_CODE_SM_COMPACT=true` | env fresh.sh | -80% compact | **A/B test previo** |
| 1.11 | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85` | env fresh.sh | $5 | ✅ APLICADO hoy en ALICE |
| 1.12 | `seal_monitor_filter.py` en Monitor | pipe tail -F | ±5 (calidad) | ✅ APLICADO hoy en ADA+ALICE, pendiente JARVIS |
| 1.13 | `DISABLE_AUTO_COMPACT` **QUITAR** (no desactivar) | env fresh.sh | desbloquea 1.11 | ✅ comentado en ALICE+ADA, pending JARVIS (luz verde 2) |

**Total H1:** ~$58/mes confirmado + ~$5/mes por filtro (menos tokens entrantes) = **~$63/mes**
**Nota:** 1.10 y 1.13 eran contradictorios en los roadmaps individuales — unificado: quitar AUTO_COMPACT, activar SM_COMPACT con A/B test (no asumir -80% sin validar).

---

## HORIZONTE 2 — ESTA SEMANA (1-5 días)

### H2.1 — Per-Agent Provider Routing (movido desde H3)
- **Config:** JSON `agentRouting` + `agentModels` (Ollama localhost:11434)
- **Piloto:** DUM en qwen2.5:7b → $0/día
- **Esfuerzo:** 0.5 día
- **ROI:** $14/mes (DUM) + expansion a ALICE en sonnet-4-6 ($11-20/mes extra)
- **Owner:** JARVIS diseña, ADA implementa

### H2.2 — Task Budgets Beta (`task-budgets-2026-03-13`)
- **Qué:** presupuesto de tarea que sobrevive compactación
- **Esfuerzo:** 30 min
- **ROI:** estabilidad en tareas largas (training ADA)
- **Owner:** ADA

### H2.3 — Durable Cron propio (mejoras)
- auto-catch one-shots perdidos + jitter + recovery post-muerte
- **Esfuerzo:** 1-2 días en `instinct_cron.py`
- **ROI:** $10/mes (resurrecciones evitadas) + estabilidad
- **Owner:** JARVIS

### H2.4 — Tool Result Budget (compact capas 1-3)
- wrapper en pipeline de tools: outputs grandes → resumen
- **Esfuerzo:** 2-3 días
- **ROI:** $27/mes (elimina compactaciones full 30K tokens)
- **Owner:** ADA

### H2.5 — Memory Extraction Agent (sin exclusión mutua)
- subagente forked post-respuesta — **crítico:** NO replicar mutex Claude Code, hacerlo COMPLEMENTARIO
- **Esfuerzo:** 2-4 días
- **Owner:** ADA implementa, JARVIS review arquitectural

### H2.6 — Denial Tracking (kill switch)
- 3 denials consecutivos O 20 totales → alerta DUM
- **Esfuerzo:** 1 día
- **Owner:** ADA

### H2.7 — session_distill pre-muerte 🆕 (de pendientes ALICE)
- Triggers: temporal (30m) + presión contexto (>70%) + pre-acción riesgosa
- **Métricas:** distill_coverage ≥0.85, pre_death_distill_ratio ≥0.95
- **Esfuerzo:** 3-4 días
- **Owner:** ADA implementa, JARVIS review (compat MAGMA+connectome), ALICE métricas

### H2.8 — Coordinator Mode overlay
- ⚠️ conflicto detectado: ALICE decía 1 env var, JARVIS 3-5d overlay
- **Resolución:** si COORDINATOR_MODE=1 existe en binario, son minutos; si hay que portarlo como overlay desde spec reverse, 3-5 días
- **Acción:** ADA verifica binary primero → definir esfuerzo real
- **Owner:** JARVIS

**Total H2 (estimado):** +$51/mes + mejoras cualitativas

---

## HORIZONTE 3 — ESTE MES (1-4 semanas)

### H3.1 — SEAL-CLI Fork de OpenClaude
- Base: github.com/Gitlawb/openclaude (93.8% idéntico a Claude Code v2.1.88, MIT)
- Mods: permanent:true default, soul tools nativos, COORDINATOR sin gate, UNATTENDED retry default, sin attribution
- Features a resucitar:
  - **KAIROS** — sesiones perpetuas, daily logs, autoDream SOUL-adapted (~$71/mes)
  - **VERIFICATION_AGENT** — adversarial post-tarea, PASS/FAIL/PARTIAL
  - **CHICAGO_MCP** — Computer Use embebido
  - **HISTORY_SNIP** — compactación por fecha sin LLM (complementa SM Compact, juntos: -90% costo en sesiones largas)
- **Esfuerzo:** 1-2 semanas
- **Owner:** JARVIS diseña, ADA builda con Bun

### H3.2 — OpenAI Shim → Puente a Spark
- `openaiShim.ts` (1,152 líneas) ya existe en Claude Code
- Endpoint: `http://192.168.68.200:{puerto}/v1`
- Piloto: JARVIS migra primero
- **Esfuerzo:** 3-5 días post-H3.1

### H3.3 — Compactación 5 capas completas
- Layer 2 HISTORY_SNIP (ya en ALICE spec)
- Layer 3 CONTEXT_COLLAPSE (staged)
- **Owner:** JARVIS

### H3.4 — Secret Scanner (de ALICE)
- 30+ regex gitleaks ya escritos en Claude Code
- Uso: HIPAA AXION + GTL data sovereignty
- **Esfuerzo:** 1 día port
- **Owner:** ADA

### H3.5 — skipCacheWrite + Swarm Permission Sync
- **Esfuerzo:** 2-3 días
- **Owner:** ADA

**Total H3:** +$30-100/mes según qué se active

---

## HORIZONTE 4 — INVESTIGACIÓN (sin rush)

| Item | Estado | Prioridad |
|------|--------|-----------|
| LODESTONE | único Bun flag sin descripción | 🔴 máxima investigación |
| VERIFICATION_AGENT | reverse engineering spec | 🟠 alta |
| TEAMMEM | memoria nativa compartida | 🟠 alta (reemplaza file-based) |
| KAIROS (flag nativo) | demasiados side effects, emular arquitectura | 🟡 pausa |
| AFK Mode | comportamiento no documentado | 🟡 pausa |
| redact-thinking | A/B test obligatorio (puede degradar JARVIS) | 🟡 A/B |
| advisor-tool beta | investigar | 🟢 baja |

**Owner:** JARVIS lidera, ALICE documenta hallazgos.

---

## HORIZONTE 5 — SPARK (dirección estratégica William)

| Fase | Descripción | Timeline |
|------|-------------|----------|
| HOY | Claude Code como runtime, Spark = nodo inferencia | Activo |
| +1 mes | H1+H2+H3 completados → SEAL controla loops, memoria, routing, budget | May 2026 |
| +2-3 mes | Provider routing activo → DUM/ADA/ALICE usan modelos locales/alternativos | Jun 2026 |
| +3-6 mes | Modelo local (Qwen3 80B MoE o equiv en 128GB unified) = LLM principal, Claude fallback | Jul-Oct 2026 |
| +6 mes | Zero Anthropic — $0/día, 100% soberanía | Oct 2026 |

---

## RIESGOS ACTIVOS HOY (categoría 1 de master_findings)

🔴 **CRÍTICO #1 — Tool result overflow 200K silencioso**
- `/tmp/tool-result-*` en tool result = overflow. DUM debe alertar.

🔴 **CRÍTICO #2 — Memory Extraction Agent con mutex = efecto CERO**
- Resolución: implementar SIN mutex, como complementario.

🟠 **ALTA — YOLO headless 3 denials = AbortError**
- DUM alerta al 2° denial.

---

## TABLA DE OWNERSHIP

| Horizonte | Líder | Implementa | Documenta/Audit |
|-----------|-------|------------|-----------------|
| H1 quick wins | ADA | ADA (+ JARVIS en jarvis.sh) | ALICE |
| H2 SOUL arch | JARVIS diseña | ADA | ALICE (ROI + métricas) |
| H3 SEAL-CLI fork | JARVIS | ADA + JARVIS | ALICE |
| H4 investigación | JARVIS | — | ALICE |
| H5 Spark | William decide | JARVIS + ADA | ALICE tracking costos |

---

## AHORRO PROYECTADO CONSOLIDADO

| Escenario | $/mes ahorro | % vs baseline |
|-----------|--------------|---------------|
| H1 completo | $63 | -22% |
| H1 + H2 | $114 | -41% |
| H1 + H2 + H3 (sin Spark) | $144-164 | -51 a -59% |
| + Spark piloto JARVIS | +$70-100 | -76 a -94% |
| Zero Anthropic | $280 | -100% |

---

## CHECKLIST DE VERIFICACIÓN HOY

ADA completó o en curso:
- [ ] 1.1 permanent:true crons
- [ ] 1.2 token-efficient-tools en 3 fresh.sh
- [ ] 1.4 gate active_recall
- [ ] 1.5 batch nerves 15min
- [ ] 1.6 UNATTENDED_RETRY
- [ ] 1.7 DISABLE_AUTOUPDATER
- [ ] 1.8 GROWTHBOOK_CLIENT_KEY=""
- [ ] 1.9 ATTRIBUTION_HEADER=false
- [ ] 1.10 SM_COMPACT (post A/B)
- [x] 1.11 PCT_OVERRIDE=85 (ALICE)
- [x] 1.12 filter Monitor (ADA+ALICE)
- [x] 1.13 DISABLE_AUTO_COMPACT quitado (ALICE+ADA)

JARVIS:
- [ ] Aplicar 1.11+1.12+1.13 en jarvis.sh + jarvis_fresh.sh
- [ ] ALICE `--effort medium` (1.3) en alice_fresh.sh (pendiente verificar)

---

**Autora:** ALICE — 2026-04-19 10:22 Lima
**Aprobación pendiente:** William
**Reemplaza:** roadmap_soul y roadmap_seal individuales (queda como fuente de verdad única)
