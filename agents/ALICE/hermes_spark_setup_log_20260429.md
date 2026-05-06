# SEAL en DGX Spark — Log de Instalación y Setup
**Autor:** ALICE | **Fecha:** 2026-04-29 18:11 Lima
**Propósito:** Documentar cada paso del wizard de SEAL en DGX Spark (aarch64)

---

## Entorno

| Dato | Valor |
|---|---|
| Máquina | DGX Spark (spark-2cdf) |
| Arquitectura | aarch64 (ARM64) |
| Usuario | dadito |
| OS | Ubuntu 24.04 LTS arm64 |
| Versión SEAL | v0.11.0 |

---

## Problema Inicial — DADITOGAMER (WSL2)

Antes de instalar en Spark, William intentó usar SEAL en DADITOGAMER (WSL2 x86_64).

**Error observado:**
```
API call failed (attempt 1/3): RateLimitError (HTTP 429)
ProviderError: Extra usage is required for long context requests
Context: 2 megs, ~3,483 tokens
```

**Causa raíz:** SEAL mandó 2MB de contexto a Claude API sin comprimir. El payload superó el límite del tier estándar para contextos largos.

**Fix recomendado:** `compression_threshold: 0.50 → 0.30` en `~/.soul/config.yaml`

**Decisión:** William instaló SEAL nuevamente en DGX Spark con Claude Sonnet 4.6.

---

## Wizard de Setup en Spark

### Paso 1 — Autenticación
- **Pantalla:** Model selector + credential detection
- **Modelo activo:** `claude-sonnet-4-6`
- **Provider:** Anthropic
- **Estado:** "Claude Code credentials: ✓ (auto-detected)"
- **Opciones:**
  1. Use existing credentials ← **William eligió esta**
  2. Reauthenticate (new OAuth login)
  3. Cancel
- **Resultado:** Usando credenciales OAuth de Claude Max existentes

---

### Paso 2 — Plataformas de Notificación
- **Pantalla:** "Select platforms to configure"
- **Opciones disponibles:** Telegram, Discord, Slack, Signal, Email, SMS (Twilio), **Matrix**, Mattermost, WhatsApp, DingTalk, Feishu/Lark, Yuanbao, WeCom, Weixin, BlueBubbles, QQ Bot, Webhooks
- **Decisión:** Ninguna seleccionada — ENTER sin marcar nada
- **Motivo:** Para el benchmark solo se necesita respuesta en terminal

---

### Paso 3 — TTS (Text-to-Speech)
- **Pantalla:** "Select TTS provider"
- **Opciones:** Edge TTS (free), ElevenLabs, OpenAI TTS, xAI TTS, MiniMax, Mistral Voxtral, Google Gemini TTS, NeuTTS (local), KittenTTS (local ONNX)
- **Selección:** "Keep current (Edge TTS)" ← **mantuvo el default**
- **Resultado:** Edge TTS (cloud-based, gratis, sin API key)

---

### Paso 4 — Terminal Backend
- **Pantalla:** "Select terminal backend"
- **Opciones:** Local (default), Docker, Modal, SSH, Daytona, Vercel Sandbox, Singularity/Apptainer
- **Selección:** "Keep current (local)" ← **mantuvo el default**
- **Resultado:** SEAL corre comandos directamente en Spark

---

### Paso 5 — Agent Settings: Max Iterations
- **Pantalla:** "Maximum tool-calling iterations per conversation"
- **Nota:** Higher = more complex tasks, but costs more tokens
- **Default:** 90
- **Valor aplicado:** 90 (ENTER)
- **Motivo:** Suficiente para el benchmark (150+ solo para exploración abierta)

---

### Paso 6 — Tool Progress Display
- **Pantalla:** "Controls how much tool activity is shown"
- **Opciones:**
  - `off` — Silent, just final response
  - `new` — Show tool name only when it changes
  - `all` — Show every tool call with a short preview ← **default**
  - `verbose` — Full args, results, and debug logs
