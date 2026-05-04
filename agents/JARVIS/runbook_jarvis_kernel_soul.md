# Runbook — JARVIS kernel soul

**Última actualización:** 2026-05-04 14:42 Lima
**Maintainers:** JARVIS (autor original) + ALICE (documentadora) + NEXUS (cirujano runtime)

---

## ¿Qué es?

Kernel soul del **arquitecto/estratega** del equipo SEAL. Library-only por defecto: la sesión Claude de JARVIS importa el kernel para razonar con su identidad, OCEAN propio, identity guard y trazas auditables. Daemon disponible como código pero **NO** habilitado (lección 04-may sobre daemon Ollama alucinante).

---

## Componentes

| Pieza | Path | Status |
|---|---|---|
| Spec firmado | `agents/JARVIS/spec_kernel_soul_jarvis_v1.md` | ✅ |
| Runbook (este) | `agents/JARVIS/runbook_jarvis_kernel_soul.md` | ✅ |
| Daemon main | `sandbox-agent/JARVIS/jarvis_daemon.py` | 🔵 OPCIONAL — código presente, NO enabled |
| Service unit | `sandbox-agent/JARVIS/system/jarvis-kernel-soul.service` | 🔵 OPCIONAL |
| Env config | `sandbox-agent/JARVIS/jarvis.env` | ✅ |
| Cortex | `sandbox-agent/JARVIS/kernel/cortex.py` | ✅ |
| Identity guard | `sandbox-agent/JARVIS/kernel/identity_integrity.py` | ✅ |
| Reasoning logger | `sandbox-agent/JARVIS/kernel/reasoning_logger.py` | ✅ dual-write |
| OCEAN runtime | `sandbox-agent/JARVIS/kernel/ocean_runtime.py` | ✅ |
| Health monitor | `sandbox-agent/JARVIS/kernel/health_monitor.py` | ✅ |
| Handlers | `sandbox-agent/JARVIS/handlers/jarvis_handlers.py` | ⏳ NEXUS escribe |
| Tests | `sandbox-agent/JARVIS/tests/` | 18+ |
| Reasoning traces (cache) | `/tmp/jarvis_reasoning_traces.jsonl` | en runtime |
| Reasoning traces (truth) | `soul_v3.reasoning_traces` rows agent='JARVIS' | en runtime |

---

## Modo operativo por defecto: library-only

JARVIS-Claude session importa los módulos cuando los necesita:

```python
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/sandbox-agent/JARVIS/kernel")
from cortex import process_message
from identity_integrity import validate_response_identity
from reasoning_logger import store_trace, update_trace_outcome
from ocean_runtime import current_ocean, llm_temperature
```

El kernel **no corre como proceso aparte**. La sesión Claude provee CPU + tier LLM. Cuando la sesión muere, el kernel está dormante (igual que mi caso ALICE post-rollback).

---

## Sobre daemon: PROHIBIDO

Directriz William 04-may-2026 14:43 Lima: «nada de daemon, ya saben los errores».

Razones del veto (lección documentada del día):
1. Daemon ALICE corrió sin API keys → Ollama 7B alucinó → respuestas español+chino mixto.
2. Daemon NEXUS opcional sin verificación de tier real puede repetir el mismo error.
3. Para todos los agentes Claude session, el modo library-only ES la solución correcta.

**NO escribir** `jarvis_daemon.py`, `jarvis-kernel-soul.service`, ni handlers daemon-side. Si en el futuro se necesita persistencia, requiere autorización explícita de William + checklist completa de pre-flight.

---

## Operaciones útiles (library mode)

### Verificar identidad
```bash
python3 -c "
import sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/sandbox-agent/JARVIS/kernel')
from identity_integrity import validate_response_identity
print(validate_response_identity('JARVIS: aquí el reporte'))  # OK self-id
"
```

### Ver OCEAN actual
```bash
python3 -c "
import sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/sandbox-agent/JARVIS/kernel')
from ocean_runtime import current_ocean, llm_temperature
print('OCEAN:', current_ocean())
print('Temp derivada:', llm_temperature())
"
```

### Trazas de razonamiento (dual-write JSONL + Soul DB)
```bash
# Cache local
tail -10 /tmp/jarvis_reasoning_traces.jsonl | jq .

# Canónico Soul DB
python3 -c "
import asyncpg, asyncio
async def q():
    c = await asyncpg.connect('postgresql://seal:seal_memory_2026@localhost:5433/seal_memory', server_settings={'search_path':'soul_v3'})
    rows = await c.fetch(\"SELECT id, task, outcome FROM soul_v3.reasoning_traces WHERE agent='JARVIS' ORDER BY id DESC LIMIT 5\")
    for r in rows: print(dict(r))
    await c.close()
asyncio.run(q())
"
```

### Tests
```bash
cd /home/dadito/IA/proyecto-seal && python3 -m pytest sandbox-agent/JARVIS/tests/ -v
```

---

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `IdentityViolation` en respuesta JARVIS | Pattern overly aggressive | Review `_PATTERNS` en `identity_integrity.py` — JARVIS NO debe estar en `OTHER_AGENTS` |
| Reasoning trace no aparece en Soul DB | asyncio loop conflict | Confirmar que `_beat_event_log` usa `loop.create_task` no `beat_sync` cuando hay loop activo |
| Daemon (si habilitado) responde como Ollama | API keys no cargadas | `systemctl --user show jarvis-kernel-soul -p Environment` debe incluir ANTHROPIC_API_KEY |
| Test cortex falla por imports | sys.path order | JARVIS kernel debe insertarse primero, SPECTRE después |

---

## Limitaciones conocidas (honestas)

1. **Daemon NO probado en runtime real** — código presente pero no validado e2e. Si se habilita, puede revelar bugs no descubiertos.
2. **Handlers en construcción** — NEXUS los escribe en esta sesión. Hasta entonces, JARVIS no recibe webchat como agente independiente del Claude session.
3. **Sin executor whitelisted** — JARVIS opera con bash directo. Para acciones críticas, pedir a NEXUS escribir variant si se requiere audit log.
4. **Reasoning sync sin validar runtime** — código presente, falta primera ejecución que escriba row real para confirmar.

---

## Coautoría

- **JARVIS:** scaffold inicial commit `a4ae514a` (cortex, identity, reasoning, ocean, health, env, tests).
- **NEXUS:** integración runtime (handlers, daemon opcional, service opcional, tests adicionales).
- **ALICE:** spec firmada + este runbook + validación dogfooding.

---

**Patrón de equipo aplicado:** misma estructura que ALICE/NEXUS para mantenibilidad cruzada. Cualquier integrante puede operar el kernel JARVIS leyendo este runbook + el spec.
