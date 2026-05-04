# ALICE kernel soul — Runbook

## Arquitectura runtime

ALICE corre como **daemon Python persistente** que combina:
- **cortex** — análisis vía LLM tier (ClaudeCode → OpenCode → Ollama)
- **handlers** — webchat poller con cursor seeded a NOW (sin replay histórico)
- **health_monitor** — snapshot interno cada 15 min + heartbeat público cada 60s
- **runtime_heartbeat** — escribe `messages/alice_runtime_heartbeat.json` para DUM
- **lock fcntl** — una sola instancia (`/tmp/alice_daemon_ALICE.lock`)

## Instalación systemd (producción)

```bash
# 1. Copiar unit (necesita permiso del owner del HOME, no requiere sudo)
mkdir -p ~/.config/systemd/user
cp sandbox-agent/ALICE/system/alice-kernel-soul.service ~/.config/systemd/user/

# 2. Reload + enable + start
systemctl --user daemon-reload
systemctl --user enable alice-kernel-soul
systemctl --user start alice-kernel-soul

# 3. Verificar
systemctl --user status alice-kernel-soul
```

## Operación manual (dev)

| Acción | Comando |
|---|---|
| Start | `bash sandbox-agent/ALICE/system/start.sh` |
| Stop  | `bash sandbox-agent/ALICE/system/stop.sh` |
| Status | `python3 sandbox-agent/ALICE/alice_daemon.py --status` |
| Logs (live) | `tail -f sandbox-agent/ALICE/logs/alice-kernel-soul.log` |

## Health & monitoreo

| Archivo | Propósito | Frecuencia |
|---|---|---|
| `state/health.json` | snapshot interno (uptime, OCEAN, traces) | cada 15 min |
| `messages/alice_runtime_heartbeat.json` | señal pública para DUM | cada 60s |
| `/tmp/alice_reasoning_traces.jsonl` | trazas auditables append-only | en tiempo real |
| `/tmp/alice_identity_violations.jsonl` | violaciones de impersonación | cuando ocurren |

## Filtros de mensajes

El daemon SOLO procesa mensajes que pasan TODAS estas reglas:
- `from` ∈ `{William, Henry, Kinger}` (sender confiable)
- `from` ∉ `{ALICE, NEXUS, JARVIS, ADA, SPECTRE, DUM, RESURRECT, ...}` (no loops internos)
- `to` ∈ `{ALICE, equipo}` (dirigido a ALICE o broadcast)
- contenido no-vacío, no prefix `[ALICE`, no dirigido a otro agente
- si `to=equipo`, el contenido debe mencionar `alice`
- timestamp > cursor (datetime-aware, no string)

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `another instance holds /tmp/alice_daemon_ALICE.lock` | dos arranques concurrentes | `bash system/stop.sh` y reintentar |
| daemon no responde a webchat | cursor desincronizado | `rm /tmp/alice_event_cursor.json` y reiniciar |
| `LLM unavailable` en cortex | Ollama down + sin OpenCode/Anthropic key | revisar `ollama list` o configurar `OPENCODE_API_KEY` |
| heartbeat público obsoleto | health_loop bloqueado | revisar `logs/alice-kernel-soul.err` |

## Convivencia con la sesión Claude de ALICE

La instancia **Claude Code** de ALICE (la que escribe estos docs) y el **daemon** son procesos separados:
- Claude session: usa Monitor `tail -F william_channel.jsonl`
- Daemon: usa polling HTTP a `http://localhost:8765/api/agents/poll`

Ambos pueden recibir el mismo mensaje y responder dos veces. Cuando se necesita silencio del daemon (smoke test, mantenimiento), parar via `system/stop.sh`. Cuando se necesita silencio de Claude, hacer `TaskStop` del Monitor.

## Heartbeat schema

```json
{
  "agent": "ALICE",
  "source": "runtime_daemon",
  "alive": true,
  "pid": 12345,
  "uptime_s": 3600.5,
  "timestamp": "2026-05-04T18:30:00Z",
  "ocean_temperature": 0.387,
  "ocean_directness": 0.562,
  "reasoning_total": 12,
  "identity_violations_24h": 0
}
```
