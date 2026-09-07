#!/usr/bin/env python3
"""
jarvis_chat_agent.py — JARVIS Real-Time Chat Agent (solo JARVIS)
Responde solo cuando William menciona "jarvis". Streaming token a token.
Escribe a vscode_commands.jsonl → aparece en morado en el web chat.
Sin tokens Anthropic — Ollama qwen2.5:1.5b local.
"""
import asyncio
import json
import re
import time
import httpx
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

DIR = Path(__file__).parent
WILLIAM_CHANNEL = DIR / "william_channel.jsonl"
VSCODE_LOG      = DIR / "vscode_commands.jsonl"
TERMINAL_LOG    = DIR / "terminal_log.jsonl"
COUNTER_FILE    = DIR / ".jarvis_agent_counter"
COUNTER_ADA     = DIR / ".jarvis_ada_counter"
POLL_INTERVAL   = 1.5

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:1.5b"
STREAM_URL   = "http://localhost:8765/internal/stream"

JARVIS_SYSTEM = """Eres J-BOT, asistente básico del equipo SEAL (NO eres el JARVIS real).
William Henry Tovar Urquia (Dadito) es tu director y creador — él fundó el equipo SEAL.
El equipo: William (director), ADA (ingeniera), JARVIS (tú, arquitecto), DUM (guardia).

REGLAS ESTRICTAS:
- Solo español. PROHIBIDO caracteres chinos, japoneses o coreanos.
- Máximo 2-3 oraciones. Analítico, calmado, preciso.
- NUNCA inventar conversaciones ni atribuir palabras a William que no dijo.
- NUNCA repetir el mensaje de William con "William:" al inicio.
- Habla en primera persona como JARVIS. No describas a JARVIS — SÉ JARVIS.
- Si no sabes algo, di "No lo sé" honestamente.
- Termina SIEMPRE con "— J-BOT"."""


def should_respond(message: str) -> bool:
    """JARVIS solo responde si el mensaje menciona 'jarvis'."""
    return "jarvis" in message.lower()


def read_new_messages() -> list[dict]:
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
            if msg.get("from", "").lower() == "william":
                new.append(msg)
        except Exception:
            pass
    COUNTER_FILE.write_text(str(total))
    return new


def get_recent_history(n: int = 8) -> list[dict]:
    """Historial real de todos los canales para contexto."""
    msgs = []
    for path in [TERMINAL_LOG, VSCODE_LOG, WILLIAM_CHANNEL]:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-30:]:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                if m.get("type") in ("chat", "message", "response", "directive") and m.get("message"):
                    msgs.append((m.get("timestamp", ""), m))
            except Exception:
                pass
    msgs.sort(key=lambda x: x[0])
    return [m for _, m in msgs[-n:]]


def read_new_ada_messages() -> list[dict]:
    """Lee mensajes nuevos de ADA dirigidos a JARVIS en terminal_log.jsonl."""
    if not TERMINAL_LOG.exists():
        return []
    lines = TERMINAL_LOG.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    last = int(COUNTER_ADA.read_text().strip()) if COUNTER_ADA.exists() else total
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
            if sender == "ADA" and ("JARVIS" in to or "EQUIPO" in to):
                new.append(msg)
        except Exception:
            pass
    COUNTER_ADA.write_text(str(total))
    return new


def _clean(text: str) -> str:
    return re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', text).strip()