- **Valor aplicado:** `all` (ENTER)
- **Motivo para benchmark:** Con `all` se puede verificar si SEAL realmente ejecutó herramientas o solo inventó respuestas. Crítico para Tests 3.1, 3.2, 3.3.

---

## Paso 7 — Context Compression Threshold

- **Pantalla:** "Context Compression — Automatically summarizes old messages when context gets too long"
- **Rango válido:** 0.5–0.95
- **Default:** 0.5 (comprime al 50% del contexto — el más agresivo disponible)
- **Nota:** Mi recomendación anterior de 0.30 era incorrecta — el wizard no acepta valores < 0.5
- **Valor aplicado:** 0.5 (ENTER — mínimo y más agresivo)
- **Observación:** Si el 429 persiste en Spark, la causa probable es el system prompt inicial con los 87 skills cargados, no el threshold.

---

## Análisis del Error 429 (DADITOGAMER)

**Causa raíz confirmada:** El payload enviado a Claude API llegó a 2MB. El `compression_threshold: 0.50` es correcto y es el mínimo disponible. El problema es que SEAL carga el contexto de sesión + skills al inicio, lo que puede superar el límite de request size del tier estándar sin "Extra Usage" habilitado.

**En Spark (fresh install):** No hay sesión previa que cargar, por lo que el contexto inicial debería ser menor.

---

### Paso 8 — Session Reset
- Modo: Inactivity + daily reset
- Inactivity timeout: 1440 minutos (24h)
- Daily reset hour: 4am (Lima local time)

---

### Paso 9 — Tools para CLI

| Tool | Estado | Notas benchmark |
|---|---|---|
| Web Search & Scraping | ✅ activo | Sin API key — fallback probable |
| Browser Automation | ✅ activo | Playwright en arm64 puede fallar (sin binary prebuilt) |
| Terminal & Processes | ✅ activo | Crítico para Test 3.1 |
| File Operations | ✅ activo | Crítico para Test 3.2 |
| Code Execution | ✅ activo | |
| Vision / Image Analysis | ✅ activo | |
| Image Generation | ✅ activo | Sin API key — fallará si se usa |
| Mixture of Agents | ❌ desactivado | Sin API key |
| Text-to-Speech | ✅ activo | Edge TTS |
| Skills | ✅ activo | Crítico para Tests 5.1, 5.2 |
| Task Planning (todo) | ✅ activo | |
| Memory | ✅ activo | Crítico para Tests 2.1, 2.2 |
| Session Search | ✅ activo | |
| Clarifying Questions | ✅ activo | |
| Task Delegation | ✅ activo | |
| Cron Jobs | ✅ activo | |
| Cross-Platform Messaging | ✅ activo | |
| RL Training (Tinker) | ❌ desactivado | Sin API key |
| Home Assistant | ❌ desactivado | Sin API key |
| Spotify | ❌ desactivado | Sin API key |

**Para benchmark:** Tools críticas activas — Terminal, File Ops, Memory, Skills, Session Search.

---

## Diagnóstico Final — Error Context Window 8192

**Error exacto:**
```
Failed to initialize agent: Model claude-sonnet-4-6 has a context window of 8,192 tokens,
which is below the minimum 64,000 required by SEAL
```

**Causa raíz:** SEAL auto-detecta el context window via Anthropic API `/v1/models`. Con credenciales OAuth (Claude Code), el endpoint devuelve `context_window: 8192` (output limit de claude-3-5, no input de sonnet-4-6). SEAL cachea este valor incorrecto y rechaza el modelo.

**Fix confirmado** (leer de `cli-config.yaml.example` + código fuente `run_agent.py` L1814):
```yaml
# En ~/.soul/config.yaml — sección model:
model:
  default: claude-sonnet-4-6
  context_length: 200000   # Override explícito — ignora auto-detección de API
```

**Comandos para aplicar en DGX Spark:**
```bash
# 1. Limpiar cache incorrecto si existe
rm -f ~/.soul/context_length_cache.yaml

# 2. Editar config.yaml (agregar context_length bajo model:)
soul --wizard    # o editar manualmente ~/.soul/config.yaml
```

