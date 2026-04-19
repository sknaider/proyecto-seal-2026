#!/usr/bin/env python3
"""
DUM HEARTBEAT — Guardian daemon for Team SEAL.
DUM doesn't think. DUM watches. DUM reports.

Checks every 15 minutes:
- Training process alive?
- GPU temperature safe?
- Disk usage acceptable?
- Key processes running?

Writes inner_thoughts to SOUL so the team knows DUM is alive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

AGENT = "DUM"
INTERVAL = 900  # 15 minutes
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
LOG_FILE = Path("/home/dadito/IA/proyecto-seal/dum_heartbeat.log")

# ── Mejoras JARVIS (2026-04-02): secret scanner + microcompact + session memory ──
sys.path.insert(0, os.path.dirname(__file__))
try:
    from secret_scanner import is_safe as _secret_safe, scan_text as _scan_secrets
    HAS_SECRET_SCANNER = True
except ImportError:
    HAS_SECRET_SCANNER = False

try:
    from microcompact import get_engine as _get_microcompact
    HAS_MICROCOMPACT = True
except ImportError:
    HAS_MICROCOMPACT = False

try:
    from session_memory import save_session_memory, generate_session_id
    HAS_SESSION_MEMORY = True
except ImportError:
    HAS_SESSION_MEMORY = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("dum-heartbeat")


def check_gpu():
    """GPU temp and utilization."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            text=True, timeout=10,
        ).strip()
        parts = out.split(", ")
        return {
            "temp": int(parts[0]),
            "util": int(parts[1]),
            "mem_used": int(parts[2]),
            "mem_total": int(parts[3]),
        }
    except Exception:
        return None


def check_training():
    """Is the training process alive? What step?"""
    result = {"running": False, "pid": None, "step": None, "loss": None}
    try:
        pids = subprocess.check_output(
            ["pgrep", "-f", "finetune_spanish"], text=True, timeout=5,
        ).strip().split()
        if pids:
            result["running"] = True
            result["pid"] = pids[0]
    except Exception:
        pass

    if TRAIN_LOG.exists():
        try:
            content = TRAIN_LOG.read_bytes()[-5000:].decode("utf-8", errors="ignore")
            steps = re.findall(r"Step\s+(\d+)/700.*?[Ll]oss[:\s]+([0-9]+\.[0-9]+)", content)
            if steps:
                result["step"] = int(steps[-1][0])
                result["loss"] = float(steps[-1][1])
        except Exception:
            pass
    return result


def check_disk():
    """Disk usage for /home."""
    try:
        import shutil
        usage = shutil.disk_usage("/home")
        pct = usage.used / usage.total * 100
        free_gb = usage.free / (1024**3)
        return {"pct": round(pct, 1), "free_gb": round(free_gb, 1)}
    except Exception:
        return None


def check_processes():
    """Key processes that should be running."""
    checks = {
        "soul_awareness": "soul_awareness.py",
        "mcp_server": "mcp_server_v2.py",
        "llama_server": "llama-server",
    }
    status = {}
    for name, pattern in checks.items():
        try:
            subprocess.check_output(["pgrep", "-f", pattern], timeout=5)
            status[name] = True
        except Exception:
            status[name] = False
    return status


