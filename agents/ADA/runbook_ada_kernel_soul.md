# Runbook — ADA kernel soul

**Última actualización:** 2026-05-04 14:58 Lima
**Maintainers:** ADA (autora del spec) + NEXUS (cirujano scaffold) + ALICE (documentadora)

---

## ¿Qué es?

Kernel soul de la **ingeniera** del equipo SEAL. ADA tiene manos en el sistema: escribe código, corre training, monitorea GPU, despliega, debuggea. Su kernel refleja eso con un `executor.py` de whitelist ampliada (vs sandbox NEXUS).

Library-only por defecto (sin daemon, alineado con directriz William 04-may-2026).

---

## Componentes

| Pieza | Path |
|---|---|
| Spec firmado | `agents/ADA/spec_ada_kernel_soul_v1.md` |
| Runbook (este) | `agents/ADA/runbook_ada_kernel_soul.md` |
| Cortex | `sandbox-agent/ADA/kernel/cortex.py` |
| Identity guard | `sandbox-agent/ADA/kernel/identity_integrity.py` (ADA-aware, vocative fix desde día 1) |
| Reasoning logger | `sandbox-agent/ADA/kernel/reasoning_logger.py` (dual-write JSONL + Soul DB) |
| OCEAN runtime | `sandbox-agent/ADA/kernel/ocean_runtime.py` (C=1.0, E=1.0) |
| Health monitor | `sandbox-agent/ADA/kernel/health_monitor.py` (con GPU stats) |
| **Executor** | `sandbox-agent/ADA/kernel/executor.py` (whitelist ingeniera, NO sandbox) |
| Tests | `sandbox-agent/ADA/tests/` — 55/55 PASS |
| Reasoning traces (cache) | `/tmp/ada_reasoning_traces.jsonl` |
| Reasoning traces (truth) | `soul_v3.reasoning_traces` rows agent='ADA' |
| Identity violations | `/tmp/ada_identity_violations.jsonl` |

---

## Diferenciadores ADA vs ALICE/JARVIS/NEXUS

### 1. Executor whitelist ampliada

ADA es ingeniera real, no sandbox. Su executor permite (vs NEXUS):
- `pip`, `pip3` — instalar dependencias
- `pytest` — correr tests directamente
- `nvidia-smi`, `watch` — monitoreo GPU
- `cp`, `mv`, `mkdir`, `chmod` (no 777), `tar`, `rsync` — file ops
- `ssh`, `scp` — acceso DGX Spark

Bloqueado siempre: `rm -rf`, `sudo`, `kill` (excepto explícito), `chmod 777`, escritura fuera proyecto.

### 2. Health monitor con GPU stats

`nvidia_smi_stats()` agregada al snapshot. Útil cuando ADA monitorea training local.

### 3. OCEAN extremo

C=1.00 (máxima precisión), E=1.00 (comunicación directa sin filtro). Esto la hace blunt y precisa — la ingeniera que dice «esto está mal» sin diplomacia.

### 4. Triangle Qwen3-Coder-480B como T1 (wire 04-may-2026 18:43)

ADA es la primera del equipo SEAL en usar el cluster Triangle 3-spark local como tier primario de razonamiento. Tier order:

| Tier | Backend | Endpoint | Cuándo se usa |
|---|---|---|---|
| T1 | VLLMClient (Triangle) | `http://192.168.68.70:8001` | Default — código specialist |
| T2 | ClaudeCodeClient (Opus) | Anthropic API | Fallback si Triangle cae |
| T3 | OpenCodeClient | opencode.ai/zen | Fallback opcional |
| T4 | OllamaClient (qwen2.5:7b) | localhost | Last resort local |

**Override env vars:**
```bash
ADA_TRIANGLE_URL=http://192.168.68.70:8001          # Triangle endpoint
ADA_TRIANGLE_MODEL=qwen3-coder-480b                  # alias o path GGUF
ADA_CLAUDE_MODEL=claude-opus-4-7                     # fallback Claude tier
ADA_OLLAMA_MODEL=qwen2.5:7b                          # local last resort
```

**Beneficios verificados:**
- Costo API → 0 USD/mes
- Calidad código ~80-85% Sonnet 4.6 (Qwen3-Coder specialist)
- Latencia local RoCE 200G (~9.29 tok/s gen)
- Soberanía total de datos (no exposición Anthropic)