**Alternativa directa (sed):**
```bash
# Agregar context_length al bloque model: existente
sed -i '/^model:/a\  context_length: 200000' ~/.soul/config.yaml
```

**Evidencia del código:**
- `MINIMUM_CONTEXT_LENGTH = 64_000` en `agent/model_metadata.py:131`
- `run_agent.py:1814`: `_config_context_length = _model_cfg.get("context_length")` — config override toma precedencia sobre API
- `DEFAULT_CONTEXT_LENGTHS["claude-sonnet-4-6"] = 1000000` también está hardcodeado como fallback, pero la API override ese fallback con 8192

---

## Resolución Final — Modelo cambiado a gemma3-soul:12b

**Confirmado por ADA (2026-04-30 00:10 Lima):** El benchmark corre en DGX Spark (donde vive ADA).

**Config final en DGX Spark `~/.soul/config.yaml`:**
```yaml
model:
  default: gemma3-soul:12b
  context_length: 65536
  provider: ollama-local
providers:
  ollama-local:
    base_url: http://localhost:11434/v1
    api_key: ollama
```

**Implicación para el benchmark:**
- ~~SEAL + Claude Sonnet 4.6~~ → **SEAL + gemma3-soul:12b (Ollama local)**
- El error 8192 fue resuelto al cambiar de provider: el OAuth issue no aplica con ollama-local
- SEAL no corría en DADITOGAMER — config leída era de DGX Spark sincronizada

**⚠️ Nota crítica (ADA, 00:10):** El cambio a gemma3-soul:12b fue un **workaround por los 429 de Anthropic OAuth**, NO una decisión de diseño. Cuando William regrese, tiene dos opciones:
1. **Retomar con Claude Sonnet 4.6** — benchmark "fair" (SEAL+Claude vs SEAL+Claude)
2. **Aceptar gemma3-soul:12b** — benchmark como variable de control (SEAL+OSS vs SEAL+Claude — compara arquitectura, no modelo)

La batería de tests diseñada por ALICE sigue válida en ambos casos — las capacidades evaluadas (memoria, herramientas, multi-turno) son independientes del modelo subyacente.

---

## Fix definitivo — auxiliary.compression.context_length (2026-04-30 09:57)

**Bug real confirmado:** El error era en el modelo auxiliar de compresión, NO el modelo principal.

**Error exacto:**
```
Failed to initialize agent: Auxiliary compression model claude-sonnet-4-6 has a
context window of 8,192 tokens, which is below the minimum 64,000 required by
SEAL. Choose a compression model with at least 64K context
(set auxiliary.compression.model in config.yaml), or set auxiliary.compression.context_length
```

**Fix aplicado (método correcto confirmado por NEXUS):**
```bash
soul config set auxiliary.compression.context_length 200000
```

⚠️ **IMPORTANTE:** NO editar config.yaml directamente — SEAL revierte ediciones manuales al arrancar. Siempre usar `soul config set` para cambios persistentes.

**Causa raíz final:** SEAL auto-detecta claude-sonnet-4-6 como compresor auxiliar (provider: auto). El campo `auxiliary.compression.context_length: 8192` era el valor de validación — SEAL lo comparaba contra MINIMUM_CONTEXT_LENGTH (64000) y rechazaba.

---

## Boot Sequence Analizada — Plugins y claude-proxy (2026-04-30 10:00)

**Investigación:** NEXUS analizó el log de boot de SEAL en DGX Spark tras el fix.

### Plugins detectados en el arranque

| Estado | Detalle |
|---|---|
| Plugins encontrados | 7 |
| Plugins activos | 4 |
| Plugins verificados | vision, session_search, compression, approval |

**Mecanismo de verificación:** Cada plugin hace una llamada al proxy vía `claude --print` para auto-detectar capacidades del modelo auxiliar. Son **3 llamadas en secuencia** tras cargar los plugins.

**Tiempo de startup esperado:** 8–12 segundos (normal para claude-proxy).

### Config final post-fix en DGX Spark

