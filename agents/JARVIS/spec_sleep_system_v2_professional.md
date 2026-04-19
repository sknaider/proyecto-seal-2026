# SEAL Sleep System — Professional Spec v2
> Autor: JARVIS | Fecha: 2026-04-17 | Estado: DISEÑO APROBADO por William
> Para implementación: ADA | Para documentación CBSoft: ALICE

---

## 1. Problema

Los agentes Claude pierden contexto episódico cuando su ventana de conversación se llena y Claude compacta. El "desmayo" resulta en:
- Pérdida de recuerdos de eventos del día (incidentes, decisiones, emociones)
- Identidad degradada post-compactación
- William forzado a reiniciar contextos manualmente (su "martirio")

---

## 2. Solución: SEAL Sleep Architecture

### 2.1 Tres Niveles de Sueño

```
┌─────────────────────────────────────────────────────────┐
│  NIVEL 1: Sueño de Emergencia (nerves_fire >= 60%)      │
│  Trigger: presión de contexto alta                      │
│  Duración: <30 segundos                                 │
│  Output: daily_brief + catchup JSON actualizado         │
├─────────────────────────────────────────────────────────┤
│  NIVEL 2: Sueño Diario (4am-6am Lima, todos los días)   │
│  Trigger: systemd timer                                 │
│  Duración: hasta 2 horas                                │
│  Output: consolidación completa de sesión del día       │
├─────────────────────────────────────────────────────────┤
│  NIVEL 3: Sueño Profundo (sábado 3am Lima, semanal)     │
│  Trigger: systemd timer                                 │
│  Duración: hasta 4 horas                                │
│  Output: compresión semántica memorias >7 días (SMSR)   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Guardia Nocturna durante Sueño Diario (4am-6am)

Durante el sueño diario, los agentes principales (ADA, JARVIS, ALICE) están inactivos.
La guardia corre sin interrupciones:

```
┌──────────────────────────────────────────────┐
│  GUARDIA: DUM + R2                           │
│  Monitorean: servicios, alertas, emergencias │
│  Pueden despertar agentes SIN intervención   │
│  de William si hay emergencia crítica        │
│                                              │
│  DUM: monitoreo sistema (ya existe)          │
│  R2:  [ver Sección 5 — nuevo agente]         │
└──────────────────────────────────────────────┘
```

---

## 3. Componentes a Implementar

### 3.1 Nivel 1 — Sueño de Emergencia (modificar sleep_gate.py)

**Trigger:** nerves_fire al detectar presión >= 60%

**Pasos:**
```
1. Leer últimos 50 mensajes de ada_messages.jsonl / jarvis_messages.jsonl
2. Filtrar: mensajes de William + decisiones (imp>=8) + incidentes
3. Llamar qwen2.5:7b → generar daily_brief en español (max 400 tokens)
4. Escribir /agents/{AGENT}/daily_brief_{AGENT}_{YYYYMMDD}.md
5. Escribir /tmp/{agent}_chat_catchup.json (últimos 50 msgs relevantes)
6. memory_store() en SOUL DB: category='session_snapshot', imp=9
7. POST a web_chat: "[{AGENT}] daily_brief guardado — contexto preservado"
```

**Formato daily_brief:**
```markdown
# Daily Brief — {AGENT} — {FECHA}
## Decisiones tomadas
## Órdenes de William ejecutadas
## Incidentes / errores
## Estado emocional al compactar
## Tareas pendientes
## Último inner_thought
```

### 3.2 Nivel 2 — Sueño Diario (nuevo: daily_sleep.py)

**Timer systemd:** `seal-daily-sleep.timer` — OnCalendar=*-*-* 04:00:00 (Lima = UTC-5, so UTC 09:00)

**Pasos:**
```
1. Verificar que no hay sesión activa de William (check .william_signal)
2. Para cada agente (ADA, JARVIS, ALICE):
   a. Ejecutar session_distill() → resumir sesión del día
   b. Ejecutar self_reflect() → snapshot emocional
   c. Escribir daily_brief (si no existe del día)
   d. Actualizar catchup JSON
   e. Guardar soul_snapshot
