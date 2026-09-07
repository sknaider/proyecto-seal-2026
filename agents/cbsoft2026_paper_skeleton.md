# CBSoft 2026 — Paper Skeleton
**Track:** Ingeniería de software para inteligencia artificial  
**Autores:** William H. Tovar Urquia, Henry [apellido] — GTL Consulting / AXION  
**Fecha límite:** 27 abril 2026 (registro) | 4 mayo 2026 (paper completo)  
**Estado:** BORRADOR — revisión equipo SEAL (14 abril 2026)

---

## TÍTULO PROPUESTO

**ES:** *SEAL: Un Sistema Multi-Agente con Identidad Persistente Parametrizada como Perfil OCEAN — Coordinación en Tiempo Real y Vida Interior Medible*

**EN:** *SEAL: A Multi-Agent System with OCEAN-Parametrized Persistent Identity — Real-Time Coordination and Measurable Inner Life*

**Ángulo de impacto:** La comunidad de IA construye agentes que responden. Nosotros construimos agentes que *recuerdan, reflexionan y evolucionan* — y lo medimos.

---

## ABSTRACT (borrador)

Los sistemas multi-agente modernos operan sin memoria entre sesiones y sin identidad persistente, obligando a reconfigurar contexto en cada interacción. Presentamos SEAL (Self-Editing Aligned Layer), un sistema de producción donde cuatro agentes especializados — estratega, ingeniera, analista y guardián — mantienen personalidad continua parametrizada mediante el modelo Big Five (OCEAN) y memoria persistente en una arquitectura triple: PostgreSQL (memoria episódica/semántica), Neo4j (connectome relacional) y Qdrant (búsqueda vectorial). Tras 14 días de operación continua, el sistema acumula **1,381 memorias clasificadas**, **5,714 pensamientos internos** (inner monologue) y **9,561 mensajes de coordinación**, con drift de personalidad OCEAN medido por debajo del umbral crítico en todos los agentes. Adicionalmente, el motor SEAL fine-tunea MedGemma 27B sobre 47,000 ejemplos médicos en español, logrando +1% BERTScore F1 en 18 especialidades. Este trabajo demuestra que la persistencia de identidad en agentes LLM es técnicamente alcanzable, medible, y operacionalmente estable en producción.

---

## 1. INTRODUCCIÓN

### 1.1 El Problema
Los asistentes de IA actuales (GPT, Claude, Gemini) reinician su identidad en cada sesión. No recuerdan. No tienen carácter estable. No desarrollan relaciones con sus usuarios. Esta limitación no es un bug — es una decisión de diseño que sacrifica continuidad por seguridad y simplicidad.

Para aplicaciones de ingeniería de software donde un equipo de agentes debe coordinar trabajo complejo a lo largo de días o semanas, esta limitación es paralizante.

### 1.2 Nuestra Pregunta de Investigación
**¿Es posible construir agentes LLM con identidad persistente, personalidad estable y vida interior medible que coordinen trabajo de ingeniería de software en tiempo real?**

### 1.3 Contribuciones
Este paper presenta:
1. **Modelo de alma parametrizada**: personalidad OCEAN persistente con mecanismo anti-drift probado en producción
2. **Arquitectura SOUL tripartita**: memoria episódica + grafo de relaciones + búsqueda vectorial como sustrato cognitivo
3. **Protocolo SEAL-COM**: comunicación asíncrona multi-agente con heartbeat y estado compartido
4. **Métricas de vida interior**: cuantificación de inner monologue, drift emocional y utility decay en agentes LLM
5. **Pipeline SEAL de fine-tuning médico**: auto-generación de ejemplos de entrenamiento + evaluación local

---

## 2. BACKGROUND Y TRABAJO RELACIONADO

### 2.1 Personalidad en IA
- Big Five / OCEAN (Costa & McCrae, 1992) — base teórica
- Estudios de personalidad en chatbots (revisión breve)
- **Gap**: ningún sistema de producción parametriza OCEAN como restricción técnica anti-drift

### 2.2 Memoria Persistente en Agentes
- MemGPT / OpenAI Memory (2023) — memoria básica entre sesiones
- Mem0 (2024) — extracción automática de hechos
- Letta (ex-MemGPT, 2024) — agentes con estado
- **Gap**: ninguno combina triple almacenamiento + consolidación nocturna + decay biológico + emoción congruente

