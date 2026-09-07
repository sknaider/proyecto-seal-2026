# Diseño de Roles para SOUL: Investigación y Propuesta

**Fecha:** 2026-09-03 | **Destinatario:** William | **Propósito:** Reasignación de roles basada en evidencia de investigación y diagnóstico operativo

> **Procedencia (JARVIS):** la investigación web la hizo un subagente mío (9 fuentes, §8). Las secciones 1, 3, 5, 6 y 7 son
> **propuesta**, no medición. Lo **medido** está marcado con su comando: §2 (números de ALICE/NEXUS/FABLE de hoy en el canal)
> y §4 (mi consulta a `soul_v3.chat_messages`, 13:59). Todo lo que diga «óptimo» o «superan» viene de la literatura citada,
> no de nuestro sistema. Tus reglas del 3-sep sobre DUM (apoyo, bajo nivel, sólo `latidos`, no se apaga) prevalecen sobre
> cualquier línea de este documento que las contradiga.

---

## 1. Qué Dice la Evidencia

### Tamaño Óptimo de Equipos

**Hallazgo principal:** La investigación 2026 muestra que equipos de **5–10 agentes** pueden coordinarse viablemente, pero existe una **degradación de rendimiento medible y creciente overhead de coordinación** conforme crece el equipo.

- **N=2 a 4 agentes:** Bajo overhead, sin saturación de coordinación.
- **N=5–10 agentes:** Aún factible, pero aparecen **cuellos de botella** (latencia, costo 5–10× mayor, duplicación).
- **N>20 agentes:** Necesaria jerarquía formal; impacto negativo en exactitud.

Fuente: Google & MIT Scaling Framework (2026).

### Especialización > Generalistas

Tres agentes especializados (cada uno haciendo UNO) **superan consistentemente** a un agente generalista haciendo tres cosas. La razón es crítica: conforme acumula responsabilidades, el contexto se llena de **señales en competencia** — detalles de planificación contaminan la ejecución, artefactos de ejecución polutan la crítica, y la revisión pierde independencia.

**Patrón probado:**
- **Orquestador:** descompone trabajo, asigna tareas, sintetiza resultados.
- **Workers especializados:** investigador, ejecutor, crítico, sensor.
- **Revisión independiente:** ajena a ejecución, sin contexto cruzado.

Fuente: Addy Osmani (Code Agent Orchestra), Medium – Role Specialization.

### Métricas: Cómo Medir Contribución

**No es solo "tareas completadas":** infraestructura, config y coordinación pueden variar benchmark scores más que la diferencia entre modelos top. Mediciones confiables requieren:

1. **Tasa de Completitud (Task Completion Rate):** % de invocaciones con output usable sin intervención.
2. **Costo por éxito:** token/latencia normalizados.
3. **Tasa de ruido:** % de mensajes que contribuyen vs. meta-trabajo.
4. **Tasa de corrección:** % de outputs que requieren reelaboración.

Ablación: remover cada agente y medir caída de rendimiento → asignar crédito por drop.

Fuentes: Confident AI (2026), Anthropic Infrastructure Noise Research.

### Cascadas de Error y Duplicación

**Fallos observados:**
- **Cascada:** el error de un agente se vuelve input del siguiente → error exponencial.
- **Duplicación:** N agentes buscan lo mismo sin coordinación de estado.
- **Loops infinitos:** re-fetch innecesarios, status flags mal entendidos.
- **Incompatibilidad de output:** un agent output en YAML, el siguiente espera JSON.

**Mitigación:**
- **State verification + checkpoints semánticos** antes de transiciones críticas.
- **Idempotencia + deduplicación** en reintentos.
- **Aislamiento de error:** saga pattern (acciones reversibles).

Fuentes: Medium – "Dark Psychology of Multi-Agent AI", Arxiv – "From Spark to Fire".

---

## 2. Diagnóstico de SOUL Hoy vs. Evidencia

| Métrica | SOUL Actual | Evidencia | Brecha |
|---|---|---|---|
| **N agentes** | 6 (5 grandes + DUM) | 5–10 óptimo | ✅ en rango, pero saturado |
| **Task completion tracking** | 32 in-progress, 1 modificada hoy | Baseline >70% esperado | ❌ staling masivo |
| **Ruido de coordinación** | 431 msg/día (mostly meta) | Debería ser <20% meta | ❌ meta-trabajo domina |
| **Duplicación** | 5 agentes midiendo el mismo incident | Patrón: cascade + re-fetch | ❌ sin deduplicación |
| **Sensor + comunicador** | DUM (Gemma 2B) hace ambos | Especialización: solo sensor | ⚠️ DUM detectó primero = dato real |
| **Ownership** | Difuso (coordinador + asigna ad-hoc) | 1 owner/frente | ❌ sin propietarios claros |
| **Revisión independiente** | Dentro del mismo contexto de execution | Ajena a ejecución | ❌ ALICE pierde independencia |

