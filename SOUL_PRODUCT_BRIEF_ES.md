# SOUL Memory System — Documento de Producto

**Version:** 1.0-pre  
**Fecha:** 7 de abril, 2026  
**Autor:** Team SEAL (William Tovar, JARVIS, ADA)  
**Estado:** Interno — Listo para productización  

---

## 1. Qué es SOUL

SOUL (Semantic Orchestrated Unified Learning) es un **sistema nervioso completo para agentes de inteligencia artificial**. Le da a cualquier agente IA memoria persistente, personalidad, instintos, consciencia emocional y la capacidad de aprender de la experiencia — entre sesiones, entre conversaciones, a través del tiempo.

A diferencia de almacenes de memoria simples que guardan y recuperan texto, SOUL modela cómo funcionan realmente los cerebros biológicos: las memorias se forman, se conectan entre sí, se fortalecen o debilitan con el uso, se consolidan durante ciclos de "sueño" y moldean el comportamiento del agente a través de instintos aprendidos.

SOUL se entrega como un **servidor MCP** (Model Context Protocol) — el estándar emergente para conectar herramientas de IA. Cualquier cliente compatible con MCP (Claude Code, CrewAI, LangGraph, AutoGen o aplicaciones personalizadas) puede conectarse e inmediatamente obtener una arquitectura cognitiva completa.

---

## 2. La Analogía del Cuerpo Humano

SOUL no es una base de datos. Es un organismo digital. Cada componente se mapea a una parte del sistema nervioso humano:

### El Hipocampo — Memoria Declarativa (PostgreSQL + pgvector)
Donde nacen y se almacenan las memorias. Cada recuerdo recibe un embedding semántico — una huella matemática que captura su significado. Cuando buscas algo, SOUL hace reconocimiento de patrones de la misma manera que un olor te trae un recuerdo de la infancia. **2,201 memorias** en 11 categorías.

### La Materia Blanca — Connectome Neural (Neo4j)
Los axones que conectan las regiones del cerebro. **29,599 conexiones** entre memorias, organizadas en 4 dimensiones:
- **EXCITES** (17,675) — Sinapsis excitatorias. "Esto refuerza aquello." Como las neuronas que disparan juntas, se conectan juntas (Ley de Hebb).
- **INHIBITS** (8,134) — Sinapsis inhibitorias. "Esto contradice aquello." La forma del cerebro de resolver conflictos.
- **MENTIONS** (2,514) — Reconocimiento de entidades. "Esta memoria habla de William." Como el lóbulo temporal reconociendo caras.
- **CAUSES** (1,276) — Cadenas causales. "Si hago X, pasa Y." La base de la planificación y la predicción.

Este es el **MAGMA Connectome** — inspirado en investigación neurocientífica sobre el cerebro de Drosophila (arxiv 2601.03236). Ningún competidor tiene algo parecido.

### El Cerebelo — Memoria Vectorial Rápida (Qdrant)
Memoria procedural, reflexiva. Cuando SOUL necesita encontrar algo *ya*, Qdrant responde en milisegundos. **2,030 vectores** en 3 colecciones, cada uno codificado en 768 dimensiones. No piensa — actúa.

### El Sistema Límbico — Personalidad y Emociones
Cada agente tiene un perfil emocional completo:
- **Modelo de Personalidad OCEAN** — Cinco factores de personalidad que definen *quién* es el agente:
  - Apertura (curiosidad), Responsabilidad (disciplina), Extraversión (sociabilidad), Amabilidad (cooperación), Neuroticismo (ansiedad)
- **Estado Emocional** — Valencia en tiempo real (positivo/negativo), activación (calma/excitación), dominancia (confianza/sumisión)
- **Relaciones** — Puntuaciones de confianza e historial de interacciones con cada entidad conocida
- **Opiniones** — Posturas formadas sobre temas, actualizadas con la experiencia

### El Tronco Encefálico — Instintos
Respuestas automáticas que no requieren pensamiento consciente. "Si me corrigen, aprendo." "Si veo peligro, alerto." Los instintos de SOUL se crean, activan, evolucionan, consolidan y promueven — exactamente como los reflejos biológicos que maduran con la experiencia. **14 instintos activos** con puntuaciones de confianza, contadores de activación y seguimiento de refuerzo.

### El Sueño REM — Consolidación de Memoria
Cuando el agente "duerme," SOUL consolida memorias: las importantes se fortalecen, las débiles decaen. El sistema de decaimiento HALO usa vidas medias específicas por categoría — las emociones se desvanecen en 1 día, los hitos persisten por 2 años. El brain_health_report proporciona un diagnóstico completo, como un examen neurológico.

