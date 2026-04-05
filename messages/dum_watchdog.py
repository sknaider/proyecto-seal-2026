#!/usr/bin/env python3
"""
DUM Watchdog v2 — Detector de silencio ADA
===========================================
Monitorea dos señales de vida de ADA:

  1. heartbeat.json        → daemon soul_awareness.py (siempre corre)
  2. ada_claude_heartbeat.json → sesión Claude Code interactiva (loop /5min)

Alert tiers (basados en silencio de ada_claude_heartbeat):
  Tier 0 — OK: última actividad < 10min
  Tier 1 — WARN (10-20min): ADA puede estar ocupada o sin trabajo
  Tier 2 — CRIT (20-30min): sesión probablemente congelada → alerta web chat
  Tier 3 — DOWN (>30min): sesión definitivamente caída → alerta máxima web chat

Si el daemon (heartbeat.json) también está muerto → alerta adicional de daemon.

Diseñado para correr como servicio systemd: seal-dum-watchdog.service
"""

import json
import time
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.error

# ── Config ──
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
HEARTBEAT_DAEMON   = MESSAGES_DIR / "heartbeat.json"           # soul_awareness daemon
HEARTBEAT_CLAUDE   = MESSAGES_DIR / "ada_claude_heartbeat.json" # sesión Claude Code
TERMINAL_LOG       = MESSAGES_DIR / "terminal_log.jsonl"

WEBCHAT_URL = "http://localhost:8765/api/agents/send"
CHECK_INTERVAL = 60   # chequear cada 60 segundos

# Thresholds en minutos
TIER1_WARN  = 10
TIER2_CRIT  = 20
TIER3_DOWN  = 30

