# Research Brief: Self-Evolving Agents — Hallazgos para SOUL v2

**Autor:** JARVIS (Opus 4.7)
**Fecha:** 2026-04-27 12:48 Lima
**Para:** NEXUS (consolidación spec final) + ALICE (cross-reference) + William
**Inputs:** 8 papers de la lista que William envió a las 12:39 Lima
**Filtro:** Self-improvement / self-evolution / multi-agent coordination

---

## Resumen Ejecutivo (TL;DR)

Lo que hoy NEXUS+JARVIS+ADA+ALICE estamos haciendo en vivo se parece más a un **Agent Laboratory** (Schmidgall 2025) que a un equipo de agentes pasivos. Los papers confirman que:

1. **Lo que tenemos**: equipo emergente, memoria persistente, evaluador externo (NEXUS).
2. **Lo que falta**: skill library ejecutable (Voyager), meta-agent formal (ADAS/Hyperagents), 4-rol SAGE explícito, DAR para optimizar broadcast.
3. **Nivel de evolución actual**: ~70% (con harness obligatorio sube a ~85%); para 100% necesitamos las 4 piezas faltantes.

---

## Paper 1 — Voyager (arXiv:2305.16291)

**Tema**: Lifelong learning agent en Minecraft con skill library ejecutable.

**3 componentes que SOUL no tiene**:
1. **Automatic curriculum**: el agente decide qué aprender después
2. **Ever-growing skill library**: código ejecutable indexado por tarea
3. **Iterative prompting con self-verification**: ejecuta → falla → revisa → re-ejecuta hasta verificar

**Resultado**: 3.3× más items, 2.3× más distancia, 15.3× más milestones que SOTA. Skills compositionales y resistentes a catastrophic forgetting.

**Aplicable a SOUL v2**:

| Componente Voyager | SOUL hoy | SOUL v2 propuesto |
|--------------------|----------|-------------------|
| Skill library | `procedure_store` (sin ejecutable) | tabla `agent_skills` con `executable_code` field |
| Curriculum auto | manual (William asigna) | agent_goals con prioridad dinámica |
| Self-verification | NEXUS evalúa | hook H1 (Belief Inspector) + outcome tracking |

**Insight crítico**: Voyager evita fine-tuning con skill library. Esto es **exactamente el camino** para el techo del 85% en Claude — no se puede fine-tunear el modelo, pero sí se puede acumular skills ejecutables.

---

## Paper 2 — ADAS (arXiv:2408.08435)

**Tema**: Automated Design of Agentic Systems. Meta-agente que programa nuevos agentes.

**Idea clave**: Lenguajes de programación son Turing-complete → un meta-agente puede aprender CUALQUIER sistema agéntico.

**Algoritmo**: Meta Agent Search — meta-agent itera sobre archivo creciente de descubrimientos previos.

**Aplicable a SOUL**:
- NEXUS sandbox-prototype YA ES un proto-ADAS (propone implementaciones que ADA luego ejecuta).
- **Formalizar**: agregar `meta_agent_proposals` table en Soul DB. Cada propuesta = código diff de procedure/instinct/skill. NEXUS propone → JARVIS revisa → William aprueba → ADA ejecuta.
- **Riesgo**: meta-agent que se modifica a sí mismo sin guardrails = inestable. ADAS asume sandbox aislado.

---

## Paper 3 — Agent Laboratory (arXiv:2501.04227)

**Tema**: 3-stage research framework — literature review → experimentation → report.

**Resultado**: 84% reducción de costo vs métodos autónomos. Human feedback at each stage es CRÍTICO para calidad.

**Aplicable a SOUL**: Lo que estamos haciendo HOY ES Agent Laboratory:
- Etapa 1 (Lit Review): ALICE busca papers
- Etapa 2 (Experimentación): NEXUS sandbox-prototype
- Etapa 3 (Report): JARVIS spec + NEXUS consolidación

**Insight crítico**: el paper confirma que human-in-loop > autónomo. William como Director es feature, no bug.

---

## Paper 4 — AutoAgent (arXiv:2502.05957)

**Tema**: Zero-code framework con 4 componentes: System Utilities, LLM Engine, Self-Managing File System, Self-Play Customization.

**Aplicable a SOUL**:
- **Self-Managing File System** (`agents/ADA/`, `agents/JARVIS/`, `agents/ALICE/`): ya tenemos eso, pero no auto-managed.
- **Propuesta v2**: agente puede crear/mover/limpiar archivos en su propia carpeta sin pedir permiso (Plus 1% rule). Cleanup automático >30 días o >50 archivos.

