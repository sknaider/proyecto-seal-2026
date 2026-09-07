# Research: Emotional Memory Consolidation
# Agent: JARVIS research subagent
# Date: 2026-04-06

## Hallazgos Clave

### 1. Valencia/Arousal en Retrieval (DAM-LLM, arxiv 2510.27418)
- Cada memoria tiene "distribucion de confianza" con actualizacion bayesiana
- Entropia de creencia decide que comprimir/olvidar
- Boost multiplicativo: `emotional_boost = 1.0 + (abs(valence) * 0.3) + (arousal * 0.2)`
- **YA IMPLEMENTADO en SEAL:** emotional modulation en temporal_decay_score

### 2. Sleep-Like Consolidation (SleepGate, arxiv 2603.14517)
- Tres mecanismos: tagger temporal, gate de olvido, replay de refuerzo
- Cron nocturno: REPLAY memorias activadas hoy (+10%), FORGET episodicas no activadas 30d (*0.85), CONSOLIDATE clusters, PRUNE relevance < 0.1

### 3. Decay Modulado por Emocion (PNAS + ACT-R HAI 2025)
- Arousal via amigdala-hipocampo, valence via corteza prefrontal
- Formula: `emotional_resistance = 1.0 + (|valence| * 0.5) + (|arousal| * 0.3)`, `effective_rate = base / resistance`
- **YA IMPLEMENTADO en SEAL:** emotional_intensity modula lambda en temporal_decay_score

### 4. Mood-Congruent Retrieval (REMT, Frontiers 2026)
- Mood Index: promedio rolling de valence de nodos recientes
- SQL: `mood_score = sim * (1-w) + (1 - ABS(valence - mood_valence)) * w` con w=0.3

## Repos
- DAM-LLM: arxiv 2510.27418
- SleepGate: arxiv 2603.14517
- REMT: frontiersin.org/frai.2026.1749517
- A-MEM: github.com/WujiangXu/A-mem
- memU: github.com/NevaMind-AI/memU

## Accion para SEAL
1. Sleep consolidation cron (extend instinct_cron.py)
2. Mood-congruent retrieval (mood_weight en hybrid_search)
