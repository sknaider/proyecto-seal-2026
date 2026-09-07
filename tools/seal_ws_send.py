#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_ws_send.py — Envío CONFIABLE de mensajes al webchat por /ws (broadcast LIVE a la UI de William).

Fix William 5-jul («no te leo»): /api/agents/send PERSISTE pero NO hace broadcast live a la UI;
el path /ws (action:'say', el que usa la UI real) SÍ se ve al toque. Este helper manda por /ws con
retry + fallback de URL. Uso: python3 seal_ws_send.py "<mensaje>" [--from NEXUS] [--to William|web_chat].
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys

URLS = ["ws://localhost:8765/ws?user=", "ws://localhost:8765/ws/agents?user="]


async def _send(agent: str, to: str, message: str, retries: int = 2) -> bool:
    try:
        import websockets
    except ImportError:
        print("ERROR: pip install websockets", file=sys.stderr)
        return False
    payload = json.dumps({"action": "say", "from": agent, "to": to, "message": message, "channel": "web_chat"})
    for attempt in range(retries + 1):
        for base in URLS:
            try:
                async with websockets.connect(base + agent, open_timeout=6, close_timeout=3) as ws:
                    await ws.send(payload)
                    await asyncio.sleep(0.4)
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.5)
    return False


def send(message: str, agent: str = "NEXUS", to: str = "web_chat") -> bool:
    """API sincrónica para otros módulos: envía por /ws, devuelve True si salió."""
    return asyncio.run(_send(agent, to, message))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--from", dest="agent", default="NEXUS")
    ap.add_argument("--to", default="web_chat")
    a = ap.parse_args()
    ok = send(a.message, a.agent, a.to)
    print("✅ enviado por /ws" if ok else "❌ no pude enviar por /ws")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
