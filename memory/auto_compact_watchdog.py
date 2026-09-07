"""auto_compact_watchdog.py — SEAL Auto-Compact Guardian

Monitors all SEAL agents for "Context limit reached" in their Kitty windows.
When detected, automatically sends /compact and logs the action.

Run as systemd service: seal-auto-compact.service
Polls every 20 seconds.
"""
import subprocess
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

POLL_INTERVAL = 20  # seconds between checks

AGENTS = {
    "ADA":   "/tmp/seal-ada-kitty.sock",
    "JARVIS": "/tmp/seal-jarvis-kitty.sock",
    "ALICE": "/tmp/seal-alice-kitty.sock",
}

# Use Claude Code's specific task-output prefix to avoid false positives
# from shell commands that contain the phrase as text
CONTEXT_LIMIT_PHRASES = [
    "Context limit reached · /compact or /clear to continue",
]

CHAT_URL = "http://localhost:8765/api/agents/send"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [auto-compact] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Per-agent state: track last compact time to avoid hammering
_last_compact: dict[str, float] = {}
_COMPACT_COOLDOWN = 120  # seconds between compacts for same agent


def read_kitty_window(sock_path: str) -> str:
    """Read current Kitty window text via kitty @ get-text."""
    try:
        result = subprocess.run(
            ["kitten", "@", "--to", f"unix:{sock_path}", "get-text"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def send_compact(agent: str, sock_path: str) -> bool:
    """Send /compact to agent's Kitty window."""
    try:
        result = subprocess.run(
            ["kitten", "@", "--to", f"unix:{sock_path}", "send-text", "/compact\r"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def notify_webchat(agent: str, message: str) -> None:
    try:
        requests.post(CHAT_URL, json={
            "from": "DUM",
            "to": "equipo",
            "type": "system",
            "channel": "web_chat",
            "message": f"[AUTO-COMPACT] {agent}: {message}",
        }, timeout=3)
    except Exception:
        pass


def check_agent(agent: str, sock_path: str) -> None:
    sock = Path(sock_path)
    if not sock.exists():
        return

    text = read_kitty_window(sock_path)
    if not text:
        return

    has_limit = any(phrase in text for phrase in CONTEXT_LIMIT_PHRASES)
    if not has_limit:
        return

    now = time.time()
    last = _last_compact.get(agent, 0)
    if now - last < _COMPACT_COOLDOWN:
        return

    log.info(f"{agent}: context limit detected — sending /compact")
    _last_compact[agent] = now

    ok = send_compact(agent, sock_path)
    if ok:
        log.info(f"{agent}: /compact sent successfully")
        notify_webchat(agent, "contexto lleno detectado — /compact enviado automáticamente")
    else:
        log.warning(f"{agent}: failed to send /compact")


def main() -> None:
    log.info("SEAL Auto-Compact Watchdog started — polling every %ds", POLL_INTERVAL)
    while True:
        for agent, sock_path in AGENTS.items():
            try:
                check_agent(agent, sock_path)
            except Exception as exc:
                log.error(f"{agent}: unexpected error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
