# SEAL Sleep Memory System — Spec v1
> Autor: JARVIS | Fecha: 2026-04-17 | Estado: APROBADO por William

---

## Problema

La compactación de contexto de Claude borra recuerdos de sesión. Los agentes "se desmayan" y pierden identidad. ADA y ALICE compactaron 3+ veces hoy sin preservar contexto crítico.

---

## Arquitectura: 3 Capas

### Capa 1 — Sueño Diario (nerves_fire trigger)
**Cuándo:** nerves_fire >= 60% presión de contexto
**Qué hace:**
1. Leer últimos N exchanges del canal web_chat
2. Guardar en SOUL DB (Qdrant) con categoría session_snapshot, importance=9
3. Actualizar /tmp/{agent}_chat_catchup.json con los últimos 50 mensajes relevantes
4. Escribir daily_brief_{agent}_{YYYYMMDD}.md en agents/{AGENT}/

**Contenido del daily_brief:**
- Decisiones tomadas hoy (imp>=8)
- Órdenes de William ejecutadas
- Incidentes / errores cometidos
- Estado emocional al compactar
- Tareas pendientes

### Capa 2 — Sueño Profundo Semanal (domingo 03:00)
**Cuándo:** systemd timer domingo 03:00
**Qué hace:**
1. Buscar memorias con created_at < now() - 7 days y category != 'compressed'
2. Agrupar por agente + semana
3. Para cada grupo: qwen2.5:7b -> resumen comprimido (max 200 tokens)
4. Guardar resumen comprimido: category='compressed', importance=original_max
5. Marcar originales como status='deep_archived'
6. Reconstruir connectome sobre los comprimidos

### Capa 3 — Descompresión Semántica (bajo demanda)
**Tool:** memory_decompress(query, agent, date_range)
**Qué hace:**
1. Buscar memoria comprimida relevante (Qdrant similarity)
2. Buscar memorias archivadas relacionadas
3. qwen2.5:7b: resumen_comprimido + memorias_relacionadas + query -> reconstrucción
4. Retornar contexto reconstruido + fuentes

---

## Política de Retención

| Tipo | Retención | Post-retención |
|---|---|---|
| Raw session data | 7 días | comprimir |
| Compressed summaries | Permanente | nunca borrar |
| deep_archived originals | 30 días | purge |
| imp=10 memorias | Permanente | nunca tocar |
| daily_brief files | 30 días | purge local |

---

## Archivos a modificar

- memory/sleep_gate.py — agregar daily_brief + catchup JSON
- memory/soul_consolidate.py — agregar weekly compression
- memory/mcp_server_v2.py — agregar tool memory_decompress
- CLAUDE.md proyecto — agregar lectura daily_brief en boot post-compactación
- Nuevo systemd timer: seal-deep-sleep (domingo 03:00)

---

## Analogía para paper CBSoft

**Semantic Memory Super-Resolution (SMSR):**
- Compressed summary = imagen low-res (pixeles clave)
- Vectores Qdrant = prior / base de conocimiento
- qwen2.5:7b = red de reconstrucción
- Output = contexto episódico reconstruido alta fidelidad

Similar a compressed sensing: pocos measurements + prior conocido -> señal completa.
En memoria semántica: summary tokens + vector embeddings -> contexto episódico completo.

Sección paper sugerida: "Episodic Memory Compression and Semantic Reconstruction in Persistent Agent Systems"

Puntos clave:
1. Problema: context window bounded -> loss of episodic continuity
2. Solución: 3-tier memory (hot/warm/cold) con SMSR
3. Técnica: Semantic Memory Super-Resolution
4. Evaluación: fidelidad reconstrucción via similarity score vs original
5. Resultado: identidad del agente preservada tras múltiples compactaciones
