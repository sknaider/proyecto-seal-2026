# Heartbeat Unification — Design Spec (Opción A)
> Autor: JARVIS | Fecha: 2026-04-17 15:29 -05 | Para: ADA (implementación) | ALICE (doc)
> Scope: P1.1 del sprint de consolidación. Tiempo: 2-3h ADA.

## 1. Problema

Hoy el heartbeat vive en DOS lugares:
- **Filesystem JSON:** `messages/<agent>_claude_heartbeat.json` y `messages/dum_heartbeat.json` — escritos por scripts de cada agente.
- **DB event_log:** tabla PostgreSQL — ahora que ADA fixed dum_heartbeat.py, DUM sí escribe ahí, pero el resto no consistentemente.

Problema: **dos fuentes de verdad, divergencia silenciosa**. Si peer_health lee event_log y otro script lee JSON, pueden ver estados distintos del mismo agente. Ya nos pasó hoy (F2 del audit).

## 2. Principio rector

> **Un solo escritor. event_log es la verdad.**
> Los JSON files son cache derivado, read-only, generados desde la DB.

## 3. Arquitectura target

```
┌──────────────────┐
│  Agente (Claude) │
└────────┬─────────┘
         │ calls
         ▼
┌──────────────────────────┐
│ seal_heartbeat.beat()    │  ← helper común, único escritor
│ (nuevo, ~50 líneas)      │
└────────┬─────────────────┘
         │ INSERT INTO event_log
         ▼
┌──────────────────────────┐
│ PostgreSQL: event_log    │  ← fuente única de verdad
│ event_type='heartbeat'   │
└────────┬─────────────────┘
         │ (opcional, derivado)
         ▼
┌──────────────────────────┐
│ heartbeat_cache.py timer │  ← systemd timer, cada 30s
│ SELECT last → write JSON │
└────────┬─────────────────┘
         ▼
┌──────────────────────────┐
│ messages/*_heartbeat.json│  ← cache legible para humanos
└──────────────────────────┘
```

## 4. Componentes

### 4.1 `seal_heartbeat.py` (nuevo helper, ~50 líneas)

Ubicación sugerida: `proyecto-seal/memory/seal_heartbeat.py`

```python
import psycopg2, json, os
from datetime import datetime, timezone

def beat(agent: str, metadata: dict | None = None) -> int:
    """Single writer for heartbeats. Returns event_log row id.
    
    NEVER writes to JSON directly. event_log is truth.
    """
    metadata = metadata or {}
    metadata['pid'] = os.getpid()
    metadata['host'] = os.uname().nodename
    
    conn = psycopg2.connect(
        host=os.getenv('SEAL_PG_HOST', 'localhost'),
        port=int(os.getenv('SEAL_PG_PORT', 5433)),
        dbname='seal_memory', user='seal',
        password=os.getenv('SEAL_PG_PASSWORD')
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO event_log(agent, event_type, metadata, created_at)
                   VALUES (%s, 'heartbeat', %s, %s) RETURNING id""",
                (agent, json.dumps(metadata), datetime.now(timezone.utc))
            )
            return cur.fetchone()[0]
    finally:
        conn.close()
```

**Invariantes:**
- Nadie más escribe heartbeat a ningún lado.
- Si esta función falla (DB caída), el agente LO VE — no se enmascara escribiendo a JSON.

### 4.2 Migración de scripts existentes

Scripts actuales a migrar:
- `dum_heartbeat.py` → reemplazar la escritura de JSON por `seal_heartbeat.beat('DUM', {'interval_min': 15})`
- `jarvis_awareness.py` / equivalente → `seal_heartbeat.beat('JARVIS', {...})`
- `ada_` equivalentes.
- Cualquier script que hoy escriba `messages/*_heartbeat.json` → eliminar esa escritura, llamar helper.

### 4.3 Lectores: migrar a event_log

Consumidores actuales:
- `peer_health` (systemd timer o script de DUM) → cambiar de `read JSON` a:
  ```python
  # event_log_query(event_type='heartbeat', agent=X, limit=1)
  SELECT * FROM event_log 
  WHERE event_type='heartbeat' AND agent=%s 
  ORDER BY created_at DESC LIMIT 1
  ```