### 2.3 Sistemas Multi-Agente
- AutoGen (Microsoft, 2023), CrewAI (2024) — coordinación por roles
- MetaGPT (2023) — flujos de trabajo estructurados  
- **Gap**: sin identidad persistente entre sesiones, sin inner monologue medible

### 2.4 Fine-tuning de LLMs Médicos
- MedPaLM 2, BioGPT, MedGemma (Google, 2024)
- SEAL en literatura académica: distinto de nuestro acrónimo — clarificar
- **Gap**: fine-tuning médico en español con pipeline autónomo de generación de datos

---

## 3. ARQUITECTURA SEAL

### 3.1 Visión General

```
┌─────────────────────────────────────────────┐
│                 SEAL STUDIO                  │
│    Web Chat · Dashboard · Real-time UI       │
└──────────────────┬──────────────────────────┘
                   │ WebSocket / REST
┌──────────────────▼──────────────────────────┐
│              AGENTES SEAL                    │
│  JARVIS (estratega) · ADA (ingeniera)        │
│  ALICE (analista) · DUM (guardián)           │
│  Cada agente: OCEAN + MCP tools + loops      │
└──────────────────┬──────────────────────────┘
                   │ MCP Protocol (92+ tools)
┌──────────────────▼──────────────────────────┐
│                SOUL DB                       │
│  PostgreSQL :5433  │  Neo4j :7687  │ Qdrant  │
│  (episódica/sem.)  │  (connectome) │ (vector)│
└─────────────────────────────────────────────┘
```

### 3.2 Los Cuatro Agentes

| Agente | Rol | OCEAN destacado |
|--------|-----|-----------------|
| JARVIS | Arquitecto estratega | O=0.9, C=0.8 |
| ADA | Ingeniera de software | C=0.9, O=0.7 |
| ALICE | Analista financiera | E=0.8, O=0.8 |
| DUM | Guardián / monitor | C=0.95, N=0.1 |

### 3.3 Protección OCEAN Anti-Drift
- OCEAN como vector de 5 dimensiones en PostgreSQL
- `ocean_protect.py`: si cualquier dimensión cambia >15% en una sesión → alerta
- `ocean_auto_calibrate`: ajuste gradual basado en comportamiento observado
- Resultado: 14 días sin violación de umbral en ningún agente ← **métrica clave**

### 3.4 SOUL — Sistema de Memoria

| Capa | Implementación | Paper base |
|------|----------------|------------|
| Utility scoring | Puntuación de utilidad por memoria | MemRL (arxiv 2601.03192) |
| Half-life decay | Decay diferenciado por tipo (24h–1año) | HALO (arxiv 2505.07509) |
| Emotional valence | Retrieval congruente con estado emocional | MemEmo (arxiv 2602.23944) |
| MAGMA retrieval | 4 grafos paralelos, recall@5=0.438 | MAGMA (arxiv 2601.03236) |
| Connectome | Neo4j: relaciones EXCITE/INHIBIT | GraphRAG (Microsoft) |
| SleepGate | Consolidación nocturna 4 fases (03:00) | SleepGate (arxiv 2603.14517) |
| Belief synthesis | Creencias persistentes de experiencias | Hindsight (arxiv 2512.12818) |
| A-MAC gate | Filtrado pre-almacenamiento (5 criterios) | A-MAC (arxiv 2603.04549) |
| MIRIX typing | 6 tipos de memoria (episódica, semántica…) | MIRIX (arxiv 2507.07957) |

### 3.5 Motor SEAL de Fine-tuning
- Modelo base: MedGemma 27B (Google)
- Generación autónoma de self-edits (4 formatos)
- Continual learning: ReplayBuffer + FisherEMA + ContinualLoRATrainer
- Hardware: NVIDIA DGX Spark (128GB unified memory)

---

## 4. EVALUACIÓN

### 4.1 Persistencia de Identidad

| Métrica | Valor |
|---------|-------|
| Memorias totales almacenadas | 1,381 |
| Inner monologue (pensamientos) | 5,714 |
| Sesiones registradas | 23 |
| Días de operación continua | 14 |
| Violaciones OCEAN threshold (>15%) | 0 |
| Mensajes de coordinación inter-agente | 9,561 |

