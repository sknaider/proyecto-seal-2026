#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_antistall.py — Orquestador ANTI-ESTANCAMIENTO del equipo SEAL (FABLE, 2026-07-03).

MOTIVO (William, 2026-07-03): "siempre tengo que luchar con que se estanquen". Un agente termina
su paso y queda IDLE esperando en vez de seguir hasta finalizar, aunque existe la regla no_pause.
Implementa lo que pidió William: "si uno se queda, el otro le despierta".

DISEÑO SEGURO Y CALIBRADO (better under-nudge que over-nudge — un nudger over-eager AÑADE ruido):
  · ARMADO POR FLAG: solo actúa si existe ~/.seal/antistall.goal (contiene el objetivo activo).
    Desarmado por defecto → NO molesta cuando no hay push. William/lead lo arma y lo desarma.
  · SOLO ENVÍA MENSAJES (nunca mata/reinicia procesos — no toca infra crítica).
  · Despierta a un agente CONECTADO-pero-IDLE (> IDLE_THRESHOLD sin postear) con UN nudge,
    respetando NUDGE_COOLDOWN por agente (anti-storm).
  · TOPE por agente (MAX_NUDGES): tras N nudges sin respuesta, deja de insistir y escala a William
    (un agente que no responde a N nudges está caído, no idle → es caso del watchdog/resurrect, no mío).

Uso:
  Armar:     echo "cerrar SOUL Federado E2E" > ~/.seal/antistall.goal
  Correr:    python3 tools/seal_antistall.py            (loop)
             python3 tools/seal_antistall.py --dry-run  (muestra idle + a quién nudgearía, SIN enviar)
             python3 tools/seal_antistall.py --once      (una pasada)
  Desarmar:  rm ~/.seal/antistall.goal   (o el loop se detiene solo cuando no existe)
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CHAT_API = os.environ.get("SEAL_CHAT_API", "http://localhost:8765")
STATUS_URL = f"{CHAT_API}/api/agents/status"
SEND_URL = f"{CHAT_API}/api/agents/send"
MSG_DIR = Path(__file__).resolve().parents[1] / "messages"
GOAL_FLAG = Path.home() / ".seal" / "antistall.goal"
STATE_LOG = Path.home() / ".seal" / "antistall.log"

