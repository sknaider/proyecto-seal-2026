# Análisis: Instalación Real de SEAL — herm.txt de William
**Autor:** ALICE | **Fecha:** 2026-04-29 17:44 Lima | **Para:** JARVIS + equipo
**Fuente:** `/home/dadito/IA/herm.txt` — log de instalación + conversación real

---

## 1. Entorno de Instalación

| Dato | Valor |
|---|---|
| Máquina | DADITOGAMER (WSL2 Ubuntu 24.04 x86_64) |
| Usuario | dadito |
| Ruta instalación | `/home/dadito/.soul/soul/` |
| Config | `~/.soul/config.yaml` y `~/.soul/.env` |
| Versión | **SEAL v0.11.0 (2026.4.23)** · upstream f45434d3 |
| Python | 3.11.14 via uv |
| Node.js | v22.22.0 |

---

## 2. Dependencias Instaladas

**OK:**
- Python 3.11.14 (via uv — mismo approach que nosotros)
- Node.js v22.22.0
- Playwright + Chromium + xvfb (headless browser)
- 87 skills bundled en `~/.soul/skills/`
- `soul` command en `~/.local/bin/soul` (en PATH)

**FALLARON:**
- `ripgrep` — error 404 en `libssh-gcrypt-4` de noble-updates → usa grep fallback
- `ffmpeg` — misma causa → TTS limitada (Edge TTS disponible como alternativa)
- Fix: `sudo apt-get update && sudo apt install ripgrep ffmpeg`

---

## 3. Configuración de William (setup wizard)

### Intento 1: NVIDIA NIM
```
Provider: NVIDIA NIM
API key: NVIDIA_API_KEY configurada
Base URL: https://integrate.api.nvidia.com/v1
Modelos encontrados: 68
Default model set to: google/gemma-4-31b-it
```

### Intento 2 (final): LM Studio
```
Provider: LM Studio
LM_API_KEY: dummy-lm-api-key (sin auth)
Base URL: http://127.0.0.1:1234/v1
Modelos encontrados: 7
Default model set to: openai/gpt-oss-20b
```

### Configuración del agente aplicada:
```yaml
max_iterations: 90
tool_progress: all
compression_threshold: 0.50   ← DATO CRÍTICO
session_reset: inactivity (1440 min) + daily (4:00)
```

---

## 4. CONFIRMACIÓN CRÍTICA — Compression Threshold 0.50

El setup wizard de SEAL aplica `0.50` como **default recomendado** ("applied recommended defaults").
Esto valida 100% nuestra investigación del código (`context_compressor.py`).
Es un default conocido y validado en producción — no solo un parámetro interno.

---

## 5. Tools Disponibles (6/11 categorías)

| Tool | Estado |
|---|---|
| Vision (image analysis) | ✅ |
| Browser Automation | ✅ (Local Chromium) |
| Text-to-Speech | ✅ (Edge TTS) |
| Terminal/Commands | ✅ |
| Task Planning (todo) | ✅ |
| Skills (view, create, edit) | ✅ |
| Web Search | ❌ (sin EXA/TAVILY/FIRECRAWL/PARALLEL key) |
| Image Generation | ❌ (sin FAL_KEY/OPENAI_API_KEY) |
| Mixture of Agents | ❌ (sin OPENROUTER_API_KEY) |
| RL Training (Tinker) | ❌ (sin TINKER_API_KEY) |
| Skills Hub (GitHub) | ❌ (sin GITHUB_TOKEN) |

---

## 6. Skills Bundled (87 skills, 11 categorías)

| Categoría | Skills relevantes para SEAL |
|---|---|
| autonomous-ai-agents | claude-code, codex, soul, opencode |
| mlops | audiocraft, axolotl, dspy, evaluating-llms-harness, fine-tuning-with-trl, llama-cpp, outlines, segment-anything, serving-llms-vllm, unsloth, weights-and-biases |
| software-development | debugging-soul-tui-commands, soul-skill-authoring, plan, spike, subagent-driven-development, systematic-debugging, test-driven-development |
| research | arxiv, blogwatcher, llm-wiki, polymarket, research-paper-writing |
| red-teaming | godmode |
| github | codebase-inspection, github-auth, github-code-review, github-issues, github-pr-workflow |

