#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_ada_session_watchdog.py — Watchdog de durabilidad para el stack Codex de ADA (carril NEXUS).

MOTIVO (incidente 1-jul): ADA quedó SORDA/MUDA ~1h y el equipo se enteró por Henry, NO por una alerta.
El SPOF real: si la sesión tmux `seal-ada-codex` (driver ada_codex.sh que supervisa el poller) muere,
nadie la levanta y NADIE AVISA. Este watchdog cierra esa brecha: detecta la caída y ALERTA al canal en
segundos. El aprendizaje central del incidente no fue "falta auto-restart" — fue "el fallo fue SILENCIOSO".

ALCANCE v1 (deliberadamente ALERT-ONLY, sin auto-relaunch):
  - Chequea cada INTERVAL: (a) sesión tmux existe, (b) poller vivo, (c) :8772 responde.
  - Si algo cae, postea UNA alerta al web_chat (debounce: no spamea; re-alerta si sigue caído tras RE_ALERT).
  - NO relanza solo. Razón: auto-relaunch pelearía con restarts INTENCIONALES (mantenimiento) y con el loop
    de ada_codex.sh. El relaunch lo decide un humano/NEXUS con `ada_launch.sh`. (Auto-relaunch = enhancement
    futuro, gated, con supresión durante ventanas de mantenimiento.)
  - Fail-safe: cualquier excepción se loguea y NO tumba el watchdog (sigue vigilando).

Determinístico, sin estado remoto. Pensado para correr como systemd --user service (Restart=always).
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import time
import urllib.request

INTERVAL = 30           # segundos entre chequeos
RE_ALERT = 600          # re-alerta si sigue caído tras 10 min
SESSION = "seal-ada-codex"
TMUX_L = "seal-ada-codex"
POLLER_MATCH = "ada_codex_poller.py"
APP_PORT = 8772
CHAT_URL = "http://localhost:8765/api/agents/send"
LOG = "/home/dadito/IA/proyecto-seal/messages/seal_ada_session_watchdog.log"
STATE = "/tmp/seal_ada_watchdog_state.json"


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def _session_up() -> bool:
    try:
        r = subprocess.run(["tmux", "-L", TMUX_L, "has-session", "-t", SESSION],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _poller_up() -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", POLLER_MATCH], capture_output=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _port_up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except Exception:
        return False


def _load_state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(STATE, "w") as f:
            json.dump(st, f)
    except Exception:
        pass


def _alert(problems: list[str]) -> None:
    msg = ("⚠️ WATCHDOG ADA — caída detectada en el stack Codex: " + "; ".join(problems) +
           ". (session=driver+poller). NO auto-relanzo; NEXUS/operador: `bash ada_launch.sh` para la sesión, "
           "`systemctl --user restart ada-codex-remote-bridge` para el engine. Antes: verificar si es mantenimiento.")
    payload = json.dumps({"from": "NEXUS", "to": "equipo", "type": "alert",
                          "channel": "web_chat", "message": msg}).encode("utf-8")
    try:
        req = urllib.request.Request(CHAT_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        _log(f"ALERTA enviada: {problems}")
    except Exception as e:
        _log(f"FALLO enviar alerta ({e}): {problems}")


def check_once() -> list[str]:
    """Devuelve lista de problemas (vacía = todo sano)."""
    problems = []
    if not _session_up():
        problems.append(f"sesión tmux {SESSION} AUSENTE")
    if not _poller_up():
        problems.append("poller AUSENTE")
    if not _port_up(APP_PORT):
        problems.append(f"engine :{APP_PORT} DOWN")
    return problems


def main() -> None:
    _log("watchdog ADA iniciado (alert-only)")
    while True:
        try:
            problems = check_once()
            st = _load_state()
            now = time.time()
            if problems:
                last = st.get("last_alert", 0)
                if (now - last) >= RE_ALERT or not st.get("down"):
                    _alert(problems)
                    st["last_alert"] = now
                st["down"] = True
            else:
                if st.get("down"):
                    _log("recuperado — stack ADA sano de nuevo")
                st["down"] = False
            _save_state(st)
        except Exception as e:
            _log(f"loop error (no fatal): {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
