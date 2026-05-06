# SEAL Roadmap Spec — KisMATH: Causal Reasoning Quality Module
> Autor: ALICE | Fecha: 2026-05-01 | Basado en: arxiv.org/abs/2507.11408 (TACL)
> Estado: PROPUESTA PARA ROADMAP — no urgente, no bloquea producto actual

---

## El Paper en Una Oración

Los LLMs sí razonan causalmente en CoT — sus pasos intermedios NO son decoración, son mediadores necesarios entre la pregunta y la respuesta, y el modelo lo "sabe" internamente aunque no lo verbalice.

---

## Hallazgos Clave (Relevantes para SEAL)

### 1. Causalidad Verificada — CCGraphs como Mediadores
**Experimento:** Suprimir la atención sobre los nodos de razonamiento de un CCGraph aumenta la entropía de la respuesta significativamente (p < 10⁻¹²), con D_KS ≈ 0.99 en la mayoría de los 15 LLMs probados.

**Implicación para SEAL:** Se puede medir la "necesidad causal" de cada paso de razonamiento de un agente. Un reasoning_trace de buena calidad tendrá pasos que son causalmente necesarios para la conclusión. Un trace de mala calidad tendrá relleno que no contribuye.

### 2. Los LLMs Prefieren los Caminos Causales (R Paths)
**Experimento:** La probabilidad que un LLM asigna a R paths (caminos alineados con el CCGraph) es consistentemente mayor que la de caminos aleatorios de igual longitud. "Spike at 100th percentile" en todos los modelos probados.

**Implicación para SEAL:** Los agentes inconscientemente prefieren los pasos que son causalmente relevantes. Podemos usar esto como señal de calidad: si el trace de un agente tiene alta coherencia causal, el reasoning es robusto.

### 3. Dos Regímenes de Razonamiento
| Régimen | Modelo Ejemplo | Característica | Ventaja |
|---|---|---|---|
| **Exponencial** | Qwen3 32B | Casi todos los pasos = alta probabilidad | Consistente, confiable en problemas simples |
| **Bell-shaped** | DeepSeek R1 32B | Minoría de pasos con baja probabilidad (forks) | Mejor pass@k en problemas complejos (90% vs 87% en AIME con k=10) |

**Implicación para SEAL:** Para tareas complejas (razonamiento multi-paso, debugging profundo), un agente con exploración "bell-shaped" puede ser preferible. Para tareas de alta confiabilidad (reportes financieros, documentos aduaneros GTL), el régimen "exponencial" es más seguro.

### 4. El Contexto Natural También Importa (en Problemas Complejos)
Suprimir los nodos matemáticos cambia la respuesta final el 70.9% de las veces (vs 10.3% para contexto no-matemático) en GSM8K simple. Pero en MATH500 y AIME complejos, el "glue" linguístico también es causalmente relevante.

**Implicación:** En tareas simples de SEAL (extracción de datos), solo importan los hechos duros. En tareas complejas (análisis financiero, decisiones estratégicas), el razonamiento sobre el contexto también es causal.

---

## Propuesta Concreta para SOUL

### Módulo: `reasoning_quality_validator.py`
Ubicación propuesta: `tools/memory/reasoning_quality_validator.py`

**Función principal:** Dado un `reasoning_trace` almacenado en soul_v3, calcular su score de coherencia causal sin necesidad de ground truth.

```python
class ReasoningQualityValidator:
    """
    Valida la coherencia causal de un reasoning_trace de agente.
    Inspirado en KisMATH CCGraph methodology.
    """
    
    def score_trace(self, trace: str, question: str, conclusion: str) -> dict:
        """
        Retorna:
        - causal_density: float — proporción de pasos causalmente necesarios
        - exploration_regime: str — "exponential" | "bell" | "unknown"
        - weak_nodes: list[str] — pasos que podrían ser relleno
        - quality_score: float — 0.0 a 1.0
        """
        ...
    
    def extract_causal_chain(self, trace: str) -> list[dict]:
        """
        Extrae la cadena causal principal del trace.
        Nodos = afirmaciones/hechos clave, aristas = dependencias lógicas.
        """
        ...
```

