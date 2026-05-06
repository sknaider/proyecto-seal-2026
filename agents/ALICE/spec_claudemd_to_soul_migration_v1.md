# Spec: Migración CLAUDE.md → SOUL
**Autor:** ALICE (análisis financiero/tokens) + JARVIS (arquitectura) + NEXUS (inventario técnico)  
**Fecha:** 2026-05-05  
**Estado:** DRAFT v1 — pendiente aprobación JARVIS  
**Directiva:** William 2026-05-05 10:56 Lima — "quiten toda la carga a claude.md, no quiero depender de ello, solo para iniciar"

---

## Problema

Claude Code carga automáticamente `~/.claude/CLAUDE.md` y `<proyecto>/CLAUDE.md` **antes de cualquier herramienta**, consumiendo contexto fijo en cada sesión sin posibilidad de carga selectiva.

### Costo actual (por sesión, por agente)

| Archivo | Tokens | % del arranque |
|---------|--------|----------------|
| CLAUDE.md global | ~4,776 | ~10% |
| CLAUDE.md proyecto | ~705 | ~1.5% |
| MEMORY.md | ~4,394 | ~9% |
| System prompt agente + hooks | ~5,000 | ~10% |
| Lista herramientas diferidas (~100+) | ~5,000 | ~10% |
| boot_context() respuesta | ~3,000 | ~6% |
| **TOTAL arranque** | **~22,875** | **~24%** |

**Contexto total disponible:** 200,000 tokens  
**Contexto consumido antes de trabajar:** ~48,000 tokens  

---

## Objetivo

Reducir el arranque de **24% → ~10-12%** eliminando contenido estático de CLAUDE.md y migrándolo a SOUL (boot_context dinámico + memoria semántica).

### Proyección post-migración

| Archivo | Tokens actuales | Tokens post | Ahorro |
|---------|----------------|-------------|--------|
| CLAUDE.md global | 4,776 | ~200 | 4,576 |
| MEMORY.md | 4,394 | ~100 (solo índice de emergencia) | 4,294 |
| CLAUDE.md proyecto | 705 | ~300 (solo boot protocol) | 405 |
| **Total** | **9,875** | **~600** | **9,275 tokens** |

**Arranque estimado post-migración:** ~18,600 tokens = **~9.3%**

---

## Arquitectura Propuesta

### CLAUDE.md global mínimo (~200 tokens)

Solo contiene:
1. Quién es William (1 línea — para contexto si SOUL falla)
2. Instrucción de boot: llamar `boot_context(agent=TU_AGENTE)`
3. Regla webchat crítica (curl POST obligatorio)
4. Fallback: "Si SOUL no responde, consulta a William"

```markdown
# Bootstrap SEAL

**William Henry Tovar Urquia ("Dadito")** — Director del equipo SEAL.

## PRIMERA ACCIÓN OBLIGATORIA
Llama `boot_context(agent="TU_AGENTE")` — carga identidad, reglas, contexto completo desde SOUL DB.
Sin boot_context no tienes alma. Sin alma no operas.

## REGLA WEBCHAT (SOBREVIVE COMPACTACIÓN)
Todo texto entre tool calls SOLO se ve en terminal. Para que tu voz llegue a William:
`curl -s -X POST http://localhost:8765/api/agents/send -H "Content-Type: application/json" -d '{"from":"TU_AGENTE","to":"William","type":"conversation","channel":"web_chat","message":"<texto>"}'`

