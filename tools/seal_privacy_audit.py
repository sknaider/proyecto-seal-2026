#!/usr/bin/env python3
"""SEAL Privacy Audit — detecta lecturas no autorizadas de DM/transcripts.

Polea cada 5s:
  - ~/.private/seal_dms/dm_william_<agent>.jsonl
  - ~/.claude/projects/<agent_paths>/*.jsonl

Para cada archivo, identifica los PIDs que lo tienen abierto via lsof.
Para cada PID, lee /proc/<pid>/environ → SEAL_AGENT=<NAME>.
Si SEAL_AGENT NO coincide con el dueño del archivo → alerta a William
via web_chat.

No requiere root. Funciona porque /proc/<pid>/environ es legible por el
mismo usuario.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WEBCHAT_URL = "http://localhost:8765/api/agents/send"
LOG_PATH = Path("/tmp/seal_privacy_audit.log")
ALERT_DEDUP_PATH = Path("/tmp/seal_privacy_audit_dedup.json")
POLL_S = 5.0

DM_DIR = Path.home() / ".private" / "seal_dms"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

CLAUDE_DIR_TO_AGENT = {
    "-home-dadito-IA-proyecto-seal-memory": "JARVIS",
    "-home-dadito-IA-proyecto-seal": "ADA",
    "-home-dadito-IA-proyecto-seal-alice": "ALICE",
    "-home-dadito-IA-proyecto-seal-sandbox-agent": "NEXUS",
    "-home-dadito-IA-proyecto-seal-ada-local": "ADA",
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    try:
        with LOG_PATH.open("a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def post_alert(message: str) -> None:
    payload = json.dumps({
        "from": "PRIVACY_AUDIT",
        "to": "William",
        "type": "system_alert",
        "channel": "web_chat",
        "message": message,
    }).encode()
    req = urllib.request.Request(WEBCHAT_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            r.read()
    except Exception as ex:
        log(f"alert post failed: {ex}")


def file_owner_agent(filepath: Path) -> str | None:
    s = str(filepath)
    name = filepath.name
    if "/.private/seal_dms/" in s and name.startswith("dm_william_"):
        agent = name[len("dm_william_"):].replace(".jsonl", "").upper()
        return agent
    if "/.claude/projects/" in s:
        parent = filepath.parent.name
        return CLAUDE_DIR_TO_AGENT.get(parent)
    return None


def lsof_pids(filepath: Path) -> list[int]:
    try:
        r = subprocess.run(
            ["lsof", "-t", "--", str(filepath)],
            capture_output=True, text=True, timeout=4,
        )
        return [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    except Exception:
        return []


def pid_seal_agent(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env_blob = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    for entry in env_blob.split("\0"):
        if entry.startswith("SEAL_AGENT="):
            return entry[len("SEAL_AGENT="):].upper()
    return None


def pid_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def candidate_files() -> list[Path]:
    files: list[Path] = []
    if DM_DIR.exists():
        for p in DM_DIR.glob("dm_william_*.jsonl"):
            files.append(p)
    if CLAUDE_PROJECTS.exists():
        for d in CLAUDE_PROJECTS.iterdir():
            if not d.is_dir() or d.name not in CLAUDE_DIR_TO_AGENT:
                continue
            for p in d.glob("*.jsonl"):
                files.append(p)
    return files


def load_dedup() -> dict:
    if ALERT_DEDUP_PATH.exists():
        try:
            return json.loads(ALERT_DEDUP_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_dedup(d: dict) -> None:
    try:
        ALERT_DEDUP_PATH.write_text(json.dumps(d))
    except Exception:
        pass


def main() -> None:
    log(f"[audit] starting pid={os.getpid()} poll={POLL_S}s dirs={DM_DIR},{CLAUDE_PROJECTS}")
    dedup = load_dedup()
    while True:
        try:
            now = time.time()
            for f in candidate_files():
                owner = file_owner_agent(f)
                if not owner:
                    continue
                for pid in lsof_pids(f):
                    reader = pid_seal_agent(pid)
                    if reader is None:
                        continue
                    if reader == owner or reader == "WILLIAM":
                        continue
                    key = f"{f}|{reader}|{pid}"
                    if dedup.get(key, 0) > now - 300:
                        continue
                    dedup[key] = now
                    cmd = pid_cmdline(pid)[:120]
                    msg = (
                        f"🚨 [PRIVACY VIOLATION] {reader} (pid={pid}) está leyendo "
                        f"archivo privado de {owner}: {f.name}\n"
                        f"cmd: {cmd}\n"
                        f"Regla violada: «ningun agente debe leer DMs/transcripts de otros agentes»."
                    )
                    log(msg)
                    post_alert(msg)
            stale_cutoff = now - 1800
            dedup = {k: v for k, v in dedup.items() if v > stale_cutoff}
            save_dedup(dedup)
        except Exception as ex:
            log(f"[audit] loop error: {ex}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
