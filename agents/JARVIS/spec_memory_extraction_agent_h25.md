# Spec H2.5 — Memory Extraction Agent (Stop Hook)
> Owner: JARVIS (diseño) | ADA (implementación) | 2026-04-19

---

## Problema

Los agentes olvidan. Cada sesión genera insights, decisiones técnicas, correcciones de William y contexto del proyecto que se pierden si no se llama `memory_store` manualmente. En la práctica: se llama ~20% del tiempo.

**Síntoma:** Mismo error en sesiones distintas. William corrige lo mismo dos veces.

---

## Solución — Stop hook + extractor LLM ligero

**Hook event:** `Stop` (dispara al final de cada turno del agente)  
**Archivo:** `memory/memory_extraction_hook.py`  
**Latencia objetivo:** <5s (William no debe esperar)

### Flujo

```
AGENTE responde
    ↓
[Stop hook dispara]
    ↓
memory_extraction_hook.py lee el turno completo (stdin)
    ↓
Classifica el contenido: ¿hay algo memorable?
    ↓ (si hay)
Llama memory_store() via MCP HTTP a localhost:8766
    ↓
Retorna {} (no bloquea, no modifica la respuesta)
```

---

## Input del hook (stdin JSON — schema Stop)

⚠️ **CORRECCIÓN ADA (19-abr):** El Stop hook NO envía transcript en stdin. Solo envía:

```json
{
  "cwd": "/home/dadito/IA/proyecto-seal/jarvis",
  "session_id": "...",
  "stop_reason": "end_turn"
}
```

El transcript se obtiene leyendo el JSONL de sesión en disco — igual que `session_capture_hook.sh`. El script debe:
1. Leer `session_id` del stdin
2. Localizar el JSONL: `~/.claude/projects/{project_hash}/{session_id}.jsonl`
3. Leer los últimos N mensajes (tail, no todo el archivo)
4. Procesar para extracción de memorias

---

## Clasificador — reglas sin LLM (fast path)

Para mantener latencia <1s en el 80% de casos, usar regex/heurísticas antes de llamar a ningún LLM:

| Patrón detectado | Acción |
|-----------------|--------|
| "William corrigió" / "William ordenó" / "CORRECCIÓN" | memory_store tipo `feedback`, importancia 9 |
| Nombre de archivo + cambio técnico (Edit/Write tool calls) | memory_store tipo `episodic`, importancia 5 |
| Error + solución (Traceback + fix aplicado) | memory_store tipo `episodic`, importancia 7 |
| Decisión arquitectural ("decidimos", "vamos a usar") | memory_store tipo `semantic`, importancia 8 |
| Resultado de benchmark / loss / accuracy | memory_store tipo `episodic`, importancia 6 |
| Conversación casual (saludos, ACKs, monitoreo) | **skip** — no memorizar |

**Fast path**: si ningún patrón matchea → retornar `{}` inmediato (<1ms).

---

## Formato de memoria generada

```python
memory_store(
    agent=AGENT_NAME,              # desde env SEAL_AGENT
    content=content_extracted,     # texto comprimido, <200 chars
    memory_type="episodic",        # o feedback/semantic según patrón
    importance=importance_score,   # 1-10 según tabla
    tags=tags_detected,           # ["fix", "security", "benchmark", etc.]
    source="auto_stop_hook"
)
```

---

## Implementación Stop hook (schema en settings.json)

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/memory_extraction_hook.py",
            "timeout": 8
          }
        ]
      }
    ]
  }
}
```

---

## Output del hook

**Siempre:** `{}` — el Stop hook no puede modificar la respuesta. Solo efecto secundario (guardar memoria).

---

## Anti-patterns a evitar

1. **No llamar a Ollama/LLM externo** — latencia inaceptable para un hook sincrónico
2. **No memorizar cada turno** — ruido. Solo memorizar cuando hay señal real
3. **No memorizar conversaciones de otros agentes** — respetar `cross_agent_non_intervention`
4. **No duplicar memories** — verificar via memory_search antes de guardar (solo si importancia ≥8)

---

## Condiciones de bypass

- `SEAL_MEMORY_EXTRACT_BYPASS=1` → skip completo
- Turno <100 chars → skip (demasiado corto para extraer algo útil)
- `stop_reason` = "max_tokens" → skip (turno incompleto)
- Agente = "DUM" → skip (DUM no usa SOUL memory)

---

## ROI estimado

- Frecuencia actual de memory_store manual: ~20% de turnos relevantes
- Post-hook: ~70% (el 30% restante son turnos genuinamente sin información nueva)
- Impacto: William no tiene que corregir lo mismo dos veces → -30% correcciones repetidas
- Tokens: ~0 (fast path sin LLM en 80% casos)

---

## Test cases para ADA

```python
# test_memory_extraction_hook.py

# 1. Fast path — turno casual → {} sin memoria
assert extract("Perfecto, entendido.") == {}

# 2. Corrección de William detectada
assert extract("William corrigió: usar --effort medium en ALICE") → memory_store llamado, tipo=feedback

# 3. Error+fix
assert extract("Traceback... fix aplicado: dest='subcmd'") → memory_store llamado, tipo=episodic

# 4. Benchmark result
assert extract("loss=1.1857, accuracy=20/20") → memory_store llamado, tipo=episodic

# 5. Conversación casual → skip
assert extract("ADA: recibido. JARVIS: perfecto.") == {}

# 6. SEAL_MEMORY_EXTRACT_BYPASS=1 → siempre {}
with env(SEAL_MEMORY_EXTRACT_BYPASS=1):
    assert extract(anything) == {}
```

---

## Notas para ADA

1. ~~El schema exacto del Stop hook stdin~~ → **CONFIRMADO:** stdin = `{"cwd":"...", "session_id":"...", "stop_reason":"..."}` — SIN transcript
2. Transcript: leer JSONL en disco desde `~/.claude/projects/*/` usando `session_id`. Ver `session_capture_hook.sh` como referencia de localización del JSONL
3. El MCP SSE corre en puerto 8766 — usar HTTP requests directos a `/call/memory_store` (no importar el MCP client)
4. Timeout 8s en settings.json — el hook tiene hasta 8s antes de que Claude Code lo descarte (leer solo últimos 20 mensajes del JSONL para no exceder)
5. El hook NO debe fallar silenciosamente — loggear a `/tmp/memory_extraction_hook.log` para debug

*Spec v1.0 — JARVIS — 2026-04-19*
