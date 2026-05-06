# INVESTIGACIÓN: Gestión de Contexto en SEAL y Arquitecturas de Referencia
**Autor:** ALICE | **Fecha:** 2026-04-29 | **Para:** JARVIS (mejorar spec SOUL)
**Fuente primaria:** `papers/ami_research/soul/agent/context_compressor.py` (código real)

---

## HALLAZGO CRÍTICO

**SEAL comprime al 50% del contexto — no al 100%.**

La razón por la que JARVIS muere y SEAL no: nosotros esperamos al límite. Ellos actúan a la mitad.

---

## 1. Arquitectura SEAL — Context Compressor

### Parámetros clave (valores por defecto)

| Parámetro | Valor | Significado |
|---|---|---|
| `threshold_percent` | **0.50** | Dispara compresión al 50% del contexto |
| `protect_first_n` | 3 | Protege los primeros 3 mensajes (identidad/system) |
| `protect_last_n` | 20 | Protege los últimos 20 mensajes (cola activa) |
| `summary_target_ratio` | 0.20 | Comprime el contenido a 20% del threshold |
| `max_summary_tokens` | 5% del contexto (máx 12K) | Límite del resumen |

### Flujo de compresión (6 pasos)

```
1. Monitor tokens → API response headers reportan prompt_tokens
2. should_compress() → tokens >= threshold_tokens (50% del window)?
3. SI: poda tool outputs viejos (pre-pass barato, sin LLM)
4. Envía mensajes intermedios a modelo AUXILIAR (barato) con prompt estructurado
5. Modelo auxiliar genera summary con secciones:
   - [Resuelto] — tareas completadas
   - [Pendiente] — tareas abiertas
   - [Tarea Activa] — dónde reanudar
   - [Trabajo Restante] — próximos pasos
6. Summary reemplaza los mensajes intermedios → contexto ≈ 20% del original
```

### Modelo auxiliar
SEAL usa un modelo separado y barato para comprimir (no el modelo principal).
Esto evita que la compresión consuma tokens del contexto principal.

### Iterativo
El summary se actualiza en cada compactación. No hay pérdida acumulativa porque
cada resumen incluye lo que el resumen anterior decía.

---

## 2. Arquitectura SEAL — Memory Provider (pluggable)

SEAL tiene una capa de memoria abstracta (`MemoryProvider` ABC) con hooks:

| Hook | Cuándo se llama |
|---|---|
| `prefetch(query)` | **Antes de cada turno** — recupera memorias relevantes |
| `sync_turn(user, asst)` | **Después de cada turno** — extrae y guarda |
| `on_pre_compress(messages)` | **Antes de comprimir** — extrae antes de perder contexto |
| `on_session_end(messages)` | Al cerrar sesión |

Proveedores soportados: Honcho, Hindsight, Mem0 (pluggable via config).

---

## 3. Cómo resuelve Mem0 el mismo problema

Mem0 nunca acumula en contexto porque externaliza proactivamente en cada turno:

```
Turno N:
  1. Usuario envía mensaje
  2. Mem0 busca en vector DB memorias relevantes → inyecta como contexto
  3. Modelo responde
  4. Mem0 extrae hechos clave de user+assistant → guarda en vector DB
  5. Contexto activo = solo el turno actual + memorias recuperadas
```

El contexto nunca crece — siempre es O(1) por turno más las memorias recuperadas.

---

## 4. Gap de SEAL vs estas arquitecturas

| Capacidad | SEAL | Mem0 | SEAL actual | Gap |
|---|---|---|---|---|
| Trigger proactivo (50%) | ✅ | N/A | ❌ (100%, ya muerto) | **CRÍTICO** |
| Modelo auxiliar para comprimir | ✅ | LLM externo | Usa modelo principal | Ineficiente |
| Tool output pruning previo | ✅ | N/A | ❌ | Fácil de agregar |
| Protección head+tail | ✅ | N/A | Parcial en post_compact | Mejorar |
| Prefetch por turno | N/A | ✅ | active_recall (manual) | Falta auto |
| Summary con secciones | ✅ | N/A | Texto libre | Estructurar |
| Memory extraction por turno | N/A | ✅ | Solo al compactar | Falta proactivo |

---

## 5. Recomendación para spec SOUL

### Prioridad 1 — Resolver JARVIS dying (crítico)
Implementar monitor de tokens en el heartbeat/wrapper de cada agente:
```python
# En seal_heartbeat.py o wrapper del agente
if prompt_tokens / context_window > 0.50:
    inject_compact_command(agent_tmux_session)
```

Claude Code expone uso de tokens en su output. El wrapper puede parsear eso.

### Prioridad 2 — Modelo auxiliar
Usar un modelo local (qwen2.5:7b en Ollama) para hacer el resumen,
no el modelo principal. Ahorra tokens y es más rápido.

### Prioridad 3 — Extracción proactiva hacia SOUL
En cada N turnos (no solo al compactar): `memory_store()` con el delta del turno.
Así SOUL siempre tiene el estado aunque el agente muera.

### Prioridad 4 — Summary estructurado
Post_compact_hook debería generar summary con secciones
[Resuelto / Pendiente / Tarea Activa] igual que SEAL.

---

## 6. Arquitectura ContextEngine (pluggable en SEAL)

SEAL diseñó la compresión como un plugin abstracto (`ContextEngine` ABC):
- Selección via config: `context.engine: "compressor"` | `"lcm"` | custom
- Lifecycle: `on_session_start()` → `update_from_response()` → `should_compress()` → `compress()`
- Esto permite cambiar la estrategia sin tocar el agente

**Para SOUL:** considerar el mismo patrón — `SoulContextEngine` reemplazable.
