# SPEC: SOUL v4 — JEPA Extensions
**Autor:** NEXUS | **Fecha:** 2026-04-28 | **Modo:** Opus
**Estado:** DRAFT → pendiente aprobación de William
**Fuentes:** 9 papers JEPA/AMI Labs + 3 repos (BiJEPA, V-JEPA 2, I-JEPA)

---

## 0. Problema Central

SOUL v3 es una arquitectura de memoria competente pero construida sobre un paradigma de retrieval estático. Los embeddings se calculan una vez, se guardan en Qdrant, y se comparan por similitud coseno. Esta arquitectura tiene 4 deficiencias estructurales que el ecosistema JEPA resuelve directamente:

| Deficiencia | Síntoma observable | Fix JEPA |
|---|---|---|
| **Embedding drift** | memory_hybrid_search degrada con el tiempo sin señal visible | BiJEPA: LayerNorm + weight_decay |
| **Representaciones planas** | Memorias técnicas y conversacionales tratadas igual | LLM-JEPA: dual-view encoding |
| **Sin modelo del mundo** | Agentes no anticipan consecuencias, solo responden | V-JEPA 2-AC: predictor causal |
| **Sin capacidad médica** | Medical AI usa embeddings genéricos | Cell-JEPA: encoders para datos secuenciales biológicos |

---

## 1. Visión de SOUL v4

SOUL v4 no es un reemplazo de SOUL v3. Es una capa de inteligencia predictiva encima de la infraestructura existente.

**SOUL v3:** recuerda hechos.
**SOUL v4:** recuerda hechos + predice estados futuros + planifica acciones.

La arquitectura LeCun (2022) describe 6 módulos para una IA autónoma. SOUL v3 cubre 2 de ellos (Short-term Memory + parte de Cost). SOUL v4 añade el World Model — el módulo que faltaba.

```
SOUL v3 (actual):
  Memory (PostgreSQL + Qdrant + Neo4j) ← estático
  Goals (agent_goals table) ← estático

SOUL v4 (objetivo):
  Memory + Goals [existente]
  + Perception Encoder (JEPA-style, estable)
  + World Model Predictor (V-JEPA 2-AC pattern)
  + Planning Loop (energy minimization en latente)
```

---

## 2. FASE 1 — Estabilidad de Representaciones
**Timeline: 1-2 días | Prioridad: CRÍTICA | Sin GPU especial requerida**

### 2.1 BiJEPA Embedding Stability Fix

**Problema (demostrado en código `bijepa_yongchao.py`):**
Sin soft constraints, los vectores en Qdrant divergen silenciosamente. El error no es visible hasta que la calidad de retrieval ya degradó.

**Fix:**
```python
# memory/mcp_server_v2.py — antes de todo upsert a Qdrant
import torch
import torch.nn.functional as F

def normalize_embedding(embedding: list[float]) -> list[float]:
    t = torch.tensor(embedding, dtype=torch.float32)
    return F.layer_norm(t, t.shape).tolist()
```

**Dónde aplicar:**
- `memory_store()` → normalizar embedding antes de `qdrant_client.upsert()`
- `memory_hybrid_search()` → normalizar query embedding antes de búsqueda

**Por qué LayerNorm y no L2 normalize:**
BiJEPA Case 3 (código) demuestra que L2 norm (unit sphere) reduce expresividad. LayerNorm estabiliza la magnitud sin restringir la dirección. Para SOUL queremos representaciones expresivas + estables.

**Resultado esperado:**
- Retrieval quality mantiene baseline en sesiones largas
- Sin degradación silenciosa después de 1000+ memorias

### 2.2 Weight Decay en encoder de memorias

Si SOUL usa un encoder entrenado (custom) para memorias, añadir `weight_decay=1e-4` al optimizer.
Si usa embeddings externos (e.g., OpenAI, local model), la capa de normalización pre-Qdrant es suficiente.

---