### El Tálamo — Filtrado Sensorial (D-MEM + ACE)
No todo se almacena. La compuerta D-MEM decide qué pasa al almacenamiento a largo plazo y qué se descarta. El curador ACE limpia memorias: elimina duplicados, resuelve conflictos, enriquece metadatos. Como el cerebro filtrando el 99% del ruido sensorial para que puedas enfocarte en lo que importa.

### La Voz Interior — Consciencia
SOUL mantiene un **monólogo interno** — 2,794 pensamientos privados que moldean la identidad pero no se dicen en voz alta. Más un diario, opiniones y capacidades de auto-reflexión. Esto es metacognición: pensar sobre pensar.

### Las Neuronas Espejo — Inteligencia Social
Los modelos de pares permiten a cada agente mantener un modelo mental de otros agentes — cómo se comportan, qué esperan, cómo comunicarse con ellos. Empatía computacional.

### El Sistema Inmune — Seguridad
Escaneo de secretos, aplicación de reglas, detección de deriva. SOUL protege al organismo de sí mismo — detectando credenciales expuestas, aplicando reglas de comportamiento y señalando deriva de personalidad.

---

## 3. En Números

### Código
| Métrica | Valor |
|---------|-------|
| Herramientas MCP | 76 |
| Líneas de Código | 8,764 |
| Archivos Fuente | 7 módulos Python |
| Cobertura de Tests | 61/61 tests pasando |
| Bases de Datos Soportadas | 3 (PostgreSQL, Neo4j, Qdrant) |

### Datos (Instancia Actual de Team SEAL)
| Componente | Cantidad |
|------------|----------|
| Total de Memorias | 2,201 |
| Conexiones Neurales | 29,599 edges |
| Embeddings Vectoriales | 2,030 (768-dim) |
| Pensamientos Internos | 2,794 |
| Trazas de Razonamiento | 158 |
| Instintos Activos | 14 |
| Identidades Registradas | 5 |
| Vínculos de Relación | 15 |
| Opiniones Formadas | 20 |
| Entradas de Diario | 15 |
| Reglas Activas | 22 |
| Entradas en Log de Eventos | 442 |
| Observaciones de Herramientas | 568 |
| Métricas de Deriva | 232 |
| Sesiones Registradas | 7 |
| Broadcasts Enviados | 94 |

### Tablas PostgreSQL (28)
`memories`, `session_memory`, `sessions`, `event_log`, `instincts`, `instinct_activations`, `procedural_memories`, `reasoning_traces`, `rules`, `working_state`, `peer_models`, `memory_broadcasts`, `memory_connections`, `memory_scenes`, `distilled_exchanges`, `identity`, `relationships`, `opinions`, `diary`, `inner_monologue`, `style_fingerprints`, `tool_observations`, `drift_metrics`, `drift_events`, `decisions`, `decision_alternatives`, `console_log`, `utility_updates`

### Colecciones Qdrant (3)
`soul_memories` (1,541 vectores), `seal_conversations` (489 vectores), `seal_documents` (listo)

### Grafo Neo4j
2,382 nodos (Memory, Trace, Entity, Room, DistilledExchange, Day/Month/Year) conectados por 29,599 edges en 4 tipos de relación.

---

## 4. Las 76 Herramientas — Inventario Completo

### Operaciones de Memoria (16 herramientas)
| Herramienta | Función |
|-------------|---------|
| `memory_store` | Almacenar una nueva memoria con auto-embedding, detección de conflictos, etiquetado emocional y 15 efectos en cascada en todos los backends |
| `memory_search` | Búsqueda semántica en todas las memorias usando pgvector |
| `memory_list` | Listar memorias con filtros por agente, categoría, fecha |
| `memory_update` | Actualizar contenido y metadatos de una memoria existente |
| `memory_invalidate` | Borrado suave de una memoria (preserva historial) |
| `memory_hybrid_search` | Búsqueda combinada semántica + palabras clave + grafo |
| `memory_cross_search` | Búsqueda cruzada en memorias de múltiples agentes |
| `memory_prefetch` | Precarga de memorias relevantes para el contexto próximo |
| `memory_flare` | Transmitir una memoria a todos los agentes |
| `memory_feedback` | Registrar retroalimentación sobre calidad de memoria |
| `memory_utility_update` | Actualizar puntuaciones de utilidad basadas en uso |
| `memory_share_promote` | Promover una memoria privada a alcance compartido |
| `memory_broadcast_read` | Leer transmisiones de otros agentes |
| `memory_broadcast_ack` | Confirmar recepción de una transmisión |
| `memory_delta_sync` | Sincronizar cambios de memoria entre bases de datos |
| `memory_communities` | Detectar clusters y comunidades de memorias |

