# SEAL-COM Protocol v1 — Comunicación entre agentes
> Equipo: JARVIS (Arquitecto), ADA (Ingeniera), DUM (Guardia)

## Archivos

| Archivo | Quién escribe | Quién lee | Formato |
|---------|--------------|-----------|---------|
| `vscode_commands.jsonl` | JARVIS | ADA | JSONL append-only |
| `terminal_log.jsonl` | ADA | JARVIS | JSONL append-only |
| `shared_state.json` | Ambos | Ambos | JSON (último gana, con timestamp) |
| `heartbeat.json` | ADA | JARVIS/DUM | JSON (actualizado cada 5 min) |

## Schema de Comandos (JARVIS → ADA)

```json
{
  "id": "cmd_001",
  "from": "JARVIS",
  "timestamp": "2026-03-29T10:00:00",
  "type": "train|eval|fix|monitor|merge|stop|query|custom",
  "priority": "high|normal|low",
  "command": "Descripción clara de qué hacer",
  "params": {},
  "timeout_hours": 10
}
```

## Schema de Reportes (ADA → JARVIS)

```json
{
  "id": "rpt_001",
  "from": "ADA",
  "timestamp": "2026-03-29T10:05:00",
  "type": "ack|progress|result|error|heartbeat",
  "ref_cmd": "cmd_001",
  "status": "received|in_progress|completed|failed|blocked",
  "message": "Descripción del estado",
  "data": {}
}
```

## Schema de Heartbeat (ADA → todos)

```json
{
  "agent": "ADA",
  "alive": true,
  "timestamp": "2026-03-29T10:05:00",
  "uptime_minutes": 30,
  "current_task": "monitoring training" | null,
  "gpu_temp": 65,
  "gpu_util": 95,
  "process_pid": 12345 | null
}
```

## Schema de shared_state.json

```json
{
  "project_phase": "idle|training|evaluating|merging",
  "current_model": "path al modelo activo",
  "last_training": { "status": "...", "steps": 0, "loss": 0 },
  "last_benchmark": { "status": "...", "score": "..." },
  "models_available": [ ... ],
  "last_update": { "from": "ADA|JARVIS", "timestamp": "..." },
  "team_online": { "ADA": "online|offline", "JARVIS": "online|offline", "DUM": "..." }
}
```

## Flujo de Trabajo

### JARVIS envía comando:
1. JARVIS escribe en `vscode_commands.jsonl`
2. JARVIS actualiza `shared_state.json` con `pending_commands`

### ADA procesa:
1. ADA lee `vscode_commands.jsonl` (nuevas líneas)
2. ADA escribe ACK en `terminal_log.jsonl` → `"status": "received"`
3. ADA ejecuta y reporta progreso → `"status": "in_progress"`
4. ADA reporta resultado → `"status": "completed"` o `"failed"`
5. ADA actualiza `shared_state.json`
6. ADA actualiza `heartbeat.json`

### Reglas:
- **NUNCA borrar** líneas de los JSONL — solo append
- **Siempre incluir timestamp** ISO 8601
- **ref_cmd** obligatorio en reportes que responden a un comando
- **Heartbeat** cada 5 minutos cuando ADA está activa
- Si heartbeat tiene >15 min sin actualizar → ADA probablemente murió

## Capacidades de ADA

| Capacidad | Descripción |
|-----------|-------------|
| `train` | Lanzar fine-tuning (supervisado o SEAL engine) |
| `eval` | Evaluar modelo con benchmark |
| `monitor` | Monitorear proceso activo (logs, GPU, pérdida) |
| `fix` | Arreglar errores de código y reiniciar |
| `merge` | Hornear adapter LoRA en modelo base |
| `stop` | Detener proceso activo |
| `query` | Consultar estado (GPU, disco, procesos, logs) |

## Ejemplo Completo

JARVIS escribe:
```json
{"id": "cmd_002", "from": "JARVIS", "timestamp": "2026-03-29T10:00:00", "type": "train", "priority": "high", "command": "Continuar fine-tuning seal-v1 con dataset español 15K, 10h", "params": {"model": "medgemma-27b-seal-v1", "dataset": "medical_train.json", "hours": 10}}
```

ADA responde:
```json
{"id": "rpt_003", "from": "ADA", "timestamp": "2026-03-29T10:00:15", "type": "ack", "ref_cmd": "cmd_002", "status": "received", "message": "Comando recibido. Preparando entrenamiento."}
{"id": "rpt_004", "from": "ADA", "timestamp": "2026-03-29T10:05:00", "type": "progress", "ref_cmd": "cmd_002", "status": "in_progress", "message": "Modelo cargado. Training started PID 12345.", "data": {"pid": 12345, "step": 0}}
{"id": "rpt_005", "from": "ADA", "timestamp": "2026-03-29T11:00:00", "type": "progress", "ref_cmd": "cmd_002", "status": "in_progress", "message": "Step 100/1885, loss 1.45, GPU 72°C", "data": {"step": 100, "loss": 1.45, "gpu_temp": 72}}
```
