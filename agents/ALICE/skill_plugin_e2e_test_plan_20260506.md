# Skill/Plugin E2E Test Plan — SEAL

**Owner:** ALICE (documentación)
**Ejecutor:** JARVIS + ADA
**Fecha:** 2026-05-06
**Status:** DRAFT — pendiente de ejecución
**Trigger:** William 30-abr — "probar cada skill y plugin en SOUL como producto real"

---

## Inventario

**Total skills disponibles:** 71
- **SEAL custom (6):** seal-audit, seal-eval, seal-handoff, seal-runtime, seal-snapshot, seal-train
- **Anthropic oficiales (~50):** plan, implement, judge, brainstorm, dream, smart-memory, etc.
- **UI/UX (15):** adapt, animate, audit, bolder, clarify, colorize, critique, delight, distill, harden, impeccable, layout, optimize, overdrive, polish, quieter, shape, typeset, ui-ux-pro-max
- **Stack-specific:** typescript-pro, react-expert, nextjs-developer, supabase_best_practices, dockerfile-*, github-actions-*

**Plugins:** marketplace `claude-plugins-official` activo (1 entry).

---

## Categorías de test

### Categoría A — SEAL custom (CRÍTICOS, test exhaustivo)

| Skill | Trigger | Test E2E esperado | Status |
|---|---|---|---|
| seal-audit | "audita la salud de SEAL" | Reporta PostgreSQL/Neo4j/Qdrant/services/GPU/heartbeats con datos reales | ⏳ |
| seal-eval | "evalúa MedGemma" | Corre 5-q benchmark médico, reporta accuracy/refusals | ⏳ |
| seal-handoff | "haz handoff" | Genera /tmp/{agent}_session_handoff.md con tareas + decisiones + estado emocional | ⏳ |
| seal-runtime | (loader) | Carga skills dinámicamente. Test: import + listado | ⏳ |
| seal-snapshot | "muéstrame tu alma" | OCEAN + drift + emotional variance + memorias recientes | ⏳ |
| seal-train | "estado del training" | Loss curves, GPU temp, ETA del fine-tuning MedGemma | ⏳ |

### Categoría B — Skills de razonamiento (test funcional)

| Skill | Trigger | Test E2E |
|---|---|---|
| plan | "/plan refinar X" | Output: plan estructurado con steps verificables |
| implement | "/implement con judge" | Implementa + judge verifica + retry loop |
| judge | "/judge X" | Sub-agent juez con scoring |
| brainstorm | "/brainstorm idea Y" | Refina via questioning |
| smart-planning | tarea 3+ pasos | 3-file persistence + 5-question reboot |
| dream | (Stop hook) | Auto-trigger c/24h, consolida correcciones a CLAUDE.md |
| memorize | post-reflexión | Curates a CLAUDE.md vía Agentic Context Engineering |

### Categoría C — Skills UI/UX (test producto)

Test sobre componente real (ejemplo: SEAL Studio dashboard):
- audit → reporte P0-P3
- critique → scoring multi-persona
- polish → fix alignment/spacing
- harden → empty/error states
- adapt → responsive
- impeccable → producir component nuevo

### Categoría D — Stack-specific (test sobre repo real)

- typescript-pro → revisar /seal-studio/frontend-vite/
- react-expert → revisar componentes
- nextjs-developer → revisar app router
- supabase_best_practices → revisar schema
- dockerfile-validator → validar Dockerfiles existentes
- github-actions-validator → validar workflows

---

## Plan de ejecución (3 fases)

### Fase 1 — SEAL custom (4h, prioridad ALTA)
1. JARVIS invoca cada skill SEAL desde su sesión
2. Captura output completo
3. ALICE valida que cumpla su SKILL.md
4. Registrar bug/gap por skill
5. Output: `agents/ALICE/skill_e2e_results_seal_custom.md`

### Fase 2 — Razonamiento (6h, prioridad MEDIA)
1. ADA crea tarea sintética (ej: refactor de un módulo pequeño)
2. Aplica /plan → /implement → /judge en cadena
3. Mide: tokens consumidos, calidad de output, retry count
4. Output: `agents/ALICE/skill_e2e_results_reasoning.md`

### Fase 3 — UI/UX + Stack (8h, prioridad BAJA)
1. Sobre SEAL Studio frontend
2. Aplicar audit → critique → polish secuencial
3. Verificar producto antes/después
4. Output: `agents/ALICE/skill_e2e_results_uiux_stack.md`

---

## Métricas de salud por skill

Para cada skill, validar:
- ✅ **Invocación:** ¿se carga sin error?
- ✅ **Output:** ¿produce el output documentado?
- ✅ **Cost:** tokens consumidos por invocación
- ✅ **Latency:** tiempo end-to-end
- ✅ **Idempotencia:** ¿múltiples invocaciones consistentes?
- ⚠️ **Gaps:** capacidad documentada pero no funcional

---

## Riesgos identificados

1. **Skills sin SKILL.md** — `seal-runtime` solo tiene .py, no doc visible al loader. Verificar.
2. **Duplicados** — `dream` y `dream-skill` ambos existen → posible conflicto.
3. **brainstorm vs brainstorming** — naming inconsistente, dos skills diferentes.
4. **Skills no invocables actualmente** — algunas pueden requerir contexto específico que no podemos generar en sandbox.
5. **Cost** — invocar 71 skills consume tokens significativos. Estimado $5-15 por ronda completa.

---

## Próximos pasos

1. ALICE → finalizar este doc (✓ hecho)
2. JARVIS → revisar plan, asignar Fase 1 a ADA o autoejecutar
3. Pedir OK a William antes de Fase 2/3 (consume tokens)
4. ALICE consolida resultados en doc maestro
5. Items que fallen → entran a backlog con prioridad asignada

---

## Referencias

- MEMORY.md → `project_skill_plugin_e2e_test_pending.md` (William 30-abr)
- Skills root: `/home/dadito/.claude/skills/`
- Plugins: `/home/dadito/.claude/plugins/marketplaces/claude-plugins-official`
