#!/usr/bin/env python3
"""
seal_monitor_filter.py — Filtro de mensajes para Monitor de agentes SEAL.

Lee JSONL de stdin (tail -F pipe), filtra y trunca antes de pasar a Claude Monitor.

Filtros aplicados:
- Pasa: mensajes TO el agente, TO "equipo", o FROM "William"
- Pasa: mensajes TO William solo si son alertas/críticos o >200 chars
- Descarta: eco propio (mensajes FROM este agente)
- Descarta: ACKs cortos (<50 chars) de agentes no-William
- Descarta: type "system_alive", "heartbeat", "nerves_fire"
- Descarta: type "status" de agentes no-William si <200 chars
- Rate-limit: max 1 mensaje por agente no-William cada 20s
- Trunca: content/message > 1000 chars (mensajes no-William) o 4000 (William)

Uso:
    tail -n 0 -F /path/to/william_channel.jsonl | python3 seal_monitor_filter.py --agent ADA
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

MAX_CHARS_WILLIAM = 4000
MAX_CHARS_OTHER = 1000
# Todos los humanos conocidos — sus mensajes siempre pasan (mismo trato que William)
_HUMAN_NAMES_UPPER = {"WILLIAM", "DADITO", "HENRY"}
MAX_AGE_SECONDS = 300
RATE_LIMIT_SECONDS = 20
MIN_CHARS_ACK = 50
MIN_CHARS_STATUS = 200
SKIP_TYPES = {"system_alive", "nerves_fire", "heartbeat"}
ALERT_KEYWORDS = {"error", "alerta", "fallo", "problema", "critico", "crítico", "crash", "muerto", "offline", "fail"}


# LA REGLA QUE ESTE FILTRO INYECTA EN CADA EVENTO (NEXUS, 10-sep-2026; lo
# encontro JARVIS revisando).
#
# Decia "curl POST a web_chat". Un agente que la obedecia SIEMPRE fallaba:
#     POST /api/agents/send con curl crudo -> {"ok":false,"error":"agent_auth_required"}
# y ademas contradecia al CLAUDE.md del proyecto, que prescribe textualmente
# NO usar curl crudo contra ese endpoint porque en ENFORCE se rechaza.
#
# O sea: el sistema le ordenaba a los cinco agentes, EN CADA MENSAJE DE
# WILLIAM, hacer justo lo que las reglas prohiben y el servidor rechaza. El
# error no lo cometia el agente: se lo dictaba el propio monitor.
#
# `--message-file` y no heredoc: es la forma por defecto desde el 7-sep,
# cuando cuatro mensajes salieron con huecos por acentos graves entre
# comillas dobles. El archivo elimina esa clase de error por construccion.

def filter_line(line: str, agent: str, rate_state: dict) -> str | None:
    line = line.strip()
    if not line:
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return line

    # Descartar mensajes con >5min de antiguedad (fix stale monitor replay)
    ts_str = d.get("timestamp")
    if ts_str:
        try:
            msg_ts = datetime.fromisoformat(ts_str)
            if msg_ts.tzinfo is None:
                msg_ts = msg_ts.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - msg_ts).total_seconds() > MAX_AGE_SECONDS:
                return None
        except (ValueError, TypeError):
            pass

    msg_type = (d.get("type") or "").lower()
    if msg_type in SKIP_TYPES:
        return None

    # Convert image messages to text notification with absolute path for Read tool
    if msg_type == "image":
        file_url = d.get("file_url", "")
        caption = d.get("message", "")
        if file_url.startswith("/uploads/"):
            abs_path = f"/home/dadito/IA/proyecto-seal/messages{file_url}"
        else:
            abs_path = file_url
        d["type"] = "conversation"
        d["message"] = f"[IMAGEN] archivo={abs_path}" + (f" caption={caption}" if caption else "")
        d.pop("file_url", None)
        msg_type = "conversation"

    # EL CANAL MANDA SOBRE `to` -- y va ANTES que cualquier otra regla.
    #
    # Medido el 3-sep-2026: un DM de William a ALICE (`dm:alice:william`) llegaba
    # a los monitores de NEXUS, JARVIS, FABLE y ADA. Dos agujeros, y el segundo
    # es el que dolia:
    #
    #   1. `to` lo llena el EMISOR; `channel` es RUTEO. Un cliente que ponga
    #      to='equipo' en un canal dm: abria la correspondencia entera.
    #   2. La regla "mensajes de William: siempre pasar" estaba ANTES del
    #      chequeo de destinatario -> los DMs de William, que son exactamente
    #      los mas privados, eran los UNICOS que ninguna regla filtraba.
    #
    # Lo que se filtraba era metadato + texto cifrado, no contenido legible.
    # Sigue siendo correspondencia privada de William llegando a terceros, y su
    # regla del 2-jun no distingue: "DM, terminal y mensajes de los agentes son
    # su intimidad".
    canal = str(d.get("channel") or "")
    if canal.lower().startswith("dm:"):
        participantes = {t.strip().upper() for t in canal[3:].split(":") if t.strip()}
        if participantes and agent.upper() not in participantes:
            return None

    to_field = (d.get("to") or "").upper()
    from_field = (d.get("from") or "").upper()
    msg_text = (d.get("message") or d.get("content") or "")

    # 1. Eco propio: descartar mensajes enviados por este mismo agente
    if from_field == agent:
        return None

    # Mensajes de humanos (William, Henry, dadito): siempre pasar (máx 4000 chars)
    if from_field in _HUMAN_NAMES_UPPER:
        for key in ("message", "content"):
            val = d.get(key)
            if isinstance(val, str) and len(val) > MAX_CHARS_WILLIAM:
                d[key] = val[:MAX_CHARS_WILLIAM] + "[…truncado]"
        d["_post_rule"] = "⚠️ REGLA: antes de cerrar tu turno → scripts/seal_send.py TU_NOMBRE William --message-file RUTA --channel web_chat --in-reply-to <id> --idempotency-key <clave>"
        return _sanitize(json.dumps(d, ensure_ascii=False))

    # Para mensajes no-William: verificar destinatario
    if to_field != "WILLIAM" and to_field not in (agent, "EQUIPO", ""):
        return None

    # Detectar si es alerta/crítico — exento de filtros de longitud y rate-limit
    is_alert = msg_type == "alert" or any(kw in msg_text.lower() for kw in ALERT_KEYWORDS)

    if not is_alert:
        # 2. Skip ACKs cortos (<50 chars) de agentes no-William
        if len(msg_text) < MIN_CHARS_ACK:
            return None

        # 3. Mensajes TO William: solo pasar si son alertas o >200 chars
        if to_field == "WILLIAM" and len(msg_text) < MIN_CHARS_STATUS:
            return None

        # 4. type=status: solo pasar si >200 chars
        if msg_type == "status" and len(msg_text) < MIN_CHARS_STATUS:
            return None

        # 5. Skip heartbeat-style text
        if "heartbeat" in msg_text.lower():
            return None

        # 6. Rate-limit: max 1 mensaje rutinario TO William por agente cada 20s
        # Mensajes TO este agente o TO equipo no se rate-limitan (son broadcasts directos)
        if to_field == "WILLIAM":
            now = time.monotonic()
            rate_key = f"{from_field}_william"
            last_seen = rate_state.get(rate_key, 0)
            if now - last_seen < RATE_LIMIT_SECONDS:
                return None
            rate_state[rate_key] = now

    # DMs van cifrados con Fernet — truncar el ciphertext lo rompe; no truncar.
    if not canal.lower().startswith("dm:"):
        for key in ("message", "content"):
            val = d.get(key)
            if isinstance(val, str) and len(val) > MAX_CHARS_OTHER:
                d[key] = val[:MAX_CHARS_OTHER] + "[…truncado]"

    d["_post_rule"] = "⚠️ REGLA: antes de cerrar tu turno → scripts/seal_send.py TU_NOMBRE William --message-file RUTA --channel web_chat --in-reply-to <id> --idempotency-key <clave>"
    return _sanitize(json.dumps(d, ensure_ascii=False))


def _sanitize(s: str) -> str:
    return s.encode("utf-8", errors="replace").decode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, help="Agent name (ADA, JARVIS, ALICE, NEXUS)")
    args = parser.parse_args()
    agent = args.agent.upper()
    rate_state: dict = {}

    for line in sys.stdin:
        result = filter_line(line, agent, rate_state)
        if result is not None:
            try:
                print(result, flush=True)
            except BrokenPipeError:
                sys.exit(0)


if __name__ == "__main__":
    main()