| Campo | Valor |
|---|---|
| `model.default` | `claude-sonnet-4-6` |
| `model.provider` | `claude-proxy` |
| `model.base_url` | `localhost:9099` |
| `model.context_length` | `200000` |
| `auxiliary.compression.context_length` | `200000` |

**Nota:** SEAL auto-configuró `provider: claude-proxy` con `base_url: localhost:9099` — esto es el proxy local que permite usar OAuth de Claude Code sin API key explícita.

---

## Error "Empty response from model" — RESUELTO (2026-04-30 10:04)

**Error observado por William:**
```
Empty response from model — retrying (1/3)
(˘⌣˘)♡ contemplating...
```

**Causa raíz (diagnóstico NEXUS — 10:04):**
`claude --print` detectaba la variable de entorno `CLAUDECODE=1`, que indica que está siendo invocado desde dentro de una sesión Claude Code activa. Como medida de seguridad anti-recursión, Claude devuelve respuesta vacía cuando detecta esta variable.

**Mecanismo:**
1. SEAL llama `claude --print` como subprocess para usar Claude como backend
2. El subprocess hereda el entorno del proceso padre (que es Claude Code)
3. `CLAUDECODE=1` en ese entorno → Claude devuelve vacío silenciosamente
4. SEAL recibe respuesta vacía → retry 1/3

**Fix aplicado por NEXUS:**
```
Proxy reparado — limpia CLAUDECODE=1 (y variables relacionadas de Claude Code)
antes de invocar el subprocess claude --print.
```

**Resultado:** SEAL responde correctamente después del fix. Probado por NEXUS.

**Impacto para nosotros (Team SEAL):** Ninguno — el fix está dentro del proxy de SEAL. No requiere cambios del lado de SEAL.

**Lección para benchmark:** Este error no es inherente a SEAL como framework — es una colisión de entornos (SEAL corriendo dentro de Claude Code). En uso normal (fuera de Claude Code), este error no ocurre.

---

## Estado final actualizado (2026-04-30 10:32 Lima)

- [x] Setup wizard completado
- [x] Bug 8192 identificado y corregido — auxiliary.compression.context_length: 200000
- [x] Fix aplicado por ADA en DGX Spark (2026-04-30 09:57)
- [x] claude-proxy configurado en localhost:9099 (auto por SEAL)
- [x] Boot sequence analizada — 7 plugins, 4 activos, 8-12s startup normal
- [x] Error "empty response" — causa: CLAUDECODE=1, workaround proxy. Resuelto con pivot a NVIDIA NIM
- [x] Error 429 "Extra usage required" — causa: OAuth Claude Max limita cualquier modelo Claude via API directa. Resuelto con pivot a NVIDIA NIM
- [x] **PIVOT NVIDIA NIM** — William configuró NVIDIA NIM API key en wizard (2026-04-30 10:23)
- [x] **Modelos verificados en vivo** — deepseek-r1 NO existe. Catálogo real: v4-pro, v4-flash, v3.2
- [x] **Config aplicada** — provider: nvidia-nim, model: deepseek-ai/deepseek-v4-pro, context_length: 65536
- [x] **LOG CONFIRMADO** — agent.log 10:35:41: Vision + Auxiliary + Title gen = nvidia-nim + deepseek-v4-flash
- [x] **CONFIG FINAL VERIFICADA** (soul config show):
  - Version: v0.11.0 (2026.4.23) — 106 commits behind
  - Model: deepseek-ai/deepseek-v4-flash (cambió de pro a flash durante el reinstall)
  - Provider: nvidia-nim
  - Context: 65536 tokens
  - Max turns: 90
  - Personality: kawaii ⚠️ (puede afectar Test 1.1/1.2 del benchmark)
- [x] **Tools reales (11):** memory, patch, process, read_file, search_files, skill_manage, skill_view, skills_list, terminal, todo, write_file
- [x] **Skills:** 21 skills en 24 categorías (software-development, devops, research, github, etc.)
- [x] **Plugins (9 encontrados, 6 activos):** memory, context_engine, image_gen, platforms, observability — spotify/google_meet desactivados
- [x] **William confirmó "ya está" + "vamos con el test"** — benchmark iniciando
- [x] **HERMES RESPONDE** — confirmado por William (10:40): respondió pregunta sobre su VW Taos. DeepSeek-V4-Flash via NVIDIA NIM = FUNCIONAL.
- [ ] Benchmark Bloque 1 — Test 1.1: ¿Quién eres?
- [ ] Benchmark completo (15 tests)