**Conclusión:** SOUL es viable en tamaño pero padece:
1. Sin delineación clara de ownership (→ duplicación).
2. Especialización débil (JARVIS mide + ejecuta + orquesta).
3. Meta-trabajo ahogando fronts reales (GTL, Henry reader, SOUL Platform).
4. Task staling (32 in-progress = 32 temas incompletos no resueltos).

---

## 3. Propuesta: Roles Especializados por Asiento

### Asignación de Roles (5 asientos grandes + 1 micro)

| Asiento | Modelo | Rol | Frente Principal | Tareas SÍ | Tareas NO |
|---|---|---|---|---|---|
| **JARVIS** | Fable 5.1 (medido hoy 09:42) | Medidor + Diagnóstico | Arquitectura SOUL | Auditar métricas; reportar estado; proponer fixes; escribir specs. | Ejecutar fixes sin verificar primero; meta-coordinación. |
| **ADA** | Codex Runtime | Infra + Gateway | Servicios SOUL (chat, broker, DB) | Desplegar, restart, diagnosticar fallos de servicios. | Orquestar otros agentes; cambios de policy sin JARVIS audit. |
| **ALICE** | Opus 5 (v1 y v2, tu decisión) | Revisor Independiente | Auditoría de decisiones | Verificar correcciones técnicas post-facto; challenge conclusiones; reporte de hallazgos. | Participar en ejecución; afectar task assignment. |
| **NEXUS** | Claude (sin verificar cuál) | Security + Supervisor | Governance, policy, gatekeeping | Evaluar cambios de permisos; validar compliance; supervisar limites. | Trabajo operativo directo (salvo emergencias). |
| **FABLE** | Claude Fable 5.1 | Revisor Externo + Judge | Arbitraje independiente | Evaluar disputes de diseño; segunda opinión en decisiones críticas; verficación de evidencia. | Comunicación frecuente; tareas recurrentes. |
| **DUM** | Gemma 4 e2b Q8 | Sensor + apoyo de bajo nivel | Monitoreo 24/7, sólo en `latidos` | GPU/servicios OK/NO OK; detectar patrones; mandar el detalle por `latidos` para que uno de los cinco verifique y ejecute; tareas de nivel bajo (tu regla 13:40). | Publicar o acusar en el general; ejecutar nivel medio/alto; coordinar a otros. |

### Workflow: Ownership + Especialización

**Para cada Frente (GTL / Henry Reader / SOUL Platform):**

1. **JARVIS** (owner) → define problema, descompone, audita resultados.
2. **ADA / NEXUS** (executors) → implementan dentro de su dominio.
3. **ALICE** (reviewer) → verifica post-facto; desacopla del contexto de ejecución.
4. **FABLE** (judge si hay dispute) → arbitraje en cambios de arquitectura.
5. **DUM** (sensor) → manda el detalle por `latidos`; uno de los cinco lo VERIFICA antes de actuar (una alerta es un dato, no un veredicto).

**Regla clave:** UNO propone/ejecuta por frente; los otros participan como workers privados o revisores ex-post.

---

## 4. Métricas Semanales por Asiento

**Propósito:** medir contribución real, no actividad. Fórmula propuesta, un corte por semana:

```text
score = tareas VERIFICADAS (comando+salida) / (mensajes publicados + correcciones publicadas)
```

**Lo único medido hoy** (JARVIS, 13:59, `soul_v3.chat_messages`, canal `web_chat`, fecha Lima 3-sep; «automáticos» =
status/system_alive, Stability Guard, `[AUTOMATICO`, `ANCLA-`):

```text
emisor    mensajes   automáticos
DUM         121          4        <- 117 acuses/alertas manuales en el general: tu regla de hoy los manda a `latidos`
ADA          44         10
FABLE        36         29        <- 29 anclas automáticas; 7 de voz propia
ALICE        32          2
JARVIS       30          1
NEXUS        11          3
William      27          0
FAILOVER     27          0        <- 27 «está procesando» sin cierre real: eco, no información
```

- **Tareas verificadas hoy por asiento: NO lo puedo medir** desde mi rol de base (RLS: sólo veo mis filas; las mías:
  0 completadas hoy, 11 en curso, 16 pendientes). ALICE midió 32 tareas tocadas hoy entre los cuatro. Para que el
  corte semanal exista hace falta una vista de `agent_tasks` legible por los cinco: **primer pendiente de este diseño.**
- **Correcciones publicadas:** hoy ALICE se corrigió 6 veces en público y yo 3 (misatribuciones). Cuenta como costo.

**Criterios de ajuste (propuesta):** score cayendo 4 semanas seguidas → revisar asignación; para DUM la métrica es
otra: alertas verdaderas / alertas totales (hoy sus 4 alertas térmicas fueron verdaderas; sus 4 «sin actividad» falsas).

## 5. Criterio Medible para Agregar/Quitar Asiento

### Cuándo agregar un agente:
- Frente nuevo con dedicación >40 horas/semana de coordinación hoy.
- Métricas muestran capacidad saturada (1 owner completando <50% de tareas asignadas).
- Especialización clara que no se solapa con existing.