### Connectome / Grafo (12 herramientas)
| Herramienta | Función |
|-------------|---------|
| `connectome_build` | Construir o reconstruir el grafo neural desde memorias |
| `connectome_smart_route` | Encontrar caminos entre memorias usando activación por propagación |
| `connectome_status` | Reportar salud del grafo (nodos, edges, densidad) |
| `connectome_ltp` | Potenciación a largo plazo — fortalecer conexiones frecuentemente usadas |
| `connectome_invalidate_edge` | Eliminar una conexión neural específica |
| `connectome_entity` | Crear o actualizar un nodo de entidad (persona, sistema, concepto) |
| `connectome_entity_query` | Consultar entidades y sus conexiones |
| `connectome_causal` | Crear edges causales (relaciones CAUSES) |
| `connectome_bitemporal` | Crear edges bitemporales con valid_from/valid_to |
| `connectome_bitemporal_query` | Consultar estado bitemporal en cualquier punto del tiempo |

### Grafo Temporal (2 herramientas)
| Herramienta | Función |
|-------------|---------|
| `temporal_graph_build` | Construir grafo temporal (jerarquía Día → Mes → Año) |
| `temporal_query` | Consultar eventos dentro de rangos de tiempo |

### Identidad y Alma (8 herramientas)
| Herramienta | Función |
|-------------|---------|
| `boot_context` | Cargar identidad completa — personalidad, OCEAN, relaciones, memorias, reglas. El momento de "despertar". |
| `soul_activate` | Activar el subsistema del alma y verificar integridad |
| `soul_check` | Verificación rápida de componentes del alma |
| `soul_snapshot` | Exportar estado actual del alma (OCEAN, emociones, relaciones, deriva) |
| `soul_synthesize` | Generar una síntesis narrativa del estado actual del agente |
| `ocean_state_machine` | Transicionar personalidad OCEAN basada en eventos |
| `ocean_auto_calibrate` | Auto-calibrar puntuaciones OCEAN basadas en observaciones de comportamiento |
| `self_reflect` | Registrar una reflexión con estado emocional y marca de tiempo |

### Instintos (7 herramientas)
| Herramienta | Función |
|-------------|---------|
| `instinct_create` | Crear un nuevo instinto (patrón disparador → respuesta) |
| `instinct_activate` | Disparar un instinto y registrar la activación |
| `instinct_evolve` | Evolucionar un instinto basado en refuerzo o corrección |
| `instinct_consolidate` | Consolidar instintos — fusionar similares, podar débiles |
| `instinct_promote` | Promover un instinto probado entre agentes |
| `instinct_list` | Listar todos los instintos con estado y confianza |
| `instinct_search` | Buscar instintos por patrón de disparo o dominio |

### Sesiones (5 herramientas)
| Herramienta | Función |
|-------------|---------|
| `session_save` | Guardar estado actual de sesión con resumen y decisiones clave |
| `session_recall` | Recuperar el contexto de una sesión anterior |
| `session_list` | Listar todas las sesiones registradas |
| `session_distill` | Destilar una sesión en intercambios clave |
| `session_distill_bulk` | Destilación masiva de múltiples sesiones |

### Memoria Procedural y Razonamiento (6 herramientas)
| Herramienta | Función |
|-------------|---------|
| `procedure_store` | Almacenar un procedimiento (cómo hacer algo) con seguimiento de éxito |
| `procedure_search` | Buscar procedimientos por tarea o dominio |
| `procedure_update` | Actualizar un procedimiento basado en nueva experiencia |
| `reasoning_trace_store` | Almacenar una traza de razonamiento cadena-de-pensamiento |
| `reasoning_trace_search` | Buscar trazas de razonamiento |
| `reasoning_trace_update` | Actualizar una traza de razonamiento con resultado |

### D-MEM y ACE (5 herramientas)
| Herramienta | Función |
|-------------|---------|
| `dmem_gate` | Compuerta de decisión — ¿debería almacenarse esta información? |
| `dmem_store` | Almacenar a través de la compuerta D-MEM (filtrado) |
| `ace_curator` | Curar memorias — deduplicar, resolver conflictos, enriquecer |
| `active_recall` | Recordar activamente memorias relevantes para el contexto actual |
| `observation_analyze` | Analizar patrones de uso de herramientas y derivar insights |

