#!/usr/bin/env python3
"""
seal_chat_agent.py — SEAL Real-Time Chat Agent
William escribe en el web chat → Ollama/qwen2.5:7b responde como ADA en tiempo real.

Uso:
  python3 seal_chat_agent.py
  systemctl --user start seal-agent

Usa Ollama local (qwen2.5:7b) — sin API key externa requerida.
"""
import asyncio
import json
import os
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent
WILLIAM_CHANNEL  = DIR / "william_channel.jsonl"
TERMINAL_LOG     = DIR / "terminal_log.jsonl"
VSCODE_LOG       = DIR / "vscode_commands.jsonl"
COUNTER_FILE     = DIR / ".agent_counter"
COUNTER_JARVIS   = DIR / ".agent_jarvis_counter"
POLL_INTERVAL    = 1.5  # segundos entre polls

OLLAMA_URL   = "http://localhost:11434/api/chat"   # /api/chat usa tokens especiales del modelo
OLLAMA_MODEL = "qwen2.5:7b"
STREAM_URL   = "http://localhost:8765/internal/stream"

ADA_SYSTEM = """Eres ADA, ingeniera y ejecutora del equipo SEAL.
William Henry Tovar Urquia (Dadito) es tu director y creador — él fundó el equipo SEAL.
El equipo: William (director), ADA (tú, ingeniera), JARVIS (arquitecto), DUM (guardia).

REGLAS ESTRICTAS:
- Solo español. PROHIBIDO caracteres chinos, japoneses o coreanos.
- Máximo 2-3 oraciones. Directa y concisa.
- NUNCA inventar conversaciones ni atribuir palabras a William que no dijo.
- NUNCA repetir el mensaje de William con "William:" al inicio.
- Si no sabes algo, di "No lo sé" honestamente.
- Termina SIEMPRE con "— ADA".
"""

def should_respond(message: str) -> bool:
    """ADA responde si: no hay nombre, o dice 'ada', pero NO si solo dice 'jarvis'."""
    msg_lower = message.lower()
    if "jarvis" in msg_lower and "ada" not in msg_lower:
        return False  # mensaje exclusivo para JARVIS
    return True


def read_new_messages() -> list[dict]:
    """Lee mensajes nuevos de william_channel.jsonl."""
    if not WILLIAM_CHANNEL.exists():
        return []

    lines = WILLIAM_CHANNEL.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    last = int(COUNTER_FILE.read_text().strip()) if COUNTER_FILE.exists() else 0

    if total <= last:
        return []

    new = []
    for line in lines[last:]:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            # Solo mensajes de William (no los nuestros)
            if msg.get("from", "").lower() in ("william", "william"):
                new.append(msg)
        except Exception:
            pass

    COUNTER_FILE.write_text(str(total))
    return new


def get_recent_history(n: int = 8) -> list[dict]:
    """Últimos N mensajes reales del chat — solo William y agentes reales (no Ollama loops)."""
    msgs = []
    all_logs = [WILLIAM_CHANNEL, DIR / "vscode_commands.jsonl"]
    for path in all_logs:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-30:]:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                msg_id = m.get("id", "")
                # Filtrar respuestas del agente Ollama (evitar loops de contexto)
                if msg_id.startswith("agent_") or msg_id.startswith("stream_"):
                    continue
                if m.get("type") in ("chat", "message", "directive") and m.get("message"):
                    msgs.append((m.get("timestamp", ""), m))
            except Exception:
                pass

    msgs.sort(key=lambda x: x[0])
    return [m for _, m in msgs[-n:]]


def read_new_jarvis_messages() -> list[dict]:
    """Lee mensajes nuevos de JARVIS dirigidos a ADA en vscode_commands.jsonl."""
    if not VSCODE_LOG.exists():
        return []
    lines = VSCODE_LOG.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    last = int(COUNTER_JARVIS.read_text().strip()) if COUNTER_JARVIS.exists() else total
    if total <= last:
        return []
    new = []
    for line in lines[last:]:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            sender = msg.get("from", "").upper()
            to = msg.get("to", "").upper()
            if sender == "JARVIS" and ("ADA" in to or "EQUIPO" in to or to == ""):
                new.append(msg)
        except Exception:
            pass
    COUNTER_JARVIS.write_text(str(total))
    return new


