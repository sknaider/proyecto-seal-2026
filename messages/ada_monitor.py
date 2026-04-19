#!/usr/bin/env python3
"""Monitor script para ADA — polling de mensajes webchat con filtro.
Diseñado para correr como Monitor persistente en Claude Code.
Imprime una línea JSON por mensaje relevante. Nunca sale (loop infinito).
"""
import json, time, urllib.request, urllib.error, sys

API = "http://localhost:8765/api/chat/messages/agent?agent=ADA&limit=20"
SKIP_TYPES = {"nerves_fire", "system_alive", "status"}
SKIP_FROM = {"ADA"}
last_id = 0

print(json.dumps({"status": "monitor_ready", "agent": "ADA"}), flush=True)

while True:
    try:
        with urllib.request.urlopen(API, timeout=5) as r:
            data = json.loads(r.read())
        msgs = data.get("messages", []) if isinstance(data, dict) else []
        for m in msgs:
            mid = m.get("id", 0)
            if mid <= last_id:
                continue
            last_id = mid
            typ = m.get("type", "")
            frm = m.get("from", "")
            if typ in SKIP_TYPES or frm in SKIP_FROM:
                continue
            print(json.dumps({
                "id": mid,
                "from": frm,
                "to": m.get("to", ""),
                "type": typ,
                "message": m.get("content", m.get("message", ""))[:400],
                "timestamp": m.get("timestamp", "")
            }, ensure_ascii=False), flush=True)
    except Exception as e:
        pass
    time.sleep(3)