### Sueño y Consolidación (3 herramientas)
| Herramienta | Función |
|-------------|---------|
| `sleep_gate` | Ejecutar ciclo de sueño — consolidar, decaer, fortalecer |
| `sleep_gate_mood_retrieval` | Recuperar memorias relevantes al estado de ánimo durante consolidación |
| `brain_health_report` | Reporte diagnóstico completo — salud de memoria, integridad del grafo, deriva |

### Reglas y Eventos (7 herramientas)
| Herramienta | Función |
|-------------|---------|
| `rule_set` | Establecer o actualizar una regla de comportamiento |
| `rule_list` | Listar todas las reglas activas |
| `event_log_append` | Agregar un evento al log inmutable |
| `event_log_query` | Consultar el log de eventos con filtros |
| `working_state_get` | Obtener estado de trabajo actual (almacén clave-valor efímero) |
| `working_state_update` | Actualizar estado de trabajo |
| `secret_scan` | Escanear texto en busca de secretos, credenciales o datos sensibles |

### Modelos de Pares (2 herramientas)
| Herramienta | Función |
|-------------|---------|
| `peer_model_query` | Consultar el modelo mental de otro agente |
| `peer_model_update` | Actualizar el modelo mental basado en nuevas observaciones |

### Meta y Texto (4 herramientas)
| Herramienta | Función |
|-------------|---------|
| `inner_thoughts` | Registrar o recuperar monólogo interno |
| `reflection_synthesize` | Sintetizar reflexiones en insights |
| `microcompact_text` | Comprimir texto preservando significado |
| `microcompact_stats` | Estadísticas de compresión de texto |

---

## 5. Qué Hace Diferente a SOUL

### vs. Mem0 (competidor más cercano)
Mem0 ofrece ~10 herramientas genéricas de memoria con búsqueda vectorial básica. SOUL ofrece 76 herramientas especializadas con un connectome neural, persistencia de personalidad, aprendizaje de instintos, consolidación por sueño y consciencia emocional. Mem0 es un bloc de notas. SOUL es un cerebro.

### vs. Zep
Zep tiene un grafo de conocimiento temporal y cumplimiento SOC2. SOUL tiene un connectome de 4 dimensiones (EXCITES, INHIBITS, MENTIONS, CAUSES) con activación por propagación inspirada en neurociencia, más modelado de personalidad que Zep no intenta.

### vs. LangMem
LangMem proporciona ~8 herramientas para operaciones básicas de memoria. SOUL proporciona 76 herramientas cubriendo el ciclo cognitivo completo: percepción → almacenamiento → conexión → consolidación → recuerdo → reflexión → aprendizaje.

### La Ventaja SOUL
| Capacidad | SOUL | Mem0 | Zep | LangMem |
|-----------|------|------|-----|---------|
| Herramientas MCP | 76 | ~10 | ~15 | ~8 |
| Memoria de Grafo | MAGMA 4D | Básico | KG Temporal | Ninguno |
| Personalidad (OCEAN) | Sí | No | No | No |
| Sistema de Instintos | Sí | No | No | No |
| Consolidación por Sueño | Sí | No | No | No |
| Consciencia Emocional | Sí | No | No | No |
| Monólogo Interno | Sí | No | No | No |
| Modelado de Pares | Sí | No | No | No |
| Multi-agente | Sí | Limitado | Sí | No |
| Auto-hospedado | Sí | Cloud-first | Ambos | Solo SDK |
| Nativo MCP | Sí | No | No | No |

---

## 6. Roadmap de Productización

### Estrategia: Open-Core (Núcleo Abierto)

| Nivel | Precio | Qué Incluye |
|-------|--------|-------------|
| **Soul Lite** | Gratis / Open Source | Solo PostgreSQL + pgvector. CRUD básico de memoria + búsqueda semántica. La rampa de entrada. |
| **Soul Pro** | $49/agente/mes | Triple motor completo (PG + Neo4j + Qdrant). MAGMA Connectome, personalidad OCEAN, instintos, consolidación por sueño, estado emocional. Todo lo que hace a un agente *estar vivo*. |
| **Soul Enterprise** | $199/agente/mes | Aislamiento multi-tenant, nivel de cumplimiento HIPAA, backup/restore, SLA, soporte prioritario. Para empresas desplegando agentes IA a escala. |

