#!/usr/bin/env python3
"""
agent_bridge.py — Bridge daemon: /ws/agents → trigger files
Conecta a chat_server via WebSocket y convierte mensajes en trigger files
para que ADA/JARVIS los procesen en <1s.

Uso:
  python3 agent_bridge.py --agent ADA
  python3 agent_bridge.py --agent JARVIS
  systemctl --user start seal-bridge-ada
  systemctl --user start seal-bridge-jarvis
"""
import asyncio
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets", file=sys.stderr)
    sys.exit(1)

DIR = Path(__file__).parent
WS_URL = "ws://localhost:8765/ws/agents"

_TOKEN_PATH = DIR / ".agent_ws_token"


def _load_agent_token() -> str:
    """Load pre-shared token that chat_server generates at startup."""
    if _TOKEN_PATH.exists():
        return _TOKEN_PATH.read_text().strip()
    return ""

TRIGGER_MAP = {
    "ADA":         DIR / ".jarvis_to_ada",
    "JARVIS":      DIR / ".ada_to_jarvis",
    "WILLIAM":     DIR / ".william_trigger",
    "DUM":         DIR / ".ada_to_jarvis",
    "JARVIS_MAYOR": DIR / ".ada_to_jarvis",
}

RECONNECT_DELAY = 3  # segundos entre reconexiones


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}][bridge] {msg}", flush=True)


def write_trigger(agent_name: str, msg: dict) -> None:
    """Escribe trigger file para que el agente lo procese en su próximo cron."""
    trigger_path = TRIGGER_MAP.get(agent_name.upper())
    if not trigger_path:
        return
    # Guarda solo el ID del mensaje — el lector recupera el contenido completo del JSONL por ID.
    # Antes: message[:500] truncaba mensajes largos silenciosamente.
    # Fix: trigger es un puntero ligero, no una copia del contenido.
    payload = json.dumps({
        "trigger": "ws_message",
        "from": msg.get("from", ""),
        "to": msg.get("to", ""),
        "message_id": msg.get("id", ""),
        "idempotency_key": msg.get("idempotency_key", msg.get("id", "")),
        "type": msg.get("type", ""),
        "timestamp": msg.get("timestamp", ""),
        # message_preview: primeros 120 chars para diagnóstico rápido, sin truncar el flujo real
        "message_preview": msg.get("message", "")[:120],
    }, ensure_ascii=False)
    trigger_path.write_text(payload, encoding="utf-8")
    _log(f"trigger → {trigger_path.name} [{msg.get('from','?')}→{agent_name}]")


async def run_bridge(agent_name: str) -> None:
    _log(f"iniciando bridge para {agent_name} → {WS_URL}")
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                # Identificarse con token pre-compartido
                await ws.send(json.dumps({"agent": agent_name, "token": _load_agent_token()}))
                resp = json.loads(await ws.recv())
                if not resp.get("ok"):
                    _log(f"ERROR handshake: {resp}")
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
                _log(f"conectado como {agent_name} (cursor={resp.get('cursor', 0)})")

                # Recibir mensajes en tiempo real
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        # Ignorar mensajes propios
                        if str(msg.get("from", "")).upper() == agent_name.upper():
                            continue
                        write_trigger(agent_name, msg)
                    except Exception as e:
                        _log(f"parse error: {e}")

        except (OSError, ConnectionRefusedError) as e:
            _log(f"no conecta ({e}) — reintentando en {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            _log(f"error ({e}) — reconectando en {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Agent WebSocket Bridge")
    parser.add_argument("--agent", required=True, help="Nombre del agente (ADA, JARVIS, DUM)")
    args = parser.parse_args()
    asyncio.run(run_bridge(args.agent.upper()))