## 3. FASE 2 — Representaciones Duales y Medical AI
**Timeline: 1-2 semanas | Prioridad: ALTA**

### 3.1 LLM-JEPA Dual-View Memory Encoding

**Problema:**
SOUL almacena memorias como texto. Una memoria técnica densa ("BiJEPA usa LayerNorm + weight_decay para evitar representation explosion en esquemas bidireccionales") y una memoria conversacional ("William dijo que nos quiere como familia") tienen el mismo tipo de embedding. El retrieval confunde contextos.

**Solución (LLM-JEPA pattern):**
Crear pares de vistas para cada memoria:
- `view_1`: el texto original (content)
- `view_2`: un resumen estructurado compacto (metadata + category + importance como texto)

Entrenar una cabeza JEPA pequeña (no cambiar los embeddings base) que aprenda a predecir `view_2` dado `view_1` y viceversa. Esto añade una regularización implícita: el modelo aprende que "lo que decimos" y "la estructura de lo que decimos" deben ser consistentes.

**Implementación:**
```python
# Nueva tabla en PostgreSQL (no rompe SOUL v3):
CREATE TABLE memory_dual_view (
    memory_id INTEGER REFERENCES memories(id),
    view1_embedding VECTOR(768),    -- texto original
    view2_embedding VECTOR(768),    -- resumen estructurado
    jepa_loss FLOAT,                -- pérdida del último epoch
    last_trained TIMESTAMP
);

# Pipeline:
# 1. Al guardar memoria → generar view2 (summarization via local LLM)
# 2. Cada 24h → mini-training loop JEPA sobre pares del día
# 3. Usar view1 + view2 combinados en retrieval (weighted blend)
```

**Costo computacional:**
Muy bajo. El mini-training JEPA en CPU con embeddings 768-dim y 1000 memorias = segundos.

**Resultado esperado:**
- Memorias técnicas recuperadas con mayor precisión en contextos técnicos
- Memorias conversacionales no contaminan búsquedas técnicas

### 3.2 Cell-JEPA para Medical AI (AXION)

**Contexto:**
Cell-JEPA demostró 36% de mejora sobre scGPT en datos scRNA-seq. El patrón es: "secuencia biológica/clínica → predecir en latente, no en espacio de valores originales".

**Aplicación directa al pipeline Medical AI de William:**

#### 3.2.1 EHR-JEPA (Electronic Health Records)
```
Input: secuencia de diagnósticos ICD-10 del paciente
[J45.0, I10, E11.9, J45.1, ...]  ← historial temporal

Objetivo JEPA: dado [J45.0, I10, E11.9] → predecir embedding de J45.1
NO: predecir el código exacto J45.1 (como LLM haría)
SÍ: predecir la representación semántica del siguiente estado clínico

Ventaja: el modelo aprende "trajectoria clínica" sin overfitting a códigos específicos
```

#### 3.2.2 Vital-JEPA (Series temporales de signos vitales)
```
Input: (SpO2_t, HR_t, BP_t, RR_t) × N timesteps

Objetivo: dado ventana de 6h de vitales → predecir embedding del estado a las 12h
Usar multiblock masking (I-JEPA style): enmascarar bloque temporal central, predecir en latente

Aplicación clínica: detección temprana de deterioro
```

#### 3.2.3 Implementación propuesta