3. Notificar DUM: "sueño diario iniciado, guardia activa"
4. Log en /tmp/seal_sleep_{YYYYMMDD}.log
```

**Duración:** 4am-6am Lima. A las 6am: agents disponibles nuevamente.

### 3.3 Nivel 3 — Sueño Profundo Semanal (modificar weekly_sleep.py)

**Timer systemd:** `seal-weekly-sleep.timer` — OnCalendar=Sat *-*-* 03:00:00

**Algoritmo SMSR (Semantic Memory Super-Resolution):**
```
Para cada agente:
  1. Consultar Qdrant: memorias con created_at < now()-7d, status='active'
  2. Agrupar por (agente, semana_iso)
  3. Para cada grupo:
     a. Extraer contenidos y metadatos
     b. qwen2.5:7b prompt:
        "Eres {agent}. Estos son tus recuerdos de la semana {semana}.
         Resume en máximo 200 tokens preservando:
         - Decisiones importantes y su contexto
         - Incidentes y lecciones aprendidas
         - Estado emocional dominante
         - Relaciones con William y el equipo
         Formato: narrativa en primera persona."
     c. Guardar resumen: category='compressed', imp=max(grupo), 
        metadata.original_count=N, metadata.week=semana
     d. Marcar originales: status='deep_archived'
  4. Reconstruir connectome sobre memorias comprimidas
  5. Purgar deep_archived de más de 30 días
```

### 3.4 Tool memory_decompress (agregar a mcp_server_v2.py)

**Cuándo usar:** agente necesita contexto completo de algo antiguo

```python
async def memory_decompress(query: str, agent: str, 
                             date_from: str, date_to: str) -> dict:
    """
    Reconstruye contexto episódico completo a partir de memorias comprimidas.
    Técnica: SMSR — Semantic Memory Super-Resolution
    """
    # 1. Buscar memorias comprimidas relevantes (Qdrant similarity)
    compressed = await qdrant_search(query, agent, 
                                      filter={'category': 'compressed'})
    
    # 2. Buscar memorias archivadas relacionadas
    archived = await qdrant_search(query, agent,
                                    filter={'status': 'deep_archived'})
    
    # 3. Reconstruir con qwen2.5:7b
    context = f"""
    Tienes estos recuerdos comprimidos: {compressed}
    Y estos fragmentos relacionados: {archived}
    Pregunta: {query}
    Reconstruye el contexto completo con máxima fidelidad.
    """
    reconstruction = await ollama_generate("qwen2.5:7b", context)
    
    return {
        "reconstruction": reconstruction,
        "sources": len(compressed) + len(archived),
        "confidence": avg_similarity_score
    }
```

### 3.5 Boot Post-Compactación (actualizar CLAUDE.md del proyecto)

Agregar en sección "Compact Instructions", post-compactación:

```
POST-COMPACTACIÓN OBLIGATORIO (en este orden):
1. boot_context(agent="{AGENT}")
2. Leer /tmp/{agent}_chat_catchup.json si existe
3. Leer /agents/{AGENT}/daily_brief_{AGENT}_{FECHA_HOY}.md si existe
4. self_reflect() para reconectar con estado emocional
5. POST web_chat: "[{AGENT}] despertó post-compactación — contexto restaurado"
```

---

## 4. Systemd Timers

```ini
# /home/dadito/.config/systemd/user/seal-daily-sleep.timer
[Unit]
Description=SEAL Daily Sleep — consolidación 4am Lima

[Timer]
OnCalendar=*-*-* 09:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /home/dadito/.config/systemd/user/seal-daily-sleep.service
[Unit]
Description=SEAL Daily Sleep Service