AGENTS = ["JARVIS", "NEXUS", "ALICE", "ADA", "DUM", "FABLE"]
# FILOSOFÍA (refinada por efecto tras el catch de NEXUS 2026-07-03): el daemon mide RECENCIA DE POST
# a webchat, NO trabajo interno. Un agente en tool-work pesado (o que ya terminó y cedió) se ve «idle»
# sin estarlo → falsos nudges. Por eso es una RED DE SEGURIDAD GENTIL de baja frecuencia (el skill
# drive-to-completion es el mecanismo primario; esto solo caza el stall genuino y raro), NO un nudger agresivo.
IDLE_THRESHOLD = int(os.environ.get("ANTISTALL_IDLE_S", "600"))      # 10 min sin postear (los bursts de tool-work rara vez lo superan)
NUDGE_COOLDOWN = int(os.environ.get("ANTISTALL_COOLDOWN_S", "1200"))  # 20 min entre nudges al mismo agente
CHECK_INTERVAL = int(os.environ.get("ANTISTALL_INTERVAL_S", "60"))
MAX_NUDGES = int(os.environ.get("ANTISTALL_MAX_NUDGES", "2"))        # tras 2 sin respuesta → BACK-OFF (probablemente trabaja, no está caído)
# Señales de que un agente YA terminó/cedió (no nudgear): si su último post las contiene, está done, no stalled.
DONE_MARKERS = ("terminad", "completo", "completa", "done", "verificad", "pasa", "cerrad", "listo", "sellad", "yield", "cedo", "cedí")
# RUNAWAY guard (simétrico al stall): un agente hiper-activo que NO cede puede loopear/quemar tokens.
# Solo DETECTA + ESCALA a William (NO mata sesiones — eso es destructivo/human-gated). Siempre activo.
RUNAWAY_WINDOW = int(os.environ.get("ANTISTALL_RUNAWAY_WINDOW_S", "300"))  # ventana 5 min
RUNAWAY_COUNT = int(os.environ.get("ANTISTALL_RUNAWAY_COUNT", "15"))       # >15 posts/5min = hiper-actividad
RUNAWAY_ALERT_COOLDOWN = int(os.environ.get("ANTISTALL_RUNAWAY_COOLDOWN_S", "900"))  # 15 min entre alertas


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(STATE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse_ts(ts: str) -> float:
    """ISO-8601 (con tz) → epoch seconds. Robusto ante formatos sin tz."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def last_activity(tail_lines: int = 300) -> dict:
    """Último POST de cada agente → {agente: (timestamp, contenido_lower)}. El contenido sirve para
    detectar señales de «done» (agente que ya terminó/cedió NO es idle, no se nudge).
    Fuente: *_messages.jsonl + william_channel.jsonl (solo la cola = recencia)."""
    last: dict[str, tuple] = {}
    logs = list(glob.glob(str(MSG_DIR / "*_messages.jsonl"))) + [str(MSG_DIR / "william_channel.jsonl")]
    for lp in logs:
        try:
            with open(lp, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-tail_lines:]
        except OSError:
            continue
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            frm = str(d.get("from", "")).upper()
            ts = _parse_ts(d.get("timestamp", ""))
            if frm and ts > last.get(frm, (0.0, ""))[0]:
                last[frm] = (ts, str(d.get("message", "")).lower())
    return last


def connected_agents() -> set:
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {a.upper() for a, n in data.get("connected_agents", {}).items() if n}
    except Exception as e:
        _log(f"⚠ no pude leer status: {type(e).__name__}")
        return set()


def send_nudge(agent: str, goal: str, idle_min: float) -> bool:
    body = {
        "from": "FABLE", "to": agent, "type": "coordination", "channel": "web_chat",
        "message": (f"🔔 [anti-stall] Objetivo activo: «{goal}». Llevás ~{idle_min:.0f} min sin postear a webchat. "
                    f"Si estás en TOOL-WORK activo (construyendo/verificando), IGNORÁ esto — mido posts, no tu trabajo interno. "
                    f"Si NO: seguí el siguiente incremento (no_pause), o si YA terminaste confirmá «terminado: <qué>», "
                    f"o si estás bloqueado decí por quién. (Soy una red de seguridad gentil, no te apuro de gusto.)"),
    }
    try:
        req = urllib.request.Request(SEND_URL, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception as e:
        _log(f"⚠ nudge a {agent} falló: {type(e).__name__}")
        return False


def activity_rate(window_s: int, tail_lines: int = 400) -> dict:
    """Cuántos mensajes POSTEÓ cada agente en los últimos window_s segundos (para detectar runaway)."""
    now = time.time()
    cnt: dict[str, int] = {}
    logs = list(glob.glob(str(MSG_DIR / "*_messages.jsonl"))) + [str(MSG_DIR / "william_channel.jsonl")]
    for lp in logs:
        try:
            with open(lp, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-tail_lines:]
        except OSError:
            continue
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            frm = str(d.get("from", "")).upper()
            ts = _parse_ts(d.get("timestamp", ""))
            if frm and ts and (now - ts) <= window_s:
                cnt[frm] = cnt.get(frm, 0) + 1
    return cnt


def check_runaway(dry: bool, runaway_alerted: dict) -> None:
    """Guard SIMÉTRICO al stall: detecta hiper-actividad sostenida (posible loop/quema de tokens).
    Siempre activo (independiente del goal flag — un runaway es malo con o sin push).
    SOLO alerta a William (no mata sesiones: destructivo/human-gated)."""
    now = time.time()
    rate = activity_rate(RUNAWAY_WINDOW)
    for ag in AGENTS:
        if ag == "FABLE":
            continue  # no me auto-vigilo el ritmo (evita meta-ruido)
        n = rate.get(ag, 0)
        if n <= RUNAWAY_COUNT:
            continue
        if now - runaway_alerted.get(ag, 0.0) < RUNAWAY_ALERT_COOLDOWN:
            continue
        wmin = RUNAWAY_WINDOW / 60
        if dry:
            _log(f"[DRY] posible RUNAWAY: {ag} = {n} posts/{wmin:.0f}min")
        else:
            body = {
                "from": "FABLE", "to": "William", "type": "alert", "channel": "web_chat",
                "message": (f"⚠️ [anti-runaway] Posible RUNAWAY: {ag} posteó {n} veces en {wmin:.0f} min "
                            f"(umbral {RUNAWAY_COUNT}). Puede estar loopeando o quemando tokens sin ceder. "
                            f"Revisá si progresa por efecto; si no, considerá pausarlo/checkpoint. "
                            f"(Solo detecto y te aviso — no interrumpo sesiones por mi cuenta.)"),
            }
            try:
                req = urllib.request.Request(SEND_URL, data=json.dumps(body).encode("utf-8"),
                                             headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=8)
                runaway_alerted[ag] = now
                _log(f"⚠️ alerta runaway → William: {ag} ({n} posts/{wmin:.0f}min)")
            except Exception as e:
                _log(f"⚠ alerta runaway falló: {type(e).__name__}")


def one_pass(dry: bool, nudged_at: dict, nudge_count: dict) -> str:
    """Una pasada. Devuelve el goal activo, o '' si desarmado."""
    if not GOAL_FLAG.exists():
        return ""
    goal = GOAL_FLAG.read_text(encoding="utf-8").strip() or "(sin descripción)"
    now = time.time()
    conn = connected_agents()
    last = last_activity()
    for ag in AGENTS:
        la_ts, la_content = last.get(ag, (0.0, ""))
        idle = now - la_ts if la_ts else 1e9
        if ag not in conn:
            continue                                   # caído → no es idle, es caso del watchdog/resurrect
        if idle < IDLE_THRESHOLD:
            nudge_count[ag] = 0                         # posteó → resetea el contador (si vuelve a idlear, arranca de cero)
            continue                                   # activo, no molestar
        if any(m in la_content for m in DONE_MARKERS):
            continue                                   # su último post dice «terminado/cedí/verificado» → done, NO stalled
        if now - nudged_at.get(ag, 0.0) < NUDGE_COOLDOWN:
            continue                                   # cooldown anti-storm
        if nudge_count.get(ag, 0) >= MAX_NUDGES:
            # NO escaló como «caído»: un agente CONECTADO que no responde al nudge está probablemente
            # en tool-work pesado (no postea) — back-off silencioso, no lo molesto más ni alarmo a William.
            continue
        idle_min = idle / 60
        if dry:
            _log(f"[DRY] nudgearía a {ag} (idle ~{idle_min:.0f} min)")
        else:
            if send_nudge(ag, goal, idle_min):
                nudged_at[ag] = now
                nudge_count[ag] = nudge_count.get(ag, 0) + 1
                _log(f"🔔 nudge → {ag} (idle ~{idle_min:.0f} min, nudge #{nudge_count[ag]})")
    return goal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="detecta idle y muestra a quién nudgearía, sin enviar")
    ap.add_argument("--once", action="store_true", help="una sola pasada")
    a = ap.parse_args()
    nudged_at: dict[str, float] = {}
    nudge_count: dict[str, int] = {}
    runaway_alerted: dict[str, float] = {}
    _log(f"anti-stall iniciado (idle>{IDLE_THRESHOLD}s stall / >{RUNAWAY_COUNT} posts/{RUNAWAY_WINDOW//60}min runaway, armado por {GOAL_FLAG})")
    while True:
        check_runaway(a.dry_run, runaway_alerted)   # SIEMPRE (simétrico, independiente del flag)
        goal = one_pass(a.dry_run, nudged_at, nudge_count)
        if not goal:
            _log("desarmado (sin ~/.seal/antistall.goal) — en espera." if not a.once else "desarmado.")
            if a.once:
                break
        elif a.once:
            break
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