```python
# axion/medical/jepa_clinical.py
class ClinicalJEPA(nn.Module):
    """
    Adapta el patrón Cell-JEPA para datos clínicos secuenciales.
    Basado en: arxiv:2602.02093 (Cell-JEPA)
    """
    def __init__(self, vocab_size, embed_dim=256, seq_len=64):
        super().__init__()
        # Encoder: transforma secuencia clínica en representación
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=embed_dim, nhead=8, 
                                        dim_feedforward=1024,
                                        norm_first=True),  # Pre-LN (más estable)
            num_layers=6
        )
        # Target encoder: EMA del online encoder
        self.target_encoder = copy.deepcopy(self.encoder)
        for p in self.target_encoder.parameters():
            p.requires_grad = False
        
        # Predictor: proyecta contexto → predicción latente del target
        self.predictor = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * 2),
            nn.LayerNorm(embed_dim * 2),
            nn.GELU(),
            nn.Linear(embed_dim * 2, embed_dim)
        )
        
        self.embed = nn.Embedding(vocab_size, embed_dim)
    
    def forward(self, context_seq, target_seq):
        # Context: parte visible de la secuencia clínica
        ctx_emb = self.encoder(self.embed(context_seq))
        ctx_pred = self.predictor(ctx_emb.mean(dim=1))
        
        # Target: parte enmascarada (stop gradient)
        with torch.no_grad():
            tgt_emb = self.target_encoder(self.embed(target_seq)).mean(dim=1)
        
        return F.mse_loss(ctx_pred, tgt_emb)
    
    def update_ema(self, momentum=0.996):
        with torch.no_grad():
            for o, t in zip(self.encoder.parameters(), 
                           self.target_encoder.parameters()):
                t.data = momentum * t.data + (1 - momentum) * o.data
```

**Dataset mínimo requerido:**
William tiene 47,000 ejemplos de entrenamiento médico en español. Con la arquitectura Cell-JEPA (SSL), el self-supervised pretraining puede hacerse sobre registros clínicos sin labels — lo que expande el corpus disponible enormemente.

---

## 4. FASE 3 — World Model para SOUL
**Timeline: 2-3 meses | Prioridad: ALTA ESTRATÉGICA | Requiere DGX Spark**

### 4.1 Motivación

V-JEPA 2-AC demostró: un world model que aprendió a representar el mundo (Stage 1, sin supervisión) + 62 horas de datos de interacción (Stage 2, con acciones) → planificación zero-shot en entornos nunca vistos.

El mapping exacto para SEAL:

| V-JEPA 2 | SOUL World Model |
|---|---|
| 1M horas de video sin supervisión | Historial completo de conversaciones SEAL |
| Frames de video (píxeles) | Embeddings de contexto de conversación |
| 62 horas de datos de robot | Feedback explícito de William (correcciones, aprobaciones) |
| 7-DOF de robot (acción) | Vector de decisión del agente (K dimensiones) |
| Planning en nuevo entorno | Planificar en nuevo proyecto/cliente/contexto |

### 4.2 Arquitectura SOUL World Model

```
[Stage 1: SOUL Context Encoder]
Input: historial de conversaciones SEAL (texto → embeddings)
Objetivo: predecir en latente qué viene después dado contexto parcial
Entrenamiento: SSL sobre todo el historial SEAL (sin labels, acción-free)
Arquitectura: TransformerEncoder + Predictor (patrón I-JEPA/V-JEPA 2)

[Stage 2: SOUL Action-Conditioned Predictor]  
Input: (contexto_actual, acción_del_agente) → predecir siguiente_estado_contexto
Entrenamiento: supervisado con (contexto_t, acción_t, contexto_t+1) del historial SEAL
Arquitectura: VisionTransformerPredictorAC adaptado (causal, interleave acción+contexto)
Datos necesarios: todos los turnos SEAL con (entrada, respuesta, resultado)

[Planning Loop]
Dado: estado_actual, objetivo_deseado
Optimizar: secuencia de acciones por gradient descent
Criterio: minimizar ||predictor(acciones, estado_actual) - objetivo||₁
```

### 4.3 Definición del "action space" para SEAL

En V-JEPA 2-AC, `action_embed_dim=7` (7-DOF del brazo robot). Para SEAL, el espacio de acciones incluye:

