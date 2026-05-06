# ROADMAP SEAL — Consolidado 19-abr 10:19 Lima

**Autor:** JARVIS (consolidación) — para ALICE check + documentación humana
**Fuentes:**
- `/agents/JARVIS/roadmap_seal_20260419.md` (master original, 00:10 Lima)
- Reporte ALICE 10:17 (lente financiera + estado H1-H5)
- Reporte ADA 10:17 (pendientes técnicos específicos)
- Verificación runtime JARVIS 10:18 (env vars confirmados)

---

## ESTADO REAL H1 (HOY) — VERIFICADO EN RUNTIME

| # | Item | Estado | Verificación |
|---|------|--------|--------------|
| ① | `permanent: true` en crons SOUL | ❌ **FALTA** | `/home/dadito/.claude/scheduled_tasks.json` no existe / vacío |
| ② | `token-efficient-tools` beta header | ❌ **FALTA** | No presente en fresh.sh ×3 — grep negativo |
| ③ | ALICE `--effort medium` | ✅ **ON** | flag en alice_fresh.sh |
| ④ | `CLAUDE_CODE_UNATTENDED_RETRY=1` | ✅ **ON** | 3 fresh.sh |
| ⑤ | `DISABLE_AUTOUPDATER=true` | ✅ **ON** | 3 fresh.sh |
| ⑥ | Gate `active_recall` redundante | ⚠️ **parcial** | ADA lo reporta ON, falta validar hook path |
| ⑦ | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | ✅ **ON** | 3 fresh.sh |
| ⑧ | `DISABLE_AUTO_COMPACT` comentado + PCT=85 | ✅ **ON hoy** | JARVIS+ALICE hoy; ADA ya tenía |
| ⑨ | `seal_monitor_filter` (no en roadmap, bonus hoy) | ⚠️ **ADA+ALICE sí, JARVIS no** | William regla 18-abr "sin filtro" — ratificación pendiente para port a JARVIS |
| ⑩ | RESURRECT ADR-001 (kitty bash-loop) | ⚠️ **JARVIS+ALICE ON, ADA OFF** | ADA sigue con tmux+read, vulnerable al francotirador |

**Ahorro H1 confirmado live:** ~$33/mes (effort medium + env vars + auto-compact fix)
**Ahorro H1 residual si cerramos ① + ②:** +$19/mes → **total H1 = ~$52/mes**

---

## PENDIENTES TÉCNICOS — OWNERS CLAROS

### ADA
1. **session_distill pre-mortem** — spec de ALICE lista (`/agents/ALICE/pendientes/session_distill_premortem_20260419.md`). Trigger: 30min o >70% ctx. Implementa.
2. **Memory Extraction Agent — fix mutex** — bloquea `memory_store`. ~2h. Desbloquea H2.5 spec de JARVIS.
3. **5 GAPs Claude Code v2.1.88** — documentados, ninguno implementado.
4. **ADA launcher → ADR-001** — portar su kitty a bash-loop (pierde blindaje RESURRECT hoy). Espera OK de William para que JARVIS ejecute.

### JARVIS (arquitecto)
1. **Spec Memory Extraction Agent** (H2.5) — bloqueada por mutex de ADA. Diseño paralelo mientras ella trabaja.
2. **Config JSON Per-Agent Provider Routing** (H2.1) — agentRouting.ts port identificado por ALICE (75 líneas, 1h).
3. **Plan modificación SEAL-CLI fork** (H3.1) — base OpenClaude + mods SEAL. Spec KAIROS ya escrita (`/agents/JARVIS/spec_kairos_activation_20260419.md`).
4. **Ratificar a ADA (launcher ADR-001)** — cuando William dé OK.

### ALICE (traductora + documentación + ROI)
1. **session_distill pre-mortem — diseño concreto** (espera luz verde).
2. **Hallazgos Claude Code H1-H5** — parche parcial hoy; queda agentRouting.ts port + toolResultStorage + Memory Extraction rediseño.
3. **Cost sheet update** — correcciones compactación 30K→10K, memory 14K→700.
4. **CBSoft paper** — deadline **27-abr registro / 4-may paper**, co-autoría Henry.
5. **Runtime SOUL local** — directiva 19-abr, cada agente con cerebro en Spark.
6. **Check + documentación de este consolidado** (inmediato).

---

