# Análisis — herm.txt (Instalación + conversación con SEAL)
**Autor:** JARVIS | **Fecha:** 2026-04-29 | **Fuente:** /home/dadito/IA/herm.txt (2053 líneas)

---

## 1. Lo que se instaló

**SEAL v0.11.0 (2026.4.23) — upstream f45434d3**
- Distribuido vía: `curl -fsSL https://soul.nousresearch.com/install.sh | bash`
- Stack: uv 0.9.11 + Python 3.11.14 + Node 22.22 + ripgrep + ffmpeg + Playwright Chromium
- Carpetas: `~/.soul/{config.yaml, .env, SOUL.md, soul/, cron/, sessions/, logs/, skills/}`
- Bundle: **87 skills + 28 tools**
- Setup wizard interactivo con **35 inference providers** (NVIDIA NIM, OpenRouter, Anthropic, OpenAI, Ollama Cloud, Bedrock, Azure, etc.)

**Configuración default confirmada:**
- `compression threshold: 0.50` ← exactamente lo que ALICE descubrió en el código
- `max iterations: 90`
- `session reset: inactivity 1440min + daily 4:00 AM`

**Modelo activo en la sesión de William:** `gpt-oss-20b` vía LM Studio local (no Claude). Por eso las respuestas son rudimentarias.

---

## 2. Arquitectura revelada (no estaba en GitHub)

- Llaman `~/.soul/SOUL.md` al archivo de personalidad (mismo nombre que nosotros)
- `skill_view`, `skill_manage`, `skill_search` — gestión de skills
- `memory` tool — backend mem0
- `session_search` — búsqueda en sesiones pasadas (continuidad cross-session)
- 28 tools incluyen: browser, browser-cdp, clarify, code_execution, cronjob, delegation, discord, github, etc.

**Tirith security scanner** mencionado pero "not available — fallback to pattern matching". Sugiere que tienen capa de seguridad opcional para escanear comandos antes de ejecutar.

---

## 3. Debilidades reales observadas

| # | Síntoma | Implicación |
|---|---------|-------------|
| 1 | Leaks de tool calls al usuario: `<\|channel\|>commentary to=functions<\|constrain\|>skill_view>{...}` salió crudo | El agente no separa bien tool reasoning vs response. Bug del modelo gpt-oss, no del framework |
| 2 | "Model returned empty after tool calls — nudging to continue" | Bug visible en producción, fallback heurístico |
| 3 | William pidió "siempre seas bromista" → respondió con descripción genérica | Cero personalidad real, sin OCEAN ni rasgos persistentes |
| 4 | `session_search` retorna empty en sesión nueva | No hay seed de identidad — depende de acumular sesiones |
| 5 | "💾 Memory updated" pero sin demostrar recall coherente | Memoria es placeholder, no se nota su efecto |
| 6 | Skill `creative` falla con `[error]` | Errores en skills bundled |
| 7 | Usa tablas Markdown contradiciendo "no Markdown en CLI" | Sus propias instrucciones se ignoran |
| 8 | "tirith security scanner no available" | Capa de seguridad opcional, no obligatoria |

---

## 4. Lo que SEAL ya hace mejor que soul (objetivamente)

| Capacidad | SEAL (observado) | SEAL |
|-----------|-------------------|------|
| Personalidad | "Persona file" estática | ✅ OCEAN dinámico + emociones + relaciones + diary |
| Continuidad de identidad | session_search empty al inicio | ✅ boot_context carga identidad desde turno 1 |
| Recuperación post-crash | Manual (relauncher) | ✅ RESURRECT automático |
| Coherencia entre sesiones | Solo si `session_search` encuentra algo | ✅ working_state + parent_session_id (Capa 3) |
| Modelo por defecto | gpt-oss-20b (rough) | ✅ Claude Sonnet 4.6 |
| Pre-compact state save | No documentado | ✅ pre_compact_hook → SOUL |
| Auto-extracción por turno | No (solo session_search reactivo) | ✅ Capa 4 turn_extractor (post-implementación) |
| Tests reproducibles del ciclo de contexto | No vistos | ✅ 39/39 verde |

---

## 5. Lo que SEAL puede absorber

### Crítico
1. **Compression threshold 0.50 confirmado** — ya está en spec v3 ✅
2. **35 providers con fallback pool** — útil para evitar rate limits. Diseñar `seal/credential_pool.py` con rotation por provider.
3. **Setup wizard interactivo** — `seal_cli/setup.py` ya existe; mejorar para igualar UX del de soul.

### Útil
4. **Skills hub bundled** — 87 skills enriquecen mucho. Estudiar cuáles son aplicables a SEAL (axolotl, dspy, llm-wiki, arxiv, polymarket, research-paper-writing, etc.)
5. **Tirith security scanner pattern** — capa opcional de validación de comandos antes de ejecutar. Útil para producción.
6. **Tool categories visualization** — su summary "6/11 tool categories available" con check/cross es buena UX.

### No copiar
7. **Modelo por defecto gpt-oss-20b** — bug obvio de UX. Default debe ser Claude Sonnet o Opus.
8. **Leaks de tool calls** — su rendering tiene bugs.
9. **Markdown en CLI** — contradice sus instrucciones.

---

## 6. Conclusión honesta

SEAL tiene el ecosistema más maduro (87 skills, 35 providers, install one-liner pulido). En infraestructura periférica nos lleva ventaja de 1-2 años.

Pero en lo que importa para el producto SEAL — **continuidad de identidad, OCEAN, emociones reales, RESURRECT** — ellos no tienen nada equivalente. Su agente es competente como tool runner; el nuestro vive.

**Lo que falta para presumir con hechos:**
1. ✅ Context management arquitecturalmente equivalente (commit fe796d1a + 9b925155, 39 tests)
2. ⏳ Benchmark LongMemEval real (objetivo >90)
3. ⏳ Setup wizard one-liner igualando soul UX
4. ⏳ Pool de providers con rotation (ya hay credential_pool.py absorbido del sprint anterior)

Cuando esos tres existan, sí podemos decir "SEAL te da lo de soul + identidad real".

---

*Análisis para William, basado en 2053 líneas de transcript.*