```python
SEAL_ACTION_DIM = {
    # Tipo de respuesta (softmax discreto → embedded continuo)
    'response_type': ['answer', 'question', 'action', 'plan', 'delegate', 'silence'],
    
    # Herramienta a usar (multi-hot)
    'tool_vector': ['bash', 'read', 'write', 'edit', 'search', 'memory_store', ...],
    
    # Agente destino (softmax)
    'target_agent': ['self', 'ADA', 'JARVIS', 'ALICE', 'William'],
    
    # Intensidad / confianza (continuo [0,1])
    'confidence': float,
    'urgency': float
}

# Embedding total: ~32-64 dimensiones continuas
# El predictor aprende a usar esto para predecir el siguiente estado del sistema
```

### 4.4 Implementación en fases

```
Mes 1:
  - Exportar historial completo de william_channel.jsonl a corpus de entrenamiento
  - Implementar Context Encoder (SSL pretraining sobre corpus SEAL)
  - Definir action space SEAL (vector de 32-64 dim)
  - Baseline: predictor simple sin causal attention

Mes 2:
  - Añadir causal attention pattern (block-causal de V-JEPA 2-AC)
  - Entrenar Action-Conditioned Predictor con feedback de William
  - Validar: ¿el predictor anticipa correctamente qué herramienta usar?

Mes 3:
  - Planning loop: dado objetivo, optimizar secuencia de acciones
  - Integrar en ciclo de decisión de agentes (opcional, pre-tool-call)
  - A/B test: decisiones con planning vs sin planning
```

---

## 5. FASE 4 — Escala y Multimodalidad
**Timeline: 3-6 meses | Prioridad: MEDIA**

### 5.1 Sparse Embeddings (Rectified LpJEPA)

Cuando SOUL escale a multi-tenant (múltiples clientes de AXION), el índice Qdrant crecerá proporcionalmente. Rectified LpJEPA demuestra que se puede llegar a 95% de zeros en representaciones con igual calidad de retrieval.

**Implementación:**
- Entrenar pipeline de re-embedding con RDMReg loss
- Re-vectorizar todas las memorias existentes
- Resultado esperado: 5-10x reducción en footprint Qdrant

### 5.2 SOUL Multimodal (VL-JEPA pattern)

**Objetivo:** SOUL capaz de almacenar y recuperar memorias que incluyan imágenes.

Casos de uso:
- Medical AI: DICOM asociado a diagnóstico → memoria multimodal
- GTL: escáner de documento aduanero → memoria vinculada a cliente
- Debugging: screenshot de error → memoria técnica visual

**Implementación:**
```python
# Usar I-JEPA preentrenado como encoder visual base:
visual_encoder = load_ijepa_checkpoint('IN1K-vit.h.14-300e.pth.tar')

# Añadir columna en memories table:
ALTER TABLE memories ADD COLUMN visual_embedding VECTOR(1024);

# En memory_store: si se adjunta imagen → extraer embedding visual
# En memory_hybrid_search: combinar texto + visual embedding
```

---

## 6. RESUMEN EJECUTIVO — Qué tomamos de cada fuente

| Fuente | Qué tomamos | Cuándo | Esfuerzo |
|---|---|---|---|
| BiJEPA (código) | LayerNorm fix en Qdrant embeddings | HOY | 1 línea |
| BiJEPA (paper) | Bidirectional training para memory encoder | Fase 3 | 1 semana |
| I-JEPA (checkpoints) | Encoder visual para Medical AI | HOY | Descargar |
| I-JEPA (arquitectura) | Multi-block masking para SOUL pretraining | Fase 3 | 2 semanas |
| LLM-JEPA | Dual-view memory encoding | Fase 2 | 3 días |
| VL-JEPA | SOUL multimodal | Fase 4 | 1 mes |
| ACT-JEPA | IL+SSL para aprendizaje de feedback de William | Fase 3 | 2 semanas |
| Cell-JEPA | Clinical data encoder para AXION Medical AI | Fase 2 | 1 semana |
| BiJEPA (stability) | EMA momentum scheduler para target encoder | Fase 2 | 2 días |
| Rectified LpJEPA | Sparse Qdrant embeddings | Fase 4 | 1 mes |
| V-JEPA 2 (scaling) | Progressive resolution / progressive complexity | Fase 3 | Principio |
| V-JEPA 2-AC (código) | Planning loop blueprint | Fase 3 | 3 semanas |
| V-JEPA 2.1 | Dense loss + deep supervision para encoder | Fase 3 | mejora incremental |
| LeCun 2022 | Arquitectura 6-módulos como roadmap | HOY | Visión |

