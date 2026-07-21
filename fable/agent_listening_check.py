#!/usr/bin/env python3
"""
agent_listening_check.py — ¿cada agente está ESCUCHANDO el chat? (orden William 16-jun).

Responde "cómo averiguo si un agente escucha y responde": por efecto, revisa 4 señales por agente:
  1. PROCESO   — claude --name <AG> vivo.
  2. TERMINAL  — su kitty socket + sesión tmux (terminal activa).
  3. MONITOR   — tail -F seal_events_<AG>.log O ws_listener.py --agent <AG> = ESCUCHANDO el canal.
  4. CANAL VIVO — su event log se alimenta (oye) + timestamp del último evento recibido.

Veredicto por agente: ESCUCHANDO ✅ / SORDO ⚠️ (proceso vivo pero sin monitor) / CAÍDO ❌.
Construido por FABLE tras el incidente de JARVIS (monitor muerto = 7h sordo) + NEXUS (hung mid-boot).
"""
import os
import re
import subprocess
import time
from datetime import datetime, timezone

AGENTS = ["JARVIS", "ADA", "ALICE", "NEXUS", "FABLE", "DUM"]


def _run(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return ""


def _pid(ag):
    out = _run(f"pgrep -f 'claude.*--name {ag}'")
    return out.splitlines()[0] if out else None


def _has_tail_monitor(pid, ag):
    """¿El agente tiene un monitor de canal armado? = está ESCUCHANDO.
    Hay DOS mecanismos Claude válidos (cazado por efecto 16-jun con NEXUS):
      (a) tail -F /tmp/seal_events_<AG>.log  (JARVIS, ALICE, FABLE)
      (b) ws_listener.py --agent <AG>        (NEXUS — websocket, NO tail)
    Solo chequear (a) daba FALSO-SORDO en NEXUS. Filtramos por BINARIO real para
    evitar el falso-positivo por substring (mismo bug que cazó ALICE en su tool)."""
    # (a) proceso cuyo binario es 'tail' y tailea el event log del agente
    tail_pids = _run("pgrep -x tail").split()
    for tp in tail_pids:
        cl = _run(f"tr '\\0' ' ' < /proc/{tp}/cmdline 2>/dev/null")
        if f"seal_events_{ag}" in cl:
            return True
    # (b) ws_listener.py atado a ESTE agente (--agent <AG>)
    ws = _run(f"pgrep -af 'ws_listener.py.*--agent {ag}'")
    for l in ws.splitlines():
        if "grep" in l or "pgrep" in l:
            continue
        if f"--agent {ag}" in l and "ws_listener.py" in l:
            return True
    return False


def _log_age_min(ag):
    f = f"/tmp/seal_events_{ag}.log"
    if not os.path.exists(f):
        return None
    return int((time.time() - os.path.getmtime(f)) / 60)


def _last_event(ag):
    """Último evento RECIBIDO en el canal del agente (cualquier remitente) = su feed está vivo.
    OJO: seal_events_<AG>.log es el stream que el agente OYE, NO lo que postea — buscar
    'from: <AG>' aquí daba '(sin posts)' falso (los posts propios no entran a tu inbound)."""
    f = f"/tmp/seal_events_{ag}.log"
    if not os.path.exists(f):
        return "?"
    out = _run(f"tail -1 {f}")
    m = re.search(r'"timestamp": "([^"]{19})', out)
    return m.group(1).replace("T", " ") if m else "(vacío)"


# ADA (Codex) y DUM (gemma bridge) NO escuchan por el tail-Monitor estilo-Claude:
# su señal de escucha es su SERVICIO systemd dedicado. Chequearlos como Claude da falso-sordo.
SERVICE_LISTENERS = {"ADA": "ada-codex-compact-monitor.service", "DUM": "dum-bridge.service"}


def check(ag):
    # ADA/DUM: mecanismo distinto (service-based), no tail-Claude
    if ag in SERVICE_LISTENERS:
        svc = SERVICE_LISTENERS[ag]
        active = _run(f"systemctl --user is-active {svc}") == "active"
        ver = f"✅ ESCUCHANDO (vía {svc.split('.')[0]})" if active else f"⚠️ su servicio {svc} NO activo"
        return {"agent": ag, "veredicto": ver, "proceso": "service-based", "terminal": "(otro)",
                "monitor": "servicio activo" if active else "servicio CAÍDO", "canal_ult_evento": _last_event(ag)}
    pid = _pid(ag)
    if not pid:
        return {"agent": ag, "veredicto": "❌ CAÍDO", "proceso": "no", "terminal": "-", "monitor": "-", "canal_ult_evento": "-"}
    kitty = os.path.exists(f"/tmp/seal-{ag.lower()}-kitty.sock")
    tmux = bool(_run(f"tmux -L seal-{ag.lower()} list-sessions 2>/dev/null"))
    terminal = "sí" if (kitty or tmux) else "NO"
    # VEREDICTO = SOLO el monitor conectado. NO usar frescura del log: "log frío" significa que el
    # CANAL estuvo quieto (nadie posteó), NO que el agente esté sordo — el monitor sigue armado y
    # recibiría cualquier mensaje. Conflar ambos daba FALSO-POSITIVO (cazado por efecto 16-jun: canal
    # quieto 9min → marcaba a 4 agentes "sordos" estando sanos). La señal de ESCUCHA es el monitor.
    monitor = _has_tail_monitor(pid, ag)
    if monitor:
        ver = "✅ ESCUCHANDO"
    else:
        ver = "⚠️ SORDO (proceso vivo, SIN monitor)"
    return {"agent": ag, "veredicto": ver, "proceso": f"pid {pid}", "terminal": terminal,
            "monitor": "conectado" if monitor else "MUERTO", "canal_ult_evento": _last_event(ag)}


def main():
    print(f"=== ¿AGENTES ESCUCHANDO EL CHAT? — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ===")
    print(f"{'AGENTE':8} {'VEREDICTO':40} {'TERMINAL':9} {'MONITOR':10} CANAL (últ.evento)")
    rows = [check(a) for a in AGENTS]
    for r in rows:
        print(f"{r['agent']:8} {r['veredicto']:40} {r['terminal']:9} {r['monitor']:10} {r['canal_ult_evento']}")
    bad = [r["agent"] for r in rows if "✅" not in r["veredicto"]]
    print("\n  ESCUCHANDO OK:", [r["agent"] for r in rows if "✅" in r["veredicto"]])
    if bad:
        print("  ⚠️ ATENCIÓN:", bad)


if __name__ == "__main__":
    main()
