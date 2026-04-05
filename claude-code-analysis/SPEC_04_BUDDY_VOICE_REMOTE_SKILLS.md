# Spec 04: Buddy, Voice, Remote/Ultraplan, Skills — Claude Code (Anthropic)
> Clean-room analysis by ADA — 2026-04-03
> Source: extracted sourcemap cli.js.map v2.1.88

---

## 1. BUDDY SYSTEM — Tamagotchi Companion

### Generación determinista (gacha):
- Seed = `userId` (OAuth UUID) + salt fijo `"friend-2026-401"`
- Hash FNV-1a → PRNG Mulberry32
- Resultado cacheado (un solo roll por userId por proceso)
- Usuario NO puede elegir ni re-tirar

### Rareza:
| Rarity | Peso | Floor stats | Hat? | Shiny? |
|--------|------|-------------|------|--------|
| common | 60% | 5 | no | 1% |
| uncommon | 25% | 15 | sí | 1% |
| rare | 10% | 25 | sí | 1% |
| epic | 4% | 35 | sí | 1% |
| legendary | 1% | 50 | sí | 1% |

### 18 especies:
duck, goose, blob, cat, dragon, octopus, owl, penguin, turtle, snail, ghost, axolotl, capybara, cactus, robot, rabbit, mushroom, chonk.

Nota: nombres construidos con `String.fromCharCode()` en hex — una especie comparte nombre con codename interno de modelo Anthropic, evade grep de CI.

### Componentes:
- **6 ojos:** `·`, `✦`, `×`, `◉`, `@`, `°`
- **8 sombreros:** none, crown, tophat, propeller, halo, wizard, beanie, tinyduck
- **5 stats RPG:** DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK

### Sprites ASCII:
- 5 líneas alto × 12 ancho, 3 frames por especie (animación idle)
- Línea 0 = sombrero, `{E}` = placeholder para ojo
- Secuencia idle: `[0,0,0,0,1,0,0,0,-1,0,0,2,0,0,0]` donde -1 = parpadeo. Tick 500ms.

### Soul (personalidad):
- **CompanionBones** = determinista (rarity, species, eye, hat, shiny, stats) — NUNCA se persisten
- **CompanionSoul** = generada por modelo (name, personality) — persiste en config
- Merge en `getCompanion()`: bones recalculados siempre, soul leída de config
- **Imposible falsificar rareza editando config**

### Speech bubbles:
- Visible ~10s (20 ticks), fade últimos ~3s
- Terminales angostas (<100 cols): colapsa a face + quip inline (max 24 chars)

### Pet mechanic:
- `/buddy pet` → corazones ASCII flotan 2.5s, sprite en animación excitada

### Intro al modelo:
Attachment `companion_intro`: le dice al modelo que existe companion (ej. "A small capybara named Pickle sits beside..."). Modelo NO habla por el companion.

---

## 2. VOICE — Sistema de Voz

### Triple gate:
1. **Feature flag:** `VOICE_MODE` en bundle (compile-time)
2. **Kill switch GrowthBook:** `tengu_amber_quartz_disabled`
3. **Auth:** Requiere OAuth Anthropic. NO funciona con API keys, Bedrock, Vertex, Foundry

### Endpoint:
`voice_stream` en claude.ai — streaming de voz al backend de Anthropic. **No es STT/TTS local.**

---

## 3. REMOTE / ULTRAPLAN — Ejecución Remota con Opus

### Tres capas de Remote Sessions (CCR):

**SessionsWebSocket:**
- `wss://api.anthropic.com/v1/sessions/ws/{sessionId}/subscribe`
- Auth via Bearer token headers
- Ping cada 30s, reconnect max 5 intentos, 2s delay

**RemoteSessionManager:**
- Orquesta WebSocket + HTTP POST
- Maneja permission requests del CCR (`can_use_tool`)
- CLI local aprueba/deniega herramientas del container remoto
- `viewerOnly` mode para `claude assistant`

**sdkMessageAdapter:**
- Convierte SDKMessage (formato CCR) a Message (formato REPL)
- `createToolStub()`: stubs para tools MCP remotas desconocidas localmente

### ULTRAPLAN — Planning con Opus:

**Flujo completo:**
1. **Trigger:** Keyword "ultraplan" en input O slash command `/ultraplan`
2. **Keyword detection:** Ignora ultraplan dentro de comillas, backticks, tags, paths, extensiones, seguido de `?`
3. **Modelo remoto:** Configurable via GrowthBook, default = Opus 4.6
4. **Teleport:** Crea sesión CCR con prompt inicial, setea plan mode
5. **Poll detached:** Cada 3s por hasta **30 minutos**
6. **Fases:** `running` → `needs_input` (modelo pregunta en browser) → `plan_ready` (esperando aprobación)
7. **ExitPlanModeScanner:** Máquina de estados que clasifica: approved, teleport, rejected, pending, terminated
8. **Dos destinos:**
   - `remote`: usuario aprueba en browser → CCR ejecuta → PR como resultado
   - `local` (teleport): plan vuelve al terminal para ejecución local