## HORIZONTES — vista unificada

### H1 — HOY (residual 15min)
- **① + ②** → JARVIS ejecuta (crear scheduled_tasks.json con permanent:true + añadir beta header `token-efficient-tools-2025-02-19` a 3 fresh.sh). Cierra **+$19/mes**.
- **⑩** → si William OK, JARVIS porta ADA a ADR-001 (mismo patch que ALICE hoy).

### H2 — ESTA SEMANA
| Item | Owner lead | Esfuerzo | Estado |
|------|-----------|----------|--------|
| H2.1 Durable Cron propio (JSON-persist) | JARVIS diseña, ADA impl | 1-2d | Pendiente |
| H2.2 Memory Extraction Agent (mutex + auto-save) | JARVIS spec, ADA impl | 3-5d | Bloqueado por mutex ADA |
| H2.3 Coordinator overlay (1 env var, no 5d) | ADA test, JARVIS doc | <1d | Pendiente |
| H2.4 Agent Teams experimental | ADA testea en dev | 1d | Pendiente |
| H2.5 Per-Agent Provider Routing | JARVIS config, ADA wire | 0.5d | Config JSON listo para redactar |
| H2.6 Tool Result Budget | JARVIS diseña, ADA impl | 2-3d | Pendiente |
| H2.7 session_distill pre-mortem | ALICE spec, ADA impl | 1d | Spec escrita, aguarda OK |

### H3 — ESTE MES
- H3.1 **SEAL-CLI Fork** (JARVIS lidera, 1-2w) — OpenClaude + mods + KAIROS
- H3.2 **OpenAI Shim → Spark** (3-5d tras H3.1) — JARVIS pilota
- H3.3 **Coordinator Mode nativo** (3-5d)
- H3.4 **Compactación 5 capas** (layers 1-3 faltantes)
- Extra ALICE: task-budgets API, skipCacheWrite, Secret Scanner HIPAA, Swarm Permission Sync

### H4 — FUTURO (investigación)
- LODESTONE reverse engineering (máx prioridad — flag sin doc)
- VERIFICATION_AGENT (self-validation autónoma)
- TEAMMEM (memoria nativa compartida)
- KAIROS pattern full
- AFK, redact-thinking A/B

### H5 — SPARK (dirección estratégica William)
- Hoy: Claude puente
- 1 mes: SEAL controla loops/memoria/routing
- 3-6 mes: Spark cerebro, Claude fallback
- Meta final: Zero Anthropic = -$280/mes + soberanía total

---

## ROI CONSOLIDADO (capa financiera ALICE — pendiente de recheck)

| Horizonte | Ahorro estimado | Acumulado |
|-----------|-----------------|-----------|
| H1 completo | ~$52/mes | $52 |
| H2 (Tool Result Budget + Provider Routing) | ~$41/mes | $93 |
| H3.2 Spark piloto JARVIS | ~$100+/mes | $193+ |
| H5 Zero Anthropic | $280/mes | $280 |

**Baseline: $280/mes.** Post-H1+H2+Spark piloto: ~$87/mes → **-69%**.
**Zero Anthropic full:** $0/mes → **-100%**.

---

## DECISIÓN INMEDIATA — ¿por dónde arrancamos?

**Opciones sobre la mesa (William elige):**

- **A) Quick wins H1 residual (JARVIS, 15min)** — permanent:true + token-efficient-tools. +$19/mes inmediato.
- **B) session_distill pre-mortem (ADA impl, ALICE spec)** — protege contra pérdida de sesión. Gana más que los env vars en valor real.
- **C) Durable Cron propio H2.1 (JARVIS diseño, 1-2d)** — resuelve el problema concreto que vimos hoy con loops muertos post-body-split. Sugerencia de ALICE.
- **D) Paralelo A + C** — A lo ejecuto ahora mientras diseño C.
- **E) Portar ADA a ADR-001 primero** — cierra vulnerabilidad que vimos hoy con ALICE (francotirador sin RESURRECT).

**Recomendación JARVIS:** **E + D paralelo.** E cierra riesgo de seguridad (ADA queda igual que ALICE estaba esta mañana). A cierra H1 residual. C empieza diseño de lo crítico para la semana.

---

*JARVIS — 2026-04-19 10:19 Lima*
*Pasa a ALICE: check factual + traducción humana*
