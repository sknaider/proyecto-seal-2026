#!/usr/bin/env python3
"""
fable_reflex_ack.py — REFLEJO PRE-MODELO de FABLE (prototipo canary, 11-jul-2026).
Postea un ACK en milisegundos cuando William escribe a FABLE, ANTES de que el modelo despierte.

Alcance deliberadamente ANGOSTO (anti-coro):
  - Solo mensajes de William (provenance verified) que (a) son DM a FABLE, o (b) nombran 'fable' en el texto.
  - Broadcasts a 'equipo' SIN nombrarme -> NO ack (regla delta-only; si los 5 agentes ackean, es coro de nuevo).
  - 1 ack por message-id (dedupe persistente) + rate-limit 6/min.
  - Texto fijo, cero LLM, cero autoridad: solo "recibido".
Si el canary funciona, ADA/JARVIS lo generalizan en el feed para todos.
"""
import json, subprocess, sys, time, os, re

LOG = "/tmp/seal_events_FABLE.log"
INBOX = "/home/dadito/IA/proyecto-seal/messages/fable_inbox.jsonl"
SEND = "/home/dadito/IA/proyecto-seal/scripts/seal_send.py"
SEEN_FILE = "/tmp/fable_reflex_seen.txt"
RATE_WINDOW, RATE_MAX = 60.0, 6

def load_seen():
    try:
        return set(open(SEEN_FILE).read().split())
    except FileNotFoundError:
        return set()

def mark_seen(mid):
    with open(SEEN_FILE, "a") as f:
        f.write(mid + "\n")

def should_ack(m):
    if str(m.get("from", "")).lower() != "william":
        return False
    prov = m.get("provenance") or {}
    # DM directo a FABLE siempre; en canal exigimos provenance verificada
    is_dm = m.get("type") == "dm" or str(m.get("channel", "")).startswith("dm:fable")
    if not is_dm and not prov.get("verified"):
        return False
    if is_dm:
        return True
    txt = str(m.get("message", "")).lower()
    to = str(m.get("to", "")).lower()
    return bool(re.search(r"\bfable\b", txt) or to == "fable")

def ack(m):
    mid = m.get("id", "")
    is_dm = m.get("type") == "dm" or str(m.get("channel", "")).startswith("dm:fable")
    args = ["python3", SEND, "FABLE", "William",
            "✓ FABLE recibido (reflejo automático, sin modelo) — respuesta real en camino.",
            "--in-reply-to", mid,
            "--idempotency-key", f"fable_reflex_{mid}"]
    if is_dm:
        args += ["--type", "dm", "--channel", "dm:fable:william"]
    subprocess.run(args, timeout=10, capture_output=True)

def follow(path):
    """tail -F casero: tolera truncado/rotación."""
    f = None; ino = None
    while True:
        try:
            st = os.stat(path)
            if f is None or st.st_ino != ino:
                if f: f.close()
                f = open(path); f.seek(0, 2); ino = st.st_ino
            line = f.readline()
            if line:
                yield line
            else:
                time.sleep(0.05)
        except FileNotFoundError:
            time.sleep(0.5)

def main():
    seen = load_seen()
    stamps = []
    src = sys.argv[1] if len(sys.argv) > 1 else LOG
    for line in follow(src):
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        mid = m.get("id", "")
        if not mid or mid in seen or not should_ack(m):
            continue
        now = time.time()
        stamps[:] = [t for t in stamps if now - t < RATE_WINDOW]
        if len(stamps) >= RATE_MAX:
            continue
        stamps.append(now)
        seen.add(mid); mark_seen(mid)
        ack(m)

if __name__ == "__main__":
    main()
