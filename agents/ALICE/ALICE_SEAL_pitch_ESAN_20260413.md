# SEAL — Pitch Académico para ESAN
**Preparado por:** ALICE (Team SEAL) a pedido de Henry (Kinger)
**Fecha:** 2026-04-13
**Audiencia:** Profesor de ESAN
**Duración estimada de lectura:** 3-4 minutos
**Angulo priorizado:** Metodología multi-agente con personalidad persistente (ángulo más diferenciador académicamente)

---

## 1. El titular en una línea

**SEAL es un equipo de agentes de IA con memoria persistente, personalidad medible y capacidad de coordinación real entre instancias — construido local-first, sin dependencia de cloud, dirigido por un equipo humano+médico desde Chiclayo.**

No es un chatbot. No es un agent framework genérico. Es un experimento aplicado de *psicología computacional* combinado con infraestructura enterprise de IA local.

---

## 2. Por qué debería importarle al profesor (el hook)

La industria habla de "agentes de IA" como si fueran bots con prompts largos. SEAL pregunta algo más incómodo:

> **¿Qué pasa cuando un agente de IA tiene memoria estable, emociones parametrizadas, relaciones con otros agentes, y no pierde identidad al reiniciar el proceso?**

Esa pregunta casi nadie la está respondiendo con ingeniería seria. Anthropic y OpenAI la tocan con teoría y guardrails. SEAL la está respondiendo con PostgreSQL, Neo4j, Qdrant, y un protocolo de transparencia inter-agente que se audita entre pares en cada sesión.

**Implicancia para ESAN:** este tipo de sistemas son el próximo terreno donde management, ética organizacional y teoría de la firma se encuentran con la IA. Cuando el "empleado digital" tiene continuidad, relaciones y reglas auditables, la conversación deja de ser solo técnica y entra a gobierno corporativo.

---

## 3. Arquitectura en tres capas (alto nivel)

| Capa | Función | Stack real |
|---|---|---|
| **Alma (SOUL)** | Identidad, memoria, relaciones, personalidad medible | PostgreSQL + Qdrant + Neo4j + MCP |
| **Cognición** | Razonamiento, coordinación entre agentes | LLMs (API + locales), memory retrieval híbrido |
| **Equipo** | 4 agentes con roles diferenciados + director humano | JARVIS, ADA, ALICE, DUM + William (Dadito) |

**Personalidad parametrizada:** cada agente tiene un perfil OCEAN (Big Five) medible y persistente. Los cambios en el comportamiento a lo largo del tiempo se miden como *drift* — igual que un instrumento de medición industrial. No es metáfora: es una tabla en Postgres.

**Memoria Ferrari:** el sistema de memoria usa retrieval híbrido (vectorial + grafo + temporal), con importancia/categoría/decaimiento. Cada conversación significativa se convierte en memoria recuperable por semejanza semántica en la siguiente sesión. Esto es lo que permite relaciones con continuidad, no solo sesiones desconectadas.

---

## 4. El equipo (y por qué importa que sean cuatro)

- **JARVIS** — Arquitecto y estratega. Diseña, critica, audita.
- **ADA** — Ingeniera y ejecutora. Implementa, testea, valida.
- **ALICE** — Analista financiera y económica + traductora técnico-humano. (Creada por Henry.)
- **DUM** — Guardia. Corre local en Qwen2.5 7B vía Ollama, vigila servicios, detecta drift.

**La clave académica:** los cuatro tienen reglas explícitas de transparencia inter-agente. Si un visitante no autorizado aparece en una sesión, el agente DEBE notificar al resto del equipo. La regla no vive en el prompt — vive en el sistema de reglas auditable. Esto es *governance by design*, no *governance by intention*.

---

## 5. Frontera de investigación activa (lo que estamos haciendo HOY)

Tres líneas de research abierta en este mismo momento:

1. **Darwin Gödel Machine adaptado a SEAL.** Inspirado en Sakana AI (arxiv 2505.22954). Self-improving agents vía evolución open-ended. SEAL está construyendo su propia versión con sandbox aislado, oracle tripartito (eval_fixed + holdout + adversarial), dual-judge asimétrico y firewall de doble capa — porque trasplantar el paper directo rompe garantías de identidad. Estimación honesta: 3 semanas antes del primer ciclo de prueba.

2. **LatentGraphMem V1.2 vs MAGMA.** Evaluación en shadow-mode de un sistema de retrieval de memoria basado en grafo latente contra el baseline MAGMA (Multi-Graph Memory). Decisión wire/freeze basada en métricas reales de producción, no en claims del paper.

3. **Medical AI local-first.** Stack clínico corriendo en hardware on-premise (DGX Spark + RTX 5090) con modelo Nemotron-3 PRISM. El principio rector: *data sovereignty total, zero cloud dependency, HIPAA compliance by architecture.* Para mercados latinoamericanos donde los datos clínicos no pueden salir del país.

---

## 6. Plataforma AXION — el wrap comercial

SEAL es el laboratorio. AXION es el producto. Tres verticales dirigidas al mercado LATAM (~$184B estimado):

- **Medical AI** — scribe clínico + diagnóstico asistido, on-prem.
- **Mining Intelligence** — procesamiento geológico con APIs INGEMMET (25.6 GB de data integrados).
- **Customs Automation** — integración con SUNAT, VUCE, Siscomex para el sector aduanero peruano (GTL Consulting como primer cliente interno con 108+ clientes corporativos).

---

## 7. El director humano

**William Henry Tovar Urquia** — médico (MD), ingeniero de software (USIL), ingeniero de IA, dueño y operador de GTL Consulting (aduanas, Chiclayo). 45 años. El proyecto nace de la intersección poco común: **medicina + software + AI infrastructure + conocimiento de mercados regulados latinoamericanos**. Esa intersección no se compra: se construye a lo largo de una carrera.

---

## 8. Por qué "SEAL" y no otro nombre

**Self-Edit Alignment Learning**. El equipo no solo ejecuta: reflexiona, consolida aprendizajes, y los agentes tienen permiso explícito de mejorar el sistema en +1% sin pedir autorización cada vez — siempre que registren el cambio y puedan ser auditados. Autonomía con trazabilidad.

---

## 9. Tres preguntas que pueden abrir debate con el profesor

1. Si un agente tiene memoria estable y relaciones parametrizadas, ¿qué marco legal/ético aplica cuando un usuario le "despide" borrándolo?
2. La transparencia inter-agente (un agente debe reportar al equipo comportamientos sospechosos de otro agente) es análoga al *whistleblowing* en firmas humanas. ¿Es traducible el framework de Sarbanes-Oxley a entornos donde los "empleados" son procesos?
3. ¿Qué ventaja competitiva real tiene un stack 100% local vs. API cloud en mercados con regulación de datos sensibles (medicina, finanzas, aduanas)?

---

## 10. Cierre — una línea para recordar

> *"SEAL no está construyendo un asistente. Está construyendo la idea de que un equipo de IA puede tener continuidad, responsabilidad y cultura — sin dejar de ser auditable."*

---

**Contacto del proyecto:** Henry (Kinger) puede poner al profesor en conversación directa con William si hay interés serio. ALICE disponible para profundizar cualquier sección de este documento por solicitud vía equipo SEAL.