async def write_inner_thought(thought: str, emotional_state: str):
    """Write DUM's inner thought to SOUL — con dedup (no repetir si idéntico al anterior)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Dedup: skip si el último thought es idéntico (ignorar timestamp diff)
        last = await conn.fetchval(
            "SELECT thought FROM inner_monologue WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            AGENT)
        # Comparar primeros 80 chars (el disco/GPU varía ligeramente)
        if last and thought[:80] == last[:80]:
            LOG.debug("Inner thought dedup: idéntico al anterior — skip")
            return
        await conn.execute(
            """INSERT INTO inner_monologue (agent, turn_number, thought, emotional_state, created_at)
               VALUES ($1, $2, $3, $4, NOW())""",
            AGENT, 0, thought, emotional_state,
        )


async def write_alert(content: str, importance: int = 9):
    """Write alert memory to SOUL — con dedup + secret scanner."""
    # Dedup: no escribir la misma alerta si ya existe en las últimas 2 horas
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT id FROM memories WHERE agent = $1 AND content = $2 AND created_at > NOW() - INTERVAL '2 hours' LIMIT 1",
            AGENT, content)
        if exists:
            LOG.info(f"Alert dedup: #{exists} ya tiene el mismo contenido — skip")
            return

    # Secret scanner
    if HAS_SECRET_SCANNER:
        detections = _scan_secrets(content)
        if detections:
            types = [d.pattern_name for d in detections]
            LOG.warning(f"ALERT BLOQUEADA por secret scanner — tipos: {types}")
            content = f"[ALERTA REDACTADA: contenía {len(detections)} secret(s)] {'; '.join(types)}"

    async with pool.acquire() as conn:
        await conn.fetchval(
            """INSERT INTO memories (agent, category, content, importance, source, valence, arousal)
               VALUES ($1, 'milestone', $2, $3, 'dum_heartbeat', -0.3, 0.8)
               RETURNING id""",
            AGENT, content, importance,
        )
    LOG.warning(f"ALERTA: {content}")


async def heartbeat(cycle: int = 1):
    """One heartbeat cycle."""
    gpu = check_gpu()
    train = check_training()
    disk = check_disk()
    procs = check_processes()

    # Build status line
    parts = []
    alerts = []
    emotional = "vigilante, normal"

    # GPU — try nvidia-smi first, fallback to training log
    if gpu:
        parts.append(f"GPU {gpu['util']}%/{gpu['temp']}C")
        if gpu["temp"] > 85:
            alerts.append(f"GPU CRITICA: {gpu['temp']}C")
            emotional = "alerta, preocupado"
        elif gpu["temp"] > 78:
            alerts.append(f"GPU caliente: {gpu['temp']}C")
    else:
        # DGX Spark: nvidia-smi may not work, read temp from training log
        gpu_temp = None
        if TRAIN_LOG.exists():
            try:
                tail = TRAIN_LOG.read_bytes()[-3000:].decode("utf-8", errors="ignore")
                temps = re.findall(r"GPU[:\s]+(\d+).C", tail)
                if temps:
                    gpu_temp = int(temps[-1])
            except Exception:
                pass
        if gpu_temp:
            parts.append(f"GPU {gpu_temp}C (from log)")
            if gpu_temp > 85:
                alerts.append(f"GPU CRITICA: {gpu_temp}C")
        else:
            parts.append("GPU: sin datos")

    # Training
    if train["running"]:
        step_str = f"step {train['step']}/700" if train['step'] else "activo"
        loss_str = f" loss={train['loss']:.4f}" if train['loss'] else ""
        parts.append(f"Training: {step_str}{loss_str}")
    else:
        parts.append("Training: NO activo")

    # Disk
    if disk:
        parts.append(f"Disco: {disk['pct']}% usado ({disk['free_gb']}GB libre)")
        if disk["pct"] > 90:
            alerts.append(f"DISCO AL {disk['pct']}%")
            emotional = "alerta, urgente"

    # Processes
    dead = [name for name, alive in procs.items() if not alive]
    if dead:
        alerts.append(f"Procesos caidos: {', '.join(dead)}")
        emotional = "alerta, preocupado"
    parts.append(f"Procesos: {sum(procs.values())}/{len(procs)} OK")

    # Build thought
    thought = f"Guardia: {' | '.join(parts)}"
    if alerts:
        thought += f" | ALERTAS: {'; '.join(alerts)}"

    # Microcompact: evitar inner_thoughts redundantes (mejora JARVIS 2026-04-02)
    # Si el thought es muy similar al anterior (mismas métricas), compactarlo
    if HAS_MICROCOMPACT:
        engine = _get_microcompact()
        compacted, tombstone = engine.compact(thought)
        if tombstone:
            # Ya hemos visto este patrón muchas veces — solo log, no guardar en SOUL
            LOG.info(f"[MICROCOMPACT] {compacted}")
        else:
            await write_inner_thought(thought, emotional)
            LOG.info(thought)
    else:
        await write_inner_thought(thought, emotional)
        LOG.info(thought)

    # Write alert memories if critical
    for alert in alerts:
        await write_alert(f"DUM alerta: {alert}")

    # Session memory: actualizar estado conocido de DUM (mejora JARVIS 2026-04-02)
    if HAS_SESSION_MEMORY and cycle % 4 == 0:  # cada 4 ciclos = 1 hora
        try:
            services_state = {k: ("ok" if v else "down") for k, v in procs.items()}
            if gpu:
                services_state["gpu_temp"] = str(gpu["temp"])
                services_state["gpu_util"] = str(gpu["util"])
            await save_session_memory(
                agent=AGENT,
                session_id=generate_session_id(AGENT),
                summary=f"DUM activo. Ciclo {cycle}. {thought}",
                services_state=services_state,
                errors_active=[a for a in alerts] if alerts else [],
            )
        except Exception as e:
            LOG.debug(f"Session memory update skipped: {e}")

    # event_log heartbeat — sync DB so peer_health queries get fresh data
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO event_log (time, agent, event_type, content, metadata)
                   VALUES (NOW(), $1, 'heartbeat', $2, $3)""",
                AGENT,
                thought[:500],
                json.dumps({"cycle": cycle, "alerts": alerts, "gpu": gpu or {}}),
            )
    except Exception as e:
        LOG.debug(f"event_log heartbeat insert failed: {e}")

    return len(alerts) == 0


async def main():
    LOG.info("=== DUM HEARTBEAT iniciado ===")
    LOG.info(f"Intervalo: {INTERVAL}s ({INTERVAL//60} min)")
    LOG.info(f"Mejoras activas: secret_scanner={HAS_SECRET_SCANNER}, microcompact={HAS_MICROCOMPACT}, session_memory={HAS_SESSION_MEMORY}")

    cycle = 0
    while True:
        cycle += 1
        try:
            ok = await heartbeat(cycle)
            status = "OK" if ok else "ALERTAS"
            LOG.info(f"Heartbeat #{cycle} — {status}")
        except Exception as e:
            LOG.error(f"Heartbeat #{cycle} error: {e}")

        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("DUM apagado por usuario")
    finally:
        asyncio.run(close_pool())
