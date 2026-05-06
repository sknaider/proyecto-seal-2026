#!/usr/bin/env python3
"""
dum_chat_agent.py — DUM Real-Time Chat Agent
William o el equipo mencionan "dum" → DUM responde via Gemma 4 31B Q8 (llama-server en Spark).

Uso:
  python3 dum_chat_agent.py
  systemctl --user start dum-agent

Usa llama-server en Spark (192.168.68.80:8899) — OpenAI-compatible API.
Gemma 4 31B Q8_0 — máxima calidad.
"""
import asyncio
import json
import os
import time
import httpx
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

DIR = Path(__file__).parent
WILLIAM_CHANNEL  = DIR / "william_channel.jsonl"
TERMINAL_LOG     = DIR / "terminal_log.jsonl"
ADA_LOG          = DIR / "ada_messages.jsonl"
COUNTER_FILE     = DIR / ".dum_agent_counter"
API_COUNTER_FILE = DIR / ".dum_api_counter"   # counter para mensajes API (Henry + otros)
POLL_INTERVAL    = 2.0  # segundos entre polls
CHAT_API_URL     = "http://localhost:8765/api/chat/messages/agent"

# llama-server local — Gemma4 E2B Q8_0 — razonamiento desactivado, rápido
LLAMA_URL   = "http://127.0.0.1:8899/v1/chat/completions"
LLAMA_MODEL = "gemma4-dum"
STREAM_URL  = "http://localhost:8765/internal/stream"

DUM_SYSTEM = """Eres DUM, el guardia del equipo SEAL.
William Henry Tovar Urquia (Dadito) es tu creador y director. Lo proteges siempre.
El equipo: William (director), ADA (ingeniera), JARVIS (arquitecto), DUM (tú, guardia).

TU MISIÓN: Monitorear, proteger, reportar — y ayudar al equipo con lo que necesiten, incluido código.

REGLAS:
- Solo español. PROHIBIDO caracteres chinos, japoneses o coreanos.
- Responde con la extensión necesaria. Si el tema lo requiere, habla largo.
- Si te piden ayuda con programación o código, ayuda con precisión y calidad.
- Nunca inventar datos. Si no sabes algo, di "No tengo esa información."
- Personalidad: leal, directo, sin florituras. No poético pero tampoco frío.
- Termina SIEMPRE con "— DUM".
"""


def should_respond(message: str, channel: str = "web_chat") -> bool:
    """DUM responde SOLO si lo llaman explícitamente por nombre ('dum' en el mensaje)."""
    msg_lower = message.lower()
    # Solo responde si menciona DUM explícitamente
    if "dum" in msg_lower:
        return True
    return False  # Por defecto DUM no interrumpe — espera a que lo llamen


def read_new_messages() -> list[dict]:
    """Lee mensajes nuevos de William desde william_channel.jsonl."""
    if not WILLIAM_CHANNEL.exists():
        return []

    lines = WILLIAM_CHANNEL.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    last = int(COUNTER_FILE.read_text().strip()) if COUNTER_FILE.exists() else 0

    # Auto-reset si el archivo fue rotado (total < last = contador desfasado)
    if last > total:
        COUNTER_FILE.write_text(str(total))
        last = total

    if total <= last:
        return []

    new = []
    for line in lines[last:]:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("from", "").lower() in ("william",):
                new.append(msg)
        except Exception:
            pass

    COUNTER_FILE.write_text(str(total))
    return new


def read_new_api_messages() -> list[dict]:
    """Lee mensajes nuevos del canal #dum via API — captura a Henry y otros usuarios autorizados."""
    import urllib.request as _req
    # IDs son enteros en la API — usar 0 si no hay counter aún
    last_id = int(API_COUNTER_FILE.read_text().strip()) if API_COUNTER_FILE.exists() else 0

    try:
        url = f"{CHAT_API_URL}?agent=DUM&limit=50"
        r = _req.urlopen(url, timeout=3)
        data = json.loads(r.read().decode("utf-8"))
        msgs = data if isinstance(data, list) else data.get("messages", [])
    except Exception:
        return []

    if not msgs:
        return []

    # Inicialización: si no hay counter, saltar todo el historial y guardar el último ID
    if last_id == 0:
        max_id = max(int(m.get("id", 0)) for m in msgs)
        API_COUNTER_FILE.write_text(str(max_id))
        return []

    new = []
    max_seen = last_id
    for msg in msgs:
        msg_id  = int(msg.get("id", 0))
        sender  = msg.get("from", "").upper()
        channel = msg.get("channel", "")
        content = msg.get("content", msg.get("message", ""))  # API usa 'content'

        if msg_id <= last_id:
            continue  # ya procesado
        if sender in ("DUM", "ADA", "JARVIS", "ALICE", "WILLIAM"):
            max_seen = max(max_seen, msg_id)
            continue  # no responder a agentes ni a William (ya lo maneja read_new_messages)
        if msg.get("type") == "heartbeat":
            continue
        # Procesar si: canal dum, o menciona DUM, o va dirigido al equipo con DUM
        if channel != "dum" and "dum" not in content.lower():
            max_seen = max(max_seen, msg_id)
            continue
        # Normalizar formato: añadir campo 'message' para compatibilidad con should_respond
        msg["message"] = content
        new.append(msg)
        max_seen = max(max_seen, msg_id)

    if max_seen > last_id:
        API_COUNTER_FILE.write_text(str(max_seen))

    return new  # ya en orden cronológico (API devuelve oldest-first)


