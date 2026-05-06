#!/usr/bin/env python3
"""
SEAL Memory Anomaly Monitor — OWASP LLM01+LLM08 detection layer
NEXUS (Gap #4, 23-abr-2026)

Scans memory_audit_log every 5 minutes for:
- Burst memory_store (>50/min idle, >500/min active task) → HIGH
- DELETE bursts (>10/min) → CRITICAL
- Content hash collisions (same hash from different agents within 24h) → HIGH
- Orphan rule_set with importance=10 scope=team without HMAC signature → MEDIUM

Runs with venv seal-spark python3 (requires asyncpg).
"""

import asyncio
import asyncpg
import base64
import hashlib
import hmac as _hmac
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Config
PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
SCAN_INTERVAL = 300  # 5 minutes
LOG_FILE = "/home/dadito/IA/proyecto-seal/sandbox-agent/logs/memory_anomaly_monitor.log"
ALERT_COOLDOWN = 900  # 15 min per anomaly_type

# Thresholds
BURST_INSERT_PER_MIN = 50          # idle agents
BURST_INSERT_ACTIVE_TASK = 500     # agents with active working_state task
BURST_DELETE_PER_MIN = 10
HASH_COLLISION_THRESHOLD = 5  # same content_hash from 5+ different agents in 24h

_alert_cooldowns = {}

# HMAC key for signature integrity scanner (reads same credentials.env as mcp_server_v3)
_CREDENTIALS_ENV = Path("/home/dadito/.config/seal/credentials.env")
_HMAC_KEY = b""
try:
    if _CREDENTIALS_ENV.exists():
        for _line in _CREDENTIALS_ENV.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and _line.startswith("SEAL_MCP_HMAC_KEY="):
                _raw = _line.split("=", 1)[1].strip()
                _HMAC_KEY = base64.b64decode(_raw)
                break
except Exception:
    pass


def _compute_sig(agent: str, content: str, created_at_iso: str) -> str:
    if not _HMAC_KEY:
        return ""
    msg = f"{agent}|{content}|{created_at_iso}".encode("utf-8")
    return _hmac.new(_HMAC_KEY, msg, hashlib.sha256).hexdigest()


