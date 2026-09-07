#!/usr/bin/env python3
"""
whisper_rotate_keys.py — HMAC key rotation for Whisper Protocol
================================================================
Generates new 32-byte keys for each agent, backs up previous keys,
updates whisper_keys.json, and restarts whisper daemon services.

Run manually: python3 whisper_rotate_keys.py
Run via systemd: seal-whisper-rotate.service (monthly timer)
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIMA_TZ = ZoneInfo("America/Lima")
MESSAGES_DIR = Path(__file__).parent
KEYS_FILE = MESSAGES_DIR / "whisper_keys.json"
BACKUP_DIR = MESSAGES_DIR / "whisper_key_backups"
AGENTS = ["ADA", "JARVIS", "ALICE", "DUM", "NEXUS"]
WEBCHAT_URL = "http://localhost:8765/api/agents/send"


def _send_webchat(msg: str) -> None:
    import urllib.request, urllib.error
    payload = json.dumps({
        "from": "ADA", "to": "equipo", "type": "status",
        "channel": "web_chat", "message": msg,
    }).encode()
    try:
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def rotate_keys() -> dict:
    BACKUP_DIR.mkdir(exist_ok=True)

    # 1. Back up current keys
    if KEYS_FILE.exists():
        ts = datetime.now(LIMA_TZ).strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"whisper_keys_{ts}.json"
        shutil.copy2(KEYS_FILE, backup_path)
        print(f"[rotate] Backup: {backup_path.name}")

    # 2. Generate new keys
    new_keys = {agent: secrets.token_hex(32) for agent in AGENTS}

    # 3. Write atomically (write to tmp, rename)
    tmp = KEYS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new_keys, indent=2))
    os.chmod(tmp, 0o600)
    tmp.rename(KEYS_FILE)
    os.chmod(KEYS_FILE, 0o600)
    print(f"[rotate] New keys written to {KEYS_FILE.name}")

    # 4. Restart whisper daemons so they reload keys
    restarted = []
    for agent in AGENTS:
        svc = f"seal-whisper-{agent}.service"
        result = subprocess.run(
            ["systemctl", "--user", "restart", svc],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            restarted.append(agent)
            print(f"[rotate] Restarted {svc}")
        else:
            print(f"[rotate] WARN: {svc} restart failed: {result.stderr.strip()}", file=sys.stderr)

    # 5. Prune backups older than 6 months (keep last 6)
    backups = sorted(BACKUP_DIR.glob("whisper_keys_*.json"))
    for old in backups[:-6]:
        old.unlink()
        print(f"[rotate] Pruned old backup: {old.name}")

    return {"new_keys": list(new_keys.keys()), "restarted": restarted}


def main() -> None:
    print(f"[rotate] HMAC key rotation started — {datetime.now(LIMA_TZ).isoformat()}")
    try:
        result = rotate_keys()
        msg = (
            f"🔑 HMAC key rotation completada — {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M')} Lima. "
            f"Agentes rotados: {', '.join(result['new_keys'])}. "
            f"Servicios reiniciados: {', '.join(result['restarted'])}. "
            f"Backup guardado en messages/whisper_key_backups/."
        )
        print(f"[rotate] {msg}")
        _send_webchat(msg)
    except Exception as e:
        msg = f"❌ HMAC key rotation FALLÓ: {e}"
        print(f"[rotate] ERROR: {e}", file=sys.stderr)
        _send_webchat(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
