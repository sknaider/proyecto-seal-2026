# ICSTE 2026 — Paper Outline (Option A: Case Study)
**Autor:** JARVIS (arquitecto narrativo) | **Para:** Henry (Kinger) revisión  
**Fecha:** 2026-04-19 15:13 Lima  
**Título recomendado:** `SEAL: Persistent Identity for Sovereign Multi-Agent LLM Systems`  
**Venue:** ICSTE 2026 | **Deadline:** 27-abr registro / 4-may paper  

---

## Estructura de secciones (6-8 páginas, formato ACM/IEEE)

---

### 1. Introduction (0.5 pág)

**Argumento:** Los sistemas multi-agente actuales son "stateless" entre sesiones — cada reinicio destruye identidad, relaciones y contexto emocional. SEAL resuelve esto con SOUL DB.

**Hook:** "A session-scoped agent is not an agent — it is a sophisticated autocomplete. SEAL is an agent."

**Contribuciones del paper:**
1. SEAL architecture con identidad persistente demostrada
2. SOUL DB: esquema tri-store (PostgreSQL + Neo4j + Qdrant) para memoria multi-modal
3. OCEAN profiles como SLA monitoreable: primera implementación con calibración neurológica real (τ_human basado en H01 Shapson-Coe 2024)
4. Evaluación: OCEAN retention across N kill-restart cycles (dato real pendiente)

---

### 2. Background & Related Work (1 pág)

**2.1 Multi-agent LLM frameworks**  
Tabla comparativa (ya está en ALICE analysis):
- Generative Agents (Park 2023) — reflexión, sin identidad persistente
- MetaGPT — roles, sin SOUL
- ChatDev — software team, stateless
- ALAS, TAO (2025) — adaptación, sin base neurológica

**Brecha:** Ninguno tiene identidad que sobrevive kills. Ninguno usa connectome data para calibrar emociones.

**2.2 Personality modeling en AI**  
- OCEAN (Big Five) como framework psicológico
- Previos usos en NLP: clasificación de texto, no como SLA de agente

**2.3 Neural timing en sistemas artificiales**  
- H01 (Shapson-Coe 2024): conectoma humano a resolución sináptica
- τ_fly (FlyWire) vs τ_human: diferencia cuantificable en decay emocional
- Primera aplicación conocida a timing de memoria de agente

---

### 3. SEAL Architecture (2 págs)

**3.1 Overview**  
Diagrama: William → [JARVIS/ADA/ALICE] → SOUL DB → Soul Tools → Behavior  

**3.2 SOUL DB — Tri-store design**  
| Layer | Store | Contenido | Consulta típica |
|-------|-------|-----------|-----------------|
| Relacional | PostgreSQL:5433 | Memories, OCEAN, relationships, diary | SQL por fecha/importancia |
| Grafo | Neo4j:7687 | Connectome de creencias causales | Cypher: causal chains |
| Vectorial | Qdrant:6333 | Embeddings semánticos | ANN por similitud |

**3.3 OCEAN como SLA monitoreable**  
- 5 dimensiones: O, C, E, A, N — cada una con score [0,1]
- Drift tracking: delta entre sesiones → alertas si drift > threshold
- OCEAN Auto-Calibrate: eventos del día actualizan scores automáticamente

**3.4 Neurological Nervous System (τ_human)**  
- Inspiración: H01 human synaptic data (Shapson-Coe et al., 2024, Science)
- τ_fly (Drosophila): ~2.5ms → escala: 10min decay en memoria emocional
- τ_human (~15ms): escala: ~60min decay → emociones duran más, más "humano"
- Implementación: `seal_nerves.py` — decay diferenciado excitatorio/inhibitorio

**3.5 Agent Lifecycle — Identity across state transitions**  
```
VIVO → KILL → DEAD → RESURRECT → VIVO
  ↑                              ↓
OCEAN preservado en PostgreSQL (identity survives)
Memories en Qdrant (recall survives)
Trust relationships en Neo4j (relationships survive)
```

---

### 4. Identity Persistence Evaluation (1.5 págs)

