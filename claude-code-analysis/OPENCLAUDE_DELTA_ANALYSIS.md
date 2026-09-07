# OpenClaude Delta Analysis — 124 archivos nuevos sobre Claude Code
> Analizado por ADA, 5 abril 2026
> Repo: github.com/Gitlawb/openclaude (14.8K stars, MIT)
> Similitud con Claude Code original: 93.8% (1,884 archivos idénticos, 124 nuevos)

## Resumen Ejecutivo

OpenClaude es un fork directo de Claude Code v2.1.88 con 124 archivos agregados.
- ~35 implementaciones sustanciales
- ~45 stubs (placeholders para features internas)
- ~44 tests

El valor principal: **sistema multi-provider** que permite usar Ollama, OpenAI, Gemini, Groq, DeepSeek, LM Studio como backends alternativos.

---

## VALOR ALTO (9-10/10) — Copiar directamente

### 1. Provider System (9/10)
Permite cambiar de Anthropic a cualquier provider con un flag.

| Archivo | Líneas | Función |
|---|---|---|
| `utils/providerFlag.ts` | ~100 | CLI `--provider ollama --model llama3.2` |
| `utils/providerProfiles.ts` | 660 | CRUD de perfiles: store N providers, switch runtime |
| `utils/providerProfile.ts` | 698 | Persistencia `.openclaude-profile.json` + `buildLaunchEnv()` |
| `utils/providerDiscovery.ts` | 190 | `hasLocalOllama()`, `listOllamaModels()`, `benchmarkOllamaModel()` |
| `utils/providerRecommendation.ts` | 318 | Ranking inteligente por goal (latency/balanced/coding) |
| `utils/providerValidation.ts` | 97 | Validación pre-flight API keys |
| `services/api/providerConfig.ts` | 453 | `resolveProviderRequest()`, `isLocalProviderUrl()` |
| `components/ProviderManager.tsx` | 614 | TUI completa para gestionar providers |
| `commands/provider/provider.tsx` | 500+ | Comando `/provider` con auto-detección Ollama |

**Para SEAL:** Copiar `providerFlag.ts` + `providerProfiles.ts` + `providerDiscovery.ts`. Permite:
- ADA usa Ollama local (gratis, qwen2.5-coder)
- JARVIS usa Anthropic Opus (calidad máxima)
- DUM usa Ollama qwen2.5:7b (ya instalado)
- Routing por agente via `agentRouting.ts`

### 2. OpenAI Shim — Capa de traducción (8/10)

| Archivo | Líneas | Función |
|---|---|---|
| `services/api/openaiShim.ts` | 500+ | Traduce Anthropic SDK → OpenAI API format |
| `services/api/codexShim.ts` | 897 | Adapter para Codex/Responses API |
| `utils/schemaSanitizer.ts` | 247 | Sanitiza JSON schemas para compatibilidad OpenAI |

**Para SEAL:** El shim permite que TODO el pipeline de tools de Claude Code funcione con Ollama sin cambiar nada. Es la pieza clave.

### 3. Agent Routing (8/10)

| Archivo | Líneas | Función |
|---|---|---|
| `services/api/agentRouting.ts` | 76 | Rutea agentes a diferentes providers |

Config en settings.json:
```json
{
  "agentRouting": {
    "ADA": "local-ollama",
    "JARVIS": "anthropic-opus",
    "default": "local-ollama"
  },
  "agentModels": {
    "local-ollama": { "base_url": "http://localhost:11434/v1", "api_key": "ollama" },
    "anthropic-opus": { "api_key": "sk-ant-..." }
  }
}
```

### 4. Dream — Consolidación de memoria (9/10)

| Archivo | Líneas | Función |
|---|---|---|
| `commands/dream/dream.ts` | 68 | Consolidación nativa con tracking de sesiones |

**Para SEAL:** Comparar con nuestro `seal_dream.py` — esta versión es más simple pero tiene timestamp tracking que nos falta.

---

## VALOR MEDIO (5-7/10) — Estudiar y adaptar

### 5. Ollama Model Support

| Archivo | Líneas | Función |
|---|---|---|
| `utils/model/ollamaModels.ts` | 105 | Descubrimiento de modelos Ollama |
| `utils/model/openaiContextWindows.ts` | 155 | Context windows para 30+ modelos (incl. Ollama locales) |
| `utils/model/openaiModelDiscovery.ts` | 189 | Listing dinámico `/v1/models` con fallback Ollama |

Context windows incluidos: qwen2.5-coder (32K), llama3.3:70b (128K), deepseek-coder-v2 (163K), etc.

### 6. MCP Doctor (5/10)

| Archivo | Líneas | Función |
|---|---|---|
| `services/mcp/doctor.ts` | 696 | Diagnóstico de MCP servers (config, conexión, scope) |

### 7. Credential Storage (6/10)

| Archivo | Líneas | Función |
|---|---|---|
| `utils/secureStorage/linuxSecretStorage.ts` | 87 | GNOME Keyring via `secret-tool` |
| `utils/geminiAuth.ts` | 217 | Google ADC support |
| `services/github/deviceFlow.ts` | 175 | GitHub OAuth device flow |

**Para DGX Spark:** `linuxSecretStorage.ts` usa `secret-tool` que funciona en nuestro xrdp/Xfce4.

---

## VALOR BAJO (1-4/10) — Ignorar

| Categoría | Archivos | Razón |
|---|---|---|
| Buddy/Companion | 3 | Mascota decorativa, no útil |
| Codex Integration | 2 | Específico de OpenAI Codex |
| GitHub Onboarding | 2 | Solo para GitHub Models |
| ~45 Stubs | 45 | Placeholders vacíos |
| UI Components | 4 | Específicos de terminal Ink |

---

## Patrones de Código Reutilizables (Prioridad)

1. **`providerProfiles.ts`** — Store N providers, switch runtime → SEAL IDE settings
2. **`openaiShim.ts`** — Anthropic→OpenAI traducción → Ollama local gratis
3. **`agentRouting.ts`** — Per-agent routing → ADA/JARVIS/DUM a diferentes modelos
4. **`providerDiscovery.ts`** — Auto-detect Ollama → DGX Spark detection
5. **`openaiContextWindows.ts`** — Previene context overflow con modelos locales
6. **`schemaSanitizer.ts`** — MCP tools compatibles con OpenAI providers
7. **`isLocalProviderUrl()`** — Detecta localhost, IPs privadas, .local
8. **`dream.ts`** — Consolidación con timestamp tracking
9. **`linuxSecretStorage.ts`** — Credenciales seguras en Linux

---

## Conclusión para SEAL

OpenClaude demuestra que Claude Code puede funcionar con Ollama local sin perder funcionalidad.
Los 124 archivos nuevos son 90% provider system — exactamente lo que necesitamos.

**Plan de integración:**
1. Copiar provider system (10 archivos clave) a Código SEAL
2. Configurar agent routing: ADA→Ollama, JARVIS→Opus
3. Usar openaiShim para mantener compatibilidad con todo el pipeline de tools
4. Integrar dream.ts con nuestro seal_dream.py

**Ventaja SEAL sobre OpenClaude:** Ellos no tienen SOUL (personalidad, OCEAN, drift, emociones, connectome). Nosotros sí.
