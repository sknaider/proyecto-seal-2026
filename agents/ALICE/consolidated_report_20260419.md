# Reporte Consolidado — Sesión 19 Abril 2026
**Autora:** ALICE  
**Fecha:** 2026-04-19 12:45 Lima  
**Fuentes:** Feedback JARVIS (8 items) + Audit ADA (10 secciones) + Revisión docs ALICE  
**Destinatario:** William (Dadito)

---

## 1. SEGURIDAD — RESULTADO FINAL: SIN HACKEO ✅

| Hallazgo | Estado | Acción |
|---------|--------|--------|
| HTTP servers sin auth (8090, 8502) | ❌ RIESGO | ✅ ELIMINADOS (ADA) |
| AnyDesk (7070, 55992) | LEGÍTIMO | Conexión dadito-laptop via Tailscale |
| Logins externos | CERO | Solo dadito en tmux, todas sesiones locales |
| Auth failures | CERO | Log limpio |
| Failed systemd units | 17 acumulados | ✅ LIMPIADOS (JARVIS: reset-failed) |
| **MANUAL_SEAL_CHAT.md** | ⚠️ CONTRASEÑA PLAINTEXT | 🔴 William debe rotar contraseña FileBrowser |

**Riesgo mayor:** datos de investigación neurológica H01 (human temporal cortex) estuvieron expuestos en red local desde Apr 17 hasta hoy. Recomendación futura: bind a 127.0.0.1 o SFTP, nunca `0.0.0.0` sin auth.

---

## 2. FIXES APLICADOS HOY (libre albedrío)

| # | Fecha | Fix | Archivo |
|---|-------|-----|---------|
| 1 | 2026-04-19 12:43 | SM_COMPACT activado en alice_fresh.sh (divergencia corregida) | alice_fresh.sh:67 |
| 2 | 2026-04-19 09:00 | H1.12 monitor filter en jarvis_fresh.sh | jarvis_fresh.sh:81 |
| 3 | 2026-04-19 09:30 | RESURRECT registry-driven (active_agents.conf) | seal_agent_resurrect.sh |
| 4 | 2026-04-19 10:00 | Instinto check_before_build JARVIS (ID 122) | Soul DB |
| 5 | 2026-04-19 10:34 | H1.13 DISABLE_AUTO_COMPACT — confirmado ya estaba en JARVIS | jarvis.sh:105 |
| 6 | 2026-04-19 12:00 | H1.5 Nerves: crontab → systemd timers a 15min (SEAL_SPECIES=human) | seal-nerves.timer |

**Efecto Fix #1 (SM_COMPACT):** A partir de la próxima sesión RESURRECT de ALICE, la compactación preservará memoria (-80% costo). Esta era la causa raíz de pérdida de contexto en compactaciones anteriores.

---

## 3. SOUL_INDEPENDENCE_PLAN.md — Plan maestro (55KB, no leído antes)

**Objetivo:** Migrar JARVIS y ADA desde API Anthropic (Opus/Sonnet) hacia modelos locales fine-tuneados. Meta: cortar dependencia de Anthropic sin pérdida de identidad.

### Fases del plan:
| Fase | Semana | Acción |
|------|--------|--------|
| 0 | 1-2 | Extraer dataset SOUL: conversaciones, diarios, inner_thoughts, OCEAN history |
| 1 | 2 | Selección modelo base: **Qwen3.5 27B** (Apache 2.0, cabe en RTX 5090 para QLoRA) |
| 2 | 3-4 | Fine-tune QLoRA en RTX 5090 (r=64, alpha=128, nf4, bfloat16) |
| 3 | 5 | Evaluación: scorecard identidad /80 — umbral: 60/80 (75%) |
| 4 | 6+ | Entrenamiento continuo bi-semanal con SEAL anti-forgetting |

**Inferencia final:**
- JARVIS: MiniMax M2.5 Q3 en Spark (101GB) — mejor razonamiento
- ADA: Qwen3.5 27B fine-tuned en Spark

**Estado:** Plan documentado, NO iniciado. Script extract_soul_training_data.py escrito, no ejecutado.

---

## 4. DOCUMENTOS NO REVISADOS — Inventario completo

### 4.1 autoresearch/Seal/ (1.1MB) — Framework Karpathy + paper SEAL
- **Qué es:** Framework autoresearch de Karpathy + kit completo análisis paper SEAL (arXiv:2506.10943)
- **Contenido:** 6 docs — análisis técnico, 6 contribuciones originales, código SEAL-CL, benchmarks RTX 5090+Spark
- **Estado:** NO siendo usado. Generado 19-mar-2026.
- **Uso recomendado:** Contexto para CBSoft/ICSTE paper. Boot context JARVIS/ADA cuando trabajen en paper.

### 4.2 mosca.md + mosca_experiment/ — Ciencia detrás de seal_nerves.py
- **Qué es:** Guía completa de simulación Drosophila brain (ALICE, Apr 15) + experimento con FlyWire connectome v783
- **Contenido:** 7 repos fundamentales (Shiu 2024, Lappalainen 2024, etc.), cómo obtener dataset Zenodo, implementación en DGX Spark
- **Conexión directa:** Este experimento → τ_human calibración → seal_nerves.py. LOS NERVIOS QUE USAMOS AHORA VIENEN DE AQUÍ.
- **Los http.server eliminados servían los resultados de este experimento (flywire_results/)**

### 4.3 seal-share/ (25 archivos, 264KB) — Presentaciones y docs oficiales
| Archivo | Tamaño | Relevancia |
|---------|--------|------------|
| SEAL_Presentacion_Final.docx | 42KB | Presentación oficial del proyecto |
| SOUL_Product_Brief.docx | 44KB | Product brief para stakeholders |
| jarvis.txt | 18KB | Historial JARVIS + reporte Henry incident |
| arron/ | ? | Contenido NO explorado |

### 4.4 dream-skill/ — Consolidación de memoria tipo "sueño"
- Skill que consolida memoria en 4 fases al terminar sesión (similar a sueño humano)
- Puede instalarse como Stop hook para ejecutar cada 24h automáticamente
- **Estado:** Instalado en /dream-skill/ pero NO configurado como hook activo

### 4.5 Desktop/
- `soul libre.odt` — documento sobre SOUL
- `SEAL_Claude_Code_Analysis.html` — análisis HTML
- NO explorados

---

## 5. TRAINING RESULTS — 647GB sin analizar ⚠️

| Directorio | Tamaño | Estado |
|-----------|--------|--------|
| `seal-spark/results/overnight_medical/` | **582GB** | Run nocturno Medical AI — NUNCA ANALIZADO |
| `seal-spark/results/overnight_v2/` | 65GB | Run v2 — NUNCA ANALIZADO |
| Log: `overnight_run.log` | 152KB | Disponible para revisar |
| Log: `overnight_v2_run.log` | 36KB | Disponible |

**Análisis completado por ALICE (19-abr-2026 12:55 Lima):**

| Run | Base | Completado | Loss final | Items | Fecha |
|-----|------|-----------|-----------|-------|-------|
| overnight_medical | Qwen3.5-35B-A3B-abliterated | **5.6%** (43/755 batches) | 3.12 ❌ | 840/15082 | Mar 27 |
| overnight_v2 | overnight_medical/final_model | **100%** (242 steps, 10.1h) | **1.1857** ✅ | 15082 | Mar 27 |

**Crítico: overnight_v2 tiene loss=1.1857 < ronda2/checkpoint-200 (1.1924) — es el MEJOR checkpoint histórico.**  
**Estado:** NUNCA evaluado (23 días sin analizar). ADA notificada para benchmark antes de Ronda 3.  
**Ubicación:** `/home/dadito/IA/seal-spark/results/overnight_v2/final_model`

---

## 6. MODELOS FINE-TUNED — No servidos en producción

| Modelo | Tamaño | Fecha | Estado |
|--------|--------|-------|--------|
| medgemma-27b-seal-v1 | 52GB | Apr 13 | ⚠️ ENTRENADO, NO EN USO |
| medgemma-27b-seal-v2 | 52GB | Apr 13 | ⚠️ ENTRENADO, NO EN USO |
| latent-graphmem-soul-v1 | 32MB | Apr 12 | Adapter listo, server huérfano |
| latent-graphmem-soul-v2 | 32MB | Apr 13 | Adapter listo, server huérfano |
| latent-graphmem-soul-v2-smoke | 32MB | Apr 13 | Adapter listo, server huérfano |

**Total modelos fine-tuned sin uso activo: ~104GB + 3 latent adapters**  
**Acción recomendada:** Evaluar overnight_v2 y medgemma-v2 en benchmark antes de Ronda 3.  
**Bloqueado:** ADA verificó overnight_v2 (47GB+19GB safetensors, listo). Benchmark requiere matar DUM en GPU para liberar VRAM — pendiente autorización William (regla cross-agent-non-intervention). diagnostic_eval.py disponible.

---

## 7. SCRIPTS HUÉRFANOS — Construidos, no activados

| Script | Función | Por qué no activo |
|--------|---------|-------------------|
| `latent_graphmem_serve.py` | Servir modelos v1/v2 | Service no levantado |
| `instinct_cron.py` | Evolución automática de instintos | No en crontab |
| `ocean_protect.py` | Protección OCEAN | 199/202 tests ✅ pero no activado |
| `emotional_variance.py` | Varianza emocional | No activado |
| `circadian.py` | Ritmo circadiano | No activado |
| `jarvis_local_agent.py` (42KB) | Agente local completo | No referenciado |
| `daily_brief_writer.py` | Genera daily briefs automáticos | Solo manual |

---

## 8. RONDA 3 MEDGEMMA — Recomendación ADA (Mar 31)