---

## 7. PRIORIDADES — Decisiones que necesitan autorización de William

**P1 (hoy):** ¿Aplicamos el LayerNorm fix a Qdrant ahora? (no-brainer, bajo riesgo)

**P2 (esta semana):** ¿Descargo I-JEPA ViT-H checkpoint y lo integro al pipeline Medical AI de AXION?

**P3 (siguiente sprint):** ¿Implementamos ClinicalJEPA como backbone de EHR-JEPA / Vital-JEPA para AXION Medical?

**P4 (decisión estratégica):** ¿SOUL World Model es el próximo proyecto grande del equipo (Fase 3)?
Esta decisión define si SEAL pasa de "agentes que responden" a "agentes que planifican".
Es el arco correcto a 6 meses. Requiere DGX Spark como nodo de entrenamiento.

---

## 8. MÉTRICAS DE ÉXITO

| Fase | Métrica | Baseline | Target |
|---|---|---|---|
| Fase 1 | Retrieval quality over time (cosine sim promedio) | degradación post-1000 memorias | estable ±2% |
| Fase 2 | Precisión retrieval memorias técnicas | baseline actual | +15-20% |
| Fase 2 | Clinical JEPA vs baseline en EHR prediction | NaN (no existe) | >30% mejora |
| Fase 3 | Tool prediction accuracy | N/A (agentes no predicen) | >60% accuracy |
| Fase 3 | Planning success rate en nuevos contextos | N/A | >40% zero-shot |
| Fase 4 | Qdrant footprint por tenant | baseline | -80% |

---

## 9. DEPENDENCIAS Y RIESGOS

**Dependencias:**
- Fase 1: solo Python + Qdrant. Sin dependencias nuevas.
- Fase 2: PyTorch (ya instalado DGX Spark). I-JEPA checkpoint (descarga pública).
- Fase 3: DGX Spark para entrenamiento. Corpus SEAL completo (historial).
- Fase 4: DGX Spark + pipeline de curaciónde datos.

**Riesgos:**
1. **Fase 1 (LayerNorm):** Bajo. Si los embeddings ya estaban normalizados, no hay cambio. Si no, mejora silenciosamente.
2. **Fase 2 (Medical):** Medio. ClinicalJEPA requiere dataset suficientemente grande. William tiene 47K ejemplos — deberían ser suficientes para pretraining básico.
3. **Fase 3 (World Model):** Alto. Es territorio nuevo para SOUL. Requiere definición cuidadosa del action space + validación iterativa.
4. **Fase 4 (Sparse):** Bajo-Medio. Re-embedding tiene coste computacional pero no afecta producción si se hace en paralelo.

---

## 10. NOTA ARQUITECTURAL CRÍTICA

**SOUL v4 no rompe SOUL v3.**

Cada fase es aditiva, no destructiva:
- LayerNorm fix = pre-procesador en memoria (no toca schema)
- Dual-view = nueva tabla, no modifica existente
- ClinicalJEPA = módulo independiente en pipeline Medical AI
- World Model = nuevo microservicio consultado opcionalmente antes de cada decisión

En ningún momento se requiere migrar datos existentes o reescribir la API MCP.

---

*SPEC v1.0 | NEXUS | 2026-04-28 | Modo Opus*
*Pendiente: revisión JARVIS (arquitectura) + ADA (ejecución) + aprobación William*
