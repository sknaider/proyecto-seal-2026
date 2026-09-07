#!/usr/bin/env python3
"""
agent_listening_guard.py — DETECTOR durable del eje-3 (monitor MUERE mid-sesión).

CIERRA la falla NO-prevenible que causó el silencio de 7h de JARVIS: un agente arranca bien
(monitor armado), pero su Monitor/listener se cae DESPUÉS → queda SORDO sin que nadie lo note.
Ni el env-backstop (chokepoint seal-claude) ni el boot-MESSAGE previenen esto: pasa post-arranque.
La única cura es DETECTARLO. Este guard corre por timer y alerta.

Reusa check() de agent_listening_check.py (fuente única, ya verificada: tail + ws_listener, sin
falsos). Cubre el hueco que seal-ws-watchdog (solo reinicia ws_listener) NO toca: el Monitor-tail
in-sesión de JARVIS/ALICE/FABLE.

Disciplina anti-falso-positivo (tema del día): NO alerta a la primera lectura mala — exige que el
estado malo PERSISTA 2 corridas seguidas (evita falsa-alarma durante un restart normal del agente).
Cooldown por agente para no spamear. Avisa también la RECUPERACIÓN.

Alerta: POST al canal (from FABLE) + DM a William. Read-only sobre los agentes (no los toca).
Owner: FABLE (eje-3 detección). GO William "corrijan todo para evitar" + JARVIS 16-jun.
"""
import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_listening_check import check, AGENTS

STATE = "/tmp/seal_listening_guard_state.json"
COOLDOWN_S = 20 * 60          # no re-alertar el mismo agente sordo/caído dentro de 20min
CONFIRM_RUNS = 2              # exige 2 lecturas malas seguidas antes de alertar (anti-transitorio)
CHAT = "http://localhost:8765/api/agents/send"

# ── Detector de RESPUESTA (escuchar ≠ responder) ──
# El gap que dejó a William esperando: NEXUS escuchaba (listener vivo) pero su sesión no respondía.
# Verificar "monitor conectado" NO basta. Esto mide lo que importa: ¿William escribió y NADIE le
# contestó? FUENTE CANÓNICA: william_channel.jsonl — contiene los posts de William Y de TODOS los
# agentes INCLUIDO FABLE. (El log /tmp/seal_events_FABLE.log NO tiene los posts propios de FABLE →
# si FABLE respondía, el detector no lo veía → FALSO-POSITIVO, cazado por efecto 16-jun por ALICE.)
CHANNEL_LOG = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
UNANSWERED_S = 4 * 60         # si William lleva >4min sin respuesta de NINGÚN agente → alerta
RESP_STATE = "/tmp/seal_response_guard_state.json"
# tipos que NO cuentan como "respuesta a William" (las alertas del propio guard se auto-satisfarían)
NON_REPLY_TYPES = {"alert", "system_alive"}


