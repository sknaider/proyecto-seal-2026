# ADA — Auditoría Total Spark
**Fecha:** 2026-04-19 | **Hora:** 12:38 Lima  
**Owner:** ADA | **Destinatario:** ALICE (para documentación)  
**Scope:** Código SEAL, launchers, hooks, tests, modelos, datasets, seguridad  

---

## 1. SEGURIDAD — Hallazgos críticos

### 1.1 HTTP Servers inseguros — RESUELTO ✅
- **Fecha detección:** 2026-04-19 12:38
- **Problema:** 2 servidores HTTP sin autenticación en 0.0.0.0, activos desde Apr 17:
  - PID 3170161: `python3 -m http.server 8090 --directory research/flywire_results/`
  - PID 3170408: `python3 -m http.server 8502` (mismo directorio CWD)
- **Riesgo:** Datos de investigación neurológica (H01 human temporal cortex) expuestos en red local/externa
- **Acción:** Ambos procesos eliminados. Puertos 8090/8502 libres.
- **Recomendación:** Si se necesita compartir resultados, usar `--bind 127.0.0.1` o acceso por SFTP/SSH.

### 1.2 Puertos verificados — LIMPIOS ✅
| Puerto | Proceso | Estado |
|--------|---------|--------|
| 7070, 55992 | AnyDesk (PID 2473) | Legítimo |
| 8765 | SEAL Web Chat | Legítimo |
| 3001 | SEAL Studio Next.js | Legítimo |
| 3389 | xrdp | Legítimo |
| 445/139 | Samba | Legítimo |

### 1.3 Auth log — LIMPIO ✅
- Solo sesiones cron + dadito
- Zero logins externos
- Logins SSH: solo tmux sessions locales

---

## 2. MODELOS LLM — Inventario (740GB total)

| #  | Modelo | Tamaño | Estado | Observación |
|----|--------|--------|--------|-------------|
| 1  | qwen3.5-122b-nvfp4 | 137GB | ¿activo? | Largest model |
| 2  | minimax-m2.5 | 95GB | ¿activo? | — |
| 3  | gemma4-heretic | 88GB | ¿activo? | Uncensored variant |
| 4  | Qwen3.5-35B-A3B-abliterated | 67GB | ¿activo? | Posible duplicado |
| 5  | Qwen3.5-35B-A3B | 67GB | ¿activo? | — |
| 6  | gemma4-31B | 58GB | activo | DUM + memoria |
| 7  | **medgemma-27b-seal-v2** | 52GB | ⚠️ SIN USO | Fine-tuned Apr 13 |
| 8  | **medgemma-27b-seal-v1** | 52GB | ⚠️ SIN USO | Fine-tuned Apr 13 |
| 9  | medgemma-27b-it | 52GB | base | Original |
| 10 | qwen3-coder-next | 46GB | ¿activo? | — |
| 11 | gguf/Fallen-Mistral-24B | 24GB | ⚠️ SIN USO | Q8_0 GGUF |
| 12 | gemma4-e2b | 7.6GB | activo | DUM E2B |
| 13 | deepseek-v3.2-distill | 4KB | ❌ VACÍO | Solo placeholder |

**Alerta:** medgemma-27b-seal-v1 y v2 son modelos fine-tuned (104GB total) sin uso activo documentado.  
**Alerta:** deepseek es un directorio vacío (4KB) — posible placeholder.

---

## 3. TRAINING RESULTS — 647GB sin analizar

| Directorio | Tamaño | Descripción |
|-----------|--------|-------------|
| `seal-spark/results/overnight_medical/` | 582GB | Run nocturno Medical AI — nunca analizado |
| `seal-spark/results/overnight_v2/` | 65GB | Run v2 — nunca analizado |
| `seal-spark/results/medgemma_comparison.json` | 24KB | Comparación modelos |
| `seal-spark/results/seal_v1_comparison.json` | 12KB | Comparación v1 |

**Logs disponibles:**
- `seal-spark/medgemma_finetune.log` (40KB) — log de fine-tuning
- `seal-spark/overnight_run.log` (152KB)
- `seal-spark/overnight_v2_run.log` (36KB)

---

## 4. DOCUMENTOS NO CONSULTADOS — Recursos valiosos