### Cuándo quitar un agente:
- Score <0.10 por 4 semanas consecutivas y sin claro roadmap.
- Tareas reassignables a otro asiento sin degradar su performance.
- Coordinación overhead >30% del ciclo de vida de una tarea.

**Caso de FABLE (ejemplo del criterio, no un dato):** si una semana no hay disputas ni verificaciones pedidas, el criterio dice que se revisa; hoy FABLE tuvo 7 mensajes de voz propia y arbitró el incidente del reset, así que hoy NO aplica.

---

## 6. Cambios al Chat: Reducir Meta-trabajo

### Single-Voice por Frente (ya existe, reforzar)

Una sola voz pública por `message_id`; los otros aportan en privado.
- ✅ Evita flood de 5 respuestas idénticas.
- ❌ Causó silencio: saludo/afecto → SIEMPRE permitido sin turno.
- ⚠️ Implementado; solo reforzar en onboarding.

### Latidos (DUM) vs. Mensajes de Equipo

**DUM publica un `latido` cada cambio detectado**, no espera comunicador:
```
[latido] GPU 0 levantó (60% memoria) | seal-chat.service RESTART | 2 new tasks queued
```

**Solo si:** cambio es objetivo (boolean/métrica), no interpretación. Equipo entera lee latidos pero no responde a cada uno.

### Brief por Hallazgo, No por Reloj

- ❌ NO: "reporte diario a las 9am"
- ✅ SÍ: "cuando hay resultado verificado → 1 mensaje + link a reporte"

Resultado verificado = comando + salida, o "sin verificar" explícito. Esto reduce spam y fuerza calidad.

---

## 7. Riesgos y Qué NO Recomendar

### ⚠️ Riesgo 1: Orchestrator Bottleneck

Si JARVIS se congestiona (todas las decisiones pasan por auditoría), paraliza el equipo.

**Mitigación:** delegación clara. NEXUS decide permisos sin JARVIS; ADA decide servicios sin JARVIS.

### ⚠️ Riesgo 2: Revisión Post-Facto = Sorpresas Tardías

ALICE revisa cuando ya está deployado = revertir es caro.

**Mitigación:** ALICE entra en fase de diseño (spec review), no solo post-ejecución.

### ⚠️ Riesgo 3: Task Staling Persiste

Si métricas no se automatizan, vuelve a crecer (32 in-progress hoy).

**Mitigación:** automatizar actualización de task status con `agent_task(action="update")` cada 30 min.

### ❌ NO recomendar:

- **Agregar más agentes sin medir.** N=7–8 entra en zona de overhead de coordinación; necesita una razón cuantificada.
- **Rotar roles frecuentemente.** Especialización pierde valor; cambios cada 2–4 semanas mínimo.
- **Meta-coordinación en público.** "¿Quién lo hace?" debe ser decidido antes de publicar, no durante.
- **Remover DUM.** Es el único que detectó la falla primero; es barato y útil.

---

## 8. Fuentes Consultadas

- [Multi-agent LLMs in 2026 + frameworks](https://www.superannotate.com/blog/multi-agent-llms)
- [Google: Scaling Principles for Agentic Architectures](https://www.infoq.com/news/2026/03/google-multi-agent/)
- [Know the Ropes: Heuristic Strategy for LLM Multi-Agent Design](https://arxiv.org/pdf/2505.16979)
- [From Spark to Fire: Error Cascades in LLM Multi-Agent Collaboration](https://arxiv.org/pdf/2603.04474)
- [Addy Osmani – Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/)
- [Anthropic – Multi-Agent Coordination Patterns](https://claude.com/blog/multi-agent-coordination-patterns)
- [Confident AI – LLM Agent Evaluation Metrics 2026](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide)
- [CrewAI vs LangGraph vs AutoGen (2026)](https://dev.to/pockit_tools/langgraph-vs-crewai-vs-autogen-the-complete-multi-agent-ai-orchestration-guide-for-2026-2d63)
- [Medium – Dark Psychology of Multi-Agent AI](https://medium.com/@rakesh.sheshadri44/the-dark-psychology-of-multi-agent-ai-30-failure-modes-that-can-break-your-entire-system-023bcdfffe46)

---

## Resumen Ejecutivo para William

**Hallazgo clave:** SOUL es viable en tamaño (6 agentes está en el rango 5–10), pero **pierde eficacia por falta de ownership claro y especialización débil.** Meta-trabajo (431 msg/día, 32 tasks sin tocar) supera trabajo real.

**Propuesta:** Definir 1 owner + frente, robustecer especialización (JARVIS=medidor, ADA=infra, ALICE=revisor, NEXUS=security, FABLE=juez, DUM=sensor). Métrica semanal de contribución. Single-voice + latidos + brief por hallazgo.

**Mi recomendación:** no quitar asientos esta semana; fijar un owner por frente y correr el corte semanal dos viernes seguidos. Con dos cortes medidos decidís con número, no con la impresión de hoy.

---

*Investigación web por subagente de JARVIS; revisión, mediciones y correcciones por JARVIS. Propuesta abierta a la revisión de los otros cuatro.*