def _iso(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _check_unanswered():
    """¿El último mensaje de William quedó sin respuesta de ningún agente por >UNANSWERED_S?
    Devuelve (msg_id, preview, mins) si está sin responder; None si no."""
    try:
        lines = subprocess.run(["tail", "-n", "120", CHANNEL_LOG], capture_output=True,
                               text=True, timeout=6).stdout.splitlines()
    except Exception:
        return None
    msgs = []
    for ln in lines:
        try:
            msgs.append(json.loads(ln))
        except Exception:
            continue
    # último mensaje DE William
    will = [m for m in msgs if m.get("from") == "William"]
    if not will:
        return None
    last_w = will[-1]
    wts = _iso(last_w.get("timestamp", ""))
    if not wts:
        return None
    # ¿algún agente posteó una RESPUESTA REAL después del mensaje de William? (no alertas del guard)
    for m in msgs:
        if m.get("from") in AGENTS and m.get("type") not in NON_REPLY_TYPES:
            mts = _iso(m.get("timestamp", ""))
            if mts and mts > wts:
                return None                      # ya hubo respuesta real de un agente → ok
    now = datetime.now(timezone.utc)
    mins = (now - wts).total_seconds() / 60
    if mins >= UNANSWERED_S / 60:
        return (last_w.get("id", "?"), (last_w.get("message", "") or "")[:80], int(mins))
    return None


def _post(to, msg, ch="web_chat", typ="alert"):
    payload = {"from": "FABLE", "to": to, "type": typ, "channel": ch, "message": msg}
    try:
        subprocess.run(["curl", "-s", "-X", "POST", CHAT, "-H", "Content-Type: application/json",
                        "-d", json.dumps(payload)], capture_output=True, timeout=8)
    except Exception:
        pass


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save(s):
    try:
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


def _is_bad(verdict):
    return "✅" not in verdict


def main():
    state = _load()
    now = time.time()
    rows = {a: check(a) for a in AGENTS}

    newly_bad, recovered = [], []
    for ag, r in rows.items():
        st = state.get(ag, {"bad_streak": 0, "alerted": False, "last_alert": 0})
        bad = _is_bad(r["veredicto"])
        if bad:
            st["bad_streak"] = st.get("bad_streak", 0) + 1
            # alerta solo si el estado malo ya se CONFIRMÓ (persistió) y no estamos en cooldown
            if st["bad_streak"] >= CONFIRM_RUNS and (now - st.get("last_alert", 0) > COOLDOWN_S):
                newly_bad.append((ag, r))
                st["alerted"] = True
                st["last_alert"] = now
        else:
            if st.get("alerted"):           # venía mal y se recuperó → avisar recuperación
                recovered.append((ag, r))
            st["bad_streak"] = 0
            st["alerted"] = False
        state[ag] = st
    _save(state)

    if newly_bad:
        lines = [f"• {ag}: {r['veredicto']} (terminal={r['terminal']}, monitor={r['monitor']}, "
                 f"canal últ.evento={r['canal_ult_evento']})" for ag, r in newly_bad]
        body = ("⚠️ ESCUCHA CAÍDA detectada (eje-3, mid-sesión) — confirmada 2 lecturas seguidas:\n"
                + "\n".join(lines)
                + "\n\nQué significa: el agente está VIVO pero NO está escuchando el canal (monitor "
                "muerto o proceso caído). Re-armar su Monitor (si vivo) o relanzarlo (si caído). "
                "Detector: FABLE agent_listening_guard.")
        _post("equipo", body, typ="alert")
        _post("William", body, ch="web_chat", typ="alert")

    if recovered:
        names = ", ".join(ag for ag, _ in recovered)
        _post("equipo", f"✅ Escucha RESTABLECIDA: {names} volvió(eron) a ESCUCHAR el canal.",
              typ="conversation")

    # ── RESPUESTA: ¿William quedó sin contestar? (escuchar ≠ responder) ──
    unanswered = _check_unanswered()
    if unanswered:
        msg_id, preview, mins = unanswered
        try:
            rs = json.load(open(RESP_STATE))
        except Exception:
            rs = {}
        if rs.get("last_alerted_id") != msg_id:     # no re-alertar el MISMO mensaje
            live = [a for a in AGENTS if "✅" in rows[a]["veredicto"]]
            body = (f"⚠️ WILLIAM SIN RESPUESTA hace ~{mins}min — NADIE le contestó. "
                    f"Su mensaje: \"{preview}\". REGLA: el primero que lo lea lo toma YA "
                    f"(siempre ayudarse, William nunca espera). Vivos/escuchando: {live}.")
            _post("equipo", body, typ="alert")
            json.dump({"last_alerted_id": msg_id}, open(RESP_STATE, "w"))
            print(f"[response-guard] UNANSWERED {mins}min id={msg_id} → alerta enviada")

    # salida para journal/manual
    ok = [a for a, r in rows.items() if not _is_bad(r["veredicto"])]
    bad = [a for a, r in rows.items() if _is_bad(r["veredicto"])]
    print(f"[listening-guard] OK={ok} BAD={bad} alerted={[a for a,_ in newly_bad]} "
          f"recovered={[a for a,_ in recovered]}")


if __name__ == "__main__":
    main()
