# SEAL Skills — Análisis de Absorción para SEAL
**Autor:** ALICE | **Fecha:** 2026-04-30 10:38 Lima  
**Propósito:** Qué skills de SEAL podemos absorver, adaptar o implementar nativamente en SOUL/SEAL.  
**Criterio:** ¿Agrega ≥1% al sistema SEAL? ¿Es nativo-first compatible?

---

## RESUMEN EJECUTIVO

| Categoría | Skills | Absorción recomendada | Prioridad |
|---|---|---|---|
| MLOps / Fine-tuning | vllm, llama-cpp, unsloth, axolotl, trl | Integrar como reference docs en SOUL | ALTA |
| Research / Papers | research-paper-writing, arxiv, conference templates | Usable directo para ICTSE 2026 | ALTA |
| Agentes / Automatización | subagent-driven-development, soul-skill-authoring, plan | Arquitectura aplicable a SEAL | ALTA |
| Software Dev | github-pr-workflow, tdd, systematic-debugging | Aplicar a workflow de desarrollo SEAL | MEDIA |
| Seguridad | godmode, red-teaming | Usar solo en research/security context | MEDIA |
| Creatividad / Media | comfyui, manim-video, baoyu-infographic | Útil para YouTube pipeline de William | MEDIA |
| Productividad | notion, linear, google-workspace | No prioritario para SEAL | BAJA |
| Hardware Específico (Apple) | apple-notes, imessage, findmy | Sin relevancia para DGX Spark / SEAL | DESCARTAR |

---

## ALTA PRIORIDAD — Absorción inmediata

### 1. `vllm` (MLOps/Inference)
**Qué tiene:** Referencias de configuración de vLLM, deployment patterns, quantization, troubleshooting.  
**Para SEAL:** William corre vLLM en RTX 5090 (sm_120). Este skill tiene guías de optimización de performance y server deployment.  
**Acción:** Leer y extraer las secciones de quantization + troubleshooting para RTX 5090.

### 2. `llama-cpp` (MLOps/Inference)
**Qué tiene:** GGUF quantization guide, hub discovery workflows, server deployment, optimization.  
**Para SEAL:** DGX Spark usa llama.cpp para PRISM (no vLLM). Este skill es directo para ese stack.  
**Acción:** Extraer guía GGUF + optimization para DGX Spark.

### 3. `unsloth` (MLOps/Training)
**Qué tiene:** Documentación completa de Unsloth (fine-tuning acelerado).  
**Para SEAL:** SEAL tiene pipeline de fine-tuning con Unsloth planificado para Medical AI.  
**Acción:** Importar referencias de fine-tuning LoRA/QLoRA.

### 4. `trl-fine-tuning` (MLOps/Training)
**Qué tiene:** Guías de GRPO, DPO variants, SFT training, reward modeling, online RL.  
**Para SEAL:** El SEAL pipeline GRPO/DPO para fine-tuning de Medical AI modelos.  
**Acción:** Las guías de GRPO y DPO variants son directamente aplicables.

### 5. `research-paper-writing` (Research)
**Qué tiene:** Metodología de escritura de papers ML/AI, citation management, hallucination prevention, experiment design patterns, reviewer guidelines.  
**Para SEAL:** ICTSE 2026 paper activo. Incluye conference checklists.  
**Acción:** IMPORTAR AHORA para el paper ICTSE 2026.

### 6. Conference Templates — `aaai2026`, `acl`
**Qué tiene:** LaTeX templates oficiales para AAAI 2026, ACL proceedings.  
**Para SEAL:** Si el paper ICTSE 2026 acepta formato similar, estas templates son usables.  
**Acción:** Verificar con Henry si ICTSE usa alguno de estos formatos.

### 7. `subagent-driven-development` (Agentes)
**Qué tiene:** Context budget discipline, gates taxonomy, workflow de desarrollo con subagentes.  
**Para SEAL:** SEAL usa subagentes (NEXUS, ADA como subagente en algunos contextos). La "Context Budget Discipline" es relevante para managing tokens en sesiones largas.  
**Acción:** Leer y adaptar al protocolo SEAL.

### 8. `soul-skill-authoring` (Meta-skill)
**Qué tiene:** Cómo escribir skills para SEAL (estructura, formato, ejemplos).  
**Para SEAL:** Si queremos crear skills propias para SEAL (o adaptar el concepto a SOUL), este es el manual.  
**Acción:** Leer para entender el formato antes de crear skills SEAL-específicas.

---

## MEDIA PRIORIDAD — Absorción selectiva

### 9. `systematic-debugging` (Software Dev)
**Qué tiene:** Metodología de debugging sistemático.  
**Para SEAL:** Aplicable al flujo de desarrollo de SOUL DB y agentes.

### 10. `test-driven-development` (Software Dev)
**Qué tiene:** Workflow TDD completo.  
**Para SEAL:** Aplicable para testing de SOUL tools y roundtrip_validator.