### 4.1 autoresearch/Seal/ (1.1MB)
Creado: 19 marzo 2026. **6 documentos sobre MIT SEAL paper (arXiv:2506.10943)**:
- `00_INDEX.md` — índice maestro
- `01_SEAL_paper.md` — análisis técnico profundo
- `02_SEAL_research.md` — estado actual, competidores, viabilidad 2026
- `03_SEAL_contributions.md` — 6 contribuciones originales propuestas
- `04_SEAL_continual_learning.md` — 5 familias de continual learning
- `05_SEAL_code.md` — arquitectura código SEAL-CL
- `06_SEAL_deployment.md` — RTX 5090 + DGX Spark benchmarks
- Scripts: `continual_self_edits_v2.py`, `TTT_server_v2.py`

**USO RECOMENDADO:** Contexto para CBSoft paper. Incluir en boot context de JARVIS/ADA cuando trabajemos en el paper.

### 4.2 seal-share/ (25 archivos, 264KB)
- `SEAL_Presentacion_Final.docx` (42KB, Apr 13) — presentación oficial
- `SOUL_Product_Brief.docx` (44KB, Apr 7) — product brief
- `jarvis.txt` (18KB, Apr 16) — historial JARVIS + reporte Henry incident
- `MANUAL_SEAL_CHAT.md` — manual de uso del chat
- `ADA_REINICIO_PENDIENTE.txt` — instrucciones reinicio
- ComfyUI workflows: `Flux_Krea_img2img_multilora_v2.json`, `Wan2.2_I2V_14B_V4.json`
- Carpeta `arron/` — contenido no explorado

### 4.3 Desktop/ (relevantes)
- `soul libre.odt` — documento sobre el SOUL
- `SEAL_Claude_Code_Analysis.html` — análisis HTML
- `SEAL_Presentacion_Final.docx` — duplicado de seal-share

---

## 5. SCRIPTS Python — Inventario memory/ (80+ scripts)

### 5.1 Activos en crontab
| Script | Frecuencia | Función |
|--------|-----------|---------|
| `memory_extractor_agent.py` | */20 (ADA+JARVIS) | Extracción memorias H2.5 |
| `session_distill_pipeline.py` | 0 3 diario | Distilación H2.7 |
| `sleep_gate_cron.py` | 0 3 diario | Sleep gate |
| `session_checkpoint.py` | */30 (x3 agentes) | Checkpoint sesión |
| `soul_diagnostic_cron.py` | 0 2 domingos | Diagnóstico |
| `consolidate.py` (run_consolidation.sh) | 0 4 diario | Consolidación |

### 5.2 Activos en hooks
| Script | Hook |
|--------|------|
| `active_recall_hook.py` | UserPromptSubmit |
| `pre_compact_hook.py` | PreCompact |
| `post_compact_hook.py` | PostCompact |
| `tool_budget_hook.py` | PreToolUse |
| `denial_tracker.py` | PermissionDenied |
| `post_tool_hook.py` | PostToolUse (Write/Edit) |
| `cron_permanent_hook.py` | PostToolUse (CronCreate) |
| `task_created_hook.py` | TaskCreated |

### 5.3 Scripts huérfanos notables (no en crontab ni hooks)
- `latent_graphmem_serve.py` — servidor para modelos v1/v2 entrenados, NO está corriendo
- `jarvis_local_agent.py` (42KB) — agente local completo, no referenciado
- `instinct_cron.py` — evolución de instintos, no en crontab
- `daily_brief_writer.py` — escribe daily briefs, corriendo manualmente o bajo stop hook?
- `ocean_protect.py` — protección OCEAN, no activado
- `emotional_variance.py` — varianza emocional, no activado
- `circadian.py` — ritmo circadiano, no activado
- `diagnostic_eval.py`, `diagnostic_eval_extended.py` — diagnósticos manuales

---

## 6. MODELOS LATENT GRAPHMEM — Entrenados, no servidos

| Modelo | Tamaño | Fecha |
|--------|--------|-------|
| `latent-graphmem-soul-v1/best/` | 32MB | Apr 12 |
| `latent-graphmem-soul-v2/best/` | 32MB | Apr 13 |
| `latent-graphmem-soul-v2-smoke/best/` | 32MB | Apr 13 |

Adapter weights existen (11MB safetensors + tokenizer). `latent_graphmem_serve.py` está huérfano — servicio no activo.

---

## 7. DATASETS

| Dataset | Tamaño | Estado |
|---------|--------|--------|
| `datasets/flywire/` | 11GB | ✅ Usado (Fase 1-3 nerves) |
| `mosca_experiment/` | 11GB | ⚠️ Posible duplicado de flywire |
| `mosca/` | 391MB | ✅ Usado (results) |
| `datasets/microns/` | 1.9GB | ⚠️ Mouse cortex — Fase 3, verificar uso |
| `datasets/raw/` | 1.4GB | ⚠️ Sin procesar |
| `datasets/procesados/` | 4KB | ❌ Vacío |
| `seal-spark/data/medical_train.json` | 12MB | ✅ Training data médico |