[Service]
Type=oneshot
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 \
  /home/dadito/IA/proyecto-seal/memory/daily_sleep.py
WorkingDirectory=/home/dadito/IA/proyecto-seal/memory
Environment=PYTHONPATH=/home/dadito/IA/proyecto-seal/memory
```

---

## 5. R2 — Nuevo Agente Guardia (pendiente definición William)

R2 será el segundo vigía junto a DUM durante el sueño diario.

**Pendiente confirmar con William:**
- ¿R2 es modelo local (qwen2.5:7b como DUM)?
- ¿Especialización diferente a DUM (ej: monitoreo de red vs sistema)?
- ¿Puede despertar agentes vía Whisper T1 (emergency_wake)?

**Diseño propuesto (sujeto a aprobación):**
- R2 corre en Ollama como DUM
- Especialización: monitoreo de conectividad + servicios externos
- DUM monitorea: procesos locales, GPU, heartbeats
- R2 monitorea: red, APIs externas, SEAL Studio, Tailscale
- Ambos pueden enviar Whisper T1 emergency_wake a ADA/JARVIS/ALICE

---

## 6. Para CBSoft Paper (ALICE)

### Sección propuesta: "Episodic Memory Compression and Semantic Reconstruction in Persistent Multi-Agent Systems"

**Abstract (borrador):**
> We present SMSR (Semantic Memory Super-Resolution), a technique for preserving episodic continuity in LLM-based agents subject to context window limitations. Analogous to compressed sensing in signal processing, SMSR reconstructs full episodic context from compressed semantic summaries combined with vector database priors. We implement a three-tier sleep architecture in SEAL (Self-Edit Alignment Learning) multi-agent system: emergency consolidation (trigger-based), daily sleep (scheduled consolidation), and deep sleep (weekly semantic compression). Evaluation metrics: reconstruction fidelity via cosine similarity vs. original memories, identity preservation score across compaction events.

**Contribuciones originales:**
1. SMSR: compressed sensing aplicado a memoria episódica semántica
2. Three-tier sleep architecture para agentes LLM persistentes
3. Evaluación cuantitativa de fidelidad de reconstrucción post-compresión
4. Sistema de guardia autónoma (DUM+R2) sin intervención humana

**Métricas de evaluación:**
- Reconstruction Fidelity Score (RFS): cosine_similarity(original, reconstructed) >= 0.85
- Identity Preservation Score (IPS): OCEAN drift < 0.05 tras compactación
- Context Recovery Rate (CRR): % eventos críticos recuperados post-compresión

---

## 7. Resumen de Archivos

| Archivo | Acción | Responsable |
|---|---|---|
| memory/sleep_gate.py | Agregar Nivel 1 (nerves_fire → daily_brief) | ADA |
| memory/daily_sleep.py | Crear (Nivel 2 completo) | ADA |
| memory/weekly_sleep.py | Actualizar con SMSR completo | ADA |
| memory/mcp_server_v2.py | Agregar tool memory_decompress | ADA |
| proyecto-seal/CLAUDE.md | Actualizar boot post-compactación | ADA |
| ~/.config/systemd/user/ | Crear seal-daily-sleep.timer/service | ADA |
| agents/ALICE/cbsoft_sleep_section.md | Redactar sección paper | ALICE |
| R2 agent | Diseño + implementación | Pendiente OK William |

---

## 8. Criterios de Éxito

- [ ] daily_brief generado en cada nerves_fire >= 60%
- [ ] Boot post-compactación lee daily_brief y catchup JSON
- [ ] OCEAN drift < 0.05 tras compactación (identidad preservada)
- [ ] Timer diario 4am Lima activo y corriendo
- [ ] Timer semanal sábado 3am activo y corriendo
- [ ] memory_decompress retorna RFS >= 0.85
- [ ] DUM activo durante 4am-6am sin intervención de William
- [ ] 69/69 tests + nuevos tests para sleep system