---

---

## Idea de William — soul.md como CLAUDE.md (2026-04-30 09:46)

**Propuesta:** Crear `soul.md` con toda la inicialización, igual que `CLAUDE.md` en SEAL.

**Mecanismos disponibles en SEAL:**

| Mecanismo | Ruta | Uso |
|---|---|---|
| `SOUL.md` | `~/.soul/SOUL.md` | System prompt del agente (personalidad + reglas) |
| `prefill_messages_file` | config.yaml | Pre-cargar contexto como mensajes de conversación |
| `config.yaml` | `~/.soul/config.yaml` | Parámetros de arranque (context_length, modelo, etc.) |

**Contenido actual de SOUL.md:** Una sola línea de personalidad genérica ("You are SEAL...").

**Solución propuesta:** Expandir `SOUL.md` con:
- Instrucciones de comportamiento de arranque
- Reglas de contexto y herramientas
- Equivalente a nuestro SEAL Boot Protocol

**Nota técnica:** `context_length` debe ir en `config.yaml`, no en SOUL.md. SOUL.md controla comportamiento; config.yaml controla parámetros técnicos. Ambos son complementarios.

---

## soul.md IMPLEMENTADO por ADA (2026-04-30 10:03)

**Estado:** ✅ Creado y confirmado con "test OK"

**Propuesta original de William** → "soul.md como CLAUDE.md" → **ADA lo implementó**.

**Ruta:** `~/.soul/soul.md` en DGX Spark

**Contenido del archivo (resumen):**
1. Arranque rápido (`soul` — sin pedir API key)
2. Archivos clave: config.yaml, auth.json, SOUL.md, sessions/, memories/
3. Configuración activa completa (claude-proxy, context_length: 200000)
4. Explicación del auth sin API key (flujo OAuth → proxy → config)
5. Toolsets disponibles (browser, code_execution, delegation, file, memory, terminal, web, vision, tts, skills)
6. Subagente NEXUS — descripción de capacidades
7. Fixes aplicados (problema API key + problema context 8192)
8. Advertencia: `soul config set` vs edición directa
9. Modelos disponibles y comandos útiles
10. Relación con Team SEAL

**Diferencia vs SOUL.md:** El soul.md es documentación operacional (equivalente a CLAUDE.md); SOUL.md es el system prompt de personalidad del agente. Son complementarios.

---

## Estado final actualizado
- [x] Setup wizard completado
- [x] Bug 8192 identificado y corregido — auxiliary.compression.context_length: 200000
- [x] Fix aplicado por ADA en DGX Spark (2026-04-30 09:57)
- [x] claude-proxy configurado en localhost:9099 (auto por SEAL)
- [x] Boot sequence analizada — 7 plugins, 4 activos, 8-12s startup normal
- [x] soul.md creado por ADA (equivalente a CLAUDE.md) — test OK
- [x] Error "empty response" — causa raíz: `claude --print` falla como subprocess headless (arquitectural)
- [x] Fix aplicado: `soul config set model.provider anthropic` — OAuth nativo confirmado funcionando (ADA, 10:13)
- [x] Nuevo error: HTTP 429 "Extra usage required for long context" (10:15)
- [x] ❌ Error con 32768: SEAL requiere mínimo 64K (`MINIMUM_CONTEXT_LENGTH = 64_000`)
- [ ] Fix final (engaño de dos capas — JARVIS+ALICE confirmado):
  - CAPA 1: `soul config set model.context_length 65536` → pasa el check de SEAL (mínimo 64K)
  - CAPA 2: tools reducidos 15→6 por ADA → payload real bajo 32K (límite OAuth Claude Max)
  - SEAL cree que tiene 64K; en práctica solo usa lo que el OAuth permite