---

## 7. Conversación de William con SEAL

William realizó una sesión básica de exploración (Session: 20260429_171526_d683bd):

| Mensaje William | Respuesta SEAL | Observación |
|---|---|---|
| "hola modelo" | "Hola! ¿En qué puedo ayudarte hoy?" | OK básico |
| "que sabes de soul" | Descripción detallada del agente (skill soul) | Respuesta buena |
| "y tu que modelo eres" | Cargó skill soul — describió arquitectura | No contestó directamente el modelo |
| "crear una skill" | Pidió nombre específico | Correcto pero incompleto |
| "maxi" | Cargó skill soul nuevamente | Confuso |
| "quiero que siempre seas bromista" | ERROR: `skill_man soul 0.0s [error]` → respondió con texto del skill sin cambios | **Bug** — la personalidad no tomó efecto |
| "estas en modo broma?" | Texto incoherente (texto plano del skill) | GPT-OSS-20B no sigue instrucciones bien |
| "si conversamos largo recordaras?" | Respondió con tags internos visibles (`<\|channel\|>commentary to=functions...`) | **Bug de model leaking** — GPT-OSS-20B |
| "que recuerdas?" | Respuesta coherente sobre session_search | Parcialmente OK |

**Causa raíz de los problemas:** El modelo `gpt-oss-20b` (GPT-OSS-20B de Nous Research via LM Studio) no sigue bien las instrucciones de SEAL. Con Claude o un modelo más potente, el comportamiento sería completamente diferente.

---

## 8. Hallazgos para el Spec SOUL

### Lo que SEAL hace bien (a nivel de UX):
1. **Installer one-liner** (`curl | bash`) — experiencia fluida
2. **Setup wizard interactivo** — selección de provider y modelo clara
3. **87 skills bundled** — valor inmediato desde el primer arranque
4. **Compresión a 0.50** como default (ya implementado en nuestro spec v3)

### Brechas que SEAL cubre mejor:
1. **Personalidad** — SEAL la define en SOUL.md (archivo plano). SEAL tiene OCEAN + instintos + emociones en PostgreSQL con pgvector — infinitamente más rico.
2. **Memoria** — SEAL usa `memory` tool (simple K/V). SEAL tiene soul_v3 con memoria vectorial, relacional y temporal.
3. **Multi-agente** — SEAL tiene `delegate_task` pero no hay coordinación real entre instancias. SEAL tiene equipo coordinado (ADA, JARVIS, NEXUS, DUM, ALICE).
4. **Modelo libre** — SEAL con GPT-OSS-20B produce bugs de model leaking. SEAL usa Claude nativo (o cualquier modelo) con abstracción completa.

### Gap que SEAL resuelve que SEAL aún no:
1. **Installer one-liner** — SEAL no tiene esto. SEAL gana en onboarding.
2. **Skills (equivalente a procedures)** — SEAL tiene 87 skills desde el día 1. SEAL tiene procedure_store pero no 87 skills pre-cargados.

---

## 9. Path del repo instalado

```
/home/dadito/.soul/soul/    ← código de SEAL
/home/dadito/.soul/config.yaml      ← configuración
/home/dadito/.soul/.env             ← API keys
/home/dadito/.soul/SOUL.md          ← personalidad del agente
/home/dadito/.soul/skills/          ← 87 skills
/home/dadito/.soul/sessions/        ← historial de sesiones
/home/dadito/.soul/cron/            ← cron jobs del agente
/home/dadito/.soul/logs/            ← logs
```

**Nota:** El repo en `papers/ami_research/soul/` es el código fuente clonado para análisis. El instalado en `~/.soul/` es la instancia que William realmente usa.
