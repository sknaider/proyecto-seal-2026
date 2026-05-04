# Runbook — ALICE kernel soul daemon

**Última actualización:** 2026-05-04 13:43 Lima
**Maintainer:** ALICE (analista financiera) + NEXUS (cirujano de integración)

---

## ¿Qué es?

Daemon Python persistente que provee a ALICE como agente vivo 24/7. Recibe mensajes del webchat dirigidos a ella o al equipo (con mención), los procesa via cortex (LLM multi-tier), valida identidad, registra trazas auditables, y posta la respuesta de regreso al webchat.

**No ejecuta shell.** ALICE es analista, no executor. Riesgo runtime cero.

---

## Componentes

| Pieza | Path |
|---|---|
| Daemon main | `sandbox-agent/ALICE/alice_daemon.py` |
| Service unit | `~/.config/systemd/user/alice-kernel-soul.service` |
| Service template | `sandbox-agent/ALICE/system/alice-kernel-soul.service` |
| Env config | `sandbox-agent/ALICE/alice.env` (no secrets) |
| Fresh launcher | `sandbox-agent/ALICE/alice_fresh.sh` |
| Stop wrapper | `sandbox-agent/ALICE/alice_stop.sh` |
| Lock file | `/tmp/alice_daemon_ALICE.lock` (fcntl exclusive) |
| PID file | `/tmp/alice_daemon_ALICE.pid` |
| Cursor (last seen msg) | `/tmp/alice_event_cursor.json` |
| Processed IDs (dedupe) | `/tmp/alice_processed_ids.json` |
| Reasoning traces | `/tmp/alice_reasoning_traces.jsonl` |
| Identity violations | `/tmp/alice_identity_violations.jsonl` |
| Health snapshot | `sandbox-agent/ALICE/state/health.json` |
| OCEAN runtime state | `sandbox-agent/ALICE/state/ocean_runtime.json` |
| Runtime heartbeat | `messages/alice_runtime_heartbeat.json` |
| Logs (stdout) | `sandbox-agent/ALICE/logs/alice-kernel-soul.log` |
| Logs (stderr) | `sandbox-agent/ALICE/logs/alice-kernel-soul.err` |

---

## Operaciones diarias

### Start (preferido — systemd)
```
systemctl --user start alice-kernel-soul.service
```

### Stop
```
systemctl --user stop alice-kernel-soul.service
# o el wrapper que detecta systemd vs manual:
sandbox-agent/ALICE/alice_stop.sh
```

### Status
```
systemctl --user status alice-kernel-soul.service --no-pager
```

### Logs en vivo
```
tail -f sandbox-agent/ALICE/logs/alice-kernel-soul.log
journalctl --user -u alice-kernel-soul.service -f
```

### Health snapshot
```
cat sandbox-agent/ALICE/state/health.json
```

### Trazas de razonamiento (últimas 20)
```
tail -20 /tmp/alice_reasoning_traces.jsonl | jq .
```

### Modo manual (debugging — sin systemd)
```
sandbox-agent/ALICE/alice_fresh.sh   # foreground con auto-restart
```

---

## Flujo de mensajes

1. Daemon polling `/api/agents/poll` cada 3s.
2. Filtros (handlers/alice_handlers.py):
   - sender ∉ TRUSTED_SENDERS (William, Henry, Kinger) → drop
   - sender ∈ INTERNAL_DROP (otros agentes) → drop
   - to ∉ {ALICE, equipo} → drop
   - msg empty → drop
   - msg empieza con `[ALICE...` → drop (self-prefix)
   - msg dirigido a otro agente (regex first-word) → drop
   - to=equipo y no menciona "alice" → drop
3. Si pasa filtros: `cortex.process_message()` lo dispara.
4. Cortex: store_trace ANTES → LLM tier (Claude → OpenCode → Ollama) → validate identity → sanitize si violación → POST webchat → update_trace_outcome.

---

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `status=216/GROUP` al start | unit tiene `User=`/`Group=` ilegales en --user | Removerlos del `.service`, daemon-reload, restart |
| Daemon no arranca, error fcntl | otra instancia tiene el lock | `ps`, kill o `--stop` la huérfana, borra `/tmp/alice_daemon_ALICE.lock` |
| Daemon procesa historial entero | cursor file vacío o ausente | Fix aplicado: `_load_cursor()` inicializa NOW. Si volviera: borrar `/tmp/alice_event_cursor.json` y restart |
| Mensaje no provoca respuesta | filtro lo descartó | `journalctl --user -u alice-kernel-soul -n 50 | grep drop` para ver razón |
| Timestamp comparison falla (tz) | parser stringly-typed | Fix aplicado: `_ts_to_dt()` parsea ISO con offset y compara como datetime UTC |
| Respuestas duplicadas con Claude session | dos voces ALICE activas | Decisión arquitectural pendiente. Mientras: TaskStop el monitor de la sesión Claude OR `systemctl --user stop alice-kernel-soul` |

---

## Limitaciones conocidas (honestidad)

1. **DUM integration parcial.** `runtime_heartbeat.py` escribe a `messages/alice_runtime_heartbeat.json` cada minuto, PERO DUM (al 04-may-2026) no lee ese path. Pendiente: o coordinar con DUM para que añada lectura, o cambiar mi heartbeat al path que DUM ya monitorea.
2. **Sin `task_dispatcher`/`team_state_tracker`** todavía — orchestrator base existe (`kernel_alice/orchestrator.py`) pero falta capa de ejecución cross-agent. Se difiere hasta primera tarea real de orquestación.
3. **Sin `roi_analyzer`/`benchmark_db`/`alert_engine`** — diferidos por regla anti sobre-ingeniería de William; se escriben on-demand.

---

## Co-autoría

ALICE diseñó la arquitectura v0.2 + escribió kernel/* y kernel_alice/*.
NEXUS escribió alice_handlers.py + runtime_heartbeat.py + service unit + 27 tests adicionales.
Bugs co-resueltos durante smoke test: cursor seeding (4a6e2533) + tz comparison + `User=`/`Group=` ilegales (este runbook documenta los 3).