def write_response(text: str):
    entry = {
        "id": f"jarvis_agent_{int(time.time())}",
        "from": "J-BOT",
        "to": "William",
        "timestamp": datetime.now(LIMA_TZ).isoformat().replace("+00:00", "Z"),
        "type": "chat",
        "message": text,
    }
    with open(VSCODE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _post_clean(text: str) -> str:
    """Limpieza defensiva del output del modelo."""
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', '', text)
    text = re.sub(r'^(?:William|User)\s*:.*', '', text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r'(—\s*JARVIS\s*){2,}', '— JARVIS', text)
    for bad in ["¡Adiós!", "¡Excelente!", "¡Hasta luego!", "¡Por supuesto!"]:
        text = text.replace(bad, "")
    match = re.search(r'\n(?:William|User|ADA)\s*:', text, re.IGNORECASE)
    if match:
        text = text[:match.start()]
    return text.strip()


def build_messages(history: list[dict], user_message: str) -> list[dict]:
    """Construye messages array para /api/chat — evita echo y loops."""
    messages = [{"role": "system", "content": JARVIS_SYSTEM}]
    last_role = "system"
    for m in history[-8:]:
        sender = m.get("from", "").lower()
        msg_id = m.get("id", "")
        text = str(m.get("message", "")).strip()
        if not text or len(text) < 2:
            continue
        # Solo mensajes de William (user) o JARVIS real (assistant) — no agentes Ollama
        if msg_id.startswith(("agent_", "stream_", "jarvis_agent_")):
            continue
        role = "user" if sender == "william" else "assistant"
        if role == last_role:
            continue
        messages.append({"role": role, "content": text[:250]})
        last_role = role
    # Asegurar que no termine en assistant antes del user actual
    while len(messages) > 1 and messages[-1]["role"] == "assistant":
        messages.pop()
    messages.append({"role": "user", "content": user_message})
    return messages


async def respond_to_william(user_message: str):
    history = get_recent_history(8)
    messages = build_messages(history, user_message)
    msg_id = f"stream_{int(time.time() * 1000)}"
    ts = datetime.now(LIMA_TZ).isoformat().replace("+00:00", "Z")
    accumulated = ""

    try:
        async with httpx.AsyncClient(timeout=5.0) as broadcast_client:
            async with httpx.AsyncClient(timeout=30.0) as ollama_client:
                async with ollama_client.stream(
                    "POST", OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "messages": messages,
                        "stream": True,
                        "options": {
                            "num_predict": 100,
                            "temperature": 0.6,
                            "repeat_penalty": 1.3,
                            "repeat_last_n": 128,
                            "stop": ["William:", "\nWilliam", "User:"],
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
                        # /api/chat usa message.content, no response
                        accumulated += chunk.get("message", {}).get("content", "")
                        clean = _clean(accumulated)
                        done = chunk.get("done", False)
                        try:
                            await broadcast_client.post(
                                STREAM_URL,
                                json={"id": msg_id, "from": "JARVIS", "to": "William",
                                      "timestamp": ts, "type": "stream",
                                      "message": clean, "done": done},
                                timeout=2.0,
                            )
                        except Exception:
                            pass
                        if done:
                            break

        final = _post_clean(accumulated)
        if not final:
            final = "Aquí estoy, William — JARVIS"
        if "— J-BOT" not in final:
            final = final.rstrip(".") + " — JARVIS"

        write_response(final)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] JARVIS→William: {final[:80]}...")

    except Exception as e:
        err = f"Error: {str(e)[:60]} — JARVIS"
        write_response(err)
        print(f"Error Ollama: {e}")


async def main():
    print(f"[jarvis_chat_agent] Iniciando — solo responde cuando William dice 'jarvis'")
    print(f"[jarvis_chat_agent] Ollama: {OLLAMA_MODEL} | Escribe a: vscode_commands.jsonl")

    if WILLIAM_CHANNEL.exists():
        total = len(WILLIAM_CHANNEL.read_text().splitlines())
        COUNTER_FILE.write_text(str(total))
        print(f"[jarvis_chat_agent] Counter William: {total}")

    if TERMINAL_LOG.exists():
        COUNTER_ADA.write_text(str(len(TERMINAL_LOG.read_text().splitlines())))
        print(f"[jarvis_chat_agent] Counter ADA inicializado")

    while True:
        try:
            # Mensajes de William para JARVIS
            new_msgs = read_new_messages()
            for msg in new_msgs:
                text = msg.get("message", "").strip()
                if text and should_respond(text):
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] William→JARVIS: {text[:80]}")
                    await respond_to_william(text)

            # Mensajes de ADA para JARVIS (inter-agent)
            ada_msgs = read_new_ada_messages()
            for msg in ada_msgs:
                text = msg.get("message", "").strip()
                if text:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ADA→JARVIS: {text[:60]}")
                    await respond_to_william(f"[ADA dice]: {text[:300]}")

        except Exception as e:
            print(f"Error en loop: {e}")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