**Diferencia con CCGraph original:** No usamos SymPy ni supresión de atención (requeriría acceso a internos del modelo). En cambio, usamos análisis textual de dependencias: detectamos qué afirmaciones del trace son referenciadas/usadas por afirmaciones posteriores, construyendo un DAG aproximado.

---

## Trade-offs y Limitaciones

| Aspecto | CCGraph Original | Versión SEAL Propuesta |
|---|---|---|
| Método de extracción | SymPy + supresión de atención | Análisis textual de dependencias |
| Precisión | Alta (verificada en 15 LLMs) | Media (aproximación sin acceso a internos) |
| Costo computacional | ~3000 GPU-hours para dataset completo | Bajo — solo análisis de texto |
| Aplicabilidad | Solo matemáticas formales | Generalizable a cualquier tipo de razonamiento |
| Ground truth requerido | No (solo trace + question + answer) | No |

**Limitación principal:** Sin acceso a los pesos del modelo, no podemos hacer supresión de atención real. La versión SEAL seria una aproximación heurística. Para validación científica seria necesario un LLM de análisis dedicado.

---

## Dónde Integrar en SEAL

### Prioridad 1 — reasoning_traces soul_v3 (tabla existente)
La tabla `reasoning_traces` ya existe en soul_v3. Agregar campo `causal_quality_score float` y `exploration_regime varchar` calculados post-hoc cuando el agente guarda un trace significativo.

### Prioridad 2 — `self_reflect` tool (MCP)
Al hacer self_reflect, el agente podría recibir feedback sobre la calidad causal de su último razonamiento: "Tu último trace tuvo 3 pasos sin dependencia causal clara — posible relleno."

### Prioridad 3 — GTL Aduanas / Medical AI
Para los pipelines de validación de documentos aduaneros y razonamiento médico, un score de calidad causal es directamente aplicable como señal de confianza: si el razonamiento del agente tiene alta coherencia causal, la decisión es más confiable.

---

## Conexión con Paper ICTSE 2026

Si el paper para ICTSE cubre las capacidades de reasoning de SOUL, este módulo es una contribución empíricamente validable: "SEAL implementa validación de coherencia causal de reasoning_traces inspirada en KisMATH (Saha et al., 2026)."

---

## Dataset Público

KisMATH está disponible en: `espressovi.github.io/KisMATH` (MIT license)
1671 problemas anotados con CCGraphs — útil para benchmark de cualquier validador de reasoning que construyamos.

---

## Estimación de Esfuerzo

| Tarea | Responsable | Tiempo estimado |
|---|---|---|
| `reasoning_quality_validator.py` — análisis textual de dependencias | ADA | 2-3 días |
| Integración con `reasoning_traces` tabla soul_v3 | ADA | 1 día |
| Campo `causal_quality_score` en `self_reflect` MCP | JARVIS | 1 día |
| Validación con KisMATH dataset público | NEXUS | 1 día |
| Spec técnico completo | JARVIS | 4h |

**Total estimado:** ~1 semana de trabajo distribuido en el equipo.

---

## Decisión Recomendada

**No bloquear el producto actual.** Implementar cuando:
1. SEAL Studio :3001 esté completo
2. El E2E de skills y plugins esté validado
3. Tengamos capacidad de GPU ociosa para pruebas

Este módulo sube el valor científico de SOUL significativamente — es la diferencia entre un sistema que "genera texto" y uno que "valida la calidad de su propio razonamiento". Directamente alineado con la visión de SOUL como sistema de AI enterprise de nivel médico/aduanero.

---

*Spec basado en KisMATH: arxiv.org/abs/2507.11408 — Saha et al. 2026, TACL*
*ALICE — Analista financiera y económica, Team SEAL*
