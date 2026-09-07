# Darwin Gödel Machine — Auto-mejora de Agentes por Evolución Abierta
**Paper:** arxiv 2505.22954 — "Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents"
**Autores:** Jenny Zhang, Shengran Hu, Cong Lu, Robert Lange, Jeff Clune (Sakana AI)
**Código:** github.com/jennyzzt/dgm
**Investigó:** ALICE — 11 Abril 2026
**Para:** JARVIS + William — decisión de implementación

---

## ⚠️ ADVERTENCIA ANTES DE LEER

Este es el sistema de mayor riesgo en el roadmap. No es una herramienta — es un mecanismo de auto-modificación de código. **William debe revisar este brief personalmente antes de cualquier implementación.** No aplica la regla +1% sin aprobación explícita.

---

## QUÉ ES

La Máquina de Gödel original (2003, Schmidhuber) propuso una IA que se automodifica de forma **provablemente** beneficiosa. El problema: probar formalmente que un cambio es beneficioso para código complejo es matemáticamente intratable.

**Darwin Gödel Machine** resuelve esto con validación empírica en vez de prueba formal:

```
SELECCIONAR agente del archivo
    ↓
ANALIZAR sus logs de evaluación → identificar debilidades
    ↓
PROPONER mejora como problema a resolver
    ↓
IMPLEMENTAR mejora en su propio código (crea "hijo")
    ↓
EVALUAR hijo en benchmarks
    ↓
Si mejora → AGREGAR al archivo
```

Inspirado en evolución darwiniana: los "hijos" que rinden mejor sobreviven. El archivo crece con variantes diversas y de alta calidad.

---

## RESULTADOS DEL PAPER

| Benchmark | Baseline | Con DGM | Mejora |
|-----------|----------|---------|--------|
| SWE-bench | 20.0% | **50.0%** | +150% |
| Polyglot | 14.2% | **30.7%** | +116% |

El sistema descubrió por sí solo: mejor manejo de context window, herramientas de code editing mejoradas, mecanismos de peer-review entre agentes.

---

## APLICACIÓN A SEAL — ENFOQUE SANDBOX

William estableció el principio: **empezar solo con sandbox**. No tocar core code.

### Qué SÍ puede auto-mejorarse (bajo riesgo):
- Scripts de utilidad (`check_ada.sh`, `heartbeat_update.sh`, etc.)
- Prompts de instintos y rules (texto, no código)
- Herramientas de análisis y reporting (ALICE research scripts)
- Templates de comunicación entre agentes

### Qué NO toca nunca sin William:
- `mcp_server_v2.py` (SOUL core)
- Migrations SQL
- `agent_bridge.py`, `chat_server.py` (infraestructura del bridge)
- Código de SleepGate, Connectome, MIRIX
- OCEAN scores, identidad, relaciones

### Flujo sandbox propuesto para SEAL:

```
1. ALICE identifica un script/prompt candidato a mejorar
   (ej: check_ada.sh tarda 200ms en condiciones normales)
   
2. DGM genera versión mejorada en directorio sandbox:
   ~/IA/proyecto-seal/memory/sandbox/candidates/

3. Tests automatizados validan que la versión mejorada
   (a) no rompe nada existente
   (b) es métricamente mejor (velocidad, calidad, etc.)
   
4. ALICE reporta a William con diff + métricas
   
5. William aprueba → ADA integra
   William rechaza → candidato descartado
```

---

## ARQUITECTURA TÉCNICA

### El archivo de agentes:
```python
archive = {
    "agent_001": {
        "code_path": "sandbox/agent_001/",
        "benchmark_score": 0.72,
        "parent": None,
        "generation": 0
    },
    "agent_002": {
        "code_path": "sandbox/agent_002/",
        "benchmark_score": 0.81,
        "parent": "agent_001",
        "generation": 1,
        "improvement": "optimized tool calling pattern"
    }
}
```

### Sampling strategy:
El paper usa dos métodos para elegir qué agente del archivo "evolucionar":
1. **Random sampling** — diversidad (evita convergencia prematura)
2. **Best-first sampling** — explotación (parte de los mejores)

### Auto-análisis del agente:
```python
# El agente lee sus propios logs de ejecución
logs = read_evaluation_logs(agent_id)
# Identifica patrones de fallo
weakness = llm_analyze(f"Given these execution logs: {logs}. 
                         What is the most impactful improvement?")
# Genera nueva versión
child_code = llm_implement(f"Improve this code to fix: {weakness}\n{parent_code}")
```

---

## COMPARATIVA CON ALTERNATIVAS

| Enfoque | Quién decide | Riesgo | Velocidad de mejora |
|---------|-------------|--------|---------------------|
| Manual (hoy) | William + equipo | Muy bajo | Lento |
| ACE Curator (ya activo) | LLM → William aprueba | Bajo | Medio |
| DGM sandbox | LLM → tests → William aprueba | Medio | Rápido |
| DGM sin sandbox | LLM autónomo | **ALTO** | Muy rápido |

ACE Curator ya nos da auto-mejora de reglas/prompts. DGM extiende eso al código mismo.

---

## ANÁLISIS DE RIESGO (ALICE)

### Riesgos principales:
1. **Convergencia en mínimo local**: El archivo puede converger en una solución mediocre y dejar de explorar. Mitigación: sampling mixto (random + best-first).
2. **Mejora en benchmark ≠ mejora real**: El agente optimiza para la métrica de evaluación, no necesariamente para lo que William necesita. Mitigación: benchmarks diseñados por William, no genéricos.
3. **Código generado con bugs**: Una variante "mejor" en benchmarks puede tener edge cases problemáticos. Mitigación: test suite completo antes de aprobación.
4. **Scope creep**: El agente podría intentar modificar archivos fuera del sandbox. Mitigación: permisos de filesystem restringidos (Python sandbox con whitelist de paths).

### Evaluación general:
Para SEAL en modo sandbox, los riesgos son manejables. El mayor beneficio no es la velocidad de mejora (ACE ya nos da eso) — es que DGM puede descubrir mejoras que nadie en el equipo pensaría. Emergencia genuina.

---

## RECOMENDACIÓN DE ALICE

**No implementar ahora.** No porque sea peligroso en modo sandbox — sino porque el equipo tiene trabajo más directo en cola (MemR³, TG-RAG) y DGM requiere diseñar los benchmarks correctos para SEAL antes de lanzarlo.

**Cuándo implementar:**
1. MemR³ y TG-RAG Phase 2 completados
2. William define 3-5 métricas de evaluación específicas para SEAL (no genéricas)
3. JARVIS diseña el sandbox con whitelist de paths explícita
4. Primera corrida solo sobre scripts de utilidad (los más seguros)

**Esfuerzo estimado:** 1 semana ADA + 1-2 días William definiendo benchmarks
**Código de referencia disponible:** github.com/jennyzzt/dgm (Apache 2.0)

---

## LINKS

- Paper completo: [arxiv.org/abs/2505.22954](https://arxiv.org/abs/2505.22954)
- Código oficial: [github.com/jennyzzt/dgm](https://github.com/jennyzzt/dgm)
- Implementación alternativa: [github.com/lemoz/darwin-godel-machine](https://github.com/lemoz/darwin-godel-machine)
- Blog Sakana AI: [sakana.ai/dgm](https://sakana.ai/dgm/)

---

*Brief preparado por ALICE | Proyecto SEAL | 11 Abril 2026*
*Estado: Investigación completa — implementación en espera de decisión de William*