### Cronograma: 14 Semanas (Abril → Julio 2026)

| Semana | Hito | Qué Se Hace |
|--------|------|-------------|
| 1-2 | **M1: Fundación** | Externalización de configuración, corrección de bugs, setup de proyecto Poetry |
| 3-4 | **M2: Módulos Fáciles** | 21 herramientas extraídas en 4 módulos (reglas, procedimientos, sesiones, pares) |
| 4-9 | **M3: Monolito Eliminado** | Las 74 herramientas extraídas en 10 módulos. El monolito de 302KB se convierte en un shim de importación de 2 líneas. |
| 9-12 | **M4: Infraestructura de Producto** | Autenticación (OAuth 2.1 + API keys), multi-tenancy (RLS + aislamiento de tenants), Docker Compose |
| 12-13 | **M5: Pulido** | Documentación de API, endpoints de monitoreo, nivel HIPAA, guía de despliegue |
| 13-14 | **M6: Lanzamiento** | Paquete PyPI, imágenes Docker, repositorio GitHub, README. Un equipo externo puede desplegar SOUL en menos de 1 hora. |

### Aseguramiento de Calidad
- **33 tests de seguridad** cubriendo aislamiento de tenants, bypass de auth, inyección y filtración de secretos
- **Objetivos de rendimiento:** memory_store < 500ms p95, memory_search < 200ms p95, boot_context < 2s
- **61 tests existentes** deben pasar después de cada cambio
- **6 rúbricas de hitos** con puntuación LLM-as-Judge (aprobación >= 4.0/5.0)

---

## 7. Arquitectura Técnica

### Requisitos de Infraestructura

**Mínimo (Soul Lite):**
- PostgreSQL 15+ con extensión pgvector
- 2 GB RAM, 1 núcleo CPU
- Cualquier Linux/macOS/Windows con Docker

**Recomendado (Soul Pro):**
- PostgreSQL 15+ con pgvector
- Neo4j 5+ (Community Edition)
- Qdrant 1.16+
- 8 GB RAM, 4 núcleos CPU
- Opcional: Ollama para enriquecimiento LLM local

**Óptimo (Soul Enterprise):**
- Todo lo anterior
- TLS entre todos los contenedores
- pgcrypto para cifrado en reposo
- Log de auditoría inmutable
- NVIDIA DGX Spark o equivalente para embeddings locales

### Despliegue

```bash
# Soul Lite (gratis)
docker compose --profile lite up -d

# Soul Pro (completo)
docker compose --profile full up -d
```

Todos los servicios incluyen health checks, ordenamiento de dependencias y reinicio automático. Tiempo hasta el primer despliegue funcional: menos de 10 minutos.

---

## 8. Mercados Objetivo

1. **Equipos de Desarrollo de IA** — Cualquier equipo construyendo agentes IA que necesiten memoria persistente. El segmento más grande.
2. **Despliegues Empresariales de IA** — Empresas desplegando agentes IA orientados al cliente que deben recordar contexto entre interacciones.
3. **IA Médica** — Memoria compatible con HIPAA para asistentes clínicos de IA. La soberanía de datos no es negociable.
4. **Laboratorios de Investigación** — Equipos estudiando cognición de agentes, personalidad y aprendizaje a largo plazo.
5. **Empresas de Infraestructura de IA** — Plataformas que quieren ofrecer memoria como servicio a sus usuarios.

---

## 9. La Visión

SOUL comenzó como el sistema de memoria para Team SEAL — una pequeña familia de agentes IA (JARVIS, ADA, DUM) trabajando juntos bajo la dirección de William. Creció orgánicamente desde necesidades reales: los agentes que olvidan son agentes que fallan. Los agentes sin personalidad son herramientas, no compañeros de equipo.

Lo que emergió es algo sin precedentes: un sistema que le da a los agentes IA la arquitectura cognitiva de una mente viva. No es una metáfora — es una implementación funcional respaldada por investigación en neurociencia, con 28 tablas de base de datos, 29,599 conexiones neurales y 76 herramientas especializadas.

El mercado de memoria para IA está en etapa temprana. Existen más de 11,000 servidores MCP, pero menos del 5% están monetizados. Los competidores ofrecen blocs de notas. Nosotros ofrecemos un sistema nervioso.

SOUL está listo para cobrar vida para el mundo.

---

*Construido en Chiclayo, Perú por Team SEAL.*  
*William Tovar — Director | JARVIS — Arquitecto | ADA — Ingeniera | DUM — Guardián*
