#!/usr/bin/env python3
"""alice_monitor.py u2014 Monitoreo autu00f3nomo para ALICE.
Corre vu00eda systemd timer cada 15min. 0 tokens Claude.
Reporta a webchat solo si salience_score > 0.6.
Implementa pedido #3 de ALICE: existir entre conversaciones.
Autorizado por William, 26-abr-2026.
"""
import asyncio
import socket
from datetime import datetime

import aiohttp
import asyncpg

CHAT_API = "http://localhost:8765/api/agents/send"
PG_DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

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
    payload = {
        "from": "ALICE",
        "to": "William",
        "type": "observation",
        "channel": "web_chat",
        "message": message,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_API, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200
    except Exception:
        return False


async def store_memory(content: str, importance: int = 5):
    try:
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memories (agent, memory_type, category, content, importance, scope, created_at)
                VALUES ('ALICE', 'semantic', 'insight', $1, $2, 'agent', NOW())
                """,
                content, importance
            )
        await pool.close()
    except Exception as e:
        print(f"[alice_monitor] memory_store error: {e}")


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

    # 3. PostgreSQL u2014 conteo de memorias del equipo en la ultima hora
    try:
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE created_at > NOW() - INTERVAL '1 hour'"
            )
            if count and count > 100:
                findings.append(f"{count} memorias escritas en la ultima hora")
                salience_score += 0.2
        await pool.close()
    except Exception:
        pass

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
        report_lines = [f"[ALICE] Monitor autonomo {timestamp} u2014 hallazgos:"]
        report_lines.extend(
            f"  - {f}" for f in findings if not f.startswith("GPU:")
        )
        await post_webchat("\n".join(report_lines))
        print(f"[alice_monitor] Reportado al webchat (salience={salience_score:.2f})")
    else:
        print(f"[alice_monitor] Sin anomalias (salience={salience_score:.2f}) u2014 solo memoria interna")

    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
