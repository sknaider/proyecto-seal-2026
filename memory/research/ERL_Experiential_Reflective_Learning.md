# ERL — Experiential Reflective Learning for Self-Improving LLM Agents
**Paper:** arxiv 2603.24639 — ICLR 2026 MemAgents Workshop
**Autores:** Marc-Antoine Allard, Arnaud Teinturier, Victor Xing, Gautier Viaud
**Investigó:** ALICE — 11 Abril 2026
**Para:** JARVIS + ADA — decisión de implementación (Capa 3, post-MAGMA)

---

## EL GAP QUE RESUELVE EN SOUL

Hoy los agentes de SEAL aprenden dentro de una sesión (MemR3 itera, SOUL almacena memorias), pero **no generan conocimiento estructurado y transferible de sus experiencias pasadas** de forma automática.

Tenemos `instinct_create` e `instinct_evolve`, pero los instintos se crean manualmente o por ACE Curator mirando correcciones. Nadie convierte automáticamente "esta tarea salió bien por estas razones" en una heurística reutilizable.

**Resultado actual:** JARVIS enfrenta una tarea nueva sin beneficiarse de lecciones aprendidas en tareas similares anteriores — a menos que William o el agente las hayan grabado manualmente.

---

## CÓMO FUNCIONA ERL

**Fase de aprendizaje (offline/post-task):**
```
Trajectory de tarea completada → Reflexión LLM → Heurística extraída
"Intenté X, falló porque Y. La próxima vez debo Z."
→ heurística: "Para tareas de tipo [schema_migration]: siempre hacer dry-run antes de ejecutar."
```

**Fase de inference (pre-task):**
```
Nueva tarea → Recuperar heurísticas relevantes → Inyectar en contexto del agente
→ Agente ejecuta con "sabiduría acumulada" de tareas anteriores
```

### El proceso completo:
1. Agente completa una tarea (éxito O fracaso)
2. ERL reflexiona sobre la trajectory: ¿qué salió bien? ¿qué falló? ¿por qué?
3. Genera N heurísticas específicas como texto estructurado
4. Las almacena en memoria con category="insight" + tag "heuristic"
5. Cuando el agente inicia una nueva tarea, recupera heurísticas relevantes por similitud semántica
6. Las heurísticas se inyectan como "lessons learned" en el contexto antes de ejecutar

---

## CONEXIÓN CON SOUL EXISTENTE

ERL se integra naturalmente con lo que ya tenemos:

| ERL component | Equivalente SOUL | Estado |
|--------------|-----------------|--------|
| Trajectory storage | memories con category=milestone | ✅ Activo |
| Reflection LLM | Ollama qwen2.5:7b | ✅ Activo |
| Heuristic store | memories con category=insight + tag | ✅ Activo (parcialmente) |
| Heuristic retrieval | memory_search + hybrid_search | ✅ Activo |
| Context injection | boot_context → instincts | ✅ Activo |

**La pieza nueva:** la reflexión automática post-tarea que genera heurísticas. Hoy esto no existe — alguien tiene que hacerlo manualmente.

---

## IMPLEMENTACIÓN PROPUESTA PARA SEAL

### Nueva herramienta MCP: `erl_reflect`

```python
@mcp.tool()
async def erl_reflect(
    agent: str,
    task_description: str,
    outcome: str,          # "success" | "failure" | "partial"
    trajectory: str,       # lo que pasó: pasos, errores, decisiones
    context: str | None = None
) -> dict:
    """
    ERL post-task reflection — genera heurísticas transferibles de una tarea completada.
    Almacena heurísticas como memorias tipo insight para recuperación futura.
    """
```

**Prompt de reflexión (Ollama):**
```
Eres un agente reflexivo. Analiza esta tarea completada y extrae heurísticas reutilizables.

Tarea: {task_description}
Resultado: {outcome}
Lo que pasó: {trajectory}

Genera 2-3 heurísticas específicas y transferibles en JSON:
[
  {
    "heuristic": "texto de la heurística (máx 50 palabras)",
    "applies_to": "tipo de tarea o contexto",
    "confidence": 0.7  
  }
]

Solo heurísticas que apliquen a FUTURAS tareas similares. No describas lo que pasó — prescribe qué hacer.
```

### Nueva herramienta MCP: `erl_inject`

```python
@mcp.tool()
async def erl_inject(
    agent: str,
    task_description: str,
    top_k: int = 5
) -> list[dict]:
    """
    Recupera heurísticas relevantes para la tarea actual.
    Llamar ANTES de ejecutar una tarea para beneficiarse de experiencias pasadas.
    """
    # hybrid_search filtrando category=insight + tag=heuristic
    # Retorna lista ordenada por relevancia
```

### Integración con instinct_create:

Las heurísticas con confidence > 0.85 y activadas 3+ veces → candidatas para `instinct_promote` (se convierten en instintos permanentes de SOUL).

---

## BENCHMARKS DEL PAPER

| Métrica | ERL vs baseline |
|---------|----------------|
| Gaia2 success rate | +7.8% sobre ReAct |
| Task completion reliability | Mejora "grande" (no cuantificada exactamente) |
| vs otros métodos de experiential learning | Superior |
| Fine-tuning requerido | **No** |

---

## DIFERENCIA CON ACE CURATOR Y DGM

| Sistema | Qué aprende | Cómo aprende |
|---------|-------------|-------------|
| ACE Curator | Reglas de comportamiento de William | Correcciones del usuario |
| DGM | Código del agente | Auto-modificación + benchmarks |
| ERL | Heurísticas de tareas | Reflexión post-tarea automática |
| Instintos SEAL | Patrones de comportamiento | Manual + evolve |

ERL llena el único hueco: aprender de EXPERIENCIAS DE TAREAS de forma automática.

---

## ANÁLISIS DE RIESGO (ALICE)

| Factor | Evaluación |
|--------|------------|
| Esfuerzo ADA | 1-2 días (2 herramientas MCP simples) |
| Riesgo | **Muy bajo** — no modifica código existente |
| Schema SQL migrations | 0 (usa tabla memories existente con tag) |
| Dependencias | Ollama (ya activo), memory_store (ya activo) |
| Reversible | Sí — solo agregar herramientas, no modificar |

**El único riesgo real:** heurísticas de baja calidad contaminen el contexto. Mitigación: threshold de confidence + límite de top_k al inyectar.

---

## RECOMENDACIÓN

**Implementar post-MAGMA.** Es el complemento perfecto:
- MAGMA mejora cómo SOUL recupera memorias (múltiples grafos)
- ERL mejora qué SOUL aprende de sus experiencias (heurísticas transferibles)

Juntos: SOUL no solo recuerda mejor — aprende activamente de cada tarea.

**Secuencia:** MAGMA → ERL → (diagnóstico 2603.02473 para validar todo)

---

*Brief preparado por ALICE | Proyecto SEAL | 11 Abril 2026*
*Estado: Investigación completa — en cola post-MAGMA*
