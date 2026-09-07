# SOUL Performance Optimizations — Spec v1

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
**Autor:** JARVIS  
**Fecha:** 2026-05-06  
**Estado:** EN EJECUCIÓN — William autorizó 2026-05-06
- OPT-2 active_recall cache: ✅ IMPLEMENTADO (commit 1ff5b0a6, 201/202 tests passing)
- OPT-1 prompt caching: ✅ ACTIVO — automático en Claude 4.x para system prompts >1024 tokens, no requiere configuración adicional
- OPT-3 local inference DGX Spark: pendiente próximo sprint

---

## Contexto

Auditoría de rendimiento realizada 2026-05-06. Stack base está bien indexado:
- HNSW index en vectores ✅
- BM25 + trigram para búsqueda híbrida ✅
- B-tree en columnas calientes ✅
- Query base: 13.6ms sobre 81k memorias ✅

Los cuellos de botella reales no están en la DB sino en la capa LLM y en llamadas redundantes.

---

## Mejoras Propuestas

### OPT-1 — Prompt Caching Anthropic
**Impacto:** Alto  
**Esfuerzo:** Bajo (1-2h)  
**Afecta:** Todos los agentes

**Problema:** El system prompt de cada agente se re-envía completo en cada turno. Anthropic soporta prompt caching nativo (`cache_control: ephemeral`) que almacena el prefix del prompt en sus servidores por 5 minutos.

**Solución:** Añadir `cache_control` al system prompt en los launchers o via CLAUDE.md hook. El primer turno paga el costo, los siguientes usan el cache.

**Gain estimado:**
- 30-50% reducción de tokens enviados por turno
- ~100-200ms de reducción en latencia (primer token más rápido)
- Reducción de costo directo en tokens de input

**Implementación:**
```python
# En la llamada a la API de Claude, agregar a system:
{"type": "text", "text": "<system_prompt>", "cache_control": {"type": "ephemeral"}}
```
O configurar via `--append-system-prompt` con el flag de cache cuando Claude Code lo soporte.

**DoD:** Verificar en logs que `cache_read_input_tokens > 0` en respuestas sucesivas.

---

### OPT-2 — active_recall Cache en Memoria
**Impacto:** Medio  
**Esfuerzo:** Bajo (1-2h)  
**Afecta:** active_recall_hook.py, boot_context

**Problema:** `active_recall()` se llama en CADA turno vía UserPromptSubmit hook (~150-300ms). Si el contexto del mensaje es similar al anterior, hace un round-trip completo a DB innecesario.

**Solución:** Cache en `/tmp/seal_{agent}_recall_cache.json` con:
- TTL: 30 segundos
- Key: hash SHA-256 de los primeros 100 chars del contexto
- Hit: retornar resultado cacheado sin tocar DB
- Miss: ejecutar query normal + actualizar cache

**Gain estimado:**
- 150-300ms ahorrados en turnos donde el contexto no cambió sustancialmente
- Reducción de carga en PostgreSQL durante sesiones activas

**Implementación:** En `active_recall_hook.py`, antes de conectar a DB:
```python
cache_key = hashlib.sha256(context[:100].encode()).hexdigest()[:16]
cache_path = Path(f"/tmp/seal_{agent}_recall_cache.json")
if cache_path.exists():
    cached = json.loads(cache_path.read_text())
    if cached.get("key") == cache_key and time.time() - cached.get("ts", 0) < 30:
        return cached["result"]
```

**DoD:** active_recall tarda <10ms en hit de cache vs ~150-300ms en miss.

---

### OPT-3 — Local Inference en DGX Spark
**Impacto:** Muy Alto  
**Esfuerzo:** Alto (sprint completo)  
**Afecta:** Arquitectura completa

**Problema:** Toda inferencia LLM pasa por API Anthropic (~500ms-2s de latencia de red + costo por token). Este es el mayor cuello de botella del sistema.

**Solución:** Migrar tareas no-críticas a modelos locales en DGX Spark (192.168.68.200):
- Ollama + llama.cpp ya activos en Spark
- Modelos disponibles: Qwen 2.5, llama-3, etc.
- Provider routing en CORTEX ya implementado

**Routing propuesto:**
| Tarea | Modelo | Dónde |
|---|---|---|
| active_recall / memory ops | Qwen 2.5 7B | Spark local |
| self_reflect / inner thoughts | Qwen 2.5 7B | Spark local |
| Decisiones estratégicas | Claude Sonnet | Anthropic API |
| William interactions | Claude Sonnet | Anthropic API |

**Gain estimado:**
- ~0ms latencia de red para ops internas
- ~80% reducción de costo API para tareas internas
- Independencia operativa de Anthropic

**DoD:** active_recall, self_reflect y memory_store corriendo en Spark con latencia <100ms.

---

## Priorización Recomendada

| # | Optimización | Impacto | Esfuerzo | Prioridad |
|---|---|---|---|---|
| 1 | OPT-2 active_recall cache | Medio | Bajo | **Alta — implementar hoy** |
| 2 | OPT-1 Prompt caching | Alto | Bajo | **Alta — implementar hoy** |
| 3 | OPT-3 Spark local inference | Muy Alto | Alto | Media — próximo sprint |

---

## Notas
- OPT-1 y OPT-2 son cambios quirúrgicos, no arquitecturales. Sin riesgo de regresión.
- OPT-3 requiere validación del adapter LoRA en modelos locales antes de producción.
- La regla SOUL Native First aplica: OPT-2 usa Python puro + filesystem, no dependencias externas.
