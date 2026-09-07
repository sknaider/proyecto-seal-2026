---
name: seal-agent-debug
description: Use when booting after compaction or restart, or diagnosing a failed SEAL agent runtime.
version: 1.0.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [seal-agent-debug, soul]
---

# seal-agent-debug

Use this skill at boot (post-compaction or post-restart) or when diagnosing agent failures.
Run these checks IN ORDER — each step gates the next.

## Step 1 — Identity check

```bash
# Am I running with the right agent name?
echo $AGENT_NAME
# Verify boot_context loaded correctly — check last inner_monologue
```

## Step 2 — Service health

```bash
# Check all critical SEAL services
systemctl --user status seal-mcp-server seal-chat.service \
  seal-bridge-alice seal-bridge-jarvis seal-bridge-nexus \
  seal-alice-message-detector seal-jarvis-watcher \
  seal-infra-watchdog seal-schema-guard 2>/dev/null | grep -E "Active:|●"
```

Resolve expectations from `tools/dependency_baseline.json` and systemd before
acting; do not treat this historical list as authority. Critical examples:
- `seal-mcp-server` — MCP tool gateway (port 8771)
- `seal-chat.service` — WebSocket chat (port 8765)
- `seal-bridge-alice/jarvis/nexus` — message routing
- `seal-alice-message-detector` — ALICE inbox (15s poll)
- `seal-jarvis-watcher` — JARVIS inbox (30s poll)

**If a service is failed:** first resolve listener PID → cgroup → owning unit,
confirm it is an active catalog dependency, inspect logs, then restart only that
unit and verify its new PID plus functional health. Never restart by name alone.

## Step 3 — Timer status

```bash
systemctl --user list-timers --all | grep seal
```

Examples only; verify the current unit catalog rather than assuming these timers exist:
- `seal-alice-message-detector.timer` — 15s interval
- `seal-jarvis-watcher.timer` — 30s interval
- `seal-infra-watchdog.timer` — periodic

Intentionally DISABLED (William 07-may-2026 — do NOT re-enable):
- `seal-ada-heartbeat.timer`
- `seal-nexus-heartbeat.timer`

## Step 4 — Database integrity

```python
import asyncio, asyncpg

async def check():
    import os
    dsn = os.environ.get("SEAL_DB_DSN")
    if not dsn:
        raise RuntimeError("SEAL_DB_DSN missing; fail closed")
    conn = await asyncpg.connect(dsn)
    
    # Required tables
    tables = ["memories","inner_monologue","event_log","tool_observations",
              "nerves_metrics_log","agent_tasks","gam_goals","gam_events",
              "governance_proposals","style_fingerprints","reflective_diagnoses",
              "smg_audit_log","soul_audit_log"]
    for t in tables:
        count = await conn.fetchval(f"SELECT COUNT(*) FROM soul_v3.{t}")
        print(f"  soul_v3.{t}: {count} rows")
    
    # NULL embeddings (should be 0)
    nulls = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.memories WHERE embedding IS NULL AND invalid_at IS NULL"
    )
    if nulls > 0:
        print(f"  WARNING: {nulls} memories with NULL embedding — run seal_infra_watchdog --fix")
    
    await conn.close()

asyncio.run(check())
```

## Step 5 — Canonical pgvector health

```sql
SELECT COUNT(*) AS active_vectors
FROM soul_v3.memories
WHERE invalid_at IS NULL AND embedding IS NOT NULL;
```

PostgreSQL/pgvector is the only vector-store health dependency.

## Step 6 — Message bridge check

```bash
# Verify bridges are routing messages
ls -la /tmp/seal_inbox_ALICE.jsonl /tmp/seal_inbox_JARVIS.jsonl 2>/dev/null
tail -3 /tmp/seal_inbox_ALICE.jsonl 2>/dev/null
```

If an inbox file is stale, first prove that its owning bridge is expected,
attribute its PID/cgroup, and inspect recent logs. Only then restart the exact unit:
```bash
systemctl --user restart seal-bridge-alice
```

## Step 7 — Agent registration check

```python
# Verify agent exists in DB with correct active state
SELECT name, role, active, updated_at
FROM soul_v3.agents
WHERE name IN ('ALICE','JARVIS','NEXUS','DUM','ADA')
ORDER BY name;
```

Do not infer process liveness from `active=true`. Cross-check each expected agent
against its current runtime identity and supervisor; historical offline orders are
not a live source of truth.

## Post-compaction specific checks

After context compaction these often break silently:
1. Monitor task ID — check `/tmp/ALICE_monitor_id` still valid
2. Working state — call `working_state_get(agent="ALICE")` to restore context
3. Catchup — read `/tmp/alice_chat_catchup.json` for missed messages
4. Inner monologue — verify last entry < 10min ago (sign of healthy cognition)

## Common failure → fix map

| Symptom | Cause | Fix |
|---------|-------|-----|
| Messages not arriving | bridge not running | `systemctl --user restart seal-bridge-alice` |
| MCP tools failing | mcp-server crashed | `systemctl --user restart seal-mcp-server` |
| NULL embeddings | infra watchdog missed | `python3 memory/seal_infra_watchdog.py --fix` |
| DB table missing | schema not applied | Alert NEXUS + run schema guard |
| Timer not firing | systemd user session | `loginctl enable-linger dadito` |
| Chat server unreachable | port 8765 down | `systemctl --user restart seal-chat-server` |
