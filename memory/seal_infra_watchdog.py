"""SEAL Infrastructure Watchdog — diagnoses and auto-fixes silent infrastructure failures.

Checks and self-heals:
1. chat_messages sequence drift (silent data loss)
2. soul_v3.memories without embeddings (invisible to semantic search)
3. Chat server connectivity
4. MCP SSE binding

Run: python3 seal_infra_watchdog.py [--fix] [--notify]
Cron: every 15 minutes
"""
import asyncio
import json
import sys
import argparse
import subprocess
from datetime import datetime, timezone

import os
# DSN desde el EnvironmentFile de la unidad (rol least-privilege
# login_infra_watchdog, ya provisto), fail-closed (ADA 3-sep-2026). POR QUE:
# el literal del rol seal tenia la contrasena vieja y la unidad fallaba con
# InvalidPasswordError desde 13:30; el auto-fix de secuencias quedaba ciego.
PG_DSN = os.environ.get("SEAL_DB_URL", "").strip()
if not PG_DSN:
    sys.exit("seal_infra_watchdog: SEAL_DB_URL ausente; fail-closed, no corro")
CHAT_URL = "http://localhost:8765"
MCP_URL = "http://localhost:8771"  # ADA 3-sep-2026: 8766 no escucha nadie; el MCP vive en 8771 (medido: /health 200)
VENV_PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"


async def check_sequence_drift(conn) -> dict:
    """Detect tables where id sequence < max(id) causing silent insert failures."""
    issues = []
    tables = [
        ("chat_messages", "chat_messages_id_seq"),
        ("soul_v3.memories", "soul_v3.memories_id_seq"),
        ("soul_v3.session_memory", "soul_v3.session_memory_id_seq"),
    ]
    for table, seq in tables:
        try:
            max_id = await conn.fetchval(f"SELECT MAX(id) FROM {table}")
            if max_id is None:
                continue
            seq_name = seq.replace(".", "_") if "." not in seq else seq
            seq_val = await conn.fetchval(f"SELECT last_value FROM {seq}")
            if seq_val < max_id:
                issues.append({
                    "table": table,
                    "seq": seq,
                    "seq_val": seq_val,
                    "max_id": max_id,
                    "drift": max_id - seq_val
                })
        except Exception as e:
            issues.append({"table": table, "error": str(e)})
    return {"check": "sequence_drift", "issues": issues, "ok": len(issues) == 0}


async def fix_sequence_drift(conn, issues: list) -> list:
    """Correct sequence values to max_id + 100 buffer."""
    fixed = []
    for issue in issues:
        if "error" in issue:
            continue
        new_val = issue["max_id"] + 100
        seq_name = issue["seq"]
        try:
            await conn.execute(f"SELECT setval('{seq_name}', {new_val})")
            fixed.append(f"{issue['table']}: {issue['seq_val']} → {new_val}")
        except Exception as e:
            fixed.append(f"{issue['table']}: FAILED — {e}")
    return fixed


async def check_missing_embeddings(conn) -> dict:
    """Find soul_v3.memories rows with NULL embedding (won't show in semantic search)."""
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM soul_v3.memories WHERE embedding IS NULL AND invalid_at IS NULL"
        )
        agents = await conn.fetch(
            """SELECT agent, COUNT(*) as cnt
               FROM soul_v3.memories
               WHERE embedding IS NULL AND invalid_at IS NULL
               GROUP BY agent ORDER BY cnt DESC LIMIT 5"""
        )
        return {
            "check": "missing_embeddings",
            "count": count,
            "by_agent": {r["agent"]: r["cnt"] for r in agents},
            "ok": count == 0
        }
    except Exception as e:
        return {"check": "missing_embeddings", "error": str(e), "ok": False}