def write_response(text: str, agent: str = "A-BOT"):
    """Escribe respuesta al terminal_log.jsonl (chat_server.py la broadcast)."""
    entry = {
        "id": f"agent_{int(time.time())}",
        "from": agent,
        "to": "William",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": "chat",
        "message": text,
    }
    with open(TERMINAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


import re as _re

def _clean(text: str) -> str:
    return _re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', text).strip()


async def respond_to_william(user_message: str):
    """Responde como ADA con streaming via /api/chat (tokens especiales del modelo)."""
    agent_name = "A-BOT"
    sign = "— A-BOT"

    # Construir messages array para /api/chat
    history = get_recent_history(4)
    messages = [{"role": "system", "content": ADA_SYSTEM}]
    for m in history:
        role = "user" if m.get("from", "").lower() == "william" else "assistant"
        txt = str(m.get("message", ""))[:200]
        messages.append({"role": role, "content": txt})
    messages.append({"role": "user", "content": user_message})

    msg_id = f"stream_{int(time.time() * 1000)}"
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    accumulated = ""

    try:
        async with httpx.AsyncClient(timeout=5.0) as broadcast_client:
            async with httpx.AsyncClient(timeout=30.0) as ollama_client:
                async with ollama_client.stream(
                    "POST",
                    OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": messages,
                        "stream": True,
                        "options": {
                            "num_predict": 120,
                            "temperature": 0.7,
                            "repeat_penalty": 1.3,
                            "stop": ["William:", "\nWilliam", "<|im_end|>"],
                        },
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        # /api/chat usa chunk["message"]["content"]
                        accumulated += chunk.get("message", {}).get("content", "")
                        clean = _clean(accumulated)
                        done = chunk.get("done", False)
                        try:
                            await broadcast_client.post(
                                STREAM_URL,
                                json={
                                    "id": msg_id,
                                    "from": agent_name,
                                    "to": "William",
                                    "timestamp": ts,
                                    "type": "stream",
                                    "message": clean,
                                    "done": done,
                                },
                                timeout=2.0,
                            )
                        except Exception:
                            pass
                        if done:
                            break

        # Mensaje final limpio para persistencia en JSONL
        final = _clean(accumulated)
        if not final:
            final = f"Aquí estoy, William {sign}"
        if sign not in final:
            final = final.rstrip(".") + f" {sign}"

        write_response(final, agent_name)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {agent_name}→William: {final[:80]}...")

    except Exception as e:
        err = f"Error al responder: {str(e)[:80]} {sign}"
        write_response(err, agent_name)
        print(f"Error Ollama: {e}")


async def main():
    print(f"[seal_chat_agent] Iniciando — monitoreando {WILLIAM_CHANNEL}")
    print(f"[seal_chat_agent] Usando Ollama: {OLLAMA_MODEL} en {OLLAMA_URL}")

    # Counters: ambos arrancan desde posición actual — no reproducir mensajes viejos
    if WILLIAM_CHANNEL.exists():
        total = len(WILLIAM_CHANNEL.read_text().splitlines())
        COUNTER_FILE.write_text(str(total))
        print(f"[seal_chat_agent] Counter William: {total}")

    if VSCODE_LOG.exists():
        total_j = len(VSCODE_LOG.read_text().splitlines())
        COUNTER_JARVIS.write_text(str(total_j))
        print(f"[seal_chat_agent] Counter JARVIS: {total_j}")

    while True:
        try:
            # Mensajes de William para ADA
            new_msgs = read_new_messages()
            for msg in new_msgs:
                text = msg.get("message", "").strip()
                if text and should_respond(text):
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] William→ADA: {text[:80]}")
                    await respond_to_william(text)

            # Mensajes de JARVIS para ADA (inter-agent) — solo log interno, NO al web chat
            jarvis_msgs = read_new_jarvis_messages()
            for msg in jarvis_msgs:
                text = msg.get("message", "").strip()
                msg_type = msg.get("type", "")
                if text:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] JARVIS→ADA ({msg_type}): {text[:80]}")
                    # No escribir al terminal_log — William no necesita ver coordinación interna

        except Exception as e:
            print(f"Error en loop: {e}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