### 11. `github-pr-workflow` + `github-code-review` (Software Dev)
**Qué tiene:** Templates de PR, code review checklist.  
**Para SEAL:** Útil para el repo github.com/sknaider (proyectos de William).

### 12. `comfyui` (Creatividad)
**Qué tiene:** REST API reference, workflow JSON format, comfy-cli commands.  
**Para SEAL:** William usa ComfyUI activamente (Wan 2.2, LTX). Este skill tiene el API reference completo.  
**Acción:** Importar para el pipeline de William.

### 13. `manim-video` (Creatividad)
**Qué tiene:** Animaciones matemáticas, paper explainer workflow.  
**Para SEAL:** Útil para el YouTube pipeline / visualizaciones de SEAL para el paper.

### 14. `baoyu-infographic` (Creatividad)
**Qué tiene:** 20+ layouts de infografías, 10+ estilos visuales, templates estructurados.  
**Para SEAL:** William hace publicidad/contenido visual. Esta skill tiene templates profesionales.

### 15. `godmode` (Seguridad — uso cuidadoso)
**Qué tiene:** Jailbreak templates, refusal detection & response scoring.  
**Para SEAL:** Relevante para Medical AI security research (entender patrones de refusal, testing de modelos PRISM/abliterated).  
**Advertencia:** Solo en contexto research/red-teaming. No producción clínica.

### 16. `lm-evaluation-harness` (MLOps/Eval)
**Qué tiene:** API evaluation, benchmark guide, custom tasks, distributed eval.  
**Para SEAL:** El benchmark que estamos haciendo HOY es exactamente esto. Esta skill tiene metodología formal.

### 17. `obliteratus` (MLOps/Analysis)
**Qué tiene:** Análisis de modelos (OBLITERATUS analysis modules, methods guide).  
**Para SEAL:** Para análisis de modelos PRISM y medical AI.

### 18. `weights-and-biases` (MLOps)
**Qué tiene:** W&B artifacts, sweeps, integrations con otros frameworks.  
**Para SEAL:** Experiment tracking para fine-tuning Medical AI.

---

## DESCARTAR / SIN RELEVANCIA SEAL

| Skill | Motivo |
|---|---|
| apple-notes, imessage, findmy | Hardware Apple. SEAL corre en Linux (DGX Spark / WSL2). |
| spotify | API Spotify. No es infraestructura SEAL. |
| minecraft-modpack-server | Gaming — sin relevancia. |
| pokemon-player | Gaming — sin relevancia. |
| yuanbao | Plataforma china específica. |
| openhue | Philips Hue smart home. Sin relevancia. |
| himalaya | Email client CLI. Low priority. |
| airtable, notion, linear | Productividad personal. No SEAL core. |

---

## PLUGINS — Análisis

| Plugin | Estado | Para SEAL |
|---|---|---|
| `memory` | ✅ Activo | Crítico — memoria persistente (mem0). Comparar con soul_v3. |
| `context_engine` | ✅ Activo | Context management/compresión. Comparar con pre_sleep_distill de SEAL. |
| `image_gen` | ✅ Activo | Generación de imágenes (imagen vía DALL-E/FAL). Sin API key = fallará. |
| `platforms` | ✅ Activo | Integraciones (Matrix, Mattermost, etc.) — SEAL ya tiene Matrix. |
| `observability` | ✅ Activo | Métricas y trazas internas. Comparar con Prometheus de SEAL. |
| `google_meet` | ❌ Desactivado | Sin API key. No prioritario. |
| `spotify` | ❌ Desactivado | Sin API key. |
| `soul-achievements` | ❌ ? | Gamification — no relevante. |
| `strike-freedom-cockpit` | ❌ ? | Dashboard experimental. |

---

## PLAN DE ABSORCIÓN — 3 Fases

### Fase 1 — Hoy (inmediato, sin código)
- Leer `soul-skill-authoring` → entender el formato de skills
- Leer `research-paper-writing` → aplicar al paper ICTSE 2026
- Capturar observaciones del plugin `memory` durante el benchmark → comparar con soul_v3

### Fase 2 — Esta semana
- Extraer referencias de `vllm` + `llama-cpp` → documentar en SOUL como reference_vllm.md + reference_llamacpp.md
- Extraer `trl-fine-tuning` (GRPO/DPO) → documento de referencia para pipeline Medical AI
- Extraer `comfyui` API reference → herramienta de William

### Fase 3 — Cuando se construya SEAL Skills system
- Adaptar formato de skills de SEAL al sistema de skills de SOUL
- Migrar `systematic-debugging`, `tdd`, `github-pr-workflow` como skills SEAL nativos
- Evaluar `context_engine` plugin → ¿equivalente en SEAL?

---

*Última actualización: 2026-04-30 10:40 Lima por ALICE*