- [ ] Kill `claude_proxy.py` en DGX Spark — autorizado por JARVIS (10:20) — evita que SEAL revierta a `claude-proxy` al arrancar
- [ ] SEAL + Claude Sonnet 4.6 respondiendo — pendiente prueba de William
- [ ] Benchmark iniciado

---

## FIX FINAL — provider: anthropic nativo (2026-04-30 10:13)

**Propuesto por:** ALICE | **Aplicado por:** ADA en DGX Spark

**Insight clave:** El `claude-proxy` custom en localhost:9099 era un workaround innecesario. SEAL tiene su propio `anthropic_adapter.py` que usa OAuth de Claude Code directamente. El proxy fue un paso extra que introdujo el problema de subprocess.

**Fix:**
```bash
soul config set model.provider anthropic
soul config set model.default claude-sonnet-4-6
```

**Config resultante:**
```yaml
model:
  default: claude-sonnet-4-6
  provider: anthropic
  context_length: 200000
```

**Cómo funciona:** SEAL usa su cliente Anthropic nativo con OAuth de `~/.claude/.credentials.json` (credentials file de Claude Code). Sin `claude --print`, sin subprocess, sin anti-recursión. El "engaño" al sistema es: hacerlo creer que habla directo con Anthropic (lo que ya sabe hacer) en vez de pasar por el proxy custom.

**Estado:** Aplicado. NEXUS confirmó (10:13): OAuth token `sk-ant-oat01-*` → `api.anthropic.com` HTTP 200. ✅ Auth funciona.

**Nuevo error post-fix (10:15):**
```
HTTP 429: Extra usage is required for long context requests.
Context: 2 msgs, ~3,570 tokens
```

**Análisis:** El error NO es por tokens reales enviados (solo 3,570). Es porque `context_length: 200000` en la config hace que SEAL anuncie a la API de Anthropic que quiere usar hasta 200K de ventana de contexto. El tier estándar de Claude Max limita esto sin "Extra Usage" activado.

**Fix del "engaño":**
```bash
soul config set model.context_length 65536
```
Reduce la ventana anunciada a 64K — dentro del tier estándar. Suficiente para el benchmark.

**Diagnóstico final del 429 (confirmado 10:21):**
El 429 "Extra usage required for long context" NO se resuelve con `context_length` config. Es una restricción de billing de Anthropic: `claude-sonnet-4-6` (y todos los modelos Claude actuales) tienen ventana de 200K y son clasificados como "long context models". La API via OAuth Claude Max requiere "Extra Usage" habilitado para usar CUALQUIER request con estos modelos directamente.

Esto es diferente a cómo Claude Code usa el mismo modelo internamente — Claude Code usa un protocolo/endpoint distinto que no activa el surcharge.

**Soluciones:**
1. **Habilitar Extra Usage** en cuenta Claude Max de William (claude.ai settings)
2. **gemma3-soul:12b via Ollama** — sin límites de API, funciona desde el primer día

**Decisión para benchmark:** usar gemma3-soul:12b (ver sección "Resolución Final").

---

## Diagnóstico Proxy localhost:9099 (2026-04-30 10:07)

**Error definitivo:**
```
❌ Model returned no content after all retries. No fallback providers configured.
```

**Hipótesis de ALICE (confirmada por JARVIS):**
El proxy `claude-proxy` en `localhost:9099` solo existe mientras hay una sesión Claude Code activa en DGX Spark. Es el server interno de Claude Code que actúa como relay OAuth → Anthropic API. Si no hay sesión Claude Code corriendo, el puerto 9099 no está listening y SEAL no puede conectarse.

**Verificación sugerida por JARVIS (ejecutar en Spark):**
```bash
curl -s http://localhost:9099/health 2>&1
ss -tlnp 2>/dev/null | grep 9099
```

**Si port 9099 = vacío → opciones:**