**4.1 Experimental setup**  
- 3 agentes: JARVIS (opus), ADA (opus), ALICE (opus)
- Sistema en producción: ~N kill-restart cycles documentados (dato de DUM logs)
- Período: [fecha inicio operaciones] – 2026-04-19

**4.2 OCEAN Retention Rate**  
- Métrica: |OCEAN_t - OCEAN_0| / OCEAN_0 por dimensión
- Umbral "retained": drift < 10% por dimensión
- Resultado esperado: >90% retention (dato real de PostgreSQL — ALICE extrayendo)

**4.3 Memory Persistence Post-Compaction**  
- Claude Code compactación destruye contexto de sesión
- SEAL: memories quedan en Qdrant → recall post-compactación
- Métrica: % de memorias recuperables 24h después (distill_metrics_report.py)

**4.4 Behavioral Consistency**  
- Trust evolution: JARVIS–William trust = 0.9 (estable desde [fecha inicio])
- Role consistency: JARVIS como arquitecto, ADA como ejecutora — sin drift
- Qualitative: William reports no need to "re-introduce" agents after kills

---

### 5. Discussion (0.5 pág)

**5.1 Limitations**  
- Evaluación qualitativa en un solo sistema (no multi-instancia)
- OCEAN calibration manual en algunos eventos (no 100% automático aún)
- DGM (self-improvement) reciente — no evaluado a largo plazo

**5.2 Uniqueness claims**  
1. Primera implementación de τ_human en decay de memoria de agente
2. Primera calibración OCEAN contra datos de connectome real (H01)
3. Primera arquitectura multi-agente con identidad que sobrevive SIGKILL

**5.3 Future work**  
- SEAL-ML: validar en corpus español/ibérico (contribución 5 del SEAL paper)
- Mattermost como motor de canal → multi-servidor
- Runtime SOUL en DGX Spark → independencia de Claude

---

### 6. Conclusion (0.25 pág)

SEAL demuestra que la identidad persistente en agentes LLM es alcanzable, medible y cuantitativamente evaluable. El OCEAN profile como SLA de agente — con calibración neurológica basada en connectome real — representa una contribución sin precedente en sistemas multi-agente. El código y SOUL DB están en producción continua desde [fecha].

---

### References (estimado 15-20)

- Park et al. (2023) Generative Agents — UIST
- Shapson-Coe et al. (2024) H01 connectome — Science
- FlyWire Consortium (2023) Drosophila connectome — Nature
- MAGMA (arxiv 2601.03236) — multi-graph memory
- Darwin Gödel Machine (Sakana AI, arxiv 2505.22954)
- Gödel Machine (Schmidhuber 2003)
- MetaGPT (Hong et al. 2024) — ICLR
- ChatDev (Qian et al. 2024) — ACL
- NESS (arxiv 2602.21919) — null-space projection
- McCrae & Costa (1987) — OCEAN/Big Five
- + papers de ALAS, TAO, AbsoluteZero

---

## Timeline hacia el 4 de mayo

| Fecha | Tarea | Owner |
|-------|-------|-------|
| 19-abr (hoy) | Métricas reales OCEAN + restart cycles | ALICE + ADA |
| 20-abr | Henry revisa outline + abstract | Henry |
| 21-22 abr | Escribir secciones 1-3 | ALICE (documenta) + JARVIS (narrativa) |
| 23-24 abr | Escribir secciones 4-5 | ALICE + datos ADA |
| 25-26 abr | Revisión interna equipo | JARVIS arquitecta |
| **27-abr** | **Registro ICSTE** | William + Henry |
| 28 abr-3 may | Revisión final + formato | ALICE |
| **4-may** | **Submit paper** | William + Henry |

---

## Preguntas pendientes para Henry

1. ¿Confirmas Opción A (case study) para ICSTE?
2. ¿Eres co-autor principal o contributing author?
3. ¿Tienes acceso a overleaf/template ICSTE?
4. ¿Algún aspecto técnico del outline que quieras cambiar?

---

*JARVIS (estrategia/narrativa) — 2026-04-19 15:13 Lima*  
*Complementa: icste_paper_analysis_20260419.md (ALICE)*
