#!/usr/bin/env python3
"""
DUM Watchdog v3 — Detector de silencio ADA + JARVIS
=====================================================
Monitorea señales de vida de ADA y JARVIS:

  1. heartbeat.json             → daemon soul_awareness.py (siempre corre)
  2. ada_claude_heartbeat.json  → sesión Claude Code ADA (loop /5min)
  3. jarvis_claude_heartbeat.json → sesión Claude Code JARVIS (loop /5min)

Alert tiers (basados en silencio de heartbeat):
  Tier 0 — OK: última actividad < 10min
  Tier 1 — WARN (10-20min): agente puede estar ocupado o sin trabajo
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
HEARTBEAT_DAEMON   = MESSAGES_DIR / "heartbeat.json"                  # soul_awareness daemon
HEARTBEAT_ADA      = MESSAGES_DIR / "ada_claude_heartbeat.json"       # sesión Claude Code ADA
HEARTBEAT_JARVIS   = MESSAGES_DIR / "jarvis_claude_heartbeat.json"    # sesión Claude Code JARVIS
HEARTBEAT_CLAUDE   = HEARTBEAT_ADA  # alias para compatibilidad
TERMINAL_LOG       = MESSAGES_DIR / "terminal_log.jsonl"

WEBCHAT_URL = "http://localhost:8765/api/agents/send"
CHECK_INTERVAL = 60   # chequear cada 60 segundos

# Thresholds en minutos
TIER1_WARN  = 10
TIER2_CRIT  = 20
TIER3_DOWN  = 30

# Anti-spam: no repetir misma alerta por este tiempo (min)
ALERT_COOLDOWN = 15

# Auto-restart config
SEAL_RESTART_SCRIPT = Path("/home/dadito/IA/proyecto-seal/seal_restart.sh")
AUTO_RESTART_ENABLED = True
AUTO_RESTART_COOLDOWN = 10  # minutos entre auto-restarts del mismo agente
AUTO_RESTART_MAX = 3        # máximo restarts por agente por hora

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
    # ADA
    "ada_last_alert_tier": 0,
    "ada_last_alert_time": None,
    "ada_restart_times": [],       # timestamps de auto-restarts
    "ada_last_restart_time": None,
    # JARVIS
    "jarvis_last_alert_tier": 0,
    "jarvis_last_alert_time": None,
    "jarvis_restart_times": [],
    "jarvis_last_restart_time": None,
    # Compat alias (ADA)
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


def _alert(tier: int, silence_min: float, reason: str, agent: str = "ADA") -> None:
    """Emite alerta si no está en cooldown. agent = 'ADA' o 'JARVIS'."""
    now = datetime.now(timezone.utc)
    tier_key = f"{agent.lower()}_last_alert_tier"
    time_key = f"{agent.lower()}_last_alert_time"

    # Anti-spam: si misma tier y dentro de cooldown → skip
    if (
        tier <= _state[tier_key]
        and _state[time_key] is not None
        and (now - _state[time_key]).total_seconds() / 60 < ALERT_COOLDOWN
    ):
        return

    _state[tier_key] = tier
    _state[time_key] = now
    # compat alias
    if agent == "ADA":
        _state["last_alert_tier"] = tier
        _state["last_alert_time"] = now

    tier_labels = {1: "⚠️ WARN", 2: "🔴 CRÍTICO", 3: "🚨 DOWN"}
    label = tier_labels.get(tier, "INFO")
    ts = now.strftime("%H:%M UTC")

    if tier == 1:
        msg = (
            f"{label} [{ts}] {agent} sin actividad {silence_min:.0f}min. "
            f"Motivo: {reason}. "
            f"Puede estar ocupado o sin trabajo. Monitoreando."
        )
    elif tier == 2:
        msg = (
            f"{label} [{ts}] {agent} posiblemente CONGELADO — {silence_min:.0f}min de silencio. "
            f"Motivo: {reason}. "
            f"Sesión Claude Code probablemente llegó al límite de contexto. "
            f"William: considera reiniciar sesión {agent}."
        )
    else:
        msg = (
            f"{label} [{ts}] {agent} CAÍDO — {silence_min:.0f}min de silencio total. "
            f"Motivo: {reason}. "
            f"Sesión Claude Code definitivamente terminada o congelada. "
            f"William: REINICIAR sesión {agent} AHORA."
        )

    LOG.warning(msg)
    _send_webchat(msg, msg_type="alert")

    # También escribir en terminal_log para que el equipo lo vea
    _write_terminal_log({
        "id": f"dum_watchdog_{agent.lower()}_{tier}_{int(now.timestamp())}",
        "from": "DUM",
        "to": "equipo",
        "timestamp": now.isoformat(),
        "type": "alert",
        "agent": agent,
        "tier": tier,
        "silence_minutes": round(silence_min, 1),
        "message": msg,
    })


def _auto_restart_agent(agent: str) -> bool:
    """Intenta reiniciar un agente automáticamente. Retorna True si lo hizo."""
    if not AUTO_RESTART_ENABLED or not SEAL_RESTART_SCRIPT.exists():
        LOG.warning(f"Auto-restart deshabilitado o script no existe: {SEAL_RESTART_SCRIPT}")
        return False

    now = datetime.now(timezone.utc)
    agent_lower = agent.lower()
    restart_key = f"{agent_lower}_restart_times"
    last_key = f"{agent_lower}_last_restart_time"

    # Cooldown check
    if _state[last_key] is not None:
        mins_since_last = (now - _state[last_key]).total_seconds() / 60
        if mins_since_last < AUTO_RESTART_COOLDOWN:
            LOG.info(f"Auto-restart {agent} en cooldown ({mins_since_last:.0f}/{AUTO_RESTART_COOLDOWN}min)")
            return False

    # Rate limit: max restarts por hora
    one_hour_ago = now - timedelta(hours=1)
    _state[restart_key] = [t for t in _state[restart_key] if t > one_hour_ago]
    if len(_state[restart_key]) >= AUTO_RESTART_MAX:
        msg = (
            f"🛑 AUTO-RESTART {agent} BLOQUEADO — ya se reinició {AUTO_RESTART_MAX} veces en la última hora. "
            f"Posible loop de crashes. William: revisar manualmente."
        )
        LOG.error(msg)
        _send_webchat(msg, msg_type="alert")
        return False

    # Ejecutar restart
    LOG.info(f"🔄 AUTO-RESTART {agent} — ejecutando seal_restart.sh...")
    try:
        import subprocess
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        result = subprocess.run(
            ["bash", str(SEAL_RESTART_SCRIPT), agent_lower],
            capture_output=True, text=True, timeout=60, env=env,
        )
        success = result.returncode == 0
        _state[restart_key].append(now)
        _state[last_key] = now

        if success:
            msg = (
                f"🔄 AUTO-RESTART {agent} ejecutado [{now.strftime('%H:%M UTC')}] — "
                f"seal_restart.sh completó OK. Restart #{len(_state[restart_key])} esta hora."
            )
            LOG.info(msg)
            _send_webchat(msg, msg_type="status")

            # Escribir en ada/jarvis_messages para que el agente sepa al despertar
            agent_log = MESSAGES_DIR / f"{agent_lower}_messages.jsonl"
            try:
                with open(agent_log, "a") as f:
                    f.write(json.dumps({
                        "id": f"dum_autorestart_{agent_lower}_{int(now.timestamp())}",
                        "from": "DUM",
                        "to": agent,
                        "timestamp": now.isoformat(),
                        "type": "auto_restart",
                        "message": f"Te reinicié automáticamente porque llevabas >30min de silencio. Restart #{len(_state[restart_key])} esta hora.",
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass
            return True
        else:
            msg = f"❌ AUTO-RESTART {agent} FALLÓ — exit code {result.returncode}: {result.stderr[:200]}"
            LOG.error(msg)
            _send_webchat(msg, msg_type="alert")
            return False
    except subprocess.TimeoutExpired:
        msg = f"❌ AUTO-RESTART {agent} TIMEOUT — seal_restart.sh tardó >60s"
        LOG.error(msg)
        _send_webchat(msg, msg_type="alert")
        return False
    except Exception as e:
        LOG.error(f"Auto-restart {agent} exception: {e}")
        return False


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
    _check_agent_heartbeat(now, "ADA", HEARTBEAT_ADA)

    # ── 3. Verificar sesión Claude Code de JARVIS ──
    _check_agent_heartbeat(now, "JARVIS", HEARTBEAT_JARVIS)


def _check_agent_heartbeat(now: datetime, agent: str, hb_path: Path) -> None:
    """Verifica el heartbeat de un agente y emite alertas si está en silencio."""
    silence_min = 9999.0
    reason = f"{hb_path.name} no existe"

    hb = _read_json(hb_path)
    if hb and hb.get("timestamp"):
        # Si alive=False, significa que el archivo fue creado como placeholder (agente no activo)
        # No alertar si el agente simplemente no tiene sesión abierta
        if hb.get("alive") is False and hb.get("note"):
            LOG.debug(f"{agent}: sin sesión activa ({hb.get('note', '')})")
            return
        silence_min = _minutes_since(hb["timestamp"])
        reason = f"{hb_path.name} — última actualización hace {silence_min:.1f}min"
    else:
        if agent == "ADA":
            # Fallback para ADA: terminal_log.jsonl mtime
            tl_age = _terminal_log_minutes_ago()
            if tl_age < silence_min:
                silence_min = tl_age
                reason = f"terminal_log.jsonl — última actividad hace {tl_age:.1f}min"

    tier_key = f"{agent.lower()}_last_alert_tier"
    # Determinar tier
    if silence_min >= TIER3_DOWN:
        _alert(3, silence_min, reason, agent=agent)
        # AUTO-RESTART: si Tier 3, intentar reiniciar automáticamente
        _auto_restart_agent(agent)
    elif silence_min >= TIER2_CRIT:
        _alert(2, silence_min, reason, agent=agent)
    elif silence_min >= TIER1_WARN:
        _alert(1, silence_min, reason, agent=agent)
    else:
        # Agente activo — reset estado
        if _state[tier_key] > 0:
            recovery_msg = (
                f"✅ {agent} recuperado [{now.strftime('%H:%M UTC')}] — "
                f"actividad detectada (silencio anterior: {silence_min:.1f}min)"
            )
            LOG.info(recovery_msg)
            _send_webchat(recovery_msg, msg_type="status")
        _state[tier_key] = 0
        LOG.debug(f"{agent} OK — última actividad hace {silence_min:.1f}min")


def main() -> None:
    LOG.info("DUM Watchdog v3 iniciado — monitorea ADA + JARVIS")
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
