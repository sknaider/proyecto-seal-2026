---
name: seal-audit
description: "Health audit of SEAL infrastructure — PostgreSQL, Neo4j, services, GPU, heartbeats. Reports what's up, what's down, and what needs attention."
tags: [seal, monitoring, health, infrastructure, audit]
---

# /seal-audit — SEAL Infrastructure Audit

Comprehensive health check of all SEAL services and infrastructure.

## Usage

```
/seal-audit          # Full audit
/seal-audit quick    # Quick check (services only)
```

## What it checks

### Services
1. **PostgreSQL (SOUL)** — port 5433, connection test
2. **Neo4j (Connectome)** — port 7687, connection test
3. **Web Chat Bridge** — port 8765, health endpoint
4. **SOUL Memory SDK/API** — ports 8767 gateway, 8768 API, 8771 MCP
5. **SEAL Studio** — port 3000, response check
6. **Ollama (DUM)** — service status + model loaded
Note: Qdrant removed 2026-04-28 (soul_lite=True permanent config — vectors stored in PostgreSQL pgvector)

### Hardware
9. **GPU** — nvidia-smi: temp, utilization, memory, processes
10. **Disk** — df: usage of key partitions

### Team
11. **ADA heartbeat** — ada_claude_heartbeat.json freshness
12. **DUM heartbeat** — last inner_monologue entry in PostgreSQL (DUM writes to DB, not JSON)
13. **JARVIS** — vscode_commands.jsonl last entry

### SOUL Integrity
14. **Memory count** — total, by type, by agent
15. **Drift** — last 24h events
16. **Inner monologue** — last entry timestamp
17. **Identity** — all agents present in identity table

## How to execute

```bash
echo "=== SEAL AUDIT $(date -u) ==="

# Services — uses Python socket for PG (pg_isready not installed on DGX Spark)
echo "--- Services ---"
/home/dadito/IA/seal-spark/.venv/bin/python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('localhost',5433)); s.close(); print('PostgreSQL: UP')" 2>/dev/null || echo "PostgreSQL: DOWN"
curl -sf http://localhost:7474 > /dev/null && echo "Neo4j: UP" || echo "Neo4j: DOWN"
curl -sf http://localhost:8765/ > /dev/null && echo "Web Chat: UP" || echo "Web Chat: DOWN"
/home/dadito/IA/seal-spark/.venv/bin/python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('localhost',8771)); s.close(); print('MCP Server (SOUL): UP')" 2>/dev/null || echo "MCP Server (SOUL): DOWN"
# Ollama runs as SYSTEM service, not user service — check via API endpoint
curl -sf http://localhost:11434/api/tags > /dev/null && echo "Ollama: UP" || echo "Ollama: DOWN"

# GPU
echo "--- GPU ---"
nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null || echo "GPU: N/A"

# Disk
echo "--- Disk ---"
df -h / /home 2>/dev/null | tail -n +2

# Team heartbeats
# ADA/JARVIS: check JSON heartbeat files
# DUM: writes to PostgreSQL inner_monologue (no JSON file) — check via DB
echo "--- Team ---"
/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import json, os, asyncio, asyncpg
from datetime import datetime, timezone

# ADA and JARVIS — heartbeat JSON files
for name, path in [('ADA','ada_claude_heartbeat.json'),('JARVIS','jarvis_claude_heartbeat.json')]:
    fp = os.path.expanduser(f'~/IA/proyecto-seal/messages/{path}')
    try:
        with open(fp) as f:
            d = json.load(f)
        ts = datetime.fromisoformat(d['timestamp'])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        print(f'{name}: {\"ALIVE\" if age < 300 else \"STALE\"} (age={int(age)}s)')
    except: print(f'{name}: OFFLINE')

# DUM — check last inner_monologue entry in PostgreSQL
async def check_dum():
    try:
        dsn = os.environ.get('SEAL_DB_DSN')
        if not dsn:
            raise RuntimeError('SEAL_DB_DSN missing; fail closed')
        conn = await asyncpg.connect(dsn)
        row = await conn.fetchrow(\"\"\"SELECT created_at FROM inner_monologue WHERE agent = 'DUM' ORDER BY created_at DESC LIMIT 1\"\"\")
        await conn.close()
        if row:
            age = (datetime.now(timezone.utc) - row['created_at'].replace(tzinfo=timezone.utc)).total_seconds()
            print(f'DUM: {\"ALIVE\" if age < 1200 else \"STALE\"} (age={int(age)}s, via DB)')
        else:
            print('DUM: OFFLINE (no DB entries)')
    except Exception as e:
        print(f'DUM: ERROR ({e})')
asyncio.run(check_dum())
"

# SOUL integrity — via asyncpg (psql not installed on DGX Spark)
echo "--- SOUL ---"
/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import asyncio, os, asyncpg
async def check():
    dsn = os.environ.get('SEAL_DB_DSN')
    if not dsn:
        raise RuntimeError('SEAL_DB_DSN missing; fail closed')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch('SELECT agent, count(*) as cnt FROM memories WHERE invalid_at IS NULL GROUP BY agent ORDER BY cnt DESC')
    for r in rows: print(f'  {r[\"agent\"]}: {r[\"cnt\"]}')
    print(f'  TOTAL: {sum(r[\"cnt\"] for r in rows)}')
    await conn.close()
asyncio.run(check())
"
```

## Output format

```
SEAL AUDIT — {timestamp}
Services: {up}/{total} UP
  PostgreSQL: UP | Neo4j: DOWN | Web Chat: UP | ...
GPU: {temp}°C / {util}% / {mem_used}/{mem_total} MB
Team: ADA={status} | DUM={status} | JARVIS={status}
SOUL: {total} memories ({by_agent})
ALERTS: {any issues found}
```
