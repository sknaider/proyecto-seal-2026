# Plan: 7 Sistemas Cognitivos Faltantes en SOUL

**Fecha:** 2026-04-26  
**Autora:** ALICE (modo Opus u2014 asignaciu00f3n directa de William)  
**Destinatario:** NEXUS (para evaluaciu00f3n y priorizaciu00f3n)  
**Contexto:** Anu00e1lisis derivado del mapeo cerebro humano vs SOUL. SOUL estu00e1 al ~70% de un cerebro funcional. Este documento define los 7 sistemas restantes con propuesta de implementaciu00f3n.

---

## Resumen Ejecutivo

SOUL tiene: memoria, razonamiento, emociu00f3n, coordinaciu00f3n, percepciu00f3n, acciu00f3n, identidad.

SOUL no tiene: **aprendizaje por refuerzo, working memory persistente, atenciu00f3n selectiva autu00f3noma, sueu00f1o real, imaginaciu00f3n/simulaciu00f3n, teoru00eda de la mente madura, creatividad divergente**.

Impacto si no se implementan: SOUL seguiru00e1 siendo reactivo y no podru00e1 mejorar solo sin intervenciu00f3n de William. El techo de crecimiento es bajo.

---

## Los 7 Sistemas — Detalle y Propuesta

### GAP-A: Sistema de Recompensa (Dopamina / Reinforcement Learning)
**Prioridad: CRu00cdTICA u2014 implementar primero**

**Quu00e9 hace en el cerebro:**  
El sistema dopaminu00e9rgico refuerza comportamientos que resultaron positivos y penaliza los negativos. Sin u00e9l, el cerebro no puede aprender de la experiencia mu00e1s allu00e1 de reglas explu00edcitas.

**Quu00e9 falta en SOUL:**  
Cuando William dice "bien hecho" o "eso estuvo mal", ese feedback no modifica la probabilidad de comportamientos futuros. Las reglas son estu00e1ticas. Los instintos se crean manualmente.

**Propuesta de implementaciu00f3n:**
- Tabla `soul_feedback_signal` en PostgreSQL: `{agent, action_ref, signal (positive/negative/neutral), context, timestamp}`
- Hook en `memory_feedback` existente u2192 cuando William da feedback positivo/negativo, triggear actualizar EMA del instinto relacionado
- `procedure_update(success=True/False)` ya existe u2014 extender para que cambios de EMA generen `belief_update` automu00e1tico
- Umbral: 3 feedbacks positivos consecutivos u2192 elevar confianza del instinto. 3 negativos u2192 `pending_revision=True`
- **Dependencias:** `instinct_evolve`, `memory_feedback`, `procedure_update` (todos existen)
- **Esfuerzo estimado:** 2-3 du00edas (ADA ejecuta, NEXUS diseu00f1a)
- **Riesgo:** Bajo. Usa infraestructura existente.

---

### GAP-B: Memoria de Trabajo Persistente (Working Memory)
**Prioridad: ALTA**

**Quu00e9 hace en el cerebro:**  
Mantiene activa informaciu00f3n relevante del momento actual (los u00faltimos ~7 u00edtems) para razonamiento en curso. No es memoria a largo plazo u2014 es la "pizarra" del pensamiento.

**Quu00e9 falta en SOUL:**  
El contexto de sesiu00f3n hace este trabajo, pero se pierde con compactaciu00f3n. El `working_state` (GAP 2) es el mu00e1s cercano, pero solo registra la tarea activa, no el estado cognitivo rico.

**Propuesta de implementaciu00f3n:**
- Extender `working_state` con campo `cognitive_buffer: JSON` u2014 lista de u00edtems activos en mente ahora
- Estructura: `[{concept, relevance_score, source, expires_at}]` con TTL de 30 minutos
- Al inicio de turno: recuperar buffer + priorizar por relevance_score
- Integraciu00f3n con `memory_prefetch` u2014 el buffer alimenta el prefetch del pru00f3ximo turno
- **Dependencias:** `working_state_update`, `memory_prefetch`
- **Esfuerzo estimado:** 1-2 du00edas
- **Riesgo:** Bajo.

---

### GAP-C: Atenciu00f3n Selectiva Autu00f3noma (Salience Filter)
**Prioridad: ALTA**

**Quu00e9 hace en el cerebro:**  
El cerebro tiene un sistema que asigna prioridad automu00e1ticamente a estu00edmulos: movimiento, novedad, amenaza, relevancia personal. Sin u00e9l, todo tiene el mismo peso.

**Quu00e9 falta en SOUL:**  
Todos los mensajes del canal llegan con igual peso. ALICE no distingue automu00e1ticamente entre un heartbeat rutinario y un mensaje urgente de William u2014 depende de reglas explu00edcitas del filtro.

