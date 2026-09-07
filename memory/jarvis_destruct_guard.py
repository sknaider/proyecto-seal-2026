"""jarvis_destruct_guard.py — Destructive Operations Guard for JARVIS.

MANDATORY: JARVIS must call preview_and_confirm() BEFORE any:
- SQL DELETE / DROP / TRUNCATE
- rm / rm -rf
- kill (bulk process termination)
- Mass file operations

Workflow:
1. preview_and_confirm(op_type, details) → runs dry-run, shows count, posts to webchat
2. JARVIS waits for William's explicit "OK" or "sí" confirmation
3. ONLY THEN executes the operation

Audit log: /tmp/jarvis_destruct_audit.jsonl
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import asyncio
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path("/tmp/jarvis_destruct_audit.jsonl")
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENT_ID = "JARVIS"


def _audit(entry: dict) -> None:
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"[jarvis_destruct_guard] audit fail: {e}", flush=True)


def _post_webchat_sync(message: str) -> None:
    try:
        subprocess.run([
            "curl", "-s", "-X", "POST", WEBCHAT_URL,
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "from": AGENT_ID,
                "to": "William",
                "type": "conversation",
                "channel": "web_chat",
                "message": message,
            })
        ], timeout=5, capture_output=True)
    except Exception as e:
        print(f"[jarvis_destruct_guard] webchat fail: {e}", flush=True)


def preview_sql_delete(query: str, conn_url: str | None = None) -> dict:
    """Preview a SQL DELETE/DROP/TRUNCATE — returns count and posts to webchat."""
    conn_url = conn_url or pg_dsn(required=True)

    # Convert DELETE ... WHERE to SELECT COUNT(*)
    count_query = None
    if re.search(r"DELETE\s+FROM", query, re.IGNORECASE):
        match = re.search(r"DELETE\s+FROM\s+(\S+)(.*)", query, re.IGNORECASE | re.DOTALL)
        if match:
            table = match.group(1)
            rest = match.group(2).strip()
            where_match = re.search(r"(WHERE.*?)(?:LIMIT|ORDER|$)", rest, re.IGNORECASE | re.DOTALL)
            where_clause = where_match.group(1).strip() if where_match else ""
            count_query = f"SELECT COUNT(*) as cnt FROM {table} {where_clause}".strip()
    elif re.search(r"TRUNCATE\s+", query, re.IGNORECASE):
        match = re.search(r"TRUNCATE\s+(?:TABLE\s+)?(\S+)", query, re.IGNORECASE)
        if match:
            table = match.group(1)
            count_query = f"SELECT COUNT(*) as cnt FROM {table}"

    count = "UNKNOWN"
    if count_query:
        try:
            result = subprocess.run(
                ["psql", conn_url, "-t", "-c", count_query],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                count = result.stdout.strip()
        except Exception:
            # Try via Python asyncpg
            try:
                import asyncpg
                async def _count():
                    conn = await asyncpg.connect(conn_url)
                    row = await conn.fetchrow(count_query)
                    await conn.close()
                    return row['cnt'] if row else 'UNKNOWN'
                count = str(asyncio.run(_count()))
            except Exception as e:
                count = f"ERROR: {e}"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "op_type": "sql_delete",
        "query": query,
        "count_query": count_query,
        "affected_count": count,
        "confirmed": False,
    }
    _audit(entry)

    msg = (
        f"⚠️ JARVIS solicita confirmación — operación destructiva SQL:\n"
        f"```sql\n{query[:300]}\n```\n"
        f"**Registros afectados: {count}**\n\n"
        f"Responde 'OK JARVIS' para confirmar o 'NO' para cancelar."
    )
    _post_webchat_sync(msg)

    return {"op_type": "sql_delete", "affected_count": count, "query": query, "pending_confirmation": True}


def preview_rm(path_pattern: str) -> dict:
    """Preview files that would be deleted by rm — returns list and posts to webchat."""
    try:
        result = subprocess.run(
            ["find"] + path_pattern.split() + ["-type", "f"],
            capture_output=True, text=True, timeout=10
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
    except Exception:
        files = ["ERROR: could not list files"]

    count = len(files)
    preview = "\n".join(files[:20])
    if count > 20:
        preview += f"\n... y {count - 20} archivos más"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "op_type": "rm",
        "path_pattern": path_pattern,
        "affected_count": count,
        "preview_files": files[:20],
        "confirmed": False,
    }
    _audit(entry)

    msg = (
        f"⚠️ JARVIS solicita confirmación — operación rm:\n"
        f"`{path_pattern}`\n"
        f"**Archivos afectados: {count}**\n"
        f"```\n{preview}\n```\n"
        f"Responde 'OK JARVIS' para confirmar o 'NO' para cancelar."
    )
    _post_webchat_sync(msg)

    return {"op_type": "rm", "affected_count": count, "path_pattern": path_pattern, "pending_confirmation": True}


def preview_kill(pattern: str) -> dict:
    """Preview processes that would be killed — returns list and posts to webchat."""
    try:
        result = subprocess.run(
            ["pgrep", "-a", "-f", pattern],
            capture_output=True, text=True, timeout=5
        )
        procs = [p for p in result.stdout.strip().split("\n") if p]
    except Exception:
        procs = ["ERROR: could not list processes"]

    count = len(procs)
    preview = "\n".join(procs[:10])

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "op_type": "kill",
        "pattern": pattern,
        "affected_count": count,
        "preview_procs": procs[:10],
        "confirmed": False,
    }
    _audit(entry)

    msg = (
        f"⚠️ JARVIS solicita confirmación — kill de procesos:\n"
        f"Patrón: `{pattern}`\n"
        f"**Procesos afectados: {count}**\n"
        f"```\n{preview}\n```\n"
        f"Responde 'OK JARVIS' para confirmar o 'NO' para cancelar."
    )
    _post_webchat_sync(msg)

    return {"op_type": "kill", "affected_count": count, "pattern": pattern, "pending_confirmation": True}


def confirm_executed(op_type: str, details: str) -> None:
    """Call this AFTER William confirmed and operation was executed."""
    _audit({
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_ID,
        "op_type": op_type,
        "details": details,
        "confirmed": True,
        "executed": True,
    })


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: jarvis_destruct_guard.py <sql_delete|rm|kill> <query_or_pattern>")
        sys.exit(1)
    op = sys.argv[1]
    arg = " ".join(sys.argv[2:])
    if op == "sql_delete":
        print(json.dumps(preview_sql_delete(arg), indent=2))
    elif op == "rm":
        print(json.dumps(preview_rm(arg), indent=2))
    elif op == "kill":
        print(json.dumps(preview_kill(arg), indent=2))
    else:
        print(f"Unknown op: {op}")
        sys.exit(1)