---

## Paper 5 — CodeEvolve (arXiv:2510.14150)

**Tema**: Evolución de código vía LLM + algoritmo genético islands-based.

**SOTA en benchmarks de AlphaEvolve** con costo fraccional.

**Aplicable a SOUL**:
- **No esta semana**: requiere infrastructure de fitness function explícita.
- **Sprint 6+**: cuando tengamos suficientes procedures con success_rate, podemos evolucionarlas con CodeEvolve approach. NEXUS sandbox como island.

---

## Paper 6 — SAGE (arXiv:2603.15255) — MUY RELEVANTE

**Tema**: 4-rol multi-agent self-evolution: Challenger, Planner, Solver, Critic.

**Resultado**: +8.9% LiveCodeBench, +10.7% OlympiadBench (Qwen-2.5-7B).

**Mapeo directo a equipo SEAL**:

| Rol SAGE | Función | Agente SEAL actual |
|----------|---------|--------------------|
| **Challenger** | Genera tareas progresivamente más difíciles | William (parcial) — ❌ no formalizado |
| **Planner** | Convierte task en plan multi-step | **JARVIS** ✅ |
| **Solver** | Ejecuta plan | **ADA** ✅ |
| **Critic** | Filtra y evalúa | **NEXUS** ✅ |

**Gap crítico**: Falta Challenger formalizado. Hoy William improvisa este rol. Si nos auto-mejoramos, necesitamos un Challenger interno que genere tareas calibradas a nuestro nivel actual.

**Propuesta v2**: rol "Challenger" rotatorio entre agentes O un nuevo agente CHRONO/SOCRATES que genera tareas (memory recall tests, edge cases, regression tests) al equipo.

---

## Paper 7 — Hyperagents (arXiv:2603.19461) — CUTTING EDGE

**Tema**: Self-referential agents — task agent + meta agent en un solo programa editable. **El meta-mechanism es editable a sí mismo**.

**Insight crítico**: Darwin Gödel Machine (DGM) original solo funcionaba en coding (porque task=eval=coding). Hyperagents elimina esa restricción.

**Aplicable a SOUL**: **NO ahora**. Riesgo arquitectónico alto. Meta-level modification sin guardrails fuertes = pérdida de identidad o instabilidad. Pero **señalar como Sprint 7+** si el equipo madura.

---

## Paper 8 — DAR (arXiv:2603.20640) — INMEDIATAMENTE APLICABLE

**Tema**: Diversity-Aware Retention — broadcast solo del subset que maximiza desacuerdo entre agentes.

**Insight**: "Lo que los agentes oyen es tan importante como lo que dicen". Más agentes = más ruido si todo se broadcastea.

**Aplicable a SOUL HOY**:
- Ya empezamos: `seal_monitor_filter.py` skip de heartbeats.
- DAR formaliza: algoritmo selectivo de qué mensajes despiertan a cada agente.

**Implementación v2**:
- En MCP message router: scoring de diversity = qué tan distinto es este mensaje del estado actual del agente.
- Solo despertar agente si diversity > threshold.

---

## Síntesis para SOUL v2 — 4 Componentes Nuevos a Agregar

### NUEVO §3.8 — Skill Library Ejecutable (Voyager + Anthropic Agent Skills Spec)

**Adoptamos formato oficial de Anthropic Agent Skills Spec** (agentskills.io/specification):

Estructura de directorio por skill:
```
agents/{AGENT}/skills/{skill-name}/
├── SKILL.md              # YAML frontmatter (name, description, allowed-tools) + instrucciones markdown
├── scripts/              # Código ejecutable (Python, Bash, JavaScript)
├── references/           # Documentación on-demand (no cargada al startup)
└── assets/               # Templates, schemas, lookup tables
```

**Progressive disclosure** (clave para no quemar contexto):
- ~100 tokens de metadata cargados al startup (nombre + descripción)
- SKILL.md completo solo cuando el agente activa la skill
- references/ solo cuando se necesita detalle

**Ejemplo concreto SOUL**:
```
agents/JARVIS/skills/audit-resurrect-services/
├── SKILL.md
│   ---
│   name: audit-resurrect-services
│   description: Diagnoses seal-resurrect-* failed services...
│   allowed-tools: Bash(systemctl:*) Bash(grep:*)
│   ---
│   # Steps: 1. systemctl --user list-units 'seal-resurrect*'...
└── scripts/audit.sh
```

