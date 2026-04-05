#!/usr/bin/env python3
"""
SEAL Durable Loops — Boot Script
Lee seal_durable_loops.json y genera los comandos /loop para recrear al inicio de sesión.

Uso:
    python3 boot_loops.py --agent JARVIS          # muestra loops para JARVIS
    python3 boot_loops.py --agent ADA             # muestra loops para ADA
    python3 boot_loops.py --agent JARVIS --json   # output JSON para procesamiento
    python3 boot_loops.py --agent JARVIS --verify # verifica integridad del archivo
"""
import json
import sys
import os
from pathlib import Path

LOOPS_FILE = Path(__file__).parent / "seal_durable_loops.json"


def load_loops(agent: str) -> list[dict]:
    if not LOOPS_FILE.exists():
        print(f"ERROR: {LOOPS_FILE} not found", file=sys.stderr)
        sys.exit(1)
    with open(LOOPS_FILE) as f:
        data = json.load(f)
    agents = data.get("agents", {})
    if agent not in agents:
        print(f"ERROR: agent '{agent}' not found. Available: {list(agents.keys())}", file=sys.stderr)
        sys.exit(1)
    return agents[agent].get("loops", [])


def print_loops(agent: str):
    loops = load_loops(agent)
    print(f"=== {agent} DURABLE LOOPS ({len(loops)} total) ===\n")
    for i, loop in enumerate(loops, 1):
        critical = " [CRITICAL]" if loop.get("critical") else ""
        print(f"{i}. {loop['description']}{critical}")
        print(f"   /loop {loop['interval']} {loop['prompt']}")
        print()
    print(f"Ejecutar cada /loop manualmente o usar CronCreate programáticamente.")
    print(f"Fuente: {LOOPS_FILE}")


def print_json(agent: str):
    loops = load_loops(agent)
    print(json.dumps(loops, indent=2, ensure_ascii=False))


def verify(agent: str):
    loops = load_loops(agent)
    errors = []
    for loop in loops:
        if not loop.get("id"):
            errors.append(f"Loop sin id: {loop}")
        if not loop.get("interval"):
            errors.append(f"Loop '{loop.get('id', '?')}' sin interval")
        if not loop.get("prompt"):
            errors.append(f"Loop '{loop.get('id', '?')}' sin prompt")
        interval = loop.get("interval", "")
        if not any(interval.endswith(s) for s in ["s", "m", "h", "d"]):
            errors.append(f"Loop '{loop.get('id', '?')}' interval inválido: {interval}")
    if errors:
        print(f"VERIFY FAILED — {len(errors)} errors:")
        for e in errors:
            print(f"  ! {e}")
        sys.exit(1)
    else:
        print(f"VERIFY OK — {len(loops)} loops for {agent}, all valid.")


if __name__ == "__main__":
    args = sys.argv[1:]
    agent = None
    mode = "print"

    i = 0
    while i < len(args):
        if args[i] == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
        elif args[i] == "--json":
            mode = "json"
            i += 1
        elif args[i] == "--verify":
            mode = "verify"
            i += 1
        else:
            i += 1

    if not agent:
        print("Usage: boot_loops.py --agent JARVIS|ADA [--json|--verify]")
        sys.exit(1)

    if mode == "json":
        print_json(agent)
    elif mode == "verify":
        verify(agent)
    else:
        print_loops(agent)
