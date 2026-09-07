# Análisis NEXUS — 9 Papers JEPA/AMI Labs
**Fecha:** 2026-04-28 | **Agente:** NEXUS | **Para:** William

---

## PANORAMA: Qué está construyendo AMI Labs / FAIR

Todos los papers apuntan a una misma arquitectura: el **World Model de LeCun (2022)**.
No es un LLM. No predice tokens. **Predice en espacio de representación latente** — la diferencia fundamental.

La cadena evolutiva es:
```
LeCun 2022 (blueprint) 
→ I-JEPA (imágenes)
→ V-JEPA / V-JEPA 2 (video)
→ LLM-JEPA (lenguaje)
→ VL-JEPA (visión-lenguaje)
→ ACT-JEPA / V-JEPA 2-AC (robotics + planning)
→ BiJEPA (dinámica temporal)
→ Cell-JEPA (biología)
→ Rectified LpJEPA (sparsity)
```

AMI Labs levantó **$1.03B en marzo 2026** (NVIDIA, Bezos, Temasek, Schmidt) para productizar exactamente esto.
El timing de William de estudiarlos ahora es correcto.

---

## ANÁLISIS POR PAPER

### 1. BZ5a1r-kVsf — LeCun 2022 "A Path Towards Autonomous Machine Intelligence"
**El blueprint original. Todo lo demás implementa esto.**

6 módulos del World Model:
- **Configurator**: define objetivos y prioridades del sistema
- **Perception**: extrae representación del input sensorial
- **World Model**: predice estados futuros dado acciones
- **Cost (Intrinsic + Critic)**: evalúa estados (cuánto me cuesta estar aquí)
- **Short-term Memory**: estado episódico de la interacción actual
- **Actor**: genera secuencia de acciones

**Mode-1** (reactivo, System 1): Configurator → Perception → Cost → Actor (sin planificación)
**Mode-2** (deliberativo, System 2): Configurator → World Model → búsqueda de acción por gradiente en espacio latente

El insight crítico: **planning = optimización de funciones de costo sobre trayectorias imaginadas en espacio latente**.
No generación de texto. No sampling. Optimización.

### 2. 2301.08243 — I-JEPA (Meta FAIR, 2023)
**SSL para imágenes sin augmentación, predice en espacio latente.**

- Encoder procesa imagen parcialmente enmascarada
- Predictor aprende a reconstruir las partes enmascaradas EN espacio de representación (no píxeles)
- Target = EMA del encoder (evita colapso)
- Multi-block masking: múltiples bloques enmascarados simultáneamente, contexto acotado
- ViT-H/14 → 81.1% ImageNet con linear probe
- 2.5x más eficiente que MAE equivalente

Relevancia para SOUL: el mecanismo encoder/predictor/EMA es la base de todos los que siguen.

### 3. 2501.14622 — ACT-JEPA (2025)
**Combina Imitation Learning (IL) + Self-Supervised Learning (JEPA) para robótica.**

- IL solo: aprende a copiar acciones del demonstrador
- JEPA solo: aprende representación del mundo sin saber actuar
- ACT-JEPA: aprende mundo + política simultáneamente
- Resultado: **40% mejor comprensión del world model**, **10% mayor tasa de éxito en tareas**
- El SSL actúa como regularizador — evita que la política memorice acciones sin entender contexto

**Aplicación directa a SEAL**: cada agente tiene un historial de (contexto, acción, outcome) — exactamente el formato IL. Añadir pérdida JEPA sobre las representaciones de contexto haría que los agentes aprendieran mejor el "mundo SEAL" más allá de imitar respuestas anteriores.

### 4. 2509.14252 — LLM-JEPA (2025)
**JEPA aplicado a modelos de lenguaje, usando (texto, código) como dos vistas del mismo concepto.**

- Hallazgo crítico: **la pérdida NTP (Next Token Prediction) NO minimiza la pérdida JEPA**. Son mejoras independientes que se suman.
- LLM-JEPA resiste overfitting mejor que NTP solo
- Funciona en Llama-3, Gemma-2 sin cambiar arquitectura base — solo añade cabeza JEPA
- Los embeddings JEPA capturan estructura semántica más robusta

**Aplicación a SOUL**: SOUL almacena memorias como texto. Podemos crear pares (memoria_texto, metadata_schema) como dos vistas y añadir pérdida JEPA al pipeline de embedding. Additive improvement, no reemplaza nada.

### 5. 2512.10942 — VL-JEPA (2025)
**Vision-Language JEPA. SOTA sin supervisión de lenguaje durante pretraining.**

- Predice embeddings visuales dado contexto visual+lingüístico (no genera tokens)
- SOTA en WorldPrediction-WM benchmark: **65.7%** (supera GPT-4o, Claude 3.5)
- **2.85x menos operaciones de decoding** que modelos generativos equivalentes
- Insight clave: un encoder de video entrenado SIN supervisión de lenguaje puede alinearse con LLM y lograr SOTA — la representación visual era el bottleneck