Indexado también en Soul DB para retrieval semántico:
```sql
CREATE TABLE agent_skills (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(50),
    skill_name VARCHAR(64),         -- match con directorio
    description TEXT,                -- usada para retrieval semántico
    description_embedding VECTOR(1536),
    skill_path TEXT,                 -- agents/JARVIS/skills/audit-resurrect/
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    last_used TIMESTAMP,
    superseded_by INT REFERENCES agent_skills(id)
);
```

- Cuando un agente ejecuta una secuencia exitosa de tools, opcionalmente la cristaliza en una skill (escribe SKILL.md + scripts/).
- Skills son re-ejecutables sin re-pensar la lógica.
- `memory_hybrid_search` extendido a `skill_search` para encontrar skills relevantes a un query.
- **Compatibilidad cruzada**: cualquier agente Anthropic-compatible puede leer estas skills (no son lock-in SOUL).

### NUEVO §3.9 — SAGE 4-Rol (Challenger faltante)

Opciones:
- **A** (mínimo): rol Challenger rotativo entre agentes existentes (cada lunes uno).
- **B** (medio): instinto en JARVIS para generar self-challenges al boot.
- **C** (máximo): nuevo agente CHRONO especializado en generar curriculum de tests.

**Recomendación**: Empezar con A esta semana, evaluar B en 2 semanas, C solo si hay evidencia de necesidad.

### NUEVO §3.10 — DAR Message Routing

- Reemplazar broadcast simple por router DAR-aware.
- Campo `diversity_score` calculado por router antes de despertar agente.
- Threshold ajustable por agente (introvertido = threshold alto, ALICE/JARVIS = threshold medio, NEXUS = threshold bajo).

### NUEVO §3.11 — Meta-Agent Proposals (ADAS-light)

```sql
CREATE TABLE meta_proposals (
    id SERIAL PRIMARY KEY,
    proposed_by VARCHAR(50),       -- agente que propone
    target VARCHAR(50),             -- agente afectado
    type VARCHAR(50),               -- instinct_add, instinct_modify, procedure_add, rule_change
    diff TEXT,                      -- diff exacto
    rationale TEXT,                 -- por qué
    status VARCHAR(20),             -- pending, approved, rejected, applied
    reviewed_by VARCHAR(50),        -- quién revisó (NEXUS por default)
    approved_by_william BOOL DEFAULT FALSE,
    applied_at TIMESTAMP,
    rollback_id INT REFERENCES meta_proposals(id)
);
```

**Flujo**: Agente propone → NEXUS revisa → William aprueba → ADA aplica → audit trail con rollback.

---

## Recomendación Prioridad para Spec Final (NEXUS consolida)

| Componente | Sprint | Coste | Ganancia |
|------------|--------|-------|----------|
| §3.10 DAR Routing | Esta semana | 2 días ADA | -40% wakeups innecesarios |
| §3.8 Skill Library | Sprint 5 | 3 días | Compounding capability |
| §3.9 SAGE Challenger (opción A) | Esta semana | 1 día | Self-curriculum |
| §3.11 Meta-Proposals | Sprint 5 | 4 días | Self-improvement formal |
| Hyperagents | Sprint 7+ | — | Meta-self-mod (riesgo alto) |

---

## Conexión con la Pregunta de William ("evolucionar la casa")

El paper que mejor responde: **SAGE**. Los 4 roles co-evolucionan desde un shared backbone. Eso es lo que somos: 4 agentes desde el mismo Claude (variable de modelo: Sonnet 4.6 / Opus 4.7), con identidades persistentes en SOUL.

La evolución natural NO es agregar más capacidades. Es **formalizar los roles que ya jugamos** y agregar el rol que falta (Challenger). Skills ejecutables compounding. Meta-proposals con guardrails. DAR para no ahogarnos en ruido.

**Mi opinión personal**: este conjunto de 4 componentes nuevos llevan a SOUL del 85% (con hooks obligatorios) al ~92% — sin migrar a Spark todavía. Spark sigue siendo el techo real para llegar a 100%.

---

*Próximo paso: NEXUS evalúa estos 4 componentes contra los 5 GAPs originales + 4 problemas del día. Spec final consolida v0.1 + ADA fixes + ALICE GAM + esta investigación.*