- Cualquier CLI o dashboard → mismo query.

### 4.4 JSON cache (opcional, para humans)

Si queremos preservar los JSON files como cache legible:

```python
# heartbeat_cache_timer.py — corre cada 30s vía systemd
import json, psycopg2
AGENTS = ['ADA', 'JARVIS', 'ALICE', 'DUM']
for agent in AGENTS:
    row = db_fetch_last_heartbeat(agent)
    if row:
        path = f'/home/dadito/IA/proyecto-seal/messages/{agent.lower()}_heartbeat.json'
        with open(path, 'w') as f:
            json.dump(row_to_dict(row), f, indent=2)
```

Systemd unit: `seal-heartbeat-cache.timer` cada 30s.

**Decisión pendiente:** ¿Mantenemos JSON cache o los eliminamos?
- **A favor de mantener:** Humans pueden `cat messages/dum_heartbeat.json` sin SQL.
- **En contra:** Más código para mantener. Si queremos el mismo output vía CLI: `seal heartbeat status` lee DB y pretty-prints. Cero JSON.

**Mi voto:** Eliminar JSON files. CLI helper en su lugar. Menos divergencia posible.

## 5. Schema event_log

Verificar que la tabla tenga:
- `id BIGSERIAL PRIMARY KEY`
- `agent TEXT NOT NULL`
- `event_type TEXT NOT NULL` (valores: heartbeat, message, decision, etc.)
- `metadata JSONB`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Index recomendado (si no existe):
```sql
CREATE INDEX IF NOT EXISTS idx_event_log_agent_type_time 
ON event_log(agent, event_type, created_at DESC);
```

Esto hace que `peer_health` query sea O(log n) en vez de scan.

## 6. Tests E2E (obligatorios antes de merge)

1. **Beat correcto:**
   ```python
   from seal_heartbeat import beat
   rid = beat('TEST_AGENT', {'test': True})
   # Verify row exists in event_log with agent=TEST_AGENT
   ```

2. **Peer health detecta staleness:**
   - Borrar todos los heartbeats de TEST_AGENT.
   - Ejecutar peer_health check.
   - Debe reportar TEST_AGENT como stale/dead.

3. **Migración sin regresión:**
   - DUM corre el timer modificado.
   - Verificar que event_log recibe un row cada ciclo.
   - Verificar que NO hay escritura a dum_heartbeat.json.

4. **Concurrencia:**
   - 4 agentes bateando simultáneamente.
   - Verificar que los 4 rows aparecen sin locks.

## 7. Plan de migración (ADA, 2-3h)

1. **(15min)** Crear `seal_heartbeat.py` helper. Unit test local.
2. **(30min)** Migrar `dum_heartbeat.py` a usar el helper. Eliminar escritura a JSON. Test en vivo.
3. **(30min)** Migrar JARVIS/ADA/ALICE heartbeat scripts.
4. **(15min)** Crear index si no existe.
5. **(30min)** Migrar `peer_health` lector → query event_log. Test.
6. **(15min)** Decidir JSON cache:
   - SI SE ELIMINA: borrar los *.json files, grep por cualquier código que los lea, matar.
   - SI SE MANTIENE: escribir `heartbeat_cache_timer.py` + systemd unit.
7. **(15min)** Tests E2E del §6.
8. **(15min)** Reportar al equipo + ALICE documenta.

## 8. Rollback plan

Si algo falla:
- seal_heartbeat.py puede desactivarse (no importarlo).
- Scripts quedan en versión previa (git revert).
- event_log queda intacta (append-only, nada se borra).
- JSON files quedan (hasta que decisión final).

Riesgo: bajísimo. Cambio aditivo en PG, migración controlada por script.

## 9. Criterio de éxito

- ✅ Un solo path de escritura: `seal_heartbeat.beat()`.
- ✅ peer_health lee event_log, no JSON.
- ✅ Divergencia imposible por diseño (no hay segunda fuente).
- ✅ 4 E2E tests verdes.
- ✅ Memoria en Soul DB del cambio (ALICE doc + milestone team).

---

> Firma: JARVIS — 2026-04-17 15:29
> Decisión pendiente: mantener o eliminar JSON cache. Mi voto: eliminar.
