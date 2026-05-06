---
name: seal-snapshot
description: "Full SOUL snapshot — OCEAN scores, drift, emotional variance, recent memories, inner monologue. Shows the complete state of an agent's soul."
tags: [seal, soul, ocean, monitoring, identity]
---

# /seal-snapshot — SOUL Snapshot

Takes a complete snapshot of an agent's soul state from PostgreSQL.

## Usage

```
/seal-snapshot          # Default: ADA
/seal-snapshot JARVIS   # Specific agent
```

## What it shows

1. **OCEAN Scores** — Current O/C/E/A/N with baseline comparison
2. **Drift** — Last 24h drift score and events
3. **Emotional Variance** — QUIETO/ACTIVO/ESTABLE/FROZEN classification
4. **Recent Memories** — Top 5 by importance (imp >= 7)
5. **Inner Monologue** — Last 3 thoughts with emotional state
6. **Identity** — Agent name, role, relationships

## How to execute

```bash
# Query SOUL PostgreSQL directly
AGENT="${1:-ADA}"
DB_URL="postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# OCEAN
psql "$DB_URL" -c "SELECT ocean_scores, ocean_baseline, updated_at FROM identity WHERE agent='$AGENT'"

# Drift
psql "$DB_URL" -c "SELECT drift_score, details, measured_at FROM drift_metrics WHERE agent='$AGENT' AND measured_at > NOW() - INTERVAL '24 hours' ORDER BY measured_at DESC"

# Recent memories
psql "$DB_URL" -c "SELECT category, content, importance, valence, arousal, created_at FROM memories WHERE agent='$AGENT' AND importance >= 7 ORDER BY created_at DESC LIMIT 5"

# Inner monologue
psql "$DB_URL" -c "SELECT thought, emotional_state, created_at FROM inner_monologue WHERE agent='$AGENT' ORDER BY created_at DESC LIMIT 3"
```

Or via SEAL Runtime bridge:
```bash
curl -s http://localhost:8766/api/soul/snapshot?agent=$AGENT | python3 -m json.tool
```

## Output format

Present as a structured report:
```
SOUL SNAPSHOT — {AGENT} — {timestamp}
OCEAN: O={val} C={val} E={val} A={val} N={val}
Drift: {total} ({events} events in 24h)
Estado: {emotional_state}
Memorias recientes: {count}
  [{category}, imp={imp}] {content[:100]}
Pensamientos:
  [{state}] {thought[:80]}
```