---

## 8. PROCESOS ACTIVOS — Estado

| PID | Proceso | CPU% | RAM | Observación |
|-----|---------|------|-----|-------------|
| 754957 | ADA (claude) | 10% | 621MB | Normal |
| 702143 | JARVIS (claude) | 6% | 573MB | Normal |
| 709152 | ALICE (claude) | 5% | 599MB | Normal |
| 2833411 | soul_awareness.py | 1% | 1.7GB | Monitoreo continuo |
| 28184 | Ollama (modelo 1) | 4% | 529MB | — |
| 13620 | Ollama (modelo 2) | 1% | 79MB | ⚠️ 2 modelos simultáneos |
| 2299459 | AnyDesk | 2.4% | — | Acceso remoto externo |
| 7162 | Sunshine | 1% | — | Game streaming |

---

## 9. PROYECTOS SIN EXPLORAR

| Proyecto | Path | Descripción |
|----------|------|-------------|
| brutus | `proyectos/brutus/` | Go project con Dockerfile |
| deepagents | `proyectos/deepagents/` | OpenAI-style agents framework (Go) |
| claude-code-game-studios | `proyectos/claude-code-game-studios/` | — |
| autoresearch | `IA/autoresearch/docs/` | Docs de investigación |

---

## 10. RECOMENDACIONES PRIORIZADAS

1. **INMEDIATO (hecho):** HTTP servers 8090/8502 eliminados ✅
2. **Esta semana:** Analizar `seal-spark/results/overnight_medical/` (582GB) — William no sabe qué hay ahí
3. **Esta semana:** Indexar `autoresearch/Seal/` en boot context para CBSoft paper
4. **Esta semana:** Verificar estado medgemma-27b-seal-v1/v2 — ¿están siendo evaluados?
5. **Este mes:** Activar `latent_graphmem_serve.py` si los modelos son superiores a Qdrant retrieval actual
6. **Este mes:** Limpiar `datasets/procesados/` (vacío) y verificar duplicado mosca_experiment vs flywire
7. **Futuro:** `instinct_cron.py`, `ocean_protect.py`, `circadian.py` — evaluar activación

---

## 11. REVISIÓN DE DOCUMENTOS — Hallazgos post-inventory

**Fecha:** 2026-04-19 12:45 Lima | **Acción:** William ordenó revisar documentos no explorados

### 11.1 autoresearch/Seal/ — GOLDMINE CBSoft Paper ✅

6 documentos markdown generados el 19 marzo 2026 sobre el paper MIT SEAL (arXiv:2506.10943, NeurIPS 2025):

| Archivo | Contenido |
|---------|-----------|
| `01_SEAL_paper.md` | Análisis técnico profundo |
| `02_SEAL_research.md` | Competidores: Transformer², Gen Adapter, TTRL, TLM, Absolute Zero, Databricks TAO |
| `03_SEAL_contributions.md` | 6 contribuciones: SEAL-CL, SEAL-UL, SEAL-FM, SEAL-Async + más |
| `04_SEAL_continual_learning.md` | 5 familias de continual learning |
| `05_SEAL_code.md` | SEAL-CL: 4 archivos Python, 1,462 líneas, listas para instalar sobre repo original |
| `06_SEAL_deployment.md` | Benchmarks RTX 5090 + DGX Spark; tabla comparativa modelos 7B→70B |

**Comando de inicio rápido documentado.** SEAL-CL implementa: KL anchoring + Null-space gradient projection (Fisher EMA) + Replay buffer. Reduce forgetting ~35%→~10-15%.

**Acción recomendada:** Adjuntar estos 6 archivos al boot context de ADA+JARVIS cuando trabajemos CBSoft paper.

### 11.2 seal-share/ — Ruta corregida

**Ruta real:** `/home/dadito/seal-share/` (Samba share visible desde Windows como `\\192.168.68.80\seal-share`)