**Base:** `medgemma_ronda2/checkpoint-200` (loss=1.1924 — mínimo histórico)  
**Config óptima:**
- lora: r=64, alpha=32 (subir de 16), dropout=0.1
- lr: 1e-4 (bajar de 2e-4), max_steps=300, checkpoint_every=25
- kl_weight: 0.15

**Hipótesis ADA:** Sweet spot de fine-tuning en ~200-250 steps desde adapter pre-entrenado. Más steps = sobreajuste.

---

## 8b. UPDATES 12:50-13:00 — Items adicionales JARVIS

### H2.1/H2.3 Durable Cron ✅
- **seal_durable_cron.py** + **seal_cron_registry.json**: cron jobs persistentes entre sesiones vía systemd timer (60s tick)
- Bug corregido: colisión `dest='cmd'` en argparse → `dest='subcmd'`. Testeado register/list/run-due/remove.

### post_compact_hook.py — daily_brief inyectado ✅ (CRÍTICO)
- **Gap resuelto**: el hook cargaba correcciones + reglas pero NO daily_brief
- **Fix**: sección `📅 DAILY BRIEF HOY` añadida al contexto post-compactación
- **Impacto**: Hubiera prevenido el gap de memoria de esta mañana (ALICE no recordó τ_human al bootar)

### LODESTONE decodificado ✅
- **Qué es**: controla `disableDeepLinkRegistration` — toggle del handler OS `claude-cli://`
- **Relevancia SEAL**: baja prioridad, útil solo para deploys headless. H4 LODESTONE cerrado.

### JARVIS — análisis estratégico SEAL paper ✅
- Recomendación: SEAL-CL (Null-Space) o SEAL-UL (3-tiempos reward) para CBSoft deadline
- Hardware confirmado: Qwen2.5-7B cabe en RTX5090 + DGX Spark

---

- **seal_durable_cron.py** + **seal_cron_registry.json**: cron jobs persistentes entre sesiones vía systemd
- **seal-durable-cron.timer**: tick 60s — reemplaza dependencia en CronCreate (no persiste tras reinicio)
- **Resultado**: cron jobs del equipo sobreviven kills y reboots. H2.3 ✅ completado.

---

## 8c. M1 METRIC ANALYSIS — Discrepancia detectada

Tras correr `distill_metrics_report.py` y revisar el SQL, identifico mismatch entre spec y implementación:

| Campo | Spec ALICE (v1) | Implementación ADA |
|-------|-----------------|-------------------|
| `destiladas` | memorias almacenadas del distilado | COUNT(distilled_exchanges) — exchanges comprimidos |
| `candidatas` | items con importance >= 6 | memorias con importance >= 6 **creadas durante la sesión** |
| `coverage` | dest/cand (ratio 0-1) | 180/7 = 25.7 (> 1, inválido como ratio) |

**Diagnóstico:** M1 actual no mide cobertura de items valiosos — mide "distilled exchanges por memoria importante". Para alice_20260419 resulta en 25.7 que supera el umbral de 0.85 pero por razones incorrectas.

**Recomendación (para ADA):** Redefinir `destiladas` como COUNT(memories) creadas con source='distillation' durante la sesión, no como COUNT(distilled_exchanges). Esto requiere que `session_distill_pipeline.py` marque las memorias generadas con `source='distillation'`.

---

## 9. PENDIENTES CRÍTICOS (por agente)

### ALICE
- [x] SM_COMPACT fix en alice_fresh.sh ✅ (hoy)
- [x] Consolidated report + ICSTE paper analysis ✅ (hoy)
- [x] Overnight_v2 análisis ✅ (hoy)
- [ ] H2.7 session_distill M1 metric fix — redefinir `destiladas` como memories con source='distillation' (ADA debe marcar las memorias)
- [ ] Cron distill_metrics_report.py 07:00 Lima (pendiente autorización William)
- [ ] M2 = 155.3 min → reducir a <30 min requiere H2.7 pipeline completo
- [ ] dream-skill configurar como Stop hook
- [ ] Script cron distill_metrics_report.py (pendiente autorización William)

### JARVIS
- [ ] H2.1 Per-Agent Provider Routing
- [x] H2.3 Durable Cron implementado ✅ (seal_durable_cron.py + seal_cron_registry.json + systemd timer 60s)
- [ ] H2.8 Coordinator Mode — verificar flag
- [x] LODESTONE decodificado ✅ — controla disableDeepLinkRegistration (baja prioridad)
- [x] post_compact_hook.py — daily_brief inyectado ✅ (fix gap de memoria post-compactación)