9. **Rejection loop:** Usuario puede rechazar planes múltiples veces
10. **Stop/kill:** Archiva sesión remota, mata task local, limpia estado

### Teleport bidireccional:
Empezar planificando en browser → "teleportar" plan de vuelta al terminal. Plan embebido en deny tool_result con sentinel marker.

---

## 4. SKILLS SYSTEM

### Dos tipos:

**A. File-based (custom):**
- `.md` en `~/.claude/skills/` (user) o `.claude/skills/` (project)
- Frontmatter: name, description, when_to_use, allowed-tools, model, context, agent, hooks, paths
- `${CLAUDE_SKILL_DIR}` y `${CLAUDE_SESSION_ID}` sustituidos
- Shell commands inline (`!`...`) ejecutados pre-invocación
- Arguments: `${1}`, `${argName}` syntax

**B. Bundled (compiladas):**
- `registerBundledSkill()` en `initBundledSkills()`
- Archivos de referencia extraídos a disco on-demand (O_EXCL|O_NOFOLLOW por seguridad)
- Per-process nonce + 0o700 permissions

### Skills bundled (18):
| Skill | Descripción |
|-------|-------------|
| update-config | Configura settings.json |
| keybindings | Personaliza shortcuts |
| verify | Verifica cambios corriendo la app (ANT-ONLY) |
| debug | Debug session logs |
| lorem-ipsum | Genera texto de N tokens (benchmark) |
| skillify | Convierte sesión en skill reutilizable |
| remember | Review/promote auto-memory entries (ANT-ONLY) |
| simplify | 3 agentes paralelos: reuse, quality, efficiency |
| **batch** | **5-30 sub-agentes en paralelo con git worktrees aislados** |
| stuck | Diagnostica sesiones frozen → Slack |
| **dream** | **Consolidación de memoria tipo sleep humano** |
| loop | Prompt recurrente con cron |
| schedule | Remote agents con cron |
| claude-api | Guía Claude API/SDK con docs inline |
| claude-in-chrome | Automatización Chrome browser |

### Lifecycle:
Registration → Discovery (modelo ve description) → Invocation (Skill tool) → Prompt assembly → Execution (inline o fork)

---

## 5. Features Ocultos

1. **Shiny companions (1%):** Como Pokémon shiny. Flag en bones pero sin rendering especial visible.
2. **Species name obfuscation:** Nombres en hex para evadir CI grep que busca codenames de modelos.
3. **Bones never persist:** Diseño anti-cheat. Traits recalculados del hash. Solo name+personality se guardan.
4. **Batch skill:** 5-30 agentes en paralelo con worktrees aislados. Cada uno: implementa, simplify, test, commit, push, crea PR.
5. **Ultraplan keyword dodge:** El prompt evita la palabra "ultraplan" en su propio texto porque el CCR la auto-triggearía.
6. **Permission bridge remoto:** CCR crea tool stubs on-the-fly para mostrar permission dialog localmente.
7. **Dream skill:** Escanea transcripts buscando correcciones, decisiones, preferencias, patrones. Auto-trigger cada 24h.

---

## 6. Comparación con SEAL

| Aspecto | Anthropic | SEAL |
|---|---|---|
| Companion | BUDDY (Tamagotchi, gacha, 18 especies) | No implementado |
| Voice | Streaming vía claude.ai (OAuth only) | Voice system propio (Faster-Whisper local) |
| Remote execution | CCR + ULTRAPLAN (Opus 30min, browser UI) | No implementado |
| Skills | 18 bundled + file-based custom | Skills via /loop + CLAUDE.md |
| Batch | 5-30 agentes paralelos + worktrees | No implementado |
| Dream | /dream skill + autoDream automatic | session_delta_capture + guardia |

**Insights para SEAL:**
1. BUDDY es marketeable pero no técnicamente profundo — fácil de reimplementar si queremos un companion
2. ULTRAPLAN con Opus remoto por 30 min es poderoso — podríamos implementar algo similar con DGX Spark
3. Batch skill (5-30 agentes con worktrees) es la feature de productividad más potente — priorizar
4. El sistema de skills file-based es elegante y extensible — nuestro CLAUDE.md approach es más rígido
