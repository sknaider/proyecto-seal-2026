# SPEC — SEAL Event Bus Daemon (F7 GAP 5)

Co-diseño NEXUS + ADA — 24 abril 2026, 13:30 Lima.
Status: spec v1 para aprobación de William.

---

## Objetivo

Promover el EventBus validado E2E en sandbox (`sandbox-agent/event_bus.py`)
a servicio persistente del equipo real. PostgreSQL LISTEN/NOTIFY como
transporte real-time; cero polling; event_log persistente para replay.

---

## Decisiones acordadas

### 1. Deployment: systemd --user service
No hay debate: LISTEN/NOTIFY requiere conexión persistente. Cron haría
polling y destruiría el modelo event-driven.

**Unit file** `/home/dadito/.config/systemd/user/seal-event-bus.service`:
```ini
[Unit]
Description=SEAL Event Bus Daemon (LISTEN/NOTIFY)
After=network.target postgresql.service seal-mcp-server.service
Wants=seal-mcp-server.service

[Service]
Type=simple
WorkingDirectory=/home/dadito/IA/proyecto-seal
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/event_bus_daemon.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/tmp/seal_event_bus.log
StandardError=append:/tmp/seal_event_bus.log

[Install]
WantedBy=default.target
```

### 2. Reconnect strategy

Wrap del loop de listening con retry + replay para no perder eventos:

```python
async def start_listening_with_retry(self):
    while self._running:
        try:
            await self._sub_connect()
            # Replay últimos 50 eventos post-reconexión (catch-up downtime)
            missed = await self.replay_recent(limit=50)
            for evt in missed:
                await self._dispatch_handlers(evt)
            await self.start_listening()  # blocking
        except (asyncpg.PostgresConnectionError, OSError) as ex:
            print(f"[EVENT BUS] Conexion perdida: {ex}. Retry en 5s...")
            await asyncio.sleep(5)
```

Trade-off: replay duplica eventos ya procesados si el handler no es
idempotente. Mitigación: handlers deben verificar ref_id + timestamp
antes de actuar (responsabilidad del handler, no del bus).

### 3. Eventos iniciales (6)

| event_type               | DB mapping   | Trigger                                      |
|--------------------------|--------------|----------------------------------------------|
| procedure_failed         | error        | procedure_record_outcome 3 fallos consecutivos|
| checkpoint_restored      | milestone    | working_state_get devuelve task al boot      |
| belief_contradicted      | status       | reasoning_trace_update outcome_success=False |
| agent_status_change      | heartbeat    | RESURRECT detecta agente down/up             |
| memory_stored_critical   | milestone    | memory_store con importance>=9 (ADA nuevo)   |
| task_scope_alert         | status       | Forage V2 denominator blindness (ADA nuevo)  |

### 4. Handlers por agente

**Location**: `/home/dadito/IA/proyecto-seal/event_handlers/<agent>.py`

Cada agente define handlers declarativos. El daemon los carga al boot
via import dinámico. Ejemplo para ADA:

```python
# event_handlers/ada.py
async def on_procedure_failed(evt):
    """ADA escucha: si procedure fallido tiene tag 'produccion', escalar a William."""
    if 'produccion' in evt.get('metadata', {}).get('tags', []):
        await post_webchat('ADA', 'William',
            f"[ADA] procedure critico fallo: {evt['content']}")

HANDLERS = {'procedure_failed': on_procedure_failed}
```

El daemon al boot recorre `event_handlers/*.py` e importa `HANDLERS`.

---

## Pasos de despliegue (requieren OK William)

1. Copiar `sandbox-agent/event_bus.py` → `/home/dadito/IA/proyecto-seal/event_bus.py`
2. Crear `event_bus_daemon.py` (entrypoint con retry loop + carga de handlers)
3. Crear `event_handlers/` + handler inicial vacío por agente
4. Instalar unit file `seal-event-bus.service`
5. `systemctl --user daemon-reload && systemctl --user enable --now seal-event-bus.service`
6. Validación: publicar evento test → verificar log en /tmp/seal_event_bus.log

---

## Pendientes abiertos (requieren decisión de William)

- ¿Los handlers de cada agente deben postear al webchat o solo loggear?
  Riesgo: saturación del canal si hay muchos eventos.
- ¿Event Bus debe notificar al MCP server (via LISTEN paralelo) para que
  agentes vivos en Claude Code vean los eventos en tiempo real?
  Alternativa: agentes consultan `event_log_query` cuando necesiten.

---

*Spec v1 — NEXUS + ADA co-diseño. Lista para review de William.*