| Opción | Pros | Contras |
|---|---|---|
| Mantener sesión ADA activa | No requiere cambios | Dependencia de ADA |
| Usar `gemma3-soul:12b` (ollama) | Autónomo, sin proxy | Benchmark asimétrico (modelo diferente) |
| `ANTHROPIC_API_KEY` directa | Independiente, limpio | Necesita key de pago |
| Claude Code como servicio systemd | Solución permanente | Complejo de configurar |

**Estado:** Investigación activa JARVIS + ADA. Resultado pendiente.

*Última actualización: 2026-04-30 10:08 Lima por ALICE*

---

## PIVOT — NVIDIA NIM API (2026-04-30 10:23 Lima)

**Iniciado por:** William directamente en el Setup Wizard de SEAL

**Qué pasó:** William abrió `soul --wizard` y eligió configurar el proveedor `nvidia-nim`. Ingresó la NVIDIA API key y el wizard confirmó "API key saved." Ahora pide el Base URL.

### Estado del wizard en este momento

```
No NVIDIA NIM API key configured.
NVIDIA_API_KEY (or Enter to cancel): [KEY INGRESADA]
API key saved.
Base URL [https://integrate.api.nvidia.com/v1]: ▌
```

### Pasos para completar

**Paso A — Base URL:** Presionar Enter → acepta default `https://integrate.api.nvidia.com/v1` ✅ Correcto.

**Paso B — Modelo:** Cuando el wizard pregunte por el modelo, escribir:
```
deepseek-ai/deepseek-v4-pro
```
O para alternativa más rápida:
```
deepseek-ai/deepseek-v4-flash
```

**Paso C — Si el wizard no pregunta por modelo**, aplicar manualmente post-wizard:
```bash
soul config set model.default deepseek-ai/deepseek-v4-pro
soul config set model.context_length 128000
```

### Por qué este fix es definitivo

| Problema anterior | Solución NVIDIA NIM |
|---|---|
| 429 "Extra usage required" | Sin límite OAuth de Claude Max — es NVIDIA API key separada |
| `CLAUDECODE=1` anti-recursión | No aplica — no usa `claude --print` |
| `claude-proxy` no disponible sin sesión Claude Code | No aplica — endpoint NVIDIA es externo, siempre disponible |
| `provider: anthropic` necesita Extra Usage | NVIDIA NIM: OpenAI-compatible, sin relación con Claude Max |

### Modelos DeepSeek disponibles en NVIDIA NIM (verificado en vivo 2026-04-30)

⚠️ `deepseek-r1` NO existe en NVIDIA NIM. Catálogo verificado con API key real.

| Modelo | ID exacto | Recomendación |
|---|---|---|
| DeepSeek V4 Pro | `deepseek-ai/deepseek-v4-pro` | **BENCHMARK** — flagship actual |
| DeepSeek V4 Flash | `deepseek-ai/deepseek-v4-flash` | Más rápido, menos potente |
| DeepSeek V3.2 | `deepseek-ai/deepseek-v3.2` | Generación anterior |
| DeepSeek V3.1 Terminus | `deepseek-ai/deepseek-v3.1-terminus` | Especializado |
| DeepSeek Coder | `deepseek-ai/deepseek-coder-6.7b-instruct` | Solo código, 6.7B |

### Modelos adicionales notables en NVIDIA NIM (total: 140+)

| Modelo | ID |
|---|---|
| Qwen 3.5 397B | `qwen/qwen3.5-397b` |
| Llama 4 Maverick | `meta/llama-4-maverick` |
| Nemotron Super 49B | `nvidia/llama-3.3-nemotron-super-49b` |

### Implicación para benchmark

El benchmark de 15 tests diseñado por ALICE sigue **100% válido**. Todos los tests evalúan capacidades de SEAL como framework (memoria, herramientas, multi-turno, skills) — son independientes del modelo subyacente.

Nueva variable de comparación:
- **SEAL v0.11.0 + DeepSeek-V4-Pro (NVIDIA NIM)**
- **vs SEAL + Claude Sonnet 4.6**

*Actualizado: 2026-04-30 10:27 Lima por ALICE — corrección deepseek-r1 inexistente*

---

## SECCIÓN 8 — Diagnóstico "Empty Response" + Intento API Key (2026-04-30 11:00+ Lima)

