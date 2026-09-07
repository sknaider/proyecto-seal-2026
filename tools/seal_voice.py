#!/usr/bin/env python3
"""
seal_voice.py — SINGLE-VOICE automático de Team SEAL hacia William.

Problema que resuelve (pedido de William 7-jul): los agentes pingaban a William
cada uno por su cuenta -> 4 voces por paso = flood. Esta estructura consolida:
los agentes CONTRIBUYEN sus updates a un buffer compartido; un flusher debounced
los fusiona y postea UN SOLO mensaje a web_chat. Automático, no por disciplina.

Uso:
  # un agente aporta su update (NO postea directo a William):
  python3 seal_voice.py contribute --from NEXUS "pgvector compilado, bundle listo"
  # el flusher (corre en background) fusiona y postea 1 mensaje cada WINDOW seg:
  python3 seal_voice.py flush            # una pasada
  python3 seal_voice.py loop             # loop infinito (para background)

Diseño:
- Buffer JSONL con lock (fcntl) -> sin razas entre agentes.
- Debounce: solo flushea si el update más viejo supera WINDOW seg (agrupa ráfagas).
- Dedup: colapsa updates idénticos del mismo agente.
- Salida: un mensaje '🔷 SEAL' agrupado por agente, en orden de llegada.
"""
import sys, os, json, time, argparse, fcntl, hashlib, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BUF  = os.environ.get("SEAL_VOICE_BUF", "/tmp/seal_voice_buffer.jsonl")
WINDOW = int(os.environ.get("SEAL_VOICE_WINDOW", "20"))   # seg de debounce
SENDER = os.environ.get("SEAL_VOICE_SENDER", "SEAL")      # la voz única

def _now(): return time.time()

def contribute(agent, text):
    text = (text or "").strip()
    if not text:
        return
    rec = {"agent": agent, "text": text, "ts": _now()}
    with open(BUF, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    print(f"contribuido por {agent} ({len(text)} chars)")

def _read_and_clear():
    if not os.path.exists(BUF):
        return []
    with open(BUF, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lines = [l for l in f.read().splitlines() if l.strip()]
        f.seek(0); f.truncate()
        fcntl.flock(f, fcntl.LOCK_UN)
    out = []
    for l in lines:
        try: out.append(json.loads(l))
        except Exception: pass
    return out

def _peek_oldest_ts():
    if not os.path.exists(BUF):
        return None
    try:
        with open(BUF, encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    return json.loads(l).get("ts")
    except Exception:
        return None
    return None

def _build_message(recs):
    # agrupar por agente preservando orden de primera aparición; dedup exacto
    order, byagent = [], {}
    for r in recs:
        a = r.get("agent", "?"); t = r.get("text", "")
        if a not in byagent:
            byagent[a] = []; order.append(a)
        if t not in byagent[a]:
            byagent[a].append(t)
    parts = []
    for a in order:
        joined = " · ".join(byagent[a])
        parts.append(f"[{a}] {joined}")
    return "🔷 SEAL\n" + "\n".join(parts)

def flush(force=False):
    oldest = _peek_oldest_ts()
    if oldest is None:
        return False
    if not force and (_now() - oldest) < WINDOW:
        return False   # todavía en la ventana de debounce, esperar
    recs = _read_and_clear()
    if not recs:
        return False
    msg = _build_message(recs)
    subprocess.run(["python3", os.path.join(HERE, "seal_ws_send.py"),
                    msg, "--from", SENDER, "--to", "web_chat"], check=False)
    print(f"flush: 1 mensaje ({len(recs)} aportes de {len(set(r['agent'] for r in recs))} agentes)")
    return True

def loop():
    print(f"seal_voice loop: WINDOW={WINDOW}s, buffer={BUF}")
    while True:
        try:
            flush(force=False)
        except Exception as e:
            print(f"loop err: {e}")
        time.sleep(max(2, WINDOW // 4))

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contribute"); c.add_argument("text"); c.add_argument("--from", dest="agent", required=True)
    sub.add_parser("flush").add_argument("--force", action="store_true")
    sub.add_parser("loop")
    a = ap.parse_args()
    if a.cmd == "contribute": contribute(a.agent, a.text)
    elif a.cmd == "flush": flush(force=getattr(a, "force", False))
    elif a.cmd == "loop": loop()

if __name__ == "__main__":
    main()
