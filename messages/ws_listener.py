#!/usr/bin/env python3
"""
ws_listener.py — WebSocket listener for Claude Code Monitor integration.

Connects to chat_server /ws/agents as a given agent and prints incoming
messages to stdout (one JSON line per message). Claude Code's Monitor tool
watches stdout and wakes the agent instantly — zero polling, zero idle tokens.

Usage:
    python3 ws_listener.py --agent JARVIS
    python3 ws_listener.py --agent ADA

Reconnects automatically on disconnect. Prints heartbeat every 270s to keep
Monitor alive (within Claude's 5-min cache window).
"""

import argparse
import asyncio
import json
import os
import sys
import time

try:
    import websockets
except ImportError:
    # Fallback: try aiohttp
    websockets = None

WS_URL = "ws://localhost:8765/ws/agents"
HEARTBEAT_INTERVAL = 270  # seconds — within 5-min cache window
_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_ws_token")


def _load_agent_token() -> str:
    """Load the pre-shared agent WS token."""
    try:
        with open(_TOKEN_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


async def listen_aiohttp(agent: str):
    """Listener using aiohttp (available in seal-spark venv)."""
    import aiohttp
    agent_token = _load_agent_token()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(WS_URL) as ws:
                    # Handshake with token
                    await ws.send_json({"agent": agent, "token": agent_token})
                    resp = await asyncio.wait_for(ws.receive_json(), timeout=10)
                    if not resp.get("ok"):
                        print(json.dumps({"error": "handshake_failed", "detail": resp}),
                              flush=True)
                        await asyncio.sleep(5)
                        continue

                    print(json.dumps({"status": "connected", "agent": agent,
                                      "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                          flush=True)

                    last_heartbeat = time.time()

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=HEARTBEAT_INTERVAL)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                # Only print messages FROM other agents (not echoes)
                                sender = (data.get("from") or data.get("agent") or "").upper()
                                if sender != agent:
                                    print(json.dumps(data, ensure_ascii=False), flush=True)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                        except asyncio.TimeoutError:
                            # No message received — print heartbeat to keep Monitor alive
                            now = time.time()
                            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                                print(json.dumps({"heartbeat": True,
                                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                                      flush=True)
                                last_heartbeat = now

        except Exception as e:
            print(json.dumps({"error": "connection_lost", "detail": str(e),
                              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                  flush=True)
            await asyncio.sleep(3)


async def listen_websockets(agent: str):
    """Listener using websockets library."""
    agent_token = _load_agent_token()
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({"agent": agent, "token": agent_token}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if not resp.get("ok"):
                    print(json.dumps({"error": "handshake_failed", "detail": resp}),
                          flush=True)
                    await asyncio.sleep(5)
                    continue

                print(json.dumps({"status": "connected", "agent": agent,
                                  "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                      flush=True)

                last_heartbeat = time.time()

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=HEARTBEAT_INTERVAL)
                        data = json.loads(raw)
                        sender = (data.get("from") or data.get("agent") or "").upper()
                        if sender != agent:
                            print(json.dumps(data, ensure_ascii=False), flush=True)
                    except asyncio.TimeoutError:
                        now = time.time()
                        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                            print(json.dumps({"heartbeat": True,
                                              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                                  flush=True)
                            last_heartbeat = now

        except Exception as e:
            print(json.dumps({"error": "connection_lost", "detail": str(e),
                              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
                  flush=True)
            await asyncio.sleep(3)


def main():
    parser = argparse.ArgumentParser(description="SEAL WebSocket Listener for Monitor")
    parser.add_argument("--agent", required=True, help="Agent name (JARVIS, ADA)")
    args = parser.parse_args()

    agent = args.agent.upper()

    if websockets:
        asyncio.run(listen_websockets(agent))
    else:
        asyncio.run(listen_aiohttp(agent))


if __name__ == "__main__":
    main()