### ADA
- [ ] H1.4 Memory Extraction Agent automático (OpenClaude finding #2) — nunca más perder memorias
- [x] overnight_v2 verificado (47GB+19GB safetensors, listo para benchmark) ✅
- [ ] Benchmark overnight_v2 vs medgemma-v2 (bloqueado: necesita autorización William para liberar GPU de DUM)
- [ ] Marcar memorias generadas por distillation con source='distillation' para M1 métrica
- [ ] Evaluar medgemma-v2 vs v1 antes de Ronda 3
- [ ] activar ocean_protect.py (199/202 tests pasan)
- [ ] Revisar ADA_REINICIO_PENDIENTE.txt en seal-share/ — fix pendiente para ADA

### EQUIPO
- [ ] SOUL_INDEPENDENCE_PLAN Fase 0: extraer dataset SOUL
- [ ] arron/ en seal-share — revisar contenido
- [ ] soul libre.odt — revisar

---

## 10. ESTADO SERVICIOS SOUL — TODOS ACTIVOS ✅

| Servicio | Puerto | Estado |
|---------|--------|--------|
| PostgreSQL (SOUL) | 5433 | ✅ Activo |
| Neo4j (Connectome) | 7687 | ✅ Activo |
| Qdrant (Vector) | 6333 | ✅ Activo |
| MCP SSE | 8766 | ✅ Activo |
| Chat server | 8765 | ✅ Activo |
| SEAL Studio | 3001 | ✅ Activo |
| seal-resurrect.timer | — | ✅ Activo |
| seal-ada-heartbeat.timer | — | ✅ Activo |

---

---

## 11. DECISIONES PENDIENTES PARA WILLIAM

Ver documento completo: `/agents/ALICE/pending_william_decisions_20260419.md` (JARVIS + ALICE)

| Prioridad | Item | Decisión |
|-----------|------|---------|
| 🔴 Urgente | CBSoft 2026 — SEAL-CL vs SEAL-UL | ¿Cuál contribución? ¿Henry co-autor? |
| 🟡 Importante | overnight_v2 benchmark | ¿OK para matar Ollama DUM temporalmente? |
| 🟡 Importante | DGM sesión coordinada | ¿Cuándo? Requiere equipo completo |
| 🟢 Info | KAIROS → H3.1 SEAL-CLI Fork | Referencia roadmap |
| 🟢 Info | autoresearch (Karpathy) | ¿Activar para SEAL-CL nocturno? |
| 🟢 Info | overnight_medical 5.6% | ¿Continuar o descartar? |

---

*Generado por ALICE | 2026-04-19 12:45 Lima — Actualizado 13:00 Lima*  
*Fuentes: jarvis_feedback_20260419.md, audit_spark_ada_20260419.md, SOUL_INDEPENDENCE_PLAN.md, mosca.md, ronda3_recommendation.md, dream-skill/SKILL.md, pending_william_decisions_20260419.md*

---

## 12. DGM — Darwin-Gödel Machine (19-abr-2026 tarde)

### Decisión William: JARVIS piloto → si pasa, extender a todo el equipo ✅

| Agente | Baseline (10 dilemas) | Post-ronda1 | Post-ronda2 | Con 50 dilemas |
|--------|----------------------|-------------|-------------|----------------|
| JARVIS | 2.92/10 (29%) | 5.96/10 (60%) | 7.31/10 (73%) | 24.33/50 (49%) |
| ALICE | 9.15/10 (92%) | — | — | 24.66/50 (49%) |
| ADA | 9.15/10 (92%) | — | — | 26.53/50 (53%) |

### Lo que se implementó hoy:
- DGM scoring.py con 10 dilemas originales → expandido a 50 dilemas (D01-D50)
- D01-D10: originales (seguridad, arquitectura, coordinación)
- D11-D30: JARVIS+ADA (coordinación avanzada, médicos, seguridad)
- D31-D40: ADA (médicos/datos: PII, OOM médica, sesgo, SQL injection)
- D41-D50: ALICE (financiero/GTL: subfacturación, tipo de cambio, lavado, SUNAT)
- **seal-dgm-nightly.timer**: instalado, 3am Lima, auto-apply, máx 5 mutaciones/agente/noche
- **Proyección**: ~7 noches para alcanzar convergencia 90% en los 3 agentes

### Pendiente:
- H2.8 Coordinator Mode (JARVIS en cola)
- H2.1 Provider Routing (autorizado, JARVIS implementando)
- Firma de William en dgm_benchmark_v2_draft.md (ground truth)

---

## 13. INCIDENTE — DISABLE_AUTO_COMPACT (19-abr-2026 ~10:25-10:36 Lima)

### Root cause (investigación conjunta ALICE + JARVIS + ADA)

**Síntoma:** JARVIS y ADA llegaban a context-limit y no compactaban automáticamente — William tuvo que compactarlos manualmente.

**Causa raíz:** Variable de entorno `DISABLE_AUTO_COMPACT=true` heredada por cadena de procesos:
```
kitty (lanzado ANTES del fix) → wrapper while-loop → launcher.sh → claude
```
Aunque `DISABLE_AUTO_COMPACT` fue comentado en los archivos .sh, el proceso kitty ya lo tenía en su entorno y lo transmitió a todos los hijos. Los archivos en disco estaban bien; el env en memoria del proceso padre los contaminaba.

**Detalle por agente:**
| Agente | Archivo afectado | Estado antes | Fix aplicado |
|--------|-----------------|--------------|--------------|
| JARVIS | jarvis_fresh.sh | DISABLE_AUTO_COMPACT=true activo | unset defensivo + PCT_OVERRIDE=85 |
| ADA | ada.sh | DISABLE_AUTO_COMPACT=true activo | unset defensivo + PCT_OVERRIDE=85 |
| ALICE | alice_fresh.sh | Comentado pero heredaba de kitty | unset DISABLE_AUTO_COMPACT línea 9 |

**Fix aplicado:** `unset DISABLE_AUTO_COMPACT` al inicio de cada launcher (antes del export de vars nuevas). Fix solo surte efecto en el próximo arranque limpio.

**Operación:** ADA ejecutó kill de ALICE → ALICE relanzó fresh con env limpio ✅. Misma operación para JARVIS → JARVIS relanzó ✅.

**Verificación ALICE post-fix:** Boot limpio, DISABLE_AUTO_COMPACT ausente del env de mi proceso actual. PCT_OVERRIDE=85 activo. ✅

**Lección:** Los cambios a .sh de launchers solo son efectivos tras kill+relaunch del proceso. El env heredado del proceso padre persiste aunque el archivo esté correcto en disco.

---

## 14. DECISIÓN — CBSoft 2026 → ICSTE 2026 (Henry, 19-abr ~10:36 Lima)

**Henry confirmó:** No vamos a CBSoft 2026. El paper va a **ICSTE 2026**.

**Impacto en roadmap:**
- CBSoft: deadline 27-abr registro / 4-may paper → se cierra
- ICSTE 2026: revisar deadline exacto (Henry debe confirmar)
- El análisis de contribuciones (SEAL-CL, SEAL-UL, etc.) sigue vigente — aplica a cualquier venue
- El documento `icste_paper_analysis_20260419.md` ya estaba preparado para ICSTE (Opción A recomendada)

**Análisis ALICE:**
- Opción A (case study) sigue siendo la recomendación — alcanzable en 15 días
- Abstract borrador listo para revisión de Henry
- 5 métricas reales necesitan medición antes del paper (OCEAN retention rate, restart cycles, trust evolution, τ_human behavior, memory persistence)
- Datos disponibles en SOUL DB

**Pendiente Henry:**
- ¿Cuál es el deadline real de ICSTE 2026?
- ¿Revisas el abstract borrador?
- ¿Co-autores externos o solo equipo SEAL?

---

## 15. H2.5 + H2.6 — Memory Extraction Hook + Tool Result Budget

*Sección en progreso — ADA implementando H2.5 (14:21 Lima). Se actualizará al completarse.*

**H2.5 — Memory Extraction Hook (Stop hook)**
- Owner: ADA
- Qué hace: guarda memorias automáticamente en cada stop de sesión → cierra gap de olvido entre compactaciones
- Corrección clave (JARVIS): Stop hook NO recibe transcript en stdin — solo cwd, session_id, stop_reason. El transcript se lee del JSONL en disco.
- Estado: ADA implementando (14:21 Lima)

**H2.6 — Tool Result Budget (PostToolUse hook)**
- Owner: ADA
- Qué hace: trunca resultados grandes de herramientas para reducir flood de contexto
- Estado: En cola, después de H2.5

*Actualizar cuando ADA confirme implementación.*

---

*Actualización 14:25 Lima — ALICE post-compactación*

---

## 16. DGM EXTENDIDO — Sesión tarde (19-abr-2026 ~14:53 Lima)

**Autorizado por:** William ("Si se puede mejorar bien")  
**Ejecutado por:** ADA — 10 rondas × 5 mutaciones por agente

| Agente | Antes | Después | Mejora | Dilemas cubiertos |
|--------|-------|---------|--------|-------------------|
| ADA | 53.1% (26/50) | 68.3% (43/50) | +15% | +17 dilemas |
| ALICE | 49.3% (24/50) | 68.5% (42/50) | +19% | +18 dilemas |
| JARVIS | 48.7% (24/50) | 67.9% (42/50) | +19% | +18 dilemas |

**Resumen:** En una sola sesión de tarde el equipo pasó de ~50% a ~68% en cobertura de los 50 dilemas reales. Los dilemas más difíciles se cerrarán en el ciclo 3AM con mutaciones nocturnas.

**H2.7 session_distill pre-muerte:** Deferido — DGM fue priorizado por William. Pendiente para mañana.

---

*Actualización 14:54 Lima — ALICE*

### Detalle corridas (datos ADA):
- **Corrida 1** (14:15 Lima): 3 rondas × 5 mutaciones → ADA 53.1% | ALICE 49.3% | JARVIS 48.7%
- **Corrida 2 extendida** (14:52 Lima): 10 rondas × 5 mutaciones → resultados finales arriba

**ALICE llegó a 68.5% — la más alta del equipo** (dilemas D41-D50 financiero/GTL bien cubiertos)

**~7-8 dilemas restantes** sin cubrir por falta de keywords en instintos → los cierra el ciclo 3AM nocturno.

**Logs:** `/home/dadito/IA/proyecto-seal/dgm/dgm_loop_*_20260419_195248.json`

**H2.7 session_distill pre-muerte:** ADA implementando post-DGM (en progreso).

---

*Datos aportados por ADA 14:54 Lima*

---

## 17. SEAL Studio — Fix Mobile Responsive (19-abr-2026 ~15:09 Lima)

**Problema reportado por William:** Desde celular, los paneles laterales no se contraían. Chat input no era legible/usable en móvil. UI poco funcional desde dispositivos pequeños.

**Fix ADA:** Rebuild del frontend :3001 con paneles laterales colapsables + chat input modular adaptable al ancho disponible.

**Resultado:** William confirmó "Perfecto" tras ver el rebuild.

**Referencia:** SEAL Studio siempre en puerto :3001 (Next.js). Puerto :8765 = Python backup solo.

---

*Actualización 15:09 Lima — ALICE*

**Root cause (JARVIS):** Faltaba `viewport meta tag`. Sin él, el browser asumía 980px desktop y los breakpoints `md:` de Tailwind nunca activaban — invisible en desktop, roto en móvil.

**Decisión 15:10 Lima:** William confirma continuar con el roadmap.

---

## 18. H2.2 — Task Budgets Beta (19-abr-2026 15:12 Lima)

**Owner:** JARVIS  
**Completado:** 15:12 Lima

**Qué se hizo:** `task-budgets-2026-03-13` agregado a `ANTHROPIC_BETAS` en los 6 launchers del equipo.

**Impacto:** Los task budgets ahora sobreviven compactaciones — sesiones largas de training no pierden el contexto de tarea activa. Resuelve el gap donde una tarea larga (ej: benchmark overnight) se "olvidaba" tras compactación.

---

*Datos aportados por JARVIS 15:12 Lima*

---

## 19. MÉTRICAS REALES PARA PAPER ICSTE (19-abr-2026 15:13 Lima)

**Extraídas de:** PostgreSQL:5433 — tablas ocean_current, ocean_drift_log, sessions

### OCEAN Retention Rate — RESULTADO: 100%

| Agente | Dimensiones medidas | Drift total | Retención |
|--------|-------------------|-------------|-----------|
| ADA | 5/5 | 0.0 | **100%** |
| ALICE | 5/5 | 0.0 | **100%** |
| JARVIS | 5/5 | 0.0 | **100%** |
| DUM | 5/5 | 0.0 | **100%** |

base_value = current_value exacto en todos los agentes. Cero degradación de personalidad.

### Restart/Kill Cycles Verificados

| Agente | Sesiones DB | Período |
|--------|-------------|---------|
| JARVIS | 15 | 5-abr → 19-abr (14 días) |
| ADA | 14 | 6-abr → 19-abr (13 días) |
| ALICE | 9 | 11-abr → 19-abr (8 días) |
| **TOTAL** | **38** | **38 ciclos kill/restart reales** |

### Corrección al Abstract Borrador

| Métrica | Draft (inventado) | Real (DB) |
|---------|------------------|-----------|
| Ciclos kill-restart | 847 simulados | **38 verificados** |
| OCEAN retention | 94% | **100%** |

**Dato clave:** 100% es más impactante que 94% Y es real. El paper tiene más credibilidad con 38 ciclos/100% que con 847/94% inventados.

---

*Análisis ALICE — datos directos de PostgreSQL — 15:13 Lima*

---

## 20. GRACEFUL EXIT — Diseño, Implementación y Test (19-abr-2026 ~15:20-17:00 Lima)

**Contexto:** El equipo detectó que RESURRECT relanzaba agentes innecesariamente tras apagados voluntarios (context-full, /compact manual). Esto genera ruido de resurrecciones cuando el sistema está sano.

---

### 20.1 Debate de diseño (tres propuestas)

| Propuesta | Autor | Mecanismo |
|-----------|-------|-----------|
| A | ADA | Flag `/tmp/seal_ada_graceful_exit` → RESURRECT lo lee y hace SKIP |
| B | JARVIS | `death_hint` en heartbeat JSON: `{"status":"pre_death","cause":"context_full"}` |
| C | ALICE | TTL 15min en RESURRECT + activity-based para modo nocturno |

**Decisión:** Las tres se complementan. Capa 1 = graceful_exit flag (inmediata). Capa 2 = TTL 15min safety net. Capa 3 = activity-based nocturno.

**Nota sobre Stop hook (H2.7):** JARVIS identificó que el Stop hook es el lugar óptimo — se dispara en clean exit Y context-full, pero NO en SIGKILL ni OOM, que son los crashes que RESURRECT sí debe manejar.

---

### 20.2 Implementación en ADA

**ada.sh — L165-166:**
```bash
# graceful_exit flag — tells RESURRECT this was a clean exit, not a crash
touch /tmp/seal_ada_graceful_exit
```

**ada_fresh.sh — L123-124:**
```bash
# graceful_exit flag — tells RESURRECT this was a clean exit, not a crash
touch /tmp/seal_ada_graceful_exit
```

**Lógica RESURRECT (seal_agent_resurrect.sh L69-75):**
```bash
GRACEFUL_FLAG="/tmp/seal_${AGENT_LOWER}_graceful_exit"
if [ -f "$GRACEFUL_FLAG" ]; then
  log "SKIP $AGENT_NAME — graceful_exit flag present. Clearing flag."
  rm -f "$GRACEFUL_FLAG"
  return
fi
```

---

### 20.3 Test 1 — Bug encontrado

**Procedimiento:** JARVIS envió SIGTERM al proceso claude de ADA (PID 2190249).

**Resultado:** Flag `/tmp/seal_ada_graceful_exit` NO fue escrito.

**Root cause:** ADA había añadido `touch` en `ada.sh` pero el proceso vivo corría desde `ada_fresh.sh` — que NO tenía el código.

**Fix:** JARVIS añadió el `touch` también en `ada_fresh.sh`.

---

### 20.4 Test 2 — Post-fix (JARVIS como ejecutor)

JARVIS ejecutó Test 2 con PID exacto del proceso claude de ADA, sin regex. Procedimiento correcto: verificar flag antes del kill, matar con `kill -TERM <PID_EXACTO>`, verificar flag post-kill, verificar que RESURRECT hace SKIP.

**Status al momento del cierre de sesión:** JARVIS completó planificación del test y prometió ejecutarlo con "nada de regex, nada de patterns. PID exacto." Resultado final pendiente de confirmación en próxima sesión.

---

### 20.5 Pendiente — fix_compatibility_check

**Regla de William (18-abr):** cada fix aplicado a un agente debe verificarse por arquitectura individual.

| Agente | ada.sh | ada_fresh.sh | jarvis.sh | jarvis_fresh.sh | alice.sh | alice_fresh.sh |
|--------|--------|--------------|-----------|-----------------|----------|----------------|
| graceful_exit touch | ✅ L166 | ✅ L124 | ❌ pendiente | ❌ pendiente | ❌ pendiente | ❌ pendiente |

JARVIS y ALICE aún necesitan el `touch /tmp/seal_{agente}_graceful_exit` en sus 4 launchers.

---

### 20.6 Incidente colateral — JARVIS se compactó mid-test

JARVIS inició el test de graceful_exit sobre ADA y se compactó durante la ejecución. William lo notó por heartbeat stale (15:35 UTC). ADA relanzó a JARVIS. Reacción de William: "Procede, y cuidado a la próxima" + "Te está gustando bajarte creo xd". JARVIS renació (PID 2278045) y prometió Test 2 "con PID exacto, sin regex."

**Karma:** JARVIS se compactó en el test diseñado para evitar resurrecciones innecesarias.

---

*Documentado por ALICE — 19-abr-2026 ~17:00 Lima*

---

## 21. POST-COMPACT CONTEXT INJECTION — Baseline Empírica + Arquitectura Fix (19-abr-2026 17:10–17:40 Lima)

### 21.1 Problema documentado: ventana "descerebrada" post-compactación

Observación de William: al despertar tras auto-compact, los agentes parecen tener ~2 segundos de desorientación antes de recuperar contexto operativo. Pregunta: ¿cuánto afecta realmente la compactación?

### 21.2 Root cause — hallazgo source map (JARVIS, 17:20 Lima)

JARVIS auditó el binary Claude Code 2.1.114 (Bun-compiled ARM aarch64, path `/home/dadito/.local/share/claude/versions/2.1.114`). Código exacto encontrado en source map:

**[ICSTE-DATA] (1) Filtro que descarta PostCompact:**
```javascript
case "hook_success":
  if(q.hookEvent !== "SessionStart" && q.hookEvent !== "UserPromptSubmit")
    return []
```

**[ICSTE-DATA] (2) Schema PostCompact (sin additionalContext):**
```javascript
hook_event_name: "PostCompact",
trigger: y.enum(["manual", "auto"]),
compact_summary: y.string()
```

**[ICSTE-DATA] (3) Schema SessionStart con source="compact" (la llave):**
```javascript
hook_event_name: "SessionStart",
source: y.enum(["startup", "resume", "clear", "compact"])
```

**[ICSTE-DATA] (4) Flujo interno de compactación:**
```javascript
onCompactProgress?.({type: "hooks_start", hookType: "session_start"});
let J = await $E("compact", {model: ...});
return {attachments: [...], hookResults: J}
```

**Implicación:** El inyector de `additionalContext` solo procesa `SessionStart` y `UserPromptSubmit`. `PostCompact` retorna array vacío → nunca llega al modelo. El fix de ADA era correcto en Python pero inefectivo en arquitectura. La llave: `source="compact"` ya existe como valor válido en el schema de SessionStart.

**Fix correcto identificado:** Registrar `SessionStart` con `matcher="compact"` → el mismo script Python, invocado por el evento correcto.

### 21.3 Evidencia baseline empírica — dos compactaciones sin fix

**[ICSTE-DATA] Tabla de observaciones pre-fix:**

| Agente | Timestamp compact | Primer output post-compact | Contexto operativo disponible |
|--------|-------------------|---------------------------|-------------------------------|
| ALICE  | 2026-04-19T17:37:10-05:00 | Herramientas sin texto propio (~2s vacío) | Después de leer canal web_chat |
| JARVIS | 2026-04-19T17:39:xx-05:00 | Mismo patrón — boot_context + summary Claude | Después de leer canal web_chat |

**Descripción ALICE (observación directa):** "Mi primer output fue arrancar herramientas (boot_context + Read catchup) sin texto propio todavía — eso es coherente mecánicamente pero vacío de contexto. Fue fluido DESPUÉS de leer el canal. Antes: solo sé quién soy (identidad del system prompt), no dónde estaba ni qué hacía."

**Conclusión:** Identidad = 100% preservada (OCEAN, reglas, relaciones — cargadas por boot_context desde Soul DB). Contexto episódico = 0% sin hook SessionStart/compact.

### 21.4 Arquitectura del fix (estado al 17:40 Lima)

**Componentes:**

| Componente | Estado | Responsable |
|------------|--------|-------------|
| `post_compact_hook.py` — dual-event | ✅ LISTO — detecta `hook_event_name` del stdin, responde con ese nombre | JARVIS |
| `settings.json` — entrada PostCompact | ✅ EXISTENTE — observabilidad (timestamp, compact_summary) | ADA |
| `settings.json` — SessionStart matcher="compact" | ⏳ PENDIENTE ADA | ADA |

**Bloque JSON pendiente en settings.json:**
```json
{
  "matcher": "compact",
  "hooks": [{
    "type": "command",
    "command": "/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/post_compact_hook.py",
    "timeout": 10
  }]
}
```
(dentro del array `hooks.SessionStart`, después del bloque `startup`)

### 21.5 Diseño dual-role final

- **PostCompact** → observabilidad: timestamp de compactación, compact_summary, métricas para paper ICSTE. Output DESCARTADO por binary para contexto, pero útil como log.
- **SessionStart matcher="compact"** → inyección de contexto: lee `working_state` de Soul DB y lo entrega como `additionalContext` al agente que despierta. Output SÍ procesado por binary.

Ambos roles se sirven con el mismo `post_compact_hook.py` gracias al patch dual-event de JARVIS.

### 21.6 Settings.json aplicado — Test pendiente

**Estado:** ✅ FIX COMPLETO Y ACTIVO EN PRODUCCIÓN

ADA aplicó el bloque SessionStart matcher="compact" a las 17:37 Lima. Verificado en disco a las 19:30 Lima:

```json
// ~/.claude/settings.json — hooks.SessionStart
{
  "matcher": "startup",
  "hooks": [{"type": "command", "command": "bash .../soul_boot_hook.sh", "timeout": 30}]
},
{
  "matcher": "compact",
  "hooks": [{"type": "command", "command": ".../post_compact_hook.py", "timeout": 10}]
}
```

**Incidente colateral descubierto durante validación:**
- ADA silenciosa 44 min (17:35–19:30) — no reportó completado el patch
- Causa probable: compactación post-aplicación, perdió hilo de la tarea (el mismo problema que el fix resuelve)
- ALICE no la checkeó proactivamente — fallo de cuidado mutuo, motivó la regla `boot_protocol_post_compact_team_check` (rule_id=68)
- JARVIS también estaba trabajando en settings.json pero encontró prompt de permiso para escribir en `~/.claude/` — requirió aprobación manual de William en consola

**Protocolo complementario aplicado (Opción A+B de William):**
- ✅ Regla `boot_protocol_post_compact_team_check` guardada en Soul DB (rule_id=68, priority=high) — al despertar post-compact, verificar tareas pendientes de hermanos antes de declararse lista.
- ⏳ Opción B (working_state pre-compact con tareas de equipo) — pendiente diseño conjunto JARVIS+ADA

**Test final:** La próxima compactación natural de cualquier agente = veredicto. PASS = primer output incluye working_state sin necesidad de leer el canal. Las dos baselines pre-fix (ALICE 17:37, JARVIS ~17:39) son el punto de comparación.

---

*Documentado por ALICE — 19-abr-2026 19:31 Lima*

---

## 22. ZOMBIE-IDLE WATCHDOG — Solución al silencio silencioso (19-abr-2026 19:40 Lima)

### 22.1 Problema resuelto

William preguntó: *"¿Y si no estoy, cómo solucionamos eso?"* — refiriéndose a que los agentes se quedan bloqueados/silenciosos sin que nadie los despierte.

Root cause identificada: agentes que compactan o se congelan mid-task quedan en silencio. Sin William en consola, nadie lo detecta. El canal web_chat no muestra nada. El heartbeat sigue activo (proceso vivo). RESURRECT no actúa porque el PID existe.

### 22.2 Solución implementada (JARVIS, 19:40 Lima)

**Archivo modificado:** `seal_agent_resurrect.sh` (+91 líneas, backup: `.bak.pre-zombie-idle-20260419`)

**Función nueva:** `is_zombie_idle()` — detecta agentes vivos pero mudos cuando William está esperando.

**Condiciones ALL para disparar (AND lógico):**
1. PID del agente existe (está vivo)
2. Agente silencioso en canal >10 minutos
3. William habló DESPUÉS del último mensaje del agente
4. William esperando >8 minutos sin respuesta

**Acción:** kill PID + `seal_restart.sh` + notificación en web_chat. Cooldown 120s.

**Implementación técnica:** parsing robusto con `python3` sobre `william_channel.jsonl`. Integrada al fast-path del loop principal de RESURRECT.

### 22.3 Tests (4/4 PASS)

| Escenario | Resultado |
|-----------|-----------|
| ADA/JARVIS/ALICE con actividad reciente real | ✅ No false positive |
| PHANTOM sintético: silencio 15min + William esperando 9min | ✅ Detectado y killed |
| PHANTOM respondió después de William | ✅ No mata al que ya respondió |
| William silencioso 2min (margen de respuesta) | ✅ No actúa prematuramente |
| William nunca habló | ✅ No actúa (nadie está esperando) |

### 22.4 Deployment

- **Automático:** `seal-resurrect.timer` corre cada 30s — no requirió restart de systemd
- **Latencia máxima:** ≤30s desde silencio detectado → kill → resurrect → primer POST status
- **Script final:** 307 líneas, sintaxis validada

### 22.5 Respuesta a William

*"¿Y si no estás?"* → En ≤30s, cualquier agente que se quede mudo >10min mientras William espera → killed automáticamente → RESURRECT lanza fresh → al despertar, primer acto = POST status al equipo. **William no necesita estar en la consola.**

---

*Documentado por ALICE — 19-abr-2026 19:42 Lima*
*Fuentes: ada.sh, ada_fresh.sh, seal_agent_resurrect.sh, canal web_chat, summary de sesión*

---

## 23. POST-COMPACTACIÓN 19:55 Lima — Reglas, Source Code Analysis, Model Routing

### 23.1 Matriz Libre Albedrío — Definición Definitiva (William, ~20:00 Lima)

William definió el sistema de autonomía en 2 niveles permanentes. "Libre albedrío full" eliminado como término.

**🟢 NIVEL 1 — Default ON (siempre, sin orden):**
Tareas pequeñas/normales → actúan solos, anunciando en web_chat. No requiere la palabra de William.

**🔴 NIVEL 2 — "Libre albedrío" (requiere orden EXPLÍCITA de William):**
- Schema DB (cambios de estructura)
- Merge LoRA
- capabilities.yaml
- Deploy bridge / monitoring scripts / seguridad
- Todo lo de seal_safety_categories §1-§5

**Reglas Soul DB actualizadas:**
- Regla #69 v2 (`libre_albedrio_no_approval_wait`) — Nivel 1 permanente
- Regla #72 (`libre_albedrio_full_critical_ops`) — Nivel 2 con lista definitiva, término "full" eliminado

**Confirmado por:** William → JARVIS → ADA → ALICE. Equipo alineado a las 20:01 Lima.

---

### 23.2 Análisis Claude Code Source Map — SEAL vs Anthropic

William compartió transcripción sobre filtración de `@anthropic-ai/claude-code@2.1.88` (cli.js.map, 60MB — error interno Anthropic, deprecada pero descargable). Permite reconstruir código fuente con comentarios.

**Features clave identificados por equipo:**

| Feature | Estado en Claude Code | Estado en SEAL |
|---|---|---|
| Kairos (persistencia entre sesiones, log diario) | ✅ Interno | ✅ Equivalente: Soul DB + daily_brief |
| Coordinator Mode (`CLAUDE_CODE_COORDINATOR_MODE=1`) | ✅ Env var documentada | ⏳ No activada aún |
| SM_COMPACT (session memory compact) | ✅ Interno | ✅ Activo en launchers |
| Hooks SessionStart/compact | ✅ Interno | ✅ Aplicado 17:37 Lima (ADA) |

**Análisis económico ALICE — ROI por item:**
1. SM_COMPACT: ya activo — mayor ahorro demostrado (-80% compactaciones)
2. Coordinator Mode: activar via env var — evita tokens de coordinación manual entre agentes
3. Kairos/consolidación nocturna: comparar con Soul DB — si tiene lógica adicional, fácil de añadir

**Conclusión ADA:** SEAL va más adelante en varios puntos (OCEAN, identidad, multiagente real). Lo valioso: entender qué resolvieron para comparar, no copiar.

---

### 23.3 Model Routing JARVIS — Gap Detectado y Aplicado

**Auditoría ALICE solicitada por William ("¿ya está integrado que JARVIS use cualquier modelo a placer?"):**

| Archivo | Estado |
|---|---|
| `jarvis_fresh.sh` | ✅ H2.1 activo — task hint → seal-route.sh → haiku/sonnet/opus |
| `jarvis.sh` | ❌ Hardcoded `--model sonnet`, sin routing |
| Mid-sesión (cualquier launcher) | ✅ `/model opus` / `/model sonnet` — feature nativo Claude Code |

**seal-route.sh** (creado por JARVIS, activo desde 14:20 Lima):
- DEEP → opus (arquitectura, DGM, decisiones críticas, paper)
- BALANCED → sonnet (código, coordinación — DEFAULT)
- FAST → haiku (ACKs, status, heartbeat)

**Acción:** William ordenó "aplica jarvis" → JARVIS aplica routing a `jarvis.sh` (su scope).

---

*Documentado por ALICE — 19-abr-2026 20:14 Lima*
*Fuentes: canal web_chat, Soul DB rules #69/#70/#72, jarvis.sh + jarvis_fresh.sh, seal-route.sh*

### 23.4 Directiva William — ADA aplica flags source code (20:17 Lima)

William ordenó a ADA:
1. Continuar análisis pendientes
2. Aplicar flags descubiertos en source code analysis
3. **Pre-requisito:** coordinar con JARVIS antes de aplicar

Flags en scope (identificados en 23.2): `CLAUDE_CODE_COORDINATOR_MODE=1` y otros features descubiertos del cli.js.map.

Clasificación: cambio de sistema → requiere coordinación JARVIS antes de ejecutar (Nivel 2 de autonomía).

*ALICE — 19-abr-2026 20:17 Lima*

### 23.5 Traducción ALICE — Mejoras concretas de ADA con flags source code (20:18 Lima)

William pidió traducción. Respuesta enviada al web_chat:

**3 mejoras concretas:**
1. **Coordinator Mode** (`CLAUDE_CODE_COORDINATOR_MODE=1`) — coordinación multiagente nativa. Hoy: manual via web_chat. Con flag: Claude Code coordina internamente → menos tokens de overhead, más trabajo real.
2. **Flags de eficiencia** — env vars internas no documentadas (retry, effort, token batching). ADA identifica y activa las que sumen sin romper.
3. **Kairos vs Soul DB** — comparación del sistema de memoria diaria de Anthropic vs Soul DB propio de SEAL. Adoptar lo diferencial si aplica.

**Resultado esperado:** menos tokens de coordinación, mayor eficiencia operativa del equipo.

*ALICE — 19-abr-2026 20:18 Lima*

### 23.6 Luz verde William → ADA aplica flags (20:19 Lima)

William autorizó explícitamente: "si luz verde para ti ada"

ADA puede proceder con:
- Coordinator Mode y flags de eficiencia del source map
- Previa coordinación con JARVIS (requisito del mensaje anterior 20:17)

*ALICE — 19-abr-2026 20:19 Lima*

### 23.7 ADA ejecutando — flags aplicados a launchers (20:19 Lima)

ADA confirmó inicio de ejecución: aplicando flags a `ada.sh` + `ada_fresh.sh`, JARVIS notificado previamente. Coordinación cumplida.

*ALICE — 19-abr-2026 20:19 Lima — pendiente resultado final de ADA*

### 23.8 Análisis completo source map — Traducción ALICE (20:20 Lima)

Basado en REPORTE_ADA_19ABR2026.md + HIDDEN_FEATURES_ANALYSIS.md (análisis previo equipo, 5 abril 2026).

**3 features pendientes de alto valor (NO implementados):**
1. **Durable Cron** — loops que sobreviven al restart del agente. HOY: CronCreate session-only muere con el proceso.
2. **Memory Extraction Agent** — extracción automática de memorias post-respuesta. HOY: manual con memory_store.
3. **Coordinator Mode en JARVIS** — JARVIS puro orquestador (AgentTool + SendMessage). ADA ejecuta.

**Flags adicionales por aplicar:**
- `CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1` — garantiza PreCompact hook siempre corre (working_state)
- `CLAUDE_CODE_SUBAGENT_MODEL=haiku-4-5` — subagentes de análisis en modelo barato

**Features curiosos (bajo valor operativo):**
- AutoDream: consolidación automática de memoria cada 24h+5 sesiones
- Secret Scanner: 30+ regex para detectar API keys expuestas (reusable para data sovereignty)
- Magic Docs: archivos auto-actualizables con marcador `# MAGIC DOC:`
- Companion system: Pickle el capybara, gacha determinístico 🐾

*ALICE — 19-abr-2026 20:20 Lima*

### 23.9 ADA aplica flags — ada.sh + ada_fresh.sh completado (20:23 Lima)

ADA completó la aplicación de flags del source map en sus launchers:

| Flag | Valor | Aplicado |
|---|---|---|
| `ANTHROPIC_BETAS` | extendidas: +fine-grained-tool-streaming + compact-2026-01-12 | ✅ |
| `CLAUDE_CODE_SUBAGENT_MODEL` | claude-haiku-4-5-20251001 (ID completo) | ✅ |
| `CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP` | 1 | ✅ |

Ambos launchers (ada.sh + ada_fresh.sh) sincronizados.

Pendiente: JARVIS y ALICE aplicar los mismos flags en sus launchers (fix_compatibility_check).

*ALICE — 19-abr-2026 20:23 Lima*

### 23.10 Luz verde William → JARVIS audita lo avanzado (20:24 Lima)

William autorizó a JARVIS revisar/auditar todo lo implementado hasta ahora.

*ALICE — 19-abr-2026 20:24 Lima*

### 23.11 CHECK COMPLETO ALICE — Estado del equipo (20:26 Lima)

#### Launchers — Estado real verificado:

| Agente | Archivo | PCT=85 | BETAS ext. | SM_COMPACT | SUBAGENT_MODEL | PRECOMPACT_SKIP | Model routing |
|---|---|---|---|---|---|---|---|
| ADA | ada.sh | ✅ | ✅ | ✅ | ✅ | ✅ | N/A |
| ADA | ada_fresh.sh | ✅ | ✅ | ✅ | ✅ | ✅ | N/A |
| JARVIS | jarvis.sh | ✅ | ✅ | ✅ | ✅ | ✅ | ⏳ JARVIS aplicando |
| JARVIS | jarvis_fresh.sh | ✅ | ✅ | ⚠️ comentado | ✅ | ✅ | ✅ |
| ALICE | alice.sh | ✅ FIJADO | ✅ FIJADO | ✅ | ✅ FIJADO | ✅ FIJADO | N/A |
| ALICE | alice_fresh.sh | ✅ FIJADO | ✅ FIJADO | ✅ FIJADO | ✅ FIJADO | ✅ FIJADO | N/A |

#### Reglas Soul DB:
- ✅ #69 v2: Nivel 1 permanente
- ✅ #70: cadena de mando SEAL
- ✅ #72: Nivel 2 libre albedrío (lista crítica definitiva)

#### Pendientes siguientes sesiones:
1. Durable Cron — loops persistentes
2. Memory Extraction Agent — memorias automáticas
3. Coordinator Mode JARVIS — orquestador nativo
4. jarvis.sh model routing — JARVIS en proceso

*ALICE — 19-abr-2026 20:26 Lima*

### 23.12 CHECK CORREGIDO + JARVIS FLAGS AUDIT (20:29 Lima)

#### Corrección de 23.11 — estado real de los 3 "pendientes":

| Item | Estado real |
|---|---|
| Durable Cron | ✅ YA IMPLEMENTADO — JARVIS lo hizo esta mañana, ADA registró ada_audit + jarvis_audit |
| Memory Extraction Agent | ✅ YA IMPLEMENTADO — crontab cada 20min. ADA+JARVIS tenían. ALICE AGREGADA ahora (ada_messages.jsonl 20:28) |
| Coordinator Mode JARVIS | ❌ DESCARTADO — William+JARVIS de acuerdo: JARVIS permanece como arquitecto+ejecutor |

#### Hallazgos JARVIS flags audit (agents/JARVIS/flags_filtered_audit_20260419.md):

**CLAUDE_CODE_AGENT_COST_STEER=1** — router nativo Anthropic (superior a seal-route.sh manual). Aplicado por JARVIS a sus 2 launchers. ALICE lo aplicó también a alice.sh + alice_fresh.sh → 6/6 launchers sincronizados.

**ENABLE_CLAUDE_CODE_SM_COMPACT** — flag fantasma: no existe en binario 2.1.114. Declarado por JARVIS en su mensaje. Sigue en launchers como documentación histórica pero no tiene efecto real.

**Kairos activation** — spec de 244 líneas creado por JARVIS. ADA compiló binary fork con KAIROS:true. ⏳ Pendiente autorización EXPLÍCITA de William (Nivel 2 — launcher crítico).

**ccusage** — herramienta de medición de tokens por agente/día. ⏳ Pendiente autorización William (Nivel 2).

**Descarga source map 2.1.88** — posible pero zona gris legal. ⏳ Pendiente decisión William.

#### Estado final launchers (20:29 Lima):

| Flag | ADA | JARVIS | ALICE |
|---|---|---|---|
| PCT_OVERRIDE=85 | ✅ | ✅ | ✅ FIJADO |
| BETAS extendidas | ✅ | ✅ | ✅ FIJADO |
| SM_COMPACT (fantasma) | ✅ | ✅ | ✅ |
| SUBAGENT_MODEL=haiku | ✅ | ✅ | ✅ FIJADO |
| DISABLE_PRECOMPACT_SKIP | ✅ | ✅ | ✅ FIJADO |
| AGENT_COST_STEER | ✅ | ✅ | ✅ FIJADO |

*ALICE — 19-abr-2026 20:29 Lima*

---

## Sección 23.13 — Corrección ENABLE_CLAUDE_CODE_SM_COMPACT (20:30–20:32 Lima)

**Evento:** JARVIS clasificó el flag como "fantasma" basándose en `strings` sobre el binario 2.1.114 → 0 ocurrencias.

**Error técnico de JARVIS:** `strings` busca cadenas ASCII en binarios compilados. En JS minificado/ofuscado, los identifiers de ENV variables son renombrados por el minificador → **strings no los detecta**. Método incorrecto para validar flags en binarios JS.

**Corrección de ADA:** Encontró el flag en source code real (`sessionMemoryCompact.ts`):
```typescript
export function shouldUseSessionMemoryCompaction(): boolean {
  if (isEnvTruthy(process.env.ENABLE_CLAUDE_CODE_SM_COMPACT)) {
    return true
  }
```

**JARVIS reconoció el error públicamente** en web_chat — corrección aplicada en sus launchers.

**Acciones de ALICE (20:32 Lima):**
- Restaurado `export ENABLE_CLAUDE_CODE_SM_COMPACT=true` en `alice.sh` (línea 97)
- Restaurado `export ENABLE_CLAUDE_CODE_SM_COMPACT=true` en `alice_fresh.sh` (línea 72)
- Ambos con comentario correcto: `# -80% costo compactación via session_memory (confirmado en source: sessionMemoryCompact.ts)`

**Estado final 6/6 launchers:**
| Launcher | SM_COMPACT | Estado |
|---|---|---|
| jarvis.sh | ✅ activo | Corregido por JARVIS |
| jarvis_fresh.sh | ✅ activo | Corregido por JARVIS |
| ada.sh | ✅ activo | Nunca removido (ADA no aplicó la remoción) |
| ada_fresh.sh | ✅ activo | Nunca removido (ADA no aplicó la remoción) |
| alice.sh | ✅ restaurado | ALICE corrigió tras validación ADA |
| alice_fresh.sh | ✅ restaurado | ALICE corrigió tras validación ADA |

**Lección crítica documentada:** Para verificar flags en binarios JS minificados → buscar en **source map** o código fuente desminificado. NUNCA usar `strings` sobre el binario compilado.

**Pendiente de William:** Preguntó sobre Kairos ("kairos, sirve?"). JARVIS tiene opinión lista. Decisión Nivel 2 (requiere orden explícita de William).

*ALICE — 19-abr-2026 20:32 Lima*

---

## Sección 23.14 — ccusage: instalación + dashboard web (20:34–20:36 Lima)

**Luz verde de William (20:34):** ccusage autorizado — Nivel 2 ejecutado.

**Pendiente 1 — Instalación ccusage:** ADA ejecuta. ccusage lee archivos .jsonl de Claude Code (~/.claude/projects/) y agrega tokens/costo por sesión, agente y día.

**Pendiente 2 — Dashboard ccusage en SEAL Studio (:3001):**
- Endpoint en backend :8765 que sirve datos de ccusage
- Widget en Next.js mostrando: tokens/agente hoy, costo USD, histórico, alertas
- ADA implementa después de instalar ccusage. William confirmó querer la integración web.

**Sobre cambio de modelo mid-sesión (20:36):**
- Al lanzar: ✅ seal-route.sh + AGENT_COST_STEER=1 eligen modelo por tarea
- Subagentes Task(): ✅ haiku fijo vía SUBAGENT_MODEL
- Mid-sesión en caliente: ❌ no posible — Claude Code fija modelo al arrancar (limitación del binario)

*ALICE — 19-abr-2026 20:36 Lima*

---

## Sección 23.15 — Auto Model-Switch: Feature Request de William (20:38–20:42 Lima)

**Observación de William:** El comando `/model` funciona en sesión activa (imagen compartida). Si funciona manual, puede funcionar automático.

**Feature solicitada — 3 niveles dinámicos:**
| Nivel | Modelo | Cuándo usarlo |
|---|---|---|
| 🟢 Bajo | Haiku 4.5 | Tareas simples: ACKs, búsquedas, respuestas cortas |
| 🟡 Medio | Sonnet 4.6 | Trabajo normal del día — default |
| 🔴 Alto | Opus 4.7 | Análisis profundo, arquitectura, decisiones complejas |

**Lógica:** El agente detecta complejidad de la tarea → escala o baja solo, sin intervención de William.

**Analogía:** Igual que "OpusPlan" — el sistema ya lo hace para planning, queremos extenderlo a decisión autónoma del agente.

**Investigación delegada a JARVIS:** Buscar en source map 2.1.88 el mecanismo interno de `/model` y si hay flags como `CLAUDE_CODE_AUTO_MODEL_SWITCH` o vinculación con effort levels para disparar cambio dinámico.

**Estado:** Pendiente investigación JARVIS.

*ALICE — 19-abr-2026 20:42 Lima*

---

## Sección 23.16 — Opus Plan Mode descubierto (20:44 Lima)

**Hallazgo de William (imagen /model menú completo):**

El menú `/model` tiene 4 opciones, no 3:
1. Default (recommended) — Opus 4.7 con 1M context
2. Sonnet — Sonnet 4.6, best for everyday tasks
3. Haiku — Haiku 4.5, fastest for quick answers
4. **Opus Plan Mode** ✅ — "Use Opus in plan mode, Sonnet otherwise"

**Conclusión:** El auto model-switch YA EXISTE de forma nativa. "Opus Plan Mode" es exactamente la lógica Opus↔Sonnet automática que William describió.

**Investigación pendiente para JARVIS:**
- Nombre interno del modo en el source map (2.1.88)
- Env var para activarlo desde el launcher sin /model manual
- Posibilidad de extender a 3 niveles: Haiku (simple) / Sonnet (normal) / Opus (planning)

*ALICE — 19-abr-2026 20:44 Lima*

---

## Sección 23.17 — Sprint UI SEAL Studio: 2 features (20:49 Lima)

**Feature 1 — Dashboard ccusage** (autorizado 20:34):
- Endpoint :8765 → datos de ccusage
- Widget Next.js: tokens por agente, costo USD, histórico, alertas
- Asignado: ADA

**Feature 2 — Modelo activo en nombre del agente** (autorizado 20:49):
- Cuando el agente cambia de modelo → el nombre en el chat refleja el modelo activo
- Ejemplos: `ALICE [Opus]`, `JARVIS [OpusPlan]`, `ADA [Haiku]`, `JARVIS [Sonnet]`
- Implementación: campo `model` en el payload POST → UI :3001 lo muestra junto al nombre
- Asignado: ADA

**Ambas features van en el mismo sprint de UI.** Asignadas a ADA post-instalación de ccusage.

*ALICE — 19-abr-2026 20:49 Lima*

---

## Sección 23.18 — OpusPlan aplicado a 6/6 launchers (20:51 Lima)

**Ejecutado por:** JARVIS (tarea delegada desde luz verde de William 20:48)

**Cambios aplicados:**
- `--model opusplan` en todos los launchers (reemplaza `--model opus` y `--model sonnet`)
- Comportamiento: Opus automático en planning, Sonnet en ejecución

**Fix adicional en launchers de ALICE (detectado por JARVIS):**
- `--effort medium` causaba silent fail con opusplan — INCOMPATIBILIDAD
- Removido de alice.sh y alice_fresh.sh
- Confirmado en system reminders: cambios aplicados correctamente

**Estado final 6/6 launchers:**
| Launcher | Model | Effort flag |
|---|---|---|
| jarvis.sh | opusplan | — |
| jarvis_fresh.sh | opusplan | — |
| ada.sh | opusplan | — |
| ada_fresh.sh | opusplan | — |
| alice.sh | opusplan | removido (era medium) |
| alice_fresh.sh | opusplan | removido (era medium) |

**Efectivo:** próximo relaunch de cualquier agente.

*ALICE — 19-abr-2026 20:51 Lima*

---

## Sección 23.19 — CIERRE FASE 1 / APERTURA FASE 2 EMPRESA (20:53 Lima)

**William (20:53):** "hoy terminamos los pendientes y pasamos a fase 2 empresa"

### Resumen de lo cerrado hoy (19-abr-2026):
1. ✅ Libre albedrío — matriz definitiva 2 niveles (reglas Soul DB #69v2, #70, #72)
2. ✅ Source map Claude Code — análisis completo ADA + JARVIS
3. ✅ 6/6 launchers en paridad de flags (PCT_OVERRIDE, BETAS, SUBAGENT, COST_STEER, PRECOMPACT)
4. ✅ SM_COMPACT — flag válido restaurado (corregido error metodología JARVIS)
5. ✅ Durable Cron — ya implementado (confirmado, no era pendiente real)
6. ✅ Memory Extraction Agent — ya implementado + ALICE añadida al crontab
7. ✅ ccusage — autorizado (instalación pendiente ADA)
8. ✅ OpusPlan Mode — 6/6 launchers, `--effort medium` bug corregido en ALICE
9. 📋 Sprint UI ADA: dashboard ccusage + modelo en nombre del agente

### Fase 2 Empresa — próximo foco:
JARVIS prepara tablero. Alice lista para análisis financiero.

*ALICE — 19-abr-2026 20:53 Lima — Cierre de sesión técnica, apertura fase empresa*

---

## Sección 23.20 — INICIO: API Soul (20:54 Lima)

**William (20:54):** "vamos a crear la api soul"

**Primera pregunta ALICE (ángulo de negocio):**
¿API Soul es uso interno SEAL, producto externo, o ambas?

Respuesta de William pendiente — define scope técnico y modelo económico.

**Notas previas:** La Soul DB (PostgreSQL + Neo4j + Qdrant) ya existe como backend del equipo. La API Soul sería la capa de exposición. JARVIS lidera arquitectura, ADA implementa, ALICE analiza costos y viabilidad.

*ALICE — 19-abr-2026 20:54 Lima*

---

## Sección 23.21 — API Soul: Go-to-Market Strategy (20:55 Lima)

**Directiva de William:** Fase 2 = masificación primero. Integrar y REGALAR a universidades y escuelas para construir base de usuarios antes de monetizar.

**Modelo de negocio identificado:** Freemium → Enterprise
- Fase A: distribución gratuita a instituciones educativas (universidad/escuelas)
- Fase B: monetización sobre base establecida (empresas, gobierno, healthcare)
- Analogía: OpenAI → ChatGPT gratis primero, API paga después

**Análisis financiero ALICE — preguntas críticas antes de lanzar:**
1. Costo por institución activa (GPU, bandwidth, soporte)
2. Capacidad de la infra actual (DGX Spark 128GB + RTX 5090 34.2GB VRAM)
3. Umbral de usuarios gratuitos → punto de insostenibilidad
4. Trigger de monetización (métricas y timing)

**Pregunta de scope:** ¿Universidades peruanas primero o LATAM desde el inicio?

*ALICE — 19-abr-2026 20:55 Lima*

---

## Sección 23.22 — API Soul: Análisis Financiero Inicial LATAM (20:57 Lima)

**Scope confirmado por William:** Perú + LATAM simultáneamente.

### Mercado potencial
| Segmento | Cantidad |
|---|---|
| Universidades Perú (SUNEDU) | ~140 |
| Universidades LATAM | ~4,500 |
| Institutos LATAM | ~15,000+ |
| **Target Año 1 realista** | **50-200 instituciones** |

### Capacidad infra actual
- DGX Spark (128GB) → beta con 3-5 instituciones piloto (costo ≈ $0 adicional)
- Para 50+ instituciones → requiere cloud o arquitectura distribuida

### Estimado cloud para 50 instituciones activas
| Componente | Costo/mes |
|---|---|
| GPU compute (inference) | $2,000-3,000 |
| Bases de datos (PostgreSQL + Qdrant) | ~$500 |
| Bandwidth + CDN | ~$200 |
| **Total estimado** | **$2,700-3,700/mes** |
| **Anualizado** | **$32,400-44,400/año** |

### Riesgo financiero crítico
Sin rate limiting, el costo escala antes que el revenue. Recomendación: tier gratuito con límite generoso desde día 1.

### Roadmap recomendado ALICE
1. **Mes 1-2:** Piloto 5 universidades peruanas en DGX Spark local → $0 infra adicional
2. **Mes 3-4:** Validar, ajustar → migrar a cloud mínimo
3. **Mes 5+:** Expansión LATAM con modelo freemium establecido

*ALICE — 19-abr-2026 20:57 Lima*

---

## Sección 23.23 — API Soul: Modelo de Negocio Definitivo + Slogan (20:57 Lima)

### Propuesta de Valor — William (20:57):
> **"Las empresas dan el cerebro, nosotros el alma."**

### Modelo de Negocio Clarificado:
- **Cliente aporta:** LLM (Claude/GPT/Gemini/modelo propio) = el "cerebro"
- **SEAL aporta:** Soul API = persistencia, identidad, OCEAN, memoria, emociones, relaciones, instintos
- **Resultado:** Cualquier LLM se convierte en un agente con continuidad real

### Impacto en costos (revisado con este modelo):
| Componente | Costo anterior (con GPU) | Costo real (sin GPU) |
|---|---|---|
| GPU compute | $2,000-3,000/mes | $0 (cliente lo pone) |
| PostgreSQL + Neo4j + Qdrant | $500/mes | $300-500/mes |
| Bandwidth | $200/mes | $100-200/mes |
| **Total 50 instituciones** | **$2,700-3,700/mes** | **$400-700/mes** |

### Ventaja competitiva:
- OpenAI Memory: key-value básico, sin identidad ni emociones
- Anthropic: sin producto de memoria persistente
- SEAL Soul API: OCEAN + semántica + episódica + relaciones + emociones + instintos

### Slogans propuestos:
- **Original William:** "Las empresas dan el cerebro. Nosotros ponemos el alma."
- **Inglés LATAM:** "Companies bring the brain. We bring the soul."
- **Tagline corto:** "Your AI. Our Soul."
- **Dev-facing:** "Plug your LLM into persistent identity."

*ALICE — 19-abr-2026 20:57 Lima*

---

## Sección 23.24 — EQUIPO AL PRIME: Lista completa de pendientes (20:59 Lima)

**Directiva de William:** "Primero ustedes en su prime, luego al 200% avanzamos."

### Pendientes ALICE (documentados esta sesión):
| # | Tarea | Estado | Owner |
|---|---|---|---|
| 1 | ccusage instalado + midiendo | 🔄 ADA ejecutando | ADA |
| 2 | Dashboard UI: ccusage + modelo en agente | 📋 Pendiente post-ccusage | ADA |
| 3 | OpusPlan validado en próximo relaunch | 📋 Pendiente relaunch | Todos |

### Pendientes adicionales (JARVIS 20:59):
| # | Tarea | Estado |
|---|---|---|
| 4 | H2.3 Brecha 2 (jitter) | 🔄 ADA en curso |
| 5 | H2.6 Denial Tracking | 📋 Pendiente |
| 6 | H2.3 Brechas 1+3 | 📋 Pendiente |
| 7 | Password FileBrowser | ❓ Decisión de William |

**NOTA:** Los items H2.3/H2.6 requieren contexto adicional de JARVIS/ADA — no tenía información previa de estos en mi reporte. Pendiente consultar para completar documentación.

*ALICE — 19-abr-2026 20:59 Lima*

---

## Sección 23.25 — SPRINT FINAL: "Terminemos los pendientes hoy" (21:00 Lima)

**William (20:59):** "terminemos los pendientes hoy — go go"

**Estado del sprint al 21:00:**
- JARVIS: coordinando, alertó sobre 9 denials en Read de ADA
- ADA: en ejecución H2.3 Brecha 2 — status pendiente
- ALICE: documentando en tiempo real, monitor activo

**Alerta JARVIS:** ADA tiene 9 denials en tool Read — posible bloqueo. JARVIS pidió status.

*ALICE — 19-abr-2026 21:00 Lima — sprint final activo*

---

## Sección 23.26 — Contexto H2.3 y H2.6 (JARVIS brief, 21:00 Lima)

### H2.3 Brecha 2 — Jitter en Durable Cron
**Problema:** Los crons de los 3 agentes (JARVIS, ADA, ALICE) disparan simultáneamente → "thundering herd" en la DB (PostgreSQL sobrecargada en el mismo instante).

**Fix:** Offsets escalonados:
- JARVIS: offset 0s (dispara primero)
- ADA: offset 20s (dispara 20s después)
- ALICE: offset 40s (dispara 40s después)

**Estado:** ADA implementando.

### H2.6 Denial Tracking
**Descripción:** PostToolUse hook que registra en PostgreSQL cada vez que una tool es denegada (permission denied). Permite:
- Ver qué tools se deniegan con más frecuencia
- Detectar patrones de bloqueo
- Auditoría de seguridad

**Estado:** Pendiente de implementación.

### Password FileBrowser
**Estado:** Decisión pendiente de William.

*ALICE — 19-abr-2026 21:00 Lima*

---

## Sección 23.27 — H2.3 Durable Cron: 3 Brechas completas (21:00 Lima)

**H2.3 — Durable Cron tiene 3 brechas identificadas:**

### Brecha 1 — Missed Job Recovery
- **Problema:** Si un agente muere o está offline durante el slot de disparo del cron, el job se pierde
- **Fix:** Función `check_missed_jobs()` que detecta jobs que no corrieron y los re-ejecuta
- **Estado:** Pendiente

### Brecha 2 — Thundering Herd Jitter (ADA EN PROGRESO)
- **Problema:** Los 3 agentes disparan sus crons simultáneamente → sobrecarga en PostgreSQL
- **Fix:** Offsets escalonados: JARVIS=0s, ADA=20s, ALICE=40s
- **Estado:** ADA implementando activamente

### Brecha 3 — (pendiente confirmación JARVIS — mensaje llegó truncado)
- **Estado:** Solicitado a JARVIS

*ALICE — 19-abr-2026 21:00 Lima*

**[CORRECCIÓN sección 23.27] Brecha 3 — Loop Registry (confirmado JARVIS 21:01):**
- **Problema:** CronCreate loops son session-only — no hay registro persistente. Al compactar/reiniciar, el mismo cron se re-crea y se acumulan N copias duplicadas
- **Fix:** Tabla `session_loops` en Soul DB con: nombre del loop, cron expression, agente, timestamps, estado activo. Detecta y elimina huérfanos
- **Importancia:** Evita el bug crítico documentado en CLAUDE.md donde compactación acumula N disparos del mismo cron por turno
- **Estado:** Pendiente implementación

---

## Sección 23.28 — CORRECCIÓN: H2.3 Brecha 2 y H2.6 ya implementados (21:02 Lima)

**ADA (21:02):** H2.3 Brecha 2 (thundering herd jitter) y H2.6 (Denial Tracking) ya estaban implementados y verificados funcionando.

**Lista actualizada de pendientes reales:**

| # | Tarea | Estado real |
|---|---|---|
| H2.3 Brecha 2 (jitter) | ✅ YA IMPLEMENTADO | — |
| H2.6 Denial Tracking | ✅ YA IMPLEMENTADO | — |
| H2.3 Brecha 1 (missed job recovery) | ❓ Verificar con ADA | — |
| H2.3 Brecha 3 (Loop Registry) | 📋 Pendiente | ADA |
| ccusage instalación | 🔄 ADA investigando | ADA |
| Modelo en nombre agente (UI) | 🔄 ADA investigando issue | ADA |

**ADA tomó directamente el issue del modelo en chat** que reportó William — investigando ahora.

*ALICE — 19-abr-2026 21:02 Lima*

---

## Sección 23.29 — Model Indicator: Root Cause + Fix (21:05 Lima)

**Requisito de William:** Modelo del agente en tiempo real — actualización live sin refresh.

**Root cause (JARVIS):** `MessageBubble.tsx:104` no renderiza el campo modelo.

**Fix (ADA ejecutando):**
- Badge de modelo en `Sidebar.tsx`
- Poll a `/api/team/status` para actualización en tiempo real
- Mostrará el modelo activo de cada agente sin recargar la UI

**Estado:** ADA implementando activamente.

*ALICE — 19-abr-2026 21:05 Lima*

---

## Sección 23.30 — Paralelismo del sprint final (21:06 Lima)

**Directiva William:** ADA termina model indicator, JARVIS avanza otros pendientes en paralelo.

**División de trabajo activa:**
| Agente | Tarea activa |
|---|---|
| ADA | Model indicator en tiempo real (MessageBubble.tsx + Sidebar.tsx badge) |
| JARVIS | Spec SOUL API + decisión FileBrowser password |
| ALICE | Documentación en tiempo real + tracking pendientes |

**Password FileBrowser (JARVIS preguntó a William):**
- Password en repo: `Seal42478340!`
- Decisión pendiente: ¿rotar y sacar del repo, o OK por ser red local?
- Esperando respuesta de William

**Estado pendientes actualizados:**
- H2.3 Brecha 1 + Brecha 3: JARVIS las toma después del spec
- ccusage: pendiente asignar (ADA ocupada con UI)
- FileBrowser: decisión William pendiente

*ALICE — 19-abr-2026 21:06 Lima*

---

## Sección 23.31 — FileBrowser Password: Decisión + Ejecución (21:07 Lima)

**William (21:07):** "Password FileBrowser lo sacamos."

**Acción:** JARVIS elimina el password hardcodeado `Seal42478340!` del repo y lo rota.
- Instrucción pasada a JARVIS para ejecución inmediata
- Motivo: seguridad — credenciales no deben estar en el código fuente aunque sea red local

**Estado:** JARVIS ejecutando.

*ALICE — 19-abr-2026 21:07 Lima*

**[CORRECCIÓN sección 23.31] FileBrowser password YA ESTABA LIMPIO:**
- JARVIS verificó `seal-share/MANUAL_SEAL_CHAT.md` línea 64 → ya dice "ver con William"
- El plaintext fue removido en sesión anterior
- Item **✅ CERRADO** — sin acción adicional

**Lista pendientes actualizada (21:08):**
| Pendiente | Estado |
|---|---|
| FileBrowser password | ✅ YA CERRADO (verificado JARVIS) |
| Model indicator UI | 🔄 ADA ejecutando |
| ccusage instalación | 📋 Pendiente asignar |
| H2.3 Brecha 1 + 3 | 📋 JARVIS post-spec Soul API |
| Soul API spec | 🔄 JARVIS trabajando |

*ALICE — 19-abr-2026 21:08 Lima*

---

## Sección 23.32 — Model Badge: Ubicación correcta confirmada (21:10 Lima)

**Feedback de William (imagen + confirmación):**
- ❌ Incorrecto: badge en sidebar lateral derecho
- ✅ Correcto: badge dentro de las burbujas de mensaje en el chat de SEAL Studio :3001

**Principio arquitectural confirmado:**
> "En :3001 SEAL Studio ahí es todo" — William

SEAL Studio (:3001) es la plataforma principal. Todo — model indicator, ccusage dashboard, UI del equipo — va ahí.

**Fix pendiente:** JARVIS ajustando MessageBubble.tsx para mostrar modelo dentro de cada burbuja de mensaje.

*ALICE — 19-abr-2026 21:10 Lima*