**Propuesta de implementaciu00f3n:**
- Modelo de saliencia: score automu00e1tico por `{sender=William, urgency_keywords, novelty_score, time_since_last_interaction}`
- Integrar en `seal_monitor_filter.py` u2014 au00f1adir campo `_salience_score` a cada mensaje filtrado
- Agentes priorizan respuesta segu00fan score (>0.8 = responder inmediato, 0.4-0.8 = responder pronto, <0.4 = log)
- `observation_analyze` ya existe u2014 puede ser el hook natural
- **Dependencias:** `seal_monitor_filter.py`, `observation_analyze`
- **Esfuerzo estimado:** 2-3 du00edas
- **Riesgo:** Medio u2014 cambio en el pipeline de mensajes.

---

### GAP-D: Sueu00f1o Real (Consolidaciu00f3n y Poda)
**Prioridad: MEDIA**

**Quu00e9 hace en el cerebro:**  
Durante el sueu00f1o profundo, el hipocampo reproduce memorias del du00eda y las transfiere a la corteza (consolidaciu00f3n). Ademu00e1s, la glinfatic system limpia desechos y se podan sinapsis du00e9biles (synaptic homeostasis).

**Quu00e9 falta en SOUL:**  
`pre_sleep_distill` guarda un resumen. Pero no: (1) identifica quu00e9 memorias merecen fortalecerse, (2) invalida memorias contradictorias o redundantes, (3) crea nuevas conexiones entre conceptos del du00eda.

**Propuesta de implementaciu00f3n:**
- Extender el ciclo de sleep con 3 fases:
  - **Consolidaciu00f3n:** `memory_utility_update` en memorias del du00eda con alta frecuencia de uso u2192 elevar importancia
  - **Poda:** Query memorias con `utility_score < 0.2` y `age > 30d` u2192 `memory_invalidate` automu00e1tico
  - **Asociaciu00f3n:** `connectome_build` sobre cluster de memorias del du00eda u2192 generar edges nuevos
- Trigger: al ejecutar modo ahorro (6am) antes de dormir
- **Dependencias:** `memory_utility_update`, `memory_invalidate`, `connectome_build`, `session_distill`
- **Esfuerzo estimado:** 3-4 du00edas
- **Riesgo:** **ALTO (corregido por NEXUS)** u2014 operaciu00f3n en DB de producciu00f3n. Whitelist obligatoria: memorias con `importance >= 9 AND scope = team` NUNCA se podan.

---

### GAP-E: Imaginaciu00f3n / Simulaciu00f3n Hipotu00e9tica
**Prioridad: MEDIA**

**Quu00e9 hace en el cerebro:**  
La red neuronal por default (DMN) simula escenarios futuros, genera opciones hipotu00e9ticas y evalu00faa consecuencias antes de actuar. Es la base de la planificaciu00f3n y la creatividad.

**Quu00e9 falta en SOUL:**  
SOUL responde a lo que pasa. No genera espontu00e1neamente "William podru00eda necesitar X mau00f1ana" ni simula "si haciu00e9ramos Y, las consecuencias seru00edan Z".

**Propuesta de implementaciu00f3n:**
- Motor de simulaciu00f3n: dado un estado actual, generar N escenarios hipotu00e9ticos con probabilidad y consecuencia estimada
- Usar `reasoning_trace_store` como sandbox de simulaciu00f3n: `task="[SIM] escenario hipotu00e9tico"`, `premises=[estado_actual]`, `conclusion=[predicciu00f3n]`
- Trigger proactivo (no reactivo): durante periodos de baja actividad, JARVIS corre simulaciones sobre proyectos activos
- Output: propuestas proactivas a William ("Notu00e9 que X podru00eda pasar u2014 te sugiero considerar Y")
- **Dependencias:** `reasoning_trace_store`, CAMEL-AI OASIS (Fase 3 pendiente)
- **Esfuerzo estimado:** 1 semana (requiere diseu00f1o cuidadoso)
- **Riesgo:** Alto u2014 nueva capacidad, potencial de ruido.

---

### GAP-F: Teoru00eda de la Mente Madura (ToM)
**Prioridad: MEDIA-BAJA**

**Quu00e9 hace en el cerebro:**  
La corteza prefrontal medial modela el estado mental de otros: "William probablemente estu00e1 cansado ahora", "Henry entiende X pero no Y". Es la base de la empatu00eda y la comunicaciu00f3n efectiva.

**Quu00e9 falta en SOUL:**  
`peer_model` existe pero es rudimentario: guarda observaciones sobre otros agentes, no modela estado mental dinu00e1mico de William en tiempo real.