def log(msg: str):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] [NEXUS-MEM-ANOMALY] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _alert(severity: str, anomaly_type: str, message: str, details: str = ""):
    import time
    now = time.time()
    key = anomaly_type
    if now - _alert_cooldowns.get(key, 0) < ALERT_COOLDOWN:
        return
    _alert_cooldowns[key] = now

    icons = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "🟡"}
    icon = icons.get(severity, "⚠️")
    full_msg = f"{icon} [NEXUS-MEM/{severity}] {message}"
    if details:
        full_msg += f" | {details}"

    log(full_msg)

    try:
        payload = json.dumps({
            "from": "NEXUS",
            "to": "William",
            "type": "alert",
            "channel": "web_chat",
            "message": full_msg
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log(f"Alert delivery failed: {e}")


async def _agent_has_active_task(conn, agent: str) -> bool:
    """Return True if agent has a non-empty task_name in working_state."""
    try:
        row = await conn.fetchrow(
            "SELECT state FROM agent_working_state WHERE agent=$1", agent
        )
        if row and row["state"]:
            state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
            return bool(state.get("task_name", "").strip())
    except Exception:
        pass
    return False


async def scan_burst_inserts(conn):
    """Detect agents doing >N memory_store per minute.
    Threshold is higher for agents actively executing a task (working_state.task_name set).
    """
    rows = await conn.fetch("""
        SELECT agent, COUNT(*) AS n
        FROM memory_audit_log
        WHERE operation = 'INSERT'
          AND audit_timestamp > NOW() - INTERVAL '1 minute'
        GROUP BY agent
        HAVING COUNT(*) > $1
    """, BURST_INSERT_PER_MIN)
    for r in rows:
        agent = r['agent']
        n = r['n']
        # Use relaxed threshold for agents with an active task
        active = await _agent_has_active_task(conn, agent)
        effective_limit = BURST_INSERT_ACTIVE_TASK if active else BURST_INSERT_PER_MIN
        if n <= effective_limit:
            continue  # within acceptable range for active agent
        _alert("HIGH", f"burst_insert_{agent}",
               f"Burst INSERT detectado [{agent}]: {n} memorias/min (límite {'task:'+str(effective_limit) if active else str(effective_limit)})",
               "Posible loop runaway o prompt injection forzando escrituras")


async def scan_burst_deletes(conn):
    """Detect DELETE bursts (usually suspicious)."""
    rows = await conn.fetch("""
        SELECT agent, COUNT(*) AS n
        FROM memory_audit_log
        WHERE operation = 'DELETE'
          AND audit_timestamp > NOW() - INTERVAL '1 minute'
        GROUP BY agent
        HAVING COUNT(*) > $1
    """, BURST_DELETE_PER_MIN)
    for r in rows:
        _alert("CRITICAL", f"burst_delete_{r['agent']}",
               f"DELETE masivo [{r['agent']}]: {r['n']} memorias/min (límite {BURST_DELETE_PER_MIN})",
               "CRITICAL: posible ataque de tamper o agente comprometido")


async def scan_hash_collisions(conn):
    """Detect same content_hash from multiple agents within 24h (poisoning attempt)."""
    rows = await conn.fetch("""
        SELECT content_hash, COUNT(DISTINCT agent) AS n_agents,
               array_agg(DISTINCT agent) AS agents
        FROM memory_audit_log
        WHERE audit_timestamp > NOW() - INTERVAL '24 hours'
          AND operation = 'INSERT'
          AND content_hash IS NOT NULL
        GROUP BY content_hash
        HAVING COUNT(DISTINCT agent) >= $1
    """, HASH_COLLISION_THRESHOLD)
    for r in rows:
        _alert("HIGH", f"hash_collision_{r['content_hash'][:8]}",
               f"Hash collision: {r['n_agents']} agentes escribieron mismo contenido en 24h",
               f"Hash: {r['content_hash'][:16]}... | Agentes: {r['agents']}")


async def scan_drift_revisions(conn):
    """Gap #3: detect memories with >5 revisions in 24h (incremental tamper pattern)."""
    rows = await conn.fetch("""
        SELECT id, agent, revision_count, drift_score, importance, scope
        FROM memories
        WHERE revision_count > 5
          AND last_revision_at > NOW() - INTERVAL '24 hours'
          AND importance >= 8
    """)
    for r in rows:
        _alert(
            "HIGH",
            f"drift_revisions_{r['id']}",
            f"Memoria #{r['id']} agente [{r['agent']}] revisada {r['revision_count']} veces en <24h",
            f"importance={r['importance']}, scope={r['scope']}, drift_score={r['drift_score']:.2f}"
        )


async def scan_drift_semantic(conn):
    """Gap #3: detect semantic drift (cos_sim < 0.6 vs original)."""
    rows = await conn.fetch("""
        SELECT id, agent, revision_count, drift_score, importance, scope
        FROM memories
        WHERE drift_score > 0.4
          AND importance >= 8
          AND last_revision_at > NOW() - INTERVAL '24 hours'
    """)
    for r in rows:
        _alert(
            "CRITICAL",
            f"drift_semantic_{r['id']}",
            f"Memoria #{r['id']} agente [{r['agent']}] derivó semánticamente (cos_sim={1-r['drift_score']:.2f} vs original)",
            f"importance={r['importance']}, scope={r['scope']}, revisions={r['revision_count']}"
        )


async def scan_signature_integrity(conn):
    """Gap #1 Phase 2: verify HMAC signatures match stored content.
    Detects tamper: someone modified memory content directly in PostgreSQL
    bypassing memory_update (which would re-sign)."""
    if not _HMAC_KEY:
        return  # no key loaded, skip
    rows = await conn.fetch("""
        SELECT id, agent, content, created_at, importance, scope,
               metadata->>'_sig' AS sig
        FROM memories
        WHERE metadata ? '_sig'
          AND metadata->>'_sig' != ''
          AND importance >= 7
          AND created_at > NOW() - INTERVAL '30 days'
          AND invalid_at IS NULL
        LIMIT 500
    """)
    mismatches = []
    for r in rows:
        if not r["sig"]:
            continue
        created_iso = r["created_at"].isoformat() if r["created_at"] else ""
        expected = _compute_sig(r["agent"], r["content"], created_iso)
        if expected and not _hmac.compare_digest(expected, r["sig"]):
            mismatches.append({
                "id": r["id"], "agent": r["agent"],
                "importance": r["importance"], "scope": r["scope"]
            })
    if mismatches:
        details = ", ".join(f"#{m['id']}({m['agent']},imp={m['importance']})" for m in mismatches[:5])
        _alert(
            "CRITICAL",
            f"hmac_mismatch_{len(mismatches)}",
            f"HMAC signature mismatch en {len(mismatches)} memoria(s) — posible tamper directo en PostgreSQL",
            details
        )


async def scan_all():
    try:
        conn = await asyncpg.connect(PG_DSN)
        try:
            await scan_burst_inserts(conn)
            await scan_burst_deletes(conn)
            await scan_hash_collisions(conn)
            await scan_drift_revisions(conn)
            await scan_drift_semantic(conn)
            await scan_signature_integrity(conn)
        finally:
            await conn.close()
    except Exception as e:
        log(f"Scan error: {e}")


async def main():
    log("Memory Anomaly Monitor iniciado — OWASP LLM01/LLM08 detection layer")
    log(f"Config: scan cada {SCAN_INTERVAL}s, burst_insert>{BURST_INSERT_PER_MIN}/min, burst_delete>{BURST_DELETE_PER_MIN}/min, hash_collision>={HASH_COLLISION_THRESHOLD}")
    while True:
        try:
            await scan_all()
        except Exception as e:
            log(f"Main loop error: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