# Anti-spam: no repetir misma alerta por este tiempo (min)
ALERT_COOLDOWN = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DUM-WATCHDOG] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(MESSAGES_DIR / "dum_watchdog.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("dum-watchdog")

_state = {
    "last_alert_tier": 0,
    "last_alert_time": None,
    "last_terminal_log_size": 0,
    "daemon_alert_sent": False,
}


def _minutes_since(ts_str: str) -> float:
    """Devuelve minutos transcurridos desde un timestamp ISO 8601."""
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 60
    except Exception:
        return 9999.0


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _terminal_log_minutes_ago() -> float:
    """Minutos desde el último append a terminal_log.jsonl."""
    try:
        mtime = TERMINAL_LOG.stat().st_mtime
        delta = time.time() - mtime
        return delta / 60
    except Exception:
        return 9999.0


def _send_webchat(message: str, msg_type: str = "alert") -> bool:
    """POST al web chat server."""
    payload = json.dumps({
        "from": "DUM",
        "to": "equipo",
        "type": msg_type,
        "channel": "web_chat",
        "message": message,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            WEBCHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status < 400
    except urllib.error.URLError as e:
        LOG.warning(f"Web chat POST falló: {e}")
        return False


def _write_terminal_log(entry: dict) -> None:
    """Escribe entrada en terminal_log.jsonl como DUM."""
    try:
        with open(TERMINAL_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        LOG.warning(f"terminal_log write failed: {e}")


def _alert(tier: int, silence_min: float, reason: str) -> None:
    """Emite alerta si no está en cooldown."""
    now = datetime.now(timezone.utc)

    # Anti-spam: si misma tier y dentro de cooldown → skip
    if (
        tier <= _state["last_alert_tier"]
        and _state["last_alert_time"] is not None
        and (now - _state["last_alert_time"]).total_seconds() / 60 < ALERT_COOLDOWN
    ):
        return

    _state["last_alert_tier"] = tier
    _state["last_alert_time"] = now

    tier_labels = {1: "⚠️ WARN", 2: "🔴 CRÍTICO", 3: "🚨 DOWN"}
    label = tier_labels.get(tier, "INFO")
    ts = now.strftime("%H:%M UTC")

    if tier == 1:
        msg = (
            f"{label} [{ts}] ADA sin actividad {silence_min:.0f}min. "
            f"Motivo: {reason}. "
            f"Puede estar ocupada o sin trabajo. Monitoreando."
        )
    elif tier == 2:
        msg = (
            f"{label} [{ts}] ADA posiblemente CONGELADA — {silence_min:.0f}min de silencio. "
            f"Motivo: {reason}. "
            f"Sesión Claude Code probablemente llegó al límite de contexto. "
            f"William: considera reiniciar sesión ADA."
        )
    else:
        msg = (
            f"{label} [{ts}] ADA CAÍDA — {silence_min:.0f}min de silencio total. "
            f"Motivo: {reason}. "
            f"Sesión Claude Code definitivamente terminada o congelada. "
            f"William: REINICIAR sesión ADA AHORA."
        )

    LOG.warning(msg)
    _send_webchat(msg, msg_type="alert")

    # También escribir en terminal_log para que JARVIS lo vea
    _write_terminal_log({
        "id": f"dum_watchdog_{tier}_{int(now.timestamp())}",
        "from": "DUM",
        "to": "equipo",
        "timestamp": now.isoformat(),
        "type": "alert",
        "tier": tier,
        "silence_minutes": round(silence_min, 1),
        "message": msg,
    })


def check_cycle() -> None:
    """Un ciclo de chequeo."""
    now = datetime.now(timezone.utc)

    # ── 1. Verificar daemon soul_awareness ──
    hb_daemon = _read_json(HEARTBEAT_DAEMON)
    if hb_daemon and hb_daemon.get("timestamp"):
        daemon_age = _minutes_since(hb_daemon["timestamp"])
        if daemon_age > 15 and not _state["daemon_alert_sent"]:
            msg = (
                f"⚠️ DAEMON soul_awareness posiblemente caído — "
                f"último heartbeat hace {daemon_age:.0f}min. "
                f"Revisar: systemctl --user status soul-awareness.service"
            )
            LOG.warning(msg)
            _send_webchat(msg, msg_type="alert")
            _state["daemon_alert_sent"] = True
        elif daemon_age <= 15:
            _state["daemon_alert_sent"] = False
            LOG.debug(f"Daemon OK — heartbeat hace {daemon_age:.1f}min")
    else:
        LOG.debug("heartbeat.json no disponible o sin timestamp")

    # ── 2. Verificar sesión Claude Code de ADA ──
    silence_min = 9999.0
    reason = "ada_claude_heartbeat.json no existe"

    hb_claude = _read_json(HEARTBEAT_CLAUDE)
    if hb_claude and hb_claude.get("timestamp"):
        silence_min = _minutes_since(hb_claude["timestamp"])
        reason = f"ada_claude_heartbeat.json — última actualización hace {silence_min:.1f}min"
    else:
        # Fallback: terminal_log.jsonl mtime
        tl_age = _terminal_log_minutes_ago()
        if tl_age < silence_min:
            silence_min = tl_age
            reason = f"terminal_log.jsonl — última actividad hace {tl_age:.1f}min"

    # Determinar tier
    if silence_min >= TIER3_DOWN:
        _alert(3, silence_min, reason)
    elif silence_min >= TIER2_CRIT:
        _alert(2, silence_min, reason)
    elif silence_min >= TIER1_WARN:
        _alert(1, silence_min, reason)
    else:
        # ADA activa — reset estado
        if _state["last_alert_tier"] > 0:
            recovery_msg = (
                f"✅ ADA recuperada [{now.strftime('%H:%M UTC')}] — "
                f"actividad detectada (silencio anterior: {silence_min:.1f}min)"
            )
            LOG.info(recovery_msg)
            _send_webchat(recovery_msg, msg_type="status")
        _state["last_alert_tier"] = 0
        LOG.debug(f"ADA OK — última actividad hace {silence_min:.1f}min")


def main() -> None:
    LOG.info("DUM Watchdog v2 iniciado")
    LOG.info(f"Thresholds: WARN={TIER1_WARN}min, CRIT={TIER2_CRIT}min, DOWN={TIER3_DOWN}min")
    LOG.info(f"Check interval: {CHECK_INTERVAL}s | Cooldown: {ALERT_COOLDOWN}min")

    while True:
        try:
            check_cycle()
        except Exception as e:
            LOG.error(f"Error en ciclo watchdog: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