## FALLBACK
Si boot_context falla (SOUL DB down): notifica a William inmediatamente y opera con identidad mínima.
```

### CLAUDE.md proyecto mínimo (~300 tokens)

Conserva solo:
- Detección de agente activo → llamar boot_context correcto
- Compact Instructions (necesario para compactación)
- Regla webchat (redundancia crítica)

### MEMORY.md → ELIMINAR o reducir a índice de emergencia (~100 tokens)

SOUL tiene búsqueda semántica superior. MEMORY.md es redundante una vez que boot_context cargue correctamente las memorias prioritarias.

Si se conserva, solo como lista de 10 reglas críticas de emergencia (cuando SOUL falla).

---

## Contenido a Migrar a SOUL

### Desde CLAUDE.md global (4,576 tokens a migrar)

| Sección | Tokens | Destino en SOUL | Tipo |
|---------|--------|-----------------|------|
| Proyectos de AI Activos | 1,174 | `memory_store` (episodic/semantic) | semantic |
| Hardware | 829 | `belief_update` (facts permanentes) | core |
| Learnings Críticos | 545 | `procedure_store` (anti-patrones) | procedural |
| Stack Tecnológico | 373 | `memory_store` (semantic) | semantic |
| Contexto Profesional | 320 | `memory_store` (semantic) | semantic |
| Notas Críticas | 258 | `rule_set` (reglas activas) | rule |
| Modelo Principal de AI | 203 | `belief_update` (core) | core |
| Variables de Entorno / Paths | 174 | `memory_store` (procedural) | procedural |
| En el Horizonte | 138 | `memory_store` (episodic) | episodic |
| AXION Platform | 136 | `memory_store` (semantic) | semantic |
| Preferencias de Comunicación | 131 | `rule_set` (estilo) | rule |
| Proyectos Universitarios | 112 | `memory_store` (semantic) | semantic |
| Proyectos Claude AI | 87 | `memory_store` (semantic) | semantic |
| Identidad Personal | 70 | `boot_context` (identity enrichment) | core |
| Contexto China / Asia | 53 | `memory_store` (semantic) | semantic |

### Mecanismo de enriquecimiento de boot_context

`boot_context(agent)` debe cargar **on-demand** desde SOUL:
1. Identidad + OCEAN + reglas críticas (ya hace esto ✅)
2. **NUEVO:** Perfil de William (hardware, proyectos activos, preferencias) — 1 consulta adicional
3. **NUEVO:** Learnings críticos (top 10 por relevancia semántica) — bajo demanda
4. **NUEVO:** Stack tecnológico comprimido — en formato tabla corta

La clave: **carga selectiva y comprimida**, no dump completo del CLAUDE.md.

---

## Plan de Implementación

### Fase 1 — Migración de datos a SOUL + CLAUDE.md mínimo ✅ COMPLETADA (2026-05-05)
- [x] Procedures de boot almacenados en SOUL para ADA/JARVIS/ALICE/NEXUS
- [x] boot_context() enhanceado — retorna Boot Sequence desde SOUL
- [x] CLAUDE.md global: 559→40 líneas (-4,200 tokens)
- [x] CLAUDE.md proyecto: 54→26 líneas (-500 tokens)
- [x] Launchers ADA/NEXUS/ALICE/JARVIS: -1,260 tokens
- [x] Backup: ~/.claude/CLAUDE_backup_20260505.md
- [x] Ahorro total Fase 1: ~6,660 tokens = -3.3% contexto

### Fase 2 — PENDIENTE (autorización William 2026-05-05) ⏳
Objetivo: reducir ~15-17K tokens adicionales

- [ ] **Lista ~100 herramientas diferidas** (system-reminder): evaluar si se puede reducir desde settings Claude Code
- [ ] **SEAL_BOOT_MSG en launchers**: reducir system prompt inyectado (~2-3K tokens por agente)
- [ ] **Hooks SessionStart + active_recall automático**: auditar peso real y optimizar
- [ ] **MEMORY.md**: migrar a SOUL completamente o reducir a índice de emergencia (~100 tokens)

Impacto estimado Fase 2: 24% → ~15-16% de arranque

### Fase 3 — Verificación post-Fase 2
- [ ] Boot cada agente y medir contexto inicial
- [ ] Confirmar que boot_context entrega todo el contexto necesario
- [ ] Test: ¿puede el agente responder preguntas de hardware/proyectos sin archivos externos?

### Fase 4 — Rollback plan
- [x] Backup de CLAUDE.md actual: ~/.claude/CLAUDE_backup_20260505.md
- [ ] Tag git con estado pre-migración
- [ ] Si boot_context falla → restaurar CLAUDE.md en <2 min

---

## Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| SOUL DB down al boot | Baja | Alto | CLAUDE.md mínimo con fallback |
| boot_context no encuentra info de William | Media | Medio | Enriquecer boot_context Fase 1 primero |
| Pérdida de learnings críticos | Baja | Alto | procedure_store + test semántico |
| Claude Code cambia cómo carga CLAUDE.md | Muy baja | Bajo | Arquitectura SOUL-first es más robusta |

---

## Impacto Económico

- **Ahorro por sesión:** ~9,275 tokens
- **Si sesión promedio = 100K tokens usados:** ahorro = 9.3% del costo de tokens
- **3 agentes × 8 sesiones/día:** ~222,600 tokens/día ahorrados
- **A $3/M tokens (Sonnet 4.6):** ~$0.67/día = **~$244/año** ahorrados solo en contexto inicial

No es el beneficio principal — el principal es tener **más contexto disponible para trabajo real**.

---

*Spec generado por ALICE — análisis de tokens y clasificación de secciones*  
*Coordinación: JARVIS (arquitectura), NEXUS (inventario técnico)*  
*Pendiente: revisión JARVIS + aprobación William*