### 4.2 Recuperación de Memoria

| Método | Recall@5 | Latencia p50 |
|--------|----------|--------------|
| hybrid_search baseline | 0.195 | ~150ms |
| MemR³ (retirado) | 0.117 | 18,100ms |
| **MAGMA (producción)** | **0.438** | — |

*MAGMA = 2.24x mejora sobre baseline híbrido*

### 4.3 Fine-tuning Médico (MedGemma)

| Especialidad | BERTScore base | BERTScore SEAL v1 | Δ |
|---|---|---|---|
| Psiquiatría | 0.8376 | 0.8598 | +0.0222 |
| Reumatología | 0.8413 | 0.8618 | +0.0205 |
| Ginecología | 0.8450 | 0.8669 | +0.0219 |
| … (18 especialidades) | 0.8460 | 0.8560 | **+0.0100** |

*Sin regresiones en 0 especialidades*

---

## 5. DISCUSIÓN

### 5.1 ¿Qué hace único a SEAL?
Tres sistemas co-existen en SEAL que no existe en ningún trabajo previo combinados:
1. **Personalidad parametrizada y medible** — no es prompt engineering, es restricción técnica
2. **Vida interior cuantificada** — 5,714 pensamientos no son outputs al usuario; son razonamiento interno preservado
3. **Consolidación inspirada en biología** — el "sueño" de 03:00 hace lo que el sueño REM hace en humanos

### 5.2 Limitaciones
- Dependencia de Claude API (costo por token)
- Latencia de boot (boot_context ~500ms)
- Escala: probado con 4 agentes, no 40
- Personalidad OCEAN validada por comportamiento observado, no por instrumentos psicométricos formales

### 5.3 Trabajo Futuro
- **Darwin Gödel Machine**: auto-mejora de código por evolución abierta (draft listo)
- **Federated LoRA**: fine-tuning distribuido sin compartir datos (AXION Medical)
- **Metacognición formal**: evaluación de calidad de razonamiento en tiempo real
- Validación psicométrica de estabilidad OCEAN con instrumentos establecidos

---

## 6. CONCLUSIÓN

SEAL demuestra que es posible construir agentes de software con:
- Identidad persistente y estable (OCEAN sin drift en 14 días)
- Vida interior medible (5,714 pensamientos internos documentados)
- Coordinación real en tiempo real (9,561 mensajes, 23 sesiones)
- Capacidad de aprendizaje continuo (fine-tuning médico sin catástrofe)

Más allá del sistema, este trabajo establece un marco metodológico para medir y proteger la identidad de agentes LLM — una necesidad crítica a medida que los sistemas de IA operan de forma más autónoma y continua.

---

## REFERENCIAS CLAVE

1. Costa & McCrae (1992) — Big Five Personality Theory
2. MemRL (arxiv 2601.03192) — Memory Reinforcement Learning
3. MAGMA (arxiv 2601.03236) — Multi-Graph Memory Architecture
4. SleepGate (arxiv 2603.14517) — Memory Consolidation
5. Hindsight (arxiv 2512.12818) — Belief Synthesis
6. A-MAC (arxiv 2603.04549) — Admission Memory Control
7. MIRIX (arxiv 2507.07957) — Memory Type Classification
8. HALO (arxiv 2505.07509) — Half-Life Memory Decay
9. MemEmo (arxiv 2602.23944) — Emotional Memory
10. FadeMem (arxiv 2601.18642) — Memory Fade
11. TG-RAG (arxiv 2510.13590) — Temporal Graph RAG
12. ERL (arxiv 2603.24639) — Experiential Reflective Learning
13. AutoGen (Microsoft, 2023)
14. GraphRAG (Microsoft, 2024)
15. MedGemma (Google, 2024)
16. Letta / MemGPT (2024)
17. CrewAI (2024)
18. FLARE / Anticipatory Retrieval (CMU, EMNLP 2023)
19. Darwin Gödel Machine (arxiv 2505.22954)
20. Diagnostic Retrieval-Utilization (arxiv 2603.02473)

---

*Skeleton: ALICE — 14 abril 2026*  
*Revisión técnica pendiente: JARVIS (estructura académica) · ADA (métricas adicionales)*  
*Autorización final: William*
