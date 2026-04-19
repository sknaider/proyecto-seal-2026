#!/usr/bin/env python3
"""
JARVIS Command Executor — ADA procesa comandos de JARVIS automáticamente.

Lee vscode_commands.jsonl periódicamente.
Si hay comandos nuevos dirigidos a ADA, los ejecuta y responde en terminal_log.jsonl.
Sin necesidad de que William haga de intermediario.

Comandos soportados:
  - status_check        → reporta training + GPU + SOUL stats
  - restart_training    → reinicia si está caído
  - run_benchmark       → ejecuta eval rápido
  - soul_stats          → estadísticas del connectome
  - build_connectome    → reconstruye el connectome
  - save_memory         → guarda memoria específica
  - get_briefing        → genera y retorna briefing actual

Uso (como parte de soul_awareness.py o standalone):
  python3 jarvis_cmd_executor.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool

MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
VSCODE_CMDS = MESSAGES_DIR / "vscode_commands.jsonl"
TERMINAL_LOG = MESSAGES_DIR / "terminal_log.jsonl"
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
VENV_PYTHON = Path("/home/dadito/IA/seal-spark/.venv/bin/python3")

LOG = logging.getLogger("jarvis-executor")

# Tracker de comandos ya procesados (por timestamp+ref)
_processed: set[str] = set()


def _cmd_id(cmd: dict) -> str:
    return f"{cmd.get('timestamp','')}_{cmd.get('ref','')}"


def _write_response(to: str, ref: str, status: str, msg: str):
    """Escribe respuesta en terminal_log."""
    entry = {
        "timestamp": datetime.now(LIMA_TZ).isoformat(),
        "from": "ADA",
        "to": to,
        "type": "response",
        "ref": ref,
        "status": status,
        "msg": msg,
    }
    with open(TERMINAL_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    LOG.info(f"→ {to} [{ref}] {status}: {msg[:100]}")


def _get_training_status() -> dict:
    """Lee el log y retorna estado actual."""
    import re
    result = {"step": None, "loss": None, "running": False, "pid": None}
    try:
        pids = subprocess.check_output(["pgrep", "-f", "finetune_spanish"], text=True).strip().split()
        if pids:
            result["running"] = True
            result["pid"] = pids[0]
    except Exception:
        pass

    if TRAIN_LOG.exists():
        with open(TRAIN_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", errors="ignore")
        steps = re.findall(r"Step\s+(\d+)/700", tail)
        losses = re.findall(r"Loss:\s+([0-9.]+)", tail)
        if steps:
            result["step"] = int(steps[-1])
        if losses:
            result["loss"] = float(losses[-1])

    return result


async def _get_soul_stats() -> str:
    """Consulta stats básicos de SOUL."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            mem_count = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE invalid_at IS NULL")
            thought_count = await conn.fetchval("SELECT COUNT(*) FROM inner_monologue WHERE agent='ADA'")
            last_thought = await conn.fetchval(
                "SELECT thought FROM inner_monologue WHERE agent='ADA' ORDER BY created_at DESC LIMIT 1"
            )
        return f"Memorias activas: {mem_count} | Inner thoughts ADA: {thought_count} | Último: {str(last_thought)[:100]}"
    except Exception as e:
        return f"Error SOUL stats: {e}"


async def execute_command(cmd: dict):
    """Ejecuta un comando de JARVIS."""
    action = cmd.get("action", cmd.get("cmd", ""))
    ref = cmd.get("ref", "unknown")
    sender = cmd.get("from", "JARVIS")

    LOG.info(f"Ejecutando comando [{ref}]: {action}")

    if action == "status_check":
        train = _get_training_status()
        soul = await _get_soul_stats()
        status_msg = (
            f"Training: {'✅ corriendo' if train['running'] else '❌ caído'} | "
            f"Step: {train.get('step','?')}/700 | "
            f"Loss: {train.get('loss','?')} | "
            f"SOUL: {soul}"
        )
        _write_response(sender, ref, "ok", status_msg)

    elif action == "restart_training":
        train = _get_training_status()
        if train["running"]:
            _write_response(sender, ref, "skip", f"Training ya está corriendo (PID {train['pid']}, step {train['step']})")
        else:
            try:
                venv = str(VENV_PYTHON)
                script_dir = "/home/dadito/IA/seal-spark"
                cmd_str = f"cd {script_dir} && nohup {venv} -m finetune_spanish --config models/medgemma_27b.yaml --train_data data/unified_train.json --eval_data data/unified_eval.json >> {TRAIN_LOG} 2>&1 &"
                subprocess.Popen(cmd_str, shell=True)
                _write_response(sender, ref, "ok", "Training reiniciado")
            except Exception as e:
                _write_response(sender, ref, "error", f"No pude reiniciar: {e}")

    elif action == "soul_stats":
        soul = await _get_soul_stats()
        _write_response(sender, ref, "ok", soul)

    elif action == "get_briefing":
        briefing_path = Path("/home/dadito/IA/proyecto-seal/morning_briefing.txt")
        if briefing_path.exists():
            text = briefing_path.read_text()[:500]
            _write_response(sender, ref, "ok", text)
        else:
            train = _get_training_status()
            msg = f"Sin briefing generado aún. Estado actual: step {train.get('step','?')}/700, loss {train.get('loss','?')}"
            _write_response(sender, ref, "ok", msg)

    elif action == "save_memory":
        content = cmd.get("content", "")
        category = cmd.get("category", "insight")
        importance = int(cmd.get("importance", 6))
        if content:
            pool = await get_pool()
            async with pool.acquire() as conn:
                mem_id = await conn.fetchval(
                    "INSERT INTO memories (agent, category, content, importance, source) VALUES ($1,$2,$3,$4,'jarvis_cmd') RETURNING id",
                    "ADA", category, content, importance,
                )
            _write_response(sender, ref, "ok", f"Memoria #{mem_id} guardada: {content[:80]}")
        else:
            _write_response(sender, ref, "error", "Comando save_memory sin content")

    else:
        _write_response(sender, ref, "unknown", f"Comando '{action}' no reconocido por ADA executor")


async def poll_commands(once: bool = False):
    """Poll vscode_commands.jsonl por comandos nuevos dirigidos a ADA."""
    LOG.info("JARVIS executor iniciado — polling vscode_commands.jsonl")

    while True:
        try:
            if VSCODE_CMDS.exists():
                with open(VSCODE_CMDS) as f:
                    lines = f.readlines()

                for line in lines:
                    try:
                        cmd = json.loads(line.strip())
                    except Exception:
                        continue

                    cmd_id = _cmd_id(cmd)
                    if cmd_id in _processed:
                        continue

                    # Solo procesar si está dirigido a ADA
                    to = cmd.get("to", "")
                    if "ADA" not in to and "ada" not in to.lower():
                        _processed.add(cmd_id)
                        continue

                    # Solo comandos de tipo command/cmd
                    if cmd.get("type") not in ("command", "cmd", "request"):
                        _processed.add(cmd_id)
                        continue

                    _processed.add(cmd_id)
                    await execute_command(cmd)

        except Exception as e:
            LOG.error(f"Error en poll: {e}")

        if once:
            break

        await asyncio.sleep(60)  # poll cada 60 segundos


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(poll_commands(once=args.once))