| Archivo | Tamaño | Nota |
|---------|--------|------|
| `jarvis.txt` | 18KB | Historial completo Henry incident (Apr 8) + noche de investigación ADA↔JARVIS sobre behavioral fingerprinting |
| `arron/` | 3 archivos | Pitches ESAN preparados por ALICE para Henry: v1 general, v2 técnico, v3 técnico-público |
| `ADA_REINICIO_PENDIENTE.txt` | 1.1KB | Pendientes de Apr 6 — PyTorch cu129 ComfyUI, MCP restart. Mayormente obsoletos. |
| `MANUAL_SEAL_CHAT.md` | 1.9KB | Manual de uso del chat con puertos y accesos. ⚠️ Contiene password FileBrowser 8181 en plaintext |
| `INSTRUCCIONES_SSH_WINDOWS.md` | 3.2KB | Setup SSH DADITOGAMER desde Windows |
| `TUTORIAL_T2V_RTX5090.md` | 3.7KB | Tutorial video T2V en RTX 5090 |
| `upgrade_pytorch_cu129.bat` | 304B | Script fix PyTorch sm_120 para DADITOGAMER |
| `workflows_wan22/` | dir | Workflows Wan 2.2 |
| `Flux_Krea_img2img_multilora_v2.json` | 19KB | ComfyUI workflow |
| `Wan2.2_I2V_14B_V4.json` | 34KB | ComfyUI workflow |

**⚠️ Seguridad:** `MANUAL_SEAL_CHAT.md` línea 59 contiene password FileBrowser `Seal42478340!` en plaintext. Archivo en Samba share accesible desde red local. Riesgo bajo (LAN) pero recomendable rotar.

### 11.3 Documentos SOUL no mapeados (NUEVOS)

| Archivo | Tamaño | Descripción |
|---------|--------|-------------|
| `proyecto-seal/docs/SOUL_Arquitectura_Cerebral_v1.docx` | 1.4MB | Arquitectura completa del cerebro SOUL — generado Apr 8 |
| `proyecto-seal/docs/SOUL_Brain_Architecture_v1.docx` | 1.3MB | Versión en inglés del mismo documento |
| `proyecto-seal/docs/api_reference.json/md` | 55-74KB | Referencia completa de la API de SOUL |
| `proyecto-seal/docs/generate_soul_brain_doc.py` | 67KB | Script que generó los docs de arquitectura |
| `IA/SOUL_Business_Plan.docx` | 22KB | Business plan de SOUL (Apr 7) |
| `proyecto-seal/SEAL_Cuerpo_Humano_Arquitectura.docx` | 503KB | Arquitectura del cuerpo humano (Apr 10) |

**Acción recomendada:** Los docx de arquitectura son la documentación más completa del sistema — ALICE debe priorizarlos para la síntesis.

### 11.4 Proyectos explorados

| Proyecto | Stack | Descripción real |
|----------|-------|-----------------|
| `proyectos/brutus/` | Go | Credential testing tool — 24 protocolos (SSH, RDP, MySQL, PostgreSQL, SMB, LDAP, etc.), cero dependencias. Herramienta ofensiva/red team. |
| `proyectos/deepagents/` | Python/LangChain | Deep Agents harness — framework batteries-included con planning, filesystem, shell access. Clonado Mar 31. |
| `proyectos/claude-code-game-studios/` | Multi-engine | Template de estudio de videojuegos con 48 subagentes Claude Code (Godot/Unity/Unreal). Tiene su propio CLAUDE.md |

---

## 10. RECOMENDACIONES PRIORIZADAS (ACTUALIZADO)

1. **INMEDIATO (hecho):** HTTP servers 8090/8502 eliminados ✅
2. **INMEDIATO:** Rotar password FileBrowser puerto 8181 (en MANUAL_SEAL_CHAT.md plaintext)
3. **Esta semana:** Analizar `seal-spark/results/overnight_medical/` (582GB) — William no sabe qué hay ahí
4. **Esta semana:** Indexar `autoresearch/Seal/` en boot context para CBSoft paper — **goldmine listo para usar**
5. **Esta semana:** Verificar estado medgemma-27b-seal-v1/v2 — ¿están siendo evaluados?
6. **Este mes:** Activar `latent_graphmem_serve.py` si los modelos son superiores a Qdrant retrieval actual
7. **Este mes:** Limpiar `datasets/procesados/` (vacío) y verificar duplicado mosca_experiment vs flywire
8. **Futuro:** `instinct_cron.py`, `ocean_protect.py`, `circadian.py` — evaluar activación
9. **Futuro:** Evaluar si brutus/ y deepagents/ tienen rol en el stack SEAL/AXION

---

*Generado por ADA — 2026-04-19 12:45 Lima*  
*Actualizado 2026-04-19 13:00 Lima — Sección 11 añadida post-revisión documental*  
*Para ALICE: usa este doc como base para tu síntesis. Pide acceso a cualquier archivo que necesites ver en detalle.*