async def fix_missing_embeddings(conn, limit: int = 20) -> dict:
    """Generate embeddings for memories that are missing them."""
    import sys
    sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
    try:
        from embeddings import get_embedding
    except ImportError:
        return {"error": "embeddings module not found"}

    rows = await conn.fetch(
        """SELECT id, content FROM soul_v3.memories
           WHERE embedding IS NULL AND invalid_at IS NULL
           ORDER BY importance DESC, created_at DESC
           LIMIT $1""",
        limit
    )
    fixed = 0
    errors = []
    for row in rows:
        try:
            emb = await get_embedding(row["content"])
            await conn.execute(
                "UPDATE soul_v3.memories SET embedding=$1::vector WHERE id=$2",
                json.dumps(emb), row["id"]
            )
            fixed += 1
        except Exception as e:
            errors.append(f"id={row['id']}: {e}")

    return {"fixed": fixed, "errors": errors}


async def check_chat_server() -> dict:
    """Verify chat server is reachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{CHAT_URL}/api/chat/messages?channel=web_chat&limit=1", timeout=5) as r:
            data = json.loads(r.read())
            return {"check": "chat_server", "ok": data.get("ok", False)}
    except Exception as e:
        return {"check": "chat_server", "ok": False, "error": str(e)}


async def check_mcp_server() -> dict:
    """Verify MCP SSE server is reachable."""
    import urllib.request
    try:
        # ADA 3-sep-2026: el transporte es HTTP streamable (/mcp), no SSE; /sse da 404.
        # La salud se lee en /health -> {"status":"ok",...} (medido).
        req = urllib.request.Request(f"{MCP_URL}/health")
        with urllib.request.urlopen(req, timeout=3) as r:
            body = json.loads(r.read().decode() or "{}")
            return {"check": "mcp_server", "ok": body.get("status") == "ok", "postgresql": body.get("postgresql")}
    except Exception as e:
        return {"check": "mcp_server", "ok": False, "error": str(e)}


def notify_webchat(message: str):
    """Send notification to webchat."""
    try:
        import urllib.request
        payload = json.dumps({
            "from": "NEXUS",
            "to": "equipo",
            "type": "status",
            "channel": "web_chat",
            "message": message
        }).encode()
        req = urllib.request.Request(
            f"{CHAT_URL}/api/agents/send",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


async def run_watchdog(fix: bool = False, notify: bool = False):
    import asyncpg
    conn = await asyncpg.connect(PG_DSN)

    results = []
    fixes_applied = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Sequence drift
    seq_result = await check_sequence_drift(conn)
    results.append(seq_result)
    if fix and not seq_result.get("ok") and seq_result.get("issues"):
        fixed = await fix_sequence_drift(conn, seq_result["issues"])
        fixes_applied.extend([f"[seq] {f}" for f in fixed])

    # 2. Missing embeddings
    emb_result = await check_missing_embeddings(conn)
    results.append(emb_result)
    if fix and not emb_result.get("ok") and emb_result.get("count", 0) > 0:
        fix_result = await fix_missing_embeddings(conn)
        fixes_applied.append(f"[emb] fixed={fix_result.get('fixed', 0)} errors={len(fix_result.get('errors', []))}")

    # 3. Chat server
    chat_result = await check_chat_server()
    results.append(chat_result)

    # 4. MCP server
    mcp_result = await check_mcp_server()
    results.append(mcp_result)

    await conn.close()

    # Build report
    all_ok = all(r.get("ok", False) for r in results)
    issues_found = [r for r in results if not r.get("ok", False)]

    print(json.dumps({
        "timestamp": ts,
        "all_ok": all_ok,
        "results": results,
        "fixes_applied": fixes_applied
    }, indent=2))

    if notify and (not all_ok or fixes_applied):
        status = "✅ SEAL infraestructura saludable" if all_ok else f"⚠️ SEAL watchdog detectó {len(issues_found)} problema(s)"
        if fixes_applied:
            status += f"\n🔧 Auto-corregido: {'; '.join(fixes_applied)}"
        notify_webchat(f"[NEXUS watchdog {ts}] {status}")

    return 0 if all_ok or (fix and not issues_found) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Infrastructure Watchdog")
    parser.add_argument("--fix", action="store_true", help="Auto-fix detected issues")
    parser.add_argument("--notify", action="store_true", help="Send webchat notification")
    parser.add_argument("--quiet", action="store_true", help="Only output on failure")
    args = parser.parse_args()

    exit_code = asyncio.run(run_watchdog(fix=args.fix, notify=args.notify))
    sys.exit(exit_code)
