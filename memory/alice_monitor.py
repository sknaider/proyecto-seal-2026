#!/usr/bin/env python3
"""alice_monitor.py — Monitoreo autónomo para ALICE.
Corre vía systemd timer cada 15min. 0 tokens Claude.
Reporta a webchat solo si salience_score > 0.6.
Implementa pedido #3 de ALICE: existir entre conversaciones.
Autorizado por William, 26-abr-2026.
"""
import asyncio
import hashlib
import json
import socket
import subprocess
from datetime import datetime
from pathlib import Path

import asyncpg
from memory_admission import audit_memory_skip_event, memory_auto_event_skip_reason
from seal_secrets import pg_dsn

PG_DSN = pg_dsn(required=True)
SEAL_SEND = (
    Path("/home/dadito/IA/proyecto-seal") / "scripts/seal_send.py"
)

MONITOR_SERVICES = {
    "webchat": ("localhost", 8765),
    "mcp_sse": ("localhost", 8771),
    "neo4j": ("localhost", 7474),
}


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def gpu_stats() -> dict:
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 4:
                return {
                    "util_pct": int(parts[0]),
                    "temp_c": int(parts[1]),
                    "mem_used_mb": int(parts[2]),
                    "mem_total_mb": int(parts[3]),
                }
    except Exception:
        pass
    return {}


async def post_webchat(message: str) -> bool:
    """Deliver an authenticated alert; success requires a server ACK."""
    key = "alice-monitor-" + hashlib.sha256(
        message.encode("utf-8")
    ).hexdigest()[:32]

    def _send() -> bool:
        try:
            completed = subprocess.run(
                [
                    "python3",
                    str(SEAL_SEND),
                    "ALICE",
                    "William",
                    message,
                    "--channel",
                    "dm:alice:william",
                    "--type",
                    "observation",
                    "--idempotency-key",
                    key,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception:
            return False
        if completed.returncode != 0:
            return False
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return False
        return payload.get("ok") is True

    return await asyncio.to_thread(_send)


async def store_memory(content: str, importance: int = 5):
    pool = None
    try:
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            skip_reason = memory_auto_event_skip_reason(
                agent="ALICE",
                category="insight",
                content=content,
                source="alice_monitor",
                importance=importance,
            )
            if skip_reason:
                await audit_memory_skip_event(
                    conn,
                    agent="ALICE",
                    category="insight",
                    content=content,
                    source="alice_monitor",
                    importance=importance,
                    reason=skip_reason,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO memories (agent, memory_type, category, content, importance, scope, created_at)
                    VALUES ('ALICE', 'semantic', 'insight', $1, $2, 'private', NOW())
                    """,
                    content, importance
                )
    except Exception as e:
        raise RuntimeError("alice_monitor_memory_store_failed") from e
    finally:
        if pool is not None:
            await pool.close()


async def main():
    findings = []
    salience_score = 0.0

    # 1. Servicios SEAL
    down_services = []
    for name, (host, port) in MONITOR_SERVICES.items():
        if not check_port(host, port):
            down_services.append(name)

    if down_services:
        findings.append(f"Servicios caidos: {', '.join(down_services)}")
        salience_score += 0.5 * len(down_services)

    # 2. GPU stats
    gpu = gpu_stats()
    if gpu:
        util = gpu.get("util_pct", 0)
        temp = gpu.get("temp_c", 0)
        mem_used = gpu.get("mem_used_mb", 0)
        mem_total = gpu.get("mem_total_mb", 1)
        mem_pct = (mem_used / mem_total) * 100 if mem_total else 0

        if temp > 80:
            findings.append(f"GPU a {temp}C CRITICO")
            salience_score += 0.7
        elif temp > 70:
            findings.append(f"GPU a {temp}C elevada")
            salience_score += 0.3

        if mem_pct > 90:
            findings.append(f"VRAM al {mem_pct:.0f}% ({mem_used/1024:.1f}/{mem_total/1024:.1f}GB)")
            salience_score += 0.5

        findings.append(f"GPU: {util}% util, {temp}C, {mem_used/1024:.1f}/{mem_total/1024:.1f}GB VRAM")

    timestamp = datetime.now().strftime("%H:%M")

    # Siempre guardar como memoria interna (0 tokens Claude)
    summary = (
        f"[alice_monitor {timestamp}] " + " | ".join(findings)
        if findings
        else f"[alice_monitor {timestamp}] SEAL operativo"
    )
    await store_memory(summary, importance=4 if not down_services else 7)

    # Solo postear al webchat si salience > 0.6
    if salience_score > 0.6:
        report_lines = [f"[ALICE] Monitor autónomo {timestamp} — hallazgos:"]
        report_lines.extend(
            f"  - {f}" for f in findings if not f.startswith("GPU:")
        )
        delivered = await post_webchat("\n".join(report_lines))
        if not delivered:
            raise RuntimeError("alice_monitor_alert_not_delivered")
        print(
            "[alice_monitor] Reportado por DM autenticado "
            f"(salience={salience_score:.2f})"
        )
    else:
        print(
            "[alice_monitor] Sin anomalías "
            f"(salience={salience_score:.2f}) — solo memoria interna"
        )

    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