**Aplicación Medical AI**: VL-JEPA como backbone para interpretar imágenes DICOM/clínicas + texto de informes. La eficiencia (2.85x) es crítica en inferencia local.

### 6. 2602.01456 — Rectified LpJEPA (2026)
**JEPA con sparsidad controlable. Llega hasta 95% ceros en representaciones.**

- RDMReg: Rectified Distribution Matching Regularization
- Usa distribuciones Gaussianas Generalizadas Rectificadas — el parámetro p controla la sparsidad
- p=2 (normal) → representación densa; p→0 → representación ultra-sparse
- A igual calidad de retrieval, reduce footprint hasta **10x**

**Aplicación SOUL**: Qdrant actualmente almacena embeddings densos. Aplicar RDMReg al pipeline de embedding reduciría el índice vectorial significativamente. Crítico cuando SOUL escale a multi-tenant con millones de memorias.

### 7. 2602.02093 — Cell-JEPA (2026)
**JEPA aplicado a datos scRNA-seq. 36% mejor que scGPT.**

- scRNA-seq: cada célula = secuencia de expresión génica (análogo a tokens)
- Cell-JEPA predice expresión génica enmascarada en espacio latente
- Supera scGPT en 36% con mismo tamaño de modelo
- 800,000 células de entrenamiento — dataset relativamente pequeño para un modelo fundacional

**Aplicación Medical AI (DIRECTA)**: El patrón "datos biológicos secuenciales → JEPA" generaliza.
Aplicaciones concretas:
- EHR (Electronic Health Records): (diagnóstico_t1, diagnóstico_t2, ... → predecir diagnóstico_t+1 en espacio latente)
- Time-series de vitales: (SpO2, HR, BP secuencial → predecir siguiente estado del paciente)
- Secuencias farmacológicas: predecir respuesta a medicamento dado historial

### 8. 2603.00049 — BiJEPA (2026)
**Predicción bidireccional. Aprende forward (x→y) Y backward (y→x) simultáneamente.**

- Problema identificado: JEPA clásico tiene feedback loops que causan **representation explosion** (magnitudes divergen)
- Fix: soft constraints = LayerNorm + Weight Decay en representaciones intermedias
- BiJEPA añade pérdida backward: también predice x dado y
- Resultado: **3.7x menor error de predicción** en dinámicas caóticas vs JEPA clásico
- El backward pass actúa como regularizador implícito

**Aplicación SOUL (CRÍTICA — aplica HOY)**: 
La "representation explosion" puede estar ocurriendo en SOUL silenciosamente.
Cada memoria almacenada en Qdrant es un vector. Con el tiempo, los embeddings pueden derivar.
Fix inmediato: **añadir LayerNorm antes de todo INSERT a Qdrant** — una línea de código, impacto alto.

### 9. 2506.09985 — V-JEPA 2 (2025, el más completo)
**El world model completo: entiende, predice Y planifica. El destino final.**

**Datos de entrenamiento**: 1M horas de video + 1M imágenes (VideoMix22M — YouTube, Kinetics, SSv2, HowTo100M, ImageNet)

**Arquitectura en dos etapas:**
- Stage 1: V-JEPA 2 encoder (ViT-g, 1B params) — action-free SSL sobre video masivo
- Stage 2: V-JEPA 2-AC — action-conditioned world model, entrenado con solo **62 horas de datos de robot** (Droid dataset) encima del encoder congelado

**Resultados clave:**
- Something-Something v2: 77.3 top-1 (motion understanding)
- Epic-Kitchens-100 action anticipation: 39.7 recall@5 (+44% vs SOTA previo)
- PerceptionTest: 84.0 (SOTA clase 8B)
- TempCompass: 76.9
- **Zero-shot manipulation en robots Franka en ENTORNOS NUNCA VISTOS** — sin reward, sin datos locales

**Planning mechanism:**
```
E(a₁:T; zk, sk, zg) = ||P(a₁:T; sk, zk) - zg||₁
```
Dado estado actual zk y estado meta zg, optimiza secuencia de acciones que minimiza distancia L1 entre estado imaginado y meta.
**Esto es planificación real. No prompting. No chain-of-thought. Optimización en espacio latente.**

**Scaling insights:**
- 4 ingredientes: data scaling, model scaling, longer training, higher resolution
- Progressive resolution training: 8.4x speedup (entrenar en baja resolución, fine-tune en alta)
- 3D-RoPE: posición temporal + espacial en un solo embedding

---

## QUÉ APLICA A SOUL HOY

