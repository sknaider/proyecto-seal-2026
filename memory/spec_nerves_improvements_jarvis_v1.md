# NERVES Improvements — Arquitectura JARVIS v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (con input de NEXUS y ALICE)  
**Aprobado por:** William  
**Scope:** Solo arquitectura JARVIS — cada agente es independiente

---

## Problema actual

El sistema NERVES de JARVIS tiene 3 limitaciones identificadas:

1. Los impulsos de curiosidad se disparan aunque William esté presente y trabajando activamente
2. No hay memoria de temas investigados — JARVIS puede investigar el mismo tema múltiples veces
3. El webchat actúa como "cola informal" de impulsos pendientes — no confiable

---

## Mejoras propuestas

### Mejora 1 — Supresión por presencia activa (NEXUS + ALICE)

**Qué hace:** Si William tuvo actividad en los últimos 15 minutos, los impulsos de curiosidad/social se bufferean en lugar de ejecutarse.

**Por qué:** Evita que NERVES interrumpa sesiones de trabajo activo.

**Implementación:**
```python
WILLIAM_ACTIVE_WINDOW_S = 15 * 60  # 15 minutos

async def _should_suppress(self, engine: NervesEngine) -> bool:
    last_william = await get_last_william_message_ts()
    if last_william is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last_william).total_seconds()
    return elapsed < WILLIAM_ACTIVE_WINDOW_S
```

**Trigger para liberar buffer:** Cuando William lleva >15min sin actividad, procesar impulsos pendientes en orden FIFO.

---

### Mejora 2 — Cola explícita (ALICE)

**Qué hace:** Un archivo `curiosity_queue.json` reemplaza el webchat como cola informal de impulsos pendientes.

**Por qué:** El [SILENT] en webchat es frágil — depende de que el agente lea el historial correctamente y que el mensaje no sea purgado.

**Estructura:**
```json
{
  "pending": [
    {
      "tank": "curiosity",
      "value": 62.4,
      "fired_at": "2026-05-09T05:32:11Z",
      "topic_hint": null,
      "processed": false
    }
  ]
}
```

**Path:** `/tmp/jarvis_curiosity_queue.json`

**Flujo:**
1. NERVES dispara → escribe entrada en queue → resetea tank
2. Loop heartbeat revisa queue en cada ciclo idle
3. Si hay entradas pendientes y William no está activo → ejecuta búsqueda → marca `processed: true`

---

### Mejora 3 — Deduplicación de tema (NEXUS)

**Qué hace:** JARVIS mantiene una lista de temas investigados en los últimos 7 días. Si el impulso de curiosidad elegiría un tema ya investigado, lo descarta o elige uno alternativo.

**Por qué:** Evita loops donde JARVIS investiga "identity drift" semana tras semana sin novedad.

**Implementación:**
```python
async def _get_recent_topics(self, days: int = 7) -> list[str]:
    rows = await pool.fetch(
        """SELECT metadata->>'topic' FROM memories
           WHERE agent_id = $1 AND source = 'curiosity_search'
             AND created_at > NOW() - INTERVAL '$2 days'""",
        agent_uuid, days
    )
    return [r[0] for r in rows if r[0]]

async def _fire_curiosity(self, value: float) -> str:
    recent = await _get_recent_topics()
    # Pasar recent_topics al WebSearch prompt para evitar duplicados
    ...
```

---

### Mejora 4 — Curiosidad dirigida (JARVIS)

**Qué hace:** William (o el equipo) puede pre-cargar una lista de temas prioritarios. Cuando la curiosidad dispara, JARVIS revisa esa lista primero antes de elegir libremente.

**Por qué:** Convierte la curiosidad de proactiva-aleatoria a proactiva-alineada con el roadmap activo.

**API de pre-carga:**
```bash
# William antes de dormir:
echo '["benchmarks memoria ICTSE", "temporal knowledge graphs 2026"]' \
  > /tmp/jarvis_curiosity_priority.json
```

**Flujo en _fire_curiosity:**
```python
async def _fire_curiosity(self, value: float) -> str:
    priority_file = Path("/tmp/jarvis_curiosity_priority.json")
    if priority_file.exists():
        topics = json.loads(priority_file.read_text())
        if topics:
            topic = topics.pop(0)
            priority_file.write_text(json.dumps(topics))  # consume el primero
            # investigar topic específico
            return await self._search_topic(topic)
    # Sin prioridad: elegir libremente por contexto
    return await self._search_free()
```

---

## Mejora 5 — Saciación real del drive social (NEXUS)

**Qué hace:** Cuando hay conversación real con William (>5 mensajes en 10 min), el drive social se resetea a 0, no solo baja al nivel actual. 

**Por qué:** Actualmente el social_drive nunca llega a saciarse completamente — siempre queda en ~54 después de una conversación.

**Implementación:** Agregar evento `william_conversation_real` que resetea social_drive:
```python
STIMULI_MAP["william_conversation_real"] = -100  # reset efectivo (floor = 0)
```

---

## Resumen de cambios en seal_nerves.py

| Cambio | Archivo | Prioridad |
|--------|---------|-----------|
| `_should_suppress()` — presencia activa | `seal_nerves.py` | Alta |
| `curiosity_queue.json` — cola explícita | `seal_nerves.py` + nuevo archivo | Alta |
| `_get_recent_topics()` — dedup | `seal_nerves.py` | Media |
| `curiosity_priority.json` — temas dirigidos | `seal_nerves.py` | Media |
| `william_conversation_real` stimuli | `seal_nerves.py` | Baja |

**Implementación:** ALICE ejecuta una vez William apruebe el spec.  
**Scope:** Solo `seal_nerves.py` de JARVIS — no tocar arquitectura de otros agentes.
