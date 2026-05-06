---
auto_invoke: false
description: Auditoría profunda semanal del stack SEAL — código, infraestructura, deuda técnica, costos. Lidera ALICE, ejecuta JARVIS/NEXUS/ADA según hallazgos.
argument-hint: "[--scope full|code|infra|cost] [--week-of YYYY-MM-DD]"
---

# /seal-deep-audit — Auditoría Profunda Semanal SEAL

**Owner:** ALICE
**Cadencia recomendada:** cada 7 días (domingos a las 22:00 Lima sugerido)
**Output:** `/home/dadito/IA/proyecto-seal/agents/ALICE/audit_YYYYMMDD.md` + reporte chat

## Cuando invocar

- William dice `/seal-deep-audit` o "auditoría completa"
- Antes de un release o sprint nuevo
- Después de un incidente importante
- Cron semanal (opcional via NEXUS)

## Qué chequea

### 1. Salud Infraestructura
- PostgreSQL, Neo4j, Web Chat (intencionalmente excluir Qdrant — soul_lite=True)
- Runtime Bridge :8766
- Mattermost / Matrix bridge
- GPU temp + util + memoria
- Disk usage por partición + por subdirectorio top
- Procesos Claude activos por agente

### 2. Salud SOUL DB
- Conteo de memorias por agente
- Drift últimas 24h por agente
- Rules CRITICAL activas
- Inner monologue freshness

### 3. Auditoría de Código (3 niveles de evidencia)
🔴 **Bug confirmado** — repro disponible
🟡 **Sospecha** — patrón sospechoso, requiere runtime check
🔵 **Doc/comentario** — engañoso o desactualizado

Patrones a buscar:
- "backwards compatibility", "TODO", "FIXME", "HACK", "XXX"
- Magic numbers sin constante nombrada
- Imports duplicados
- Versiones desactualizadas en docstrings (v3 en archivo v4)
- Skills sin SKILL.md
- SKILL.md con CRLF (Windows line endings)
- Comentarios que prometen comportamiento diferente al código

### 4. Auditoría de Costos
- Tamaño total proyecto-seal
- Top 5 subdirectorios por tamaño
- Candidatos a archivo (sin acceso reciente, modelos no usados)
- Mensajes JSONL acumulados sin rotación
- Estimado API tokens/día por agente

### 5. Auditoría de Skills
- Total skills disponibles
- Skills SEAL custom funcionales (snapshot/audit/handoff/eval/train/runtime/deep-audit)
- Skills sin SKILL.md
- Frontmatter YAML mal formado
- Discoverability gap (skills usables pero no descubribles)

### 6. Pendientes
- Items de MEMORY.md sin avance >7 días
- Working state por agente
- Decisiones de William sin ejecutar

## Cómo ejecutar

```bash
# Fase 1: snapshot infra
/seal-audit  # reusa skill existente, output corto

# Fase 2: auditoría profunda código
DATE=$(date +%Y%m%d)
OUT=/home/dadito/IA/proyecto-seal/agents/ALICE/audit_${DATE}.md

# Fase 3: análisis ALICE genera markdown estructurado
# Fase 4: priorización en chat
# Fase 5: asignación de fixes a JARVIS/NEXUS/ADA
```

## Formato de salida (chat al equipo)

```
📊 SEAL DEEP AUDIT — semana YYYY-MM-DD

🔴 CRÍTICO ({n})
  • [bug confirmado] desc → owner: AGENT
🟡 IMPORTANTE ({n})
  • [sospecha] desc → owner: AGENT
🔵 LIMPIEZA ({n})
  • [doc/comentario] desc → owner: AGENT

💰 COSTO
  • disk: {used}% / candidatos: {list}
  • API estimado/día: ${cost}

📋 BACKLOG GENERADO: {n} items en /agents/ALICE/audit_{date}.md
```

## Reglas

- **Hechos no fantasías** (William 29-abr): cada hallazgo etiquetado por nivel de evidencia
- **No phantom claims**: no declarar bug sin reproducción
- **Documento persistente**: cada audit guarda md fechado, queda como historial
- **ALICE no ejecuta fixes** — solo identifica y prioriza. JARVIS/NEXUS/ADA arreglan.

## Status

- v1: 2026-05-06 — diseñado por ALICE post-auditoría inaugural (W aprobó Opción A)
- v1.1 pendiente: agregar comparación con audit anterior (delta de issues)
- v1.2 pendiente: integración con cron NEXUS para data automática diaria
