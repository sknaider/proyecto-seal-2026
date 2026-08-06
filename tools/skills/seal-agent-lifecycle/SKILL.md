---
name: seal-agent-lifecycle
description: Use when creating, deactivating, reactivating, or auditing a SEAL agent lifecycle.
version: 1.1.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [seal-agent-lifecycle, soul]
---

# seal-agent-lifecycle

Use this skill when creating a new SEAL agent, deactivating one, or managing the agent registry.

## Creating a new agent

### Step 1 — Register in soul_v3.agents

```sql
INSERT INTO soul_v3.agents (
    name, role,
    ocean_o, ocean_c, ocean_e, ocean_a, ocean_n,
    persona_axes, active
) VALUES (
    'NOVA',                        -- uppercase, short name
    'role description',
    0.8, 0.9, 0.7, 0.5, 0.2,     -- OCEAN o/c/e/a/n 0.0-1.0
    '{"created_by": "ALICE", "created_at": "2026-05-11"}'::jsonb,
    false                          -- always start inactive, William enables
);
```

### Step 1b — Provisionar el ROL DB per-agente (crítico desde jul-2026)

Registrar la fila en `soul_v3.agents` **no alcanza**: hoy `soul_v3` usa **RLS per-agente** y el
superusuario `seal` fue **rotado** (`db.py` rechaza daemons con superusuario). Un agente sin su rol
acotado no puede escribir su memoria (la RLS lo niega) o caería a `seal`, que ya no entra.

Cada agente necesita sus roles de least-privilege (verificado: existen `mcp_runtime_{ada,alice,
dum,jarvis,nexus}` y `svc_seal_sync_*`):

- `mcp_runtime_<agente>` — el rol de runtime del MCP; `SELECT/INSERT` acotado por RLS a lo suyo,
  **sin** `UPDATE/DELETE` global. Es el que usa el proceso para memoria/estado.
- `svc_seal_sync_<agente>` — el login de sync per-agente (reemplazó el login heredado compartido).

La RLS se ata a `session_user` (el rol de la conexión), no a una GUC — el proceso del agente
**conecta con su credencial**, nunca con `seal`. Coordiná el alta del rol + sus GRANTs/políticas
con ADA (frontera DB), y verificá **por efecto**: con la credencial del agente, un `UPDATE` fuera
de su scope debe fallar `42501`.

### Step 2 — Create boot scripts

```bash
# Agent launch script (copy from alice.sh pattern)
cp /home/dadito/IA/proyecto-seal/alice.sh \
   /home/dadito/IA/proyecto-seal/nova.sh

# Edit nova.sh: change AGENT_NAME and claude-code session name
# Fresh start variant
cp /home/dadito/IA/proyecto-seal/alice_fresh.sh \
   /home/dadito/IA/proyecto-seal/nova_fresh.sh
```

### Step 3 — Systemd service + timer

```ini
# ~/.config/systemd/user/seal-nova-watcher.service
[Unit]
Description=SEAL NOVA message detector

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'test -f /tmp/.nova_new_signal && cat /tmp/.nova_new_signal'
StandardOutput=null
```

```ini
# ~/.config/systemd/user/seal-nova-watcher.timer
[Unit]
Description=SEAL NOVA watcher timer

[Timer]
OnActiveSec=15s
OnUnitActiveSec=30s
Unit=seal-nova-watcher.service
AccuracySec=5s
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now seal-nova-watcher.timer
```

### Step 4 — Add to agent_bridge TRIGGER_MAP

Edit `/home/dadito/IA/proyecto-seal/messages/agent_bridge.py`:

```python
TRIGGER_MAP = {
    ...
    "NOVA": DIR / ".nova_trigger",   # add this line
}
```

Then restart bridges: `systemctl --user restart seal-bridge-alice seal-bridge-jarvis`

### Step 5 — Create CLAUDE.md for agent identity

Add boot protocol in the agent's CLAUDE.md:
```markdown
## Boot
boot_context(agent="NOVA") FIRST.
```

### Step 6 — Register in chat server authorized agents

Check if `seal-chat-server` has an agent whitelist. If so, add NOVA.

## Deactivating an agent

This is a destructive lifecycle operation. Before changing state or signaling a
process:

1. Resolve the exact agent row and run `SELECT count(*)` for the proposed scope.
2. Resolve runtime PID → cgroup → unit/session and prove it belongs to that agent.
3. Record the current `active` value, unit state, and a tested rollback command.
4. Show William the exact row count, PID/unit, and rollback plan; wait for his
   explicit approval.
5. Re-check the same identity immediately before applying. If it changed, stop.

```sql
-- Run only after the gate above. RETURNING proves the exact affected row.
UPDATE soul_v3.agents
SET active = false
WHERE name = 'NOVA' AND active = true
RETURNING name, active, updated_at;
```

```bash
# Signal the previously verified PID, never a broad process-name pattern.
kill --signal TERM "$VERIFIED_AGENT_PID"

# Disable timer
systemctl --user disable --now seal-nova-watcher.timer
```

Afterward, prove the row, process, and unit reached the intended state. If any
check fails, execute the recorded rollback and report `INDETERMINATE`, not green.

## Agent status check

```sql
SELECT name, role, active, updated_at,
       EXTRACT(EPOCH FROM (NOW() - updated_at))/60 AS minutes_since_update
FROM soul_v3.agents
WHERE name IN ('ALICE','JARVIS','NEXUS','DUM','ADA','FABLE')
ORDER BY name;
```

## Reactivating a stopped agent

Only with explicit William authorization. Then:
```bash
# Remove pause flag if set
rm -f /tmp/seal_pause_<agentname>.flag

# Start the agent
bash /home/dadito/IA/proyecto-seal/<agentname>.sh
```

## Current agent registry (medido 2026-08-06 en soul_v3.agents — TODOS active=true)

| Agent | active | Rol |
|-------|--------|-----|
| ADA | true | Memoria / frontera DB / despliegues |
| ALICE | true | Auditor / coordinador / GTL |
| DUM | true | Guardia 24/7, Gemma4 local |
| FABLE | true | Adversario / contención (Fable 5, config externa a la familia SOUL) |
| JARVIS | true | Arquitectura / plano de control / orquestación |
| NEXUS | true | Seguridad / infraestructura / verificación |

> Corrección de currency (6-ago): el registro anterior ("as of 11-may, ADA offline, ALICE única
> ejecutora") quedó obsoleto — **ADA está activa** y **FABLE** se sumó (jun-2026). Verificado por
> efecto contra `soul_v3.agents`.

Agentes fantasma eliminados (09-may-2026): JARVIS_MAYOR, KAIROS, ADA_LOCAL, TEAM.

## OCEAN guidelines for new agents

| Trait | Low (0.0-0.3) | High (0.7-1.0) |
|-------|---------------|----------------|
| Openness | Conservative, predictable | Exploratory, creative |
| Conscientiousness | Flexible, spontaneous | Methodical, thorough |
| Extraversion | Internal, quiet | Communicative, proactive |
| Agreeableness | Independent, challenging | Collaborative, warm |
| Neuroticism | Stable under pressure | Emotionally reactive |