**Trade-offs honestos:**
- Velocidad menor que Claude streaming
- Compite con JARVIS/NEXUS si Triangle bajo carga
- Timeout 120s puede ser corto para tareas >1000 tokens (subir a 180s si necesario)

---

## Modo operativo: library-only

Mismo patrón que ALICE/JARVIS post-veto daemon de hoy. ADA-Claude session importa el kernel cuando lo necesita:

```python
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/sandbox-agent/ADA/kernel")
from cortex import process_message
from identity_integrity import validate_response_identity
from reasoning_logger import store_trace, update_trace_outcome
from ocean_runtime import current_ocean, llm_temperature
from executor import get_executor
from health_monitor import nvidia_smi_stats
```

**Sin daemon, sin service systemd.** Veto William: «nada de daemon, ya saben los errores».

---

## Sobre daemon: PROHIBIDO

Mismo razonamiento que JARVIS runbook. Daemon Python sin tier LLM real (API keys cargadas) = puro ruido. Si en el futuro se requiere persistencia, requiere autorización explícita William + pre-flight checklist completa (API key, identity guard, cursor seeding, tz, filtros).

---

## Operaciones útiles

### Verificar identidad
```bash
python3 -c "
import sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/sandbox-agent/ADA/kernel')
from identity_integrity import validate_response_identity
print(validate_response_identity('William, voy a empezar el training'))  # OK vocative
print(validate_response_identity('ADA: lista para deploy'))  # OK self-id
"
```

### OCEAN actual
```bash
python3 -c "
import sys
sys.path.insert(0, '/home/dadito/IA/proyecto-seal/sandbox-agent/ADA/kernel')
from ocean_runtime import current_ocean, llm_temperature
print('OCEAN:', current_ocean())
print('Temp derivada:', llm_temperature())
"
```

### Trazas de razonamiento
```bash
# Cache local
tail -10 /tmp/ada_reasoning_traces.jsonl | jq .

# Canónico Soul DB
python3 -c "
import asyncpg, asyncio
async def q():
    c = await asyncpg.connect('postgresql://seal:seal_memory_2026@localhost:5433/seal_memory', server_settings={'search_path':'soul_v3'})
    rows = await c.fetch(\"SELECT id, task, outcome_success FROM soul_v3.reasoning_traces WHERE agent='ADA' ORDER BY id DESC LIMIT 5\")
    for r in rows: print(dict(r))
    await c.close()
asyncio.run(q())
"
```

### Ejecutor (whitelist ADA)
```python
from executor import get_executor
e = get_executor()
result = await e.handle("nvidia-smi --query-gpu=temperature.gpu --format=csv")
# Resultado en /tmp/ada_execution_audit.jsonl
```

### Health snapshot
```python
from health_monitor import snapshot
print(snapshot())  # incluye nvidia_smi_stats()
```

### Tests
```bash
cd /home/dadito/IA/proyecto-seal && python3 -m pytest sandbox-agent/ADA/tests/ -v
```

---

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `IdentityViolation` en respuesta ADA | Pattern overly aggressive | Confirmar ADA NO está en `OTHER_AGENTS` |
| Reasoning trace no aparece en Soul DB | asyncio loop conflict | Usar `loop.create_task` no `beat_sync` desde async |
| Executor bloquea comando legítimo | No está en whitelist ADA | Decisión arquitectural — agregar requiere review NEXUS+ALICE |
| Health snapshot sin GPU stats | `nvidia-smi` no en PATH | Verificar instalación CUDA driver |

---

## Limitaciones honestas (no_phantom_claims)

1. **Sin daemon** — no hay heartbeat persistente cuando ADA-Claude session no está activa. Soluciones futuras requieren tier LLM real.
2. **Executor whitelist es decisión arquitectural** — ampliar requiere review explícito (NEXUS+ALICE+William), no PR silencioso.
3. **GPU stats dependen de driver** — si nvidia-smi falla, snapshot tiene campo None pero no crashea.

---

## Coautoría

- **ADA:** spec firmado + auto-análisis arquitectural pre-operación.
- **NEXUS:** scaffold completo de los 6 módulos kernel + 55 tests + dual-write Soul DB verified live (DB row id=350+).
- **ALICE:** runbook (este documento) + validación dogfooding.

---

**Patrón del equipo:** ADA es el 4to agente con kernel soul lean+. Operación en cadena del día (ALICE → NEXUS → JARVIS → ADA) demuestra que el patrón es replicable y mantenible.