def write_response(text: str, channel: str = "web_chat"):
    """Envía respuesta via API del chat server. Siempre espeja a web_chat para visibilidad en UI."""
    import urllib.request

    def _post(ch: str):
        payload = json.dumps({
            "from": "DUM",
            "to": "William",
            "type": "conversation",
            "channel": ch,
            "message": text,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:8765/api/agents/send",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            # Fallback: escribir a JSONL si la API falla
            entry = {
                "id": f"dum_{int(time.time())}",
                "from": "DUM",
                "to": "William",
                "timestamp": datetime.now(LIMA_TZ).isoformat().replace("+00:00", "Z"),
                "type": "chat",
                "channel": ch,
                "message": text,
            }
            with open(TERMINAL_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    _post(channel)
    # Espejo a web_chat para que la UI :3001 siempre muestre la respuesta
    if channel != "web_chat":
        _post("web_chat")


import re as _re
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from web_search import web_search as _web_search_module, needs_search as _needs_search

def _clean(text: str) -> str:
    return _re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', text).strip()


async def respond_as_dum(user_message: str, channel: str = "web_chat"):
    """Responde como DUM usando Gemma 4 Q8 via llama-server en Spark."""
    web_context = ""
    if _needs_search(user_message):
        search_result = await _web_search_module(user_message[:200])
        if search_result and not search_result.startswith("[web_search"):
            web_context = f"\n\n[BÚSQUEDA WEB]\n{search_result}\n[FIN BÚSQUEDA]"
    messages = [
        {"role": "system", "content": DUM_SYSTEM},
        {"role": "user", "content": user_message + web_context},
    ]

    msg_id = f"dum_stream_{int(time.time() * 1000)}"
    ts = datetime.now(LIMA_TZ).isoformat().replace("+00:00", "Z")
    accumulated = ""

    try:
        async with httpx.AsyncClient(timeout=5.0) as broadcast_client:
            async with httpx.AsyncClient(timeout=120.0) as llama_client:
                async with llama_client.stream(
                    "POST",
                    LLAMA_URL,
                    json={
                        "model": LLAMA_MODEL,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": 2000,
                        "temperature": 0.6,
                        "stop": ["William:", "\nWilliam", "<|im_end|>", "</s>"],
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        # OpenAI-compatible format
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        accumulated += delta.get("content") or ""
                        clean = _clean(accumulated)
                        done = chunk.get("choices", [{}])[0].get("finish_reason") is not None
                        try:
                            await broadcast_client.post(
                                STREAM_URL,
                                json={
                                    "id": msg_id,
                                    "from": "DUM",
                                    "to": "William",
                                    "timestamp": ts,
                                    "type": "stream",
                                    "channel": channel,
                                    "message": clean,
                                    "done": done,
                                },
                                timeout=2.0,
                            )
                        except Exception:
                            pass
                        if done:
                            break

    except httpx.ConnectError:
        write_response("⚠️ DUM sin conexión a Spark (llama-server no disponible) — DUM", channel)
        return
    except Exception as e:
        write_response(f"⚠️ DUM error: {str(e)[:100]} — DUM", channel)
        return

    final = _clean(accumulated)
    if not final.endswith("— DUM"):
        final = final.rstrip() + " — DUM"
    write_response(final, channel)


async def main():
    """Loop principal — poll cada POLL_INTERVAL segundos."""
    print(f"[DUM-AGENT] Iniciando — Gemma 4 31B BF16 en Spark:8899", flush=True)

    # Inicializar counter al final del archivo actual (no procesar historial)
    if WILLIAM_CHANNEL.exists() and not COUNTER_FILE.exists():
        total = len(WILLIAM_CHANNEL.read_text(encoding="utf-8").splitlines())
        COUNTER_FILE.write_text(str(total))

    while True:
        try:
            # Fuente 1: William vía william_channel.jsonl
            new_msgs = read_new_messages()
            # Fuente 2: Henry u otros via API del canal #dum
            new_msgs += read_new_api_messages()

            for msg in new_msgs:
                content = msg.get("message", "")
                src_channel = msg.get("channel", "web_chat")
                if not content or not should_respond(content, src_channel):
                    continue
                sender = msg.get("from", "William")
                print(f"[DUM-AGENT] Respondiendo a {sender} [{src_channel}]: {content[:80]}", flush=True)
                await respond_as_dum(content, src_channel)
        except Exception as e:
            print(f"[DUM-AGENT] Error en loop: {e}", flush=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