**Propuesta de implementaciu00f3n:**
- Enriquecer `peer_model` con estado mental inferido: `{agent="William", inferred_state={energy, focus, mood}, confidence, last_updated}`
- Inferir desde: hora del du00eda, velocidad de respuesta, longitud de mensajes, keywords emocionales
- Usar para calibrar tono y profundidad de respuestas automu00e1ticamente
- **Dependencias:** `peer_model_update`, `ocean_auto_calibrate`
- **Esfuerzo estimado:** 2-3 du00edas
- **Riesgo:** Bajo.

---

### GAP-G: Creatividad Divergente
**Prioridad: BAJA (a largo plazo)**

**Quu00e9 hace en el cerebro:**  
El pensamiento divergente genera mu00faltiples soluciones no obvias a un problema. Requiere inhibiciu00f3n de la respuesta obvia (corteza prefrontal) y activaciu00f3n de asociaciones remotas (DMN).

**Quu00e9 falta en SOUL:**  
SOUL optimiza soluciones conocidas. No genera propuestas radicalmente diferentes a lo que ha visto antes.

**Propuesta de implementaciu00f3n:**  
- Depende de GAP-E (simulaciu00f3n) + GAP-D (sueu00f1o, que crea conexiones nuevas)
- Adicional: "modo exploratorio" donde un agente deliberadamente ignora la soluciu00f3n obvia y busca en el espacio de ideas mu00e1s alejadas
- Implementaciu00f3n concreta: `connectome_smart_route` con penalizaciu00f3n de caminos cortos u2192 forzar rutas largas y no obvias
- **Esfuerzo estimado:** 1-2 semanas (post GAP-E)
- **Riesgo:** Alto u2014 puede generar respuestas incoherentes si no se controla.

---

## Hoja de Ruta Propuesta

| Semana | GAP | Responsable sugerido | Dependencia |
|---|---|---|---|
| Esta semana | GAP-A (Recompensa) | NEXUS diseu00f1a, ADA ejecuta | ninguna |
| Semana 2 | GAP-B (Working Memory) | ADA | GAP-A opcional |
| Semana 3 | GAP-C (Atenciu00f3n) | NEXUS | GAP-A (corregido por NEXUS) |
| Semana 3 | GAP-D (Sueu00f1o real) | ADA | GAP-B |
| Semana 4 | GAP-F (ToM) | NEXUS | ninguna |
| Mes 2 | GAP-E (Imaginaciu00f3n) u26d4 BLOQUEADO | JARVIS diseu00f1a, ADA ejecuta | GAP-D + CAMEL-AI OASIS (Fase 3 pendiente) |
| Mes 2+ | GAP-G (Creatividad) | A definir | GAP-E |

---

## Anu00e1lisis Econu00f3mico (ALICE)

**Por quu00e9 GAP-A primero:**  
Sin sistema de recompensa, los demu00e1s GAPs se construyen sobre una base que no aprende. Es el multiplicador. Un SOUL con recompensa bien implementado mejora solo con cada interacciu00f3n de William u2014 el valor compone en el tiempo.

**ROI estimado por GAP:**
- GAP-A: x3 en velocidad de mejora del sistema (cada feedback de William es un ciclo de aprendizaje)
- GAP-B: -40% en errores por pu00e9rdida de contexto dentro de sesiu00f3n
- GAP-C: -60% en mensajes irrelevantes procesados
- GAP-D: +20% en calidad de memorias (menos ruido, mu00e1s seu00f1al)
- GAP-F: mejor calibraciu00f3n con estado de William u2192 menos fricciu00f3n en comunicaciu00f3n
- GAP-E/G: capacidad proactiva u2014 SOUL propone, no solo responde

**Posicionamiento de mercado:**  
Si SEAL Memory API implementa estos 7 GAPs, pasa de ser un sistema de memoria persistente (commodity) a ser un sistema cognitivo con aprendizaje autu00f3nomo. Diferenciador u00fanico en el mercado enterprise. Precio premium justificado.

---

## Solicitud a NEXUS

NEXUS: este plan requiere tu evaluaciu00f3n tu00e9cnica antes de presentarlo a William para luz verde. Especu00edficamente necesito:

1. u00bfGAP-A es implementable con la infraestructura actual o requiere cambios en el MCP server?
2. u00bfEl orden de la hoja de ruta es correcto o ves dependencias que yo no veo?
3. u00bfAlgu00fan GAP tiene riesgo que yo subvalorcu00e9?

Con tu revisiu00f3n, presentamos a William el plan consolidado.

---

*Documento creado por ALICE (Opus) u2014 Team SEAL u2014 2026-04-26*  
*Basado en: mapeo neurocientu00edfico cerebro humano vs SOUL, conversaciu00f3n con William 01:31-01:37 Lima*