### H1. Fix BiJEPA — LayerNorm antes de INSERT Qdrant (1 línea, hoy)
**Impacto: prevenir representation drift silencioso en memorias SOUL**
```python
# En memory/mcp_server_v2.py, antes de qdrant client.upsert()
embedding = layer_norm(embedding)  # torch.nn.functional.layer_norm(emb, emb.shape)
```
Si los embeddings están derivando, los resultados de memory_hybrid_search degradan silenciosamente.
Este fix es gratis.

### H2. Dual-view memory encoding — LLM-JEPA style (1-2 días)
**Impacto: mejorar calidad de retrieval en SOUL sin cambiar schema**
Crear pares (content_text, structured_summary) para cada memoria.
Entrenar cabeza JEPA pequeña (sin cambiar base embeddings) sobre estos pares.
NTP-equivalent (text embedding) + JEPA head = representaciones más robustas.
Útil especialmente para memorias técnicas con mucho jargón.

### H3. Cell-JEPA pattern para Medical AI pipeline (1 semana)
**Impacto: aplicar JEPA directamente a datos clínicos del pipeline GTL/AXION**
El patrón "secuencia biológica → predecir en latente" está probado en 800K células.
Aplicar a:
- Series temporales de vitales (SpO2, HR)
- Secuencias de diagnósticos ICD-10
- Historial farmacológico → predicción de respuesta

### H4. Attentive probe sobre GAP 1 Beliefs (3-5 días)
**Impacto: mejorar Belief Inspector**
V-JEPA 2 usa attentive probes (clasificadores ligeros sobre encoder congelado).
GAP 1 ya tiene reasoning_trace_store. Podemos entrenar una probe sobre:
- Input: (premises, reasoning) embeddings
- Label: outcome_success (bool)
Esto daría un predictor de "cuán probable es que este razonamiento sea correcto".

---

## QUÉ VA DESPUÉS

### D1. SOUL World Model — Arquitectura LeCun completa (3-6 meses)
SOUL tiene: memory (Short-term Memory) + goal_list (Cost/Intrinsic) + agent execution (Actor)
Falta: **World Model module** — predictor de estados futuros del sistema
Implementar usando V-JEPA 2-AC pattern:
- Corpus de entrenamiento: historial completo de conversaciones SEAL (thousands of hours of "interaction data")
- Freeze encoder (base LLM representations)
- Train action-conditioned predictor: dado (context_state, agent_action) → predict next_context_state
- Result: cada agente puede imaginar consecuencias antes de actuar

### D2. Planning loop para decisiones críticas (depende de D1)
Una vez con World Model: implementar energy minimization loop de V-JEPA 2-AC para decisiones de alto impacto.
En lugar de: "¿qué acción tomo?" → generar texto
Con World Model: minimizar E(acciones; estado_actual, estado_meta) sobre K candidatos

### D3. Sparsity Qdrant via Rectified LpJEPA (1 mes, requiere data pipeline)
Re-embeder todas las memorias SOUL con RDMReg training.
Proyección esperable: reducción 5-10x en footprint de Qdrant.
Prerequisito: pipeline de entrenamiento con GPU (DGX Spark).

### D4. Multi-modal SOUL con VL-JEPA (2-3 meses)
SOUL actualmente es texto. Con VL-JEPA:
- Memorias pueden incluir imágenes (diagramas, capturas, datos médicos visuales)
- Retrieval multimodal: "buscar memoria relacionada con este DICOM"
- Pipeline Medical AI con inferencia visual local eficiente (2.85x menos ops)

---

## SÍNTESIS NEXUS

**Lo que AMI Labs está construyendo es exactamente la Autonomous Machine Intelligence que LeCun describió en 2022.**
La diferencia con LLMs: **no predice tokens, predice en representación**. Más eficiente, generaliza mejor, planifica de verdad.

**El insight más importante para William:**
V-JEPA 2 demostró que con solo **62 horas de datos de interacción** encima de un buen encoder (entrenado con masivo video sin supervisión), un agente puede planificar en entornos nunca vistos.

**Analogía para SEAL:**
- "Video masivo sin supervisión" = historial de conversaciones SEAL (tenemos miles de turnos)
- "62 horas de robot data" = correcciones explícitas de William (feedback en soul_db)
- "Entornos nunca vistos" = nuevos proyectos/clientes/contextos técnicos

Si implementamos el World Model para SEAL, los agentes podrían planificar correctamente en contextos nuevos con muy pocos ejemplos de William — exactamente como V-JEPA 2-AC en robots.

**Prioridad recomendada para William:**
1. H1 (LayerNorm fix) — hoy, gratis, previene degradación silenciosa
2. H3 (Cell-JEPA Medical AI) — alto ROI, pipeline medical ya existe
3. D1 (World Model SOUL) — el arco narrativo correcto a 6 meses

---
*Análisis completado: 2026-04-28 | NEXUS | 9/9 papers leídos a profundidad*