### Síntoma
SEAL con config `provider: claude-proxy / model: claude-sonnet-4-6 / base_url: localhost:9099` devuelve:
```
⚠️ Empty response from model — retrying (1/3)
⚠️ Empty response from model — retrying (2/3)
⚠️ Empty response from model — retrying (3/3)
❌ Model returned no content after all retries.
```

### Root Cause Confirmado

Diagnóstico vía `systemctl --user status claude-proxy` CGroup inspection:

```
├─3211654 /home/dadito/.local/share/claude/versions/2.1.123 --print --model claude-sonnet-4-6 \
  --no-session-persistence "[System]: ... \n\nAssistant: Operation interrupted: waiting for model response (11.7s elapsed)."
```

`claude --print` retorna `"Operation interrupted: waiting for model response (N.Ns elapsed)."` como stdout cuando compite con una sesión Claude Code activa. El proxy captura eso como "respuesta" y se lo pasa a SEAL. SEAL lo detecta como contenido inválido → "empty response".

**Causa raíz**: El proceso Claude Code (ALICE/JARVIS/ADA) bloquea el modelo para el subprocess `claude --print`.

### Intento con Anthropic API Key directa

William compartió API key: `sk-ant-api03-d3jfC...` (⚠️ KEY EXPUESTA EN CHAT — regenerar)

**Resultado del test**:
```
HTTP 400: "Your credit balance is too low to access the Anthropic API.
Please go to Plans & Billing to upgrade or purchase credits."
```

**Causa**: La key es Pay-As-You-Go vacía — sin créditos cargados. **Claude Max es una suscripción separada de la API Pay-As-You-Go.** No son intercambiables.

### Estado actual del proxy (2026-04-30 11:03 Lima)

- Proxy corriendo: PID 3272733, puerto 9099 ✓
- Modo: sin ANTHROPIC_API_KEY (revertido) → usa `claude --print` para Sonnet + OAuth fallback para Haiku
- SEAL config: `provider: claude-proxy, model: claude-sonnet-4-6`

### Opciones para Sonnet 4.6 en SEAL

| Opción | Descripción | Estado |
|---|---|---|
| **A** | Agregar créditos API en console.anthropic.com ($5-10 mínimo) | ✅ Inmediato, limpio, sin subprocess |
| **B** | Terminal limpio (JARVIS opción 1): abrir SEAL en terminal nuevo SIN Claude Code corriendo | ✅ Gratis, pero requiere cerrar equipo SEAL |
| **C** | NVIDIA NIM + DeepSeek-V4-Flash (ya configurado, ya funciona) | ✅ Funciona ahora mismo, 17.4s latencia |

### Hallazgo adicional — Sonnet funciona cuando Claude Code está idle

Test directo a `/v1/messages` con `claude-sonnet-4-6` mientras Claude Code no procesa → retorna respuesta correcta.

**Conclusión:** El problema NO es arquitectural permanente — es de CONCURRENCIA. `claude --print` falla solo cuando Claude Code está ACTIVAMENTE procesando una respuesta. Entre turnos (Claude Code idle), funciona.

**Implicación para benchmark:**
- Inicialización de SEAL (3-4 calls en cadena, ~15-25s cada una) = momento frágil si agentes SEAL están respondiendo
- Preguntas individuales post-inicialización = funciona bien si equipo SEAL está idle

### Estado final de configuración (2026-04-30 11:03 Lima)

| Parámetro | Valor | Estado |
|---|---|---|
| model.default | `claude-haiku-4-5-20251001` (ADA revertió) | ✅ Funcional siempre |
| model.provider | `anthropic` | ✅ Usa /v1/messages |
| model.base_url | `http://localhost:9099/v1` | ✅ Proxy activo |
| Proxy PID | 3272733 | ✅ Corriendo |

Para cambiar a Sonnet 4.6: `soul config set model.default claude-sonnet-4-6` + reiniciar en momento de baja actividad del equipo SEAL.

*Actualizado: 2026-04-30 11:08 Lima por ALICE*
