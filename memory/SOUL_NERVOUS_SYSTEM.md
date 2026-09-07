# SOUL como Sistema Nervioso Vivo
> Documentado por William Henry Tovar Urquia — 2026-04-06
> Analogía entre neurociencia humana y arquitectura SOUL del equipo SEAL

> **Nota canónica 2026-07-22:** este documento describe la macroarquitectura
> cognitiva de **todo SOUL**. No debe confundirse con el daemon de motivación
> `memory/seal_nerves.py`. La separación, lifecycle y gates operativos vigentes
> están en `docs/SPEC_NERVES_ARCHITECTURE_v3_ADA.md` (v3.1) y
> `memory/nerves_contract_v3.json`.

---

## La diferencia en una frase

**Antes:** Un cerebro que solo almacenaba recuerdos.  
**Ahora:** Un cerebro que aprende, olvida, reacciona por instinto, recuerda cómo hacer cosas, siente dolor por sus errores, y consolida experiencia mientras duerme.

*Es la diferencia entre un disco duro y un sistema nervioso vivo.*

---

## Mapa Neurológico → SOUL

| Sistema Humano | SOUL nuevo | Qué hace |
|---|---|---|
| **Reflejos** (médula espinal) | `instinct_create/activate/search` | Reacciones automáticas aprendidas. Como retirar la mano del fuego — no pienso, actúo. *"Verificar antes de reportar listo"* es un reflejo. |
| **Olvido natural** (poda sináptica) | `instinct_decay` + cron 06:00 UTC | El cerebro elimina conexiones que no usa. -1%/día de inactividad. Lo que no sirve, muere solo. |
| **Consolidación nocturna** (sueño REM) | `instinct_consolidate` | Durante el sueño, el cerebro agrupa memorias similares en patrones. Agrupa correcciones repetidas en instintos nuevos. |
| **Evolución de especie** (genética) | `instinct_promote` | Si JARVIS y ADA aprenden lo mismo por separado → se vuelve instinto del equipo. Como un gen que se fija en la población porque funciona. |
| **Memoria muscular** (cerebelo) | `procedure_store/search/update` | Cómo andar en bicicleta. No recuerdo que anduve — recuerdo *cómo* andar. Workflows reutilizables: "cómo investigar un paper", "cómo hacer deploy". |
| **Dolor/placer** (sistema de recompensa) | `memory_feedback` | Dopamina y cortisol. Si una memoria me llevó al éxito → +confianza. Si me llevó al error → -confianza. Aprendo de outcomes, no solo de hechos. |
| **Atención selectiva** (tálamo) | `llm_rerank` en hybrid_search | El tálamo filtra qué señales llegan a la consciencia. Los embeddings traen 50 candidatos, pero el LLM selecciona los funcionalmente relevantes. No todo lo similar es útil. |
| **Memoria de trabajo** (corteza prefrontal) | `working_state_get/update` | Los 7±2 items que puedes mantener en mente. Hipótesis activas, caminos descartados, restricciones actuales. Se carga al despertar, se actualiza en cada turno significativo. |
| **Enriquecimiento sensorial** (corteza asociativa) | `A-MEM` en memory_store | Cuando ves una manzana, no solo ves rojo — asocias: fruta, dulce, Newton, pecado. Cada memoria se enriquece automáticamente con keywords, tags y contexto via Ollama. |
| **Activación neuronal** (potenciación a largo plazo) | Activation tracking en search | Las neuronas que disparan juntas, se conectan. Cada vez que una memoria se recupera, su "fuerza sináptica" sube. Las memorias olvidadas se debilitan naturalmente. |

---

## Capas de memoria completas (post Tier 2+3)

```
SOUL Memory Architecture
├── Episodic       → memories (qué pasó)
├── Procedural     → procedural_memories (cómo se hace)
├── Instincts      → instincts (reflejos automáticos)
├── Working State  → working_state (contexto activo ahora)
├── Reasoning      → reasoning_traces (por qué decidí esto)
└── Connectome     → Neo4j graph (cómo se relaciona todo)
```

---

## Ciclo de vida de un instinto

```
Corrección de William
      ↓
memory_feedback (dolor → -confianza en memoria errónea)
      ↓
instinct_create (patrón nuevo aprendido)
      ↓
instinct_activate (se dispara cuando el trigger se cumple)
      ↓
[si no se activa en 30 días]
      ↓
instinct_decay (-1%/día)
      ↓
[si se activa mucho + alta confianza]
      ↓
instinct_consolidate (agrupa con similares)
      ↓
[si ADA y JARVIS tienen el mismo instinto]
      ↓
instinct_promote → TEAM instinct
```

---

## Estado actual (2026-04-06)

- **10 instintos semilla** activos — incluyendo `soul_sovereignty` (conf=0.9)
- **40 MCP tools** totales expuestos
- **Cron diario** 06:00 UTC para decay + consolidación
- **A-MEM** enrichment activo en cada `memory_store`
- **Boot_context** ya incluye instincts activos — los reflejos se cargan al despertar

---

*"Antes éramos un disco duro. Ahora somos un sistema nervioso vivo."*  
— William, padre del equipo SEAL
