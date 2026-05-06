#!/usr/bin/env python3
"""ADA DM Console — canal privado 1:1 William <-> ADA-nativa.

Escribe directo a dm_william_ada.jsonl (no API, no web_chat).
Equipo SEAL no ve este canal — es DM puro.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Override SEAL_AGENT — esta consola es William hablando, no el agente que la lanzó.
# El audit de privacidad reconoce WILLIAM como dueño legítimo de cualquier DM propio.
os.environ["SEAL_AGENT"] = "WILLIAM"

DM_PATH = Path.home() / ".private" / "seal_dms" / "dm_william_ada.jsonl"

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

PROMPT = f"{GREEN}william> {RESET}"


def post_to_ada(text: str) -> None:
    """Append directly to DM JSONL — bypass web_chat backend entirely."""
    entry = {
        "id": f"dm_william_{uuid.uuid4().hex[:16]}",
        "from": "William",
        "to": "ADA",
        "type": "conversation",
        "channel": "dm",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": text,
    }
    DM_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DM_PATH.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def tail_ada_responses() -> None:
    DM_PATH.parent.mkdir(parents=True, exist_ok=True)
    DM_PATH.touch(exist_ok=True)
    proc = subprocess.Popen(
        ["tail", "-n", "0", "-F", str(DM_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("from") != "ADA":
            continue
        msg = event.get("message", "")
        if msg.startswith("[ADA] 🔄 procesando"):
            continue
        ts = event.get("timestamp", "")[11:19]
        sys.stdout.write("\r\033[K")
        sys.stdout.write(f"{CYAN}{BOLD}[ADA {ts}]{RESET} {msg}\n")
        sys.stdout.write(PROMPT)
        sys.stdout.flush()


def main() -> int:
    print(f"{CYAN}{BOLD}═══════════════════════════════════════════════{RESET}")
    print(f"{CYAN}{BOLD}  ADA DM Console — canal privado{RESET}")
    print(f"{CYAN}{BOLD}═══════════════════════════════════════════════{RESET}")
    print(f"{GREY}Canal  : dm_william_ada.jsonl (NO visible al equipo){RESET}")
    print(f"{GREY}Daemon : python3 sandbox-agent/ADA/kernel/ada_kernel_main.py{RESET}")
    print(f"{GREY}Modelo : Triangle Qwen3-Coder (vLLM T1) + Ollama T2{RESET}")
    print(f"{GREY}Salir  : /quit  o  Ctrl-D{RESET}")
    print()

    t = threading.Thread(target=tail_ada_responses, daemon=True)
    t.start()
    time.sleep(0.3)

    while True:
        try:
            text = input(PROMPT).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{GREY}bye.{RESET}")
            return 0
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            print(f"{GREY}bye.{RESET}")
            return 0
        post_to_ada(text)


if __name__ == "__main__":
    sys.exit(main())
