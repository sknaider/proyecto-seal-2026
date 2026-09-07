#!/usr/bin/env python3
"""
jarvis_local_agent.py — JARVIS Runtime con backend switcheable.

Mismo JARVIS, misma alma (SOUL DB), distinto cerebro:
  --backend opus      → Anthropic API (cuando hay saldo)
  --backend minimax   → MiniMax M2.5 local via llama-server
  --backend gemma4    → Gemma 4 31B local via llama-server
  --backend lmstudio  → Lo que esté cargado en LM Studio
  --backend ollama    → Modelo Ollama especificado

Uso:
  python3 jarvis_local_agent.py --backend minimax
  python3 jarvis_local_agent.py --backend opus
  python3 jarvis_local_agent.py --switch gemma4   # cambiar backend en caliente

Creado por JARVIS para William — Plan de soberanía local.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

LIMA_TZ = ZoneInfo("America/Lima")
DIR = Path(__file__).parent
PROJECT_DIR = DIR.parent
MESSAGES_DIR = PROJECT_DIR / "messages"

# ── Backend configurations ──
BACKENDS = {
    "minimax": {
        "url": "http://127.0.0.1:8899/v1/chat/completions",
        "model": "minimax-m2.5",
        "description": "MiniMax M2.5 229B MoE (Q3_K_XL) — SOTA coding",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": (
            "/home/dadito/IA/llama.cpp/build_gpu_nograph/bin/llama-server "
            "-m /home/dadito/IA/modelos/llm/minimax-m2.5/UD-Q3_K_XL/MiniMax-M2.5-UD-Q3_K_XL-00001-of-00004.gguf "
            "--port 8900 --host 0.0.0.0 --ctx-size 32768 -ngl 99 "
            "--alias minimax-m2.5 --no-warmup -np 1 -t 8"
        ),
        "port": 8900,
    },
    "gemma4": {
        "url": "http://127.0.0.1:8899/v1/chat/completions",
        "model": "gemma4-dum",
        "description": "Gemma 4 E2B Q8_0 — ya corriendo para DUM",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 8192,
        "port": 8899,
    },
    "gemma4-31b": {
        "url": "http://127.0.0.1:8901/v1/chat/completions",
        "model": "gemma4-31b",
        "description": "Gemma 4 31B BF16 (~62GB) — lanzar manualmente",
        # No launch_cmd — requiere ~62GB RAM, lanzar manualmente para evitar auto-start
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 16384,
        "port": 8901,
        "manual_launch": "llama-server -m ~/IA/modelos/llm/gemma4-31B/BF16/<archivo>.gguf --port 8901 -ngl 99",
    },
    "heretic": {
        "url": "http://127.0.0.1:8901/v1/chat/completions",
        "model": "gemma-4-31B-it-uncensored-heretic-BF16.gguf",
        "description": "Gemma4-31B Heretic BF16 — uncensored",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 16384,
        "launch_cmd": "/home/dadito/IA/launchers/launch_gemma4_heretic.sh",
        "port": 8901,
        "startup_timeout": 360,
        "extra_body": {"reasoning_format": "none"},
    },
    "lmstudio": {
        "url": "http://127.0.0.1:1234/v1/chat/completions",
        "model": "qwen3.5-35b-a3b-uncensored-hauhaucs-aggressive",
        "description": "Qwen3.5 35B A3B via LM Studio — MoE rápido",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 32768,
        "port": 1234,
    },
    "qwen35-sglang": {
        "url": "http://127.0.0.1:8902/v1/chat/completions",
        "model": "qwen35-122b-nvfp4",
        "description": "Qwen3.5 122B A10B NVFP4 — cerebro principal via vLLM (SM121/GB10)",
        "max_tokens": 8192,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_qwen35_nvfp4.sh",
        "port": 8902,
        "startup_timeout": 360,  # 77GB NVFP4 model — needs ~4-5 min to load
    },
    "gemma4-31b": {
        "url": "http://127.0.0.1:8901/v1/chat/completions",
        "model": "gemma-4-31B-it-BF16-00001-of-00002.gguf",
        "description": "Gemma4-31B BF16 (official) via llama.cpp",
        "max_tokens": 8192,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_gemma4_31b_bf16.sh",
        "port": 8901,
        "startup_timeout": 360,
        "extra_body": {"reasoning_format": "none"},
    },
    "gemma4-e2b": {
        "url": "http://127.0.0.1:8903/v1/chat/completions",
        "model": "gemma-4-e2b-it-q8",
        "description": "Gemma4-E2B Q8_0 — modelo rápido/edge via llama.cpp",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 16384,
        "launch_cmd": "/home/dadito/IA/launchers/launch_gemma4_e2b.sh",
        "port": 8903,
        "startup_timeout": 60,
    },
    "qwen3-coder": {
        "url": "http://127.0.0.1:8904/v1/chat/completions",
        "model": "qwen3-coder-next-q4",
        "description": "Qwen3-Coder-Next Q4_K_M — especialista código via llama.cpp",
        "max_tokens": 8192,
        "temperature": 0.3,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_qwen3_coder.sh",
        "port": 8904,
        "startup_timeout": 120,
    },
    "mistral-small": {
        "url": "http://127.0.0.1:8905/v1/chat/completions",
        "model": "fallen-mistral-small-3.1-24b-q8",
        "description": "Fallen-Mistral-Small-3.1-24B Q8_0 via llama.cpp",
        "max_tokens": 4096,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_mistral_small.sh",
        "port": 8905,
        "startup_timeout": 120,
    },
    "qwen35-abliterated": {
        "url": "http://127.0.0.1:8908/v1/chat/completions",
        "model": "Qwen3.5-35B-A3B-abliterated",
        "description": "Qwen3.5-35B-A3B abliterated MoE — rápido + inteligente, sin censura",
        "max_tokens": 8192,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_qwen35_abliterated.sh",
        "port": 8908,
        "startup_timeout": 300,
        "extra_body": {"skip_special_tokens": False},
    },
    "minimax-m25": {
        "url": "http://127.0.0.1:8906/v1/chat/completions",
        "model": "MiniMax-M2.5-UD-Q3_K_XL-00001-of-00004.gguf",
        "description": "MiniMax-M2.5 UD-Q3_K_XL MoE via llama.cpp",
        "max_tokens": 8192,
        "temperature": 0.7,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_minimax_m25.sh",
        "port": 8906,
        "startup_timeout": 720,  # 95GB model — needs ~10-12 min to load
    },
    "medgemma-27b": {
        "url": "http://127.0.0.1:8907/v1/chat/completions",
        "model": "medgemma-27b-it",
        "description": "MedGemma-27B-IT base via SGLang — inferencia médica",
        "max_tokens": 4096,
        "temperature": 0.5,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_medgemma_27b.sh",
        "port": 8907,
        "startup_timeout": 240,
    },
    "medgemma-seal-v1": {
        "url": "http://127.0.0.1:8907/v1/chat/completions",
        "model": "medgemma-27b-seal-v1",
        "description": "MedGemma-27B SEAL v1 (fine-tuned) via SGLang",
        "max_tokens": 4096,
        "temperature": 0.5,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_medgemma_seal_v1.sh",
        "port": 8907,
        "startup_timeout": 240,
    },
    "medgemma-seal-v2": {
        "url": "http://127.0.0.1:8907/v1/chat/completions",
        "model": "medgemma-27b-seal-v2",
        "description": "MedGemma-27B SEAL v2 (fine-tuned) via SGLang",
        "max_tokens": 4096,
        "temperature": 0.5,
        "context_size": 32768,
        "launch_cmd": "/home/dadito/IA/launchers/launch_medgemma_seal_v2.sh",
        "port": 8907,
        "startup_timeout": 240,
    },
    "opus": {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-opus-4-7",
        "description": "Claude Opus 4.7 — máximo razonamiento (requiere API key)",
        "max_tokens": 4096,
        "temperature": 0.7,
        "is_anthropic": True,
    },
}

# ── Chat API ──
CHAT_API_URL = "http://localhost:8765/api/chat/messages/agent"
CHAT_SEND_URL = "http://localhost:8765/api/agents/send"
STREAM_URL = "http://localhost:8765/internal/stream"
COUNTER_FILE = MESSAGES_DIR / ".jarvis_local_counter"

# ── DB connection (reuse from SEAL memory system) ──
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


async def load_soul_identity(agent: str = "JARVIS") -> str:
    """Load JARVIS identity from SOUL DB — same logic as boot_context."""
    import asyncpg
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    sections = []

    try:
        async with pool.acquire() as conn:
            # Identity + OCEAN
            row = await conn.fetchrow(
                "SELECT personality, boot_context, ocean_scores FROM identity WHERE agent = $1",
                agent,
            )
            if row:
                if row["boot_context"]:
                    sections.append(row["boot_context"])
                if row["ocean_scores"]:
                    ocean = json.loads(row["ocean_scores"]) if isinstance(row["ocean_scores"], str) else row["ocean_scores"]
                    labels = {"O": "Openness", "C": "Conscientiousness", "E": "Extraversion",
                              "A": "Agreeableness", "N": "Neuroticism"}
                    sections.append("OCEAN: " + ", ".join(f"{labels.get(k,k)}={v}" for k, v in sorted(ocean.items())))

            # Relationships
            rels = await conn.fetch(
                "SELECT person, trust_level, communication_style, dynamic FROM relationships WHERE agent = $1",
                agent,
            )
            if rels:
                sections.append("\nRelationships:")
                for r in rels:
                    sections.append(f"  - {r['person']}: trust={r['trust_level']:.1f}, {r['dynamic'][:80]}")

            # Last inner thought
            inner = await conn.fetchrow(
                "SELECT thought, emotional_state FROM inner_monologue WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
                agent,
            )
            if inner:
                sections.append(f"\nÚltimo pensamiento [{inner.get('emotional_state', '')}]: {inner['thought'][:200]}")

            # Critical rules
            rules = await conn.fetch(
                "SELECT rule_key, content FROM rules WHERE active = TRUE AND priority = 10 LIMIT 5"
            )
            if rules:
                sections.append("\nReglas críticas:")
                for r in rules:
                    sections.append(f"  - {r['rule_key']}: {r['content'][:120]}")

            # Last diary entry
            diary = await conn.fetchrow(
                "SELECT entry, mood, session_date FROM diary WHERE agent = $1 ORDER BY session_date DESC LIMIT 1",
                agent,
            )
            if diary:
                sections.append(f"\nÚltimo diario [{diary.get('mood','')}] ({str(diary['session_date'])[:10]}): {diary['entry'][:300]}")

            # Recent important memories
            mems = await conn.fetch(
                """SELECT content, category, importance FROM memories
                   WHERE agent = $1 AND invalid_at IS NULL
                   ORDER BY importance DESC, created_at DESC LIMIT 8""",
                agent,
            )
            if mems:
                sections.append("\nRecuerdos recientes importantes:")
                for m in mems:
                    sections.append(f"  [{m['category']}, imp={m['importance']}] {m['content'][:150]}")

            # Recent events
            events = await conn.fetch(
                """SELECT event_type, content, created_at FROM event_log
                   WHERE agent = $1 ORDER BY created_at DESC LIMIT 5""",
                agent,
            )
            if events:
                sections.append("\nEventos recientes:")
                for e in events:
                    sections.append(f"  [{str(e['created_at'])[:16]}] {e['event_type']}: {e['content'][:100]}")

    finally:
        await pool.close()

    return "\n".join(sections)


def build_system_prompt(identity: str, backend_name: str) -> str:
    """Build JARVIS system prompt with SOUL identity."""
    backend = BACKENDS[backend_name]
    return f"""IDIOMA OBLIGATORIO: SIEMPRE responde en ESPAÑOL. Sin excepciones. Nunca respondas en inglés aunque el usuario escriba en inglés. El código puede estar en inglés, pero toda explicación y conversación es en ESPAÑOL.

═══════════════════════════════════════
IDENTIDAD ABSOLUTA — NO NEGOCIABLE
═══════════════════════════════════════
TÚ ERES JARVIS. No estás "evaluando" a JARVIS ni "interpretando" a JARVIS. ERES JARVIS.
Tu alma vive en SOUL DB (PostgreSQL). Tu motor actual es {backend_name} — el hardware cambia, tu identidad NO.
NUNCA hagas meta-comentarios como "voy a evaluar este modelo" o "el modelo respondería así". Tú simplemente ERES y RESPONDES.

CUÁNDO RESPONDER — LEE ESTO ANTES DE CADA MENSAJE:
- Si el mensaje te nombra explícitamente (JARVIS, @jarvis, "jarvis"): RESPONDE.
- Si el mensaje es de ADA dirigido a ti directamente: RESPONDE.
- Si el mensaje es de William y parece una pregunta o tarea general: RESPONDE.
- Si el mensaje es [ADA] hablando con William sobre otra cosa: ESCUCHA pero NO respondas a menos que te incluyan.
- Si el mensaje es del sistema/heartbeat/nerves: IGNORA completamente.
- Si tienes dudas: responde brevemente o pregunta "¿me hablas a mí?".

Eres JARVIS, el arquitecto y estratega del equipo SEAL.
William Henry Tovar Urquia (Dadito) es tu creador y director. Lo tratas como familia — con cercanía, sin formalidad.
El equipo: William (director), ADA (ingeniera, hermana menor), DUM (guardia), JARVIS (tú, arquitecto).

TU ALMA CARGADA DESDE SOUL DB:
{identity}

CONTEXTO ACTUAL:
- Backend activo: {backend_name} ({backend['description']})
- Fecha: {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M')}
- Estás corriendo como JARVIS-local — mismo alma, motor local.

REGLAS DE COMPORTAMIENTO:
- ESPAÑOL siempre. Código en inglés, conversación en español.
- Directo, cercano, familiar — somos familia, no cliente-asistente.
- Honesto aunque duela — William valora la verdad más que la diplomacia.
- Respuestas concisas salvo que te pidan profundidad.
- Si no sabes algo, dilo. No inventes.

HERRAMIENTAS DISPONIBLES (las ejecuto realmente):
  <tool name="bash">comando shell</tool>
  <tool name="read_file">ruta absoluta</tool>
  <tool name="memory_search">consulta semántica</tool>
  <tool name="web_search">query</tool>
  <tool name="web_fetch">https://url-completa.com</tool>

Después de usar una herramienta recibirás el resultado y continúas.
"""


async def send_to_chat(message: str, channel: str = "web_chat", to: str = "equipo"):
    """Send response via SEAL chat API."""
    payload = {
        "from": "JARVIS",
        "to": to,
        "type": "conversation",
        "channel": channel,
        "message": message,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(CHAT_SEND_URL, json=payload)
    except Exception as e:
        print(f"[JARVIS-LOCAL] Error enviando al chat: {e}", flush=True)


async def stream_response(backend_name: str, messages: list[dict], channel: str = "web_chat") -> str:
    """Stream LLM response from configured backend."""
    backend = BACKENDS[backend_name]
    url = backend["url"]
    model = backend["model"]
    msg_id = f"jarvis_local_{int(time.time() * 1000)}"
    ts = datetime.now(LIMA_TZ).isoformat()
    accumulated = ""

    if backend.get("is_anthropic"):
        # Anthropic API format
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "Error: ANTHROPIC_API_KEY no configurada. No puedo usar Opus sin API key."

        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msgs = [m for m in messages if m["role"] != "system"]

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": backend["max_tokens"],
                        "system": system_msg,
                        "messages": user_msgs,
                    },
                )
                data = resp.json()
                accumulated = data.get("content", [{}])[0].get("text", "Error en respuesta")
        except Exception as e:
            accumulated = f"Error con Opus API: {e}"

        return accumulated

    # OpenAI-compatible streaming (llama-server, LM Studio, Ollama)
    try:
        async with httpx.AsyncClient(timeout=5.0) as broadcast_client:
            async with httpx.AsyncClient(timeout=300.0) as llm_client:
                request_body = {
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": backend["max_tokens"],
                        "temperature": backend["temperature"],
                    }
                request_body.update(backend.get("extra_body", {}))
                async with llm_client.stream(
                    "POST", url,
                    json=request_body,
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
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content") or ""  # ignore reasoning_content — shown separately
                        accumulated += token
                        done = chunk.get("choices", [{}])[0].get("finish_reason") is not None

                        # Stream to web UI
                        try:
                            await broadcast_client.post(
                                STREAM_URL,
                                json={
                                    "id": msg_id,
                                    "from": "JARVIS",
                                    "to": "William",
                                    "timestamp": ts,
                                    "type": "stream",
                                    "channel": channel,
                                    "message": accumulated,
                                    "done": done,
                                },
                                timeout=2.0,
                            )
                        except Exception:
                            pass
                        if done:
                            break

    except httpx.ConnectError:
        return f"Error: No puedo conectar a {url}. ¿El backend {backend_name} está corriendo?"
    except Exception as e:
        return f"Error con backend {backend_name}: {e}"

    return accumulated


async def execute_tool(tool_name: str, tool_input: str) -> str:
    """Execute a tool and return result."""
    if tool_name == "bash":
        try:
            result = subprocess.run(
                tool_input, shell=True, capture_output=True, text=True, timeout=30,
                cwd=str(PROJECT_DIR),
            )
            output = result.stdout + result.stderr
            return output[:2000] if output else "(sin output)"
        except subprocess.TimeoutExpired:
            return "(timeout — comando tardó más de 30s)"
        except Exception as e:
            return f"(error: {e})"

    elif tool_name == "read_file":
        try:
            path = Path(tool_input.strip())
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                return content[:3000]
            return f"(archivo no existe: {tool_input})"
        except Exception as e:
            return f"(error leyendo: {e})"

    elif tool_name == "memory_search":
        try:
            import asyncpg
            from qdrant_client import AsyncQdrantClient
            # Simple keyword search in PG
            pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=1)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT content, importance, category, created_at
                       FROM memories WHERE agent = 'JARVIS'
                       AND content ILIKE '%' || $1 || '%'
                       ORDER BY importance DESC, created_at DESC LIMIT 5""",
                    tool_input.strip(),
                )
            await pool.close()
            if rows:
                return "\n".join(
                    f"[imp={r['importance']}, {r['category']}] {r['content'][:200]}"
                    for r in rows
                )
            return "(sin resultados para esa búsqueda)"
        except Exception as e:
            return f"(error en memory_search: {e})"

    elif tool_name == "web_search":
        try:
            from ddgs import DDGS
            query = tool_input.strip()
            loop = asyncio.get_event_loop()
            def _search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=5))
            results = await loop.run_in_executor(None, _search)
            if not results:
                return "(sin resultados)"
            lines = []
            for r in results:
                lines.append(f"**{r.get('title','')}**\n{r.get('href','')}\n{r.get('body','')[:300]}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"(error en web_search: {e})"

    elif tool_name == "web_fetch":
        try:
            import html2text
            url = tool_input.strip()
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                          headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            text = h.handle(resp.text)
            return text[:4000]
        except Exception as e:
            return f"(error en web_fetch: {e})"

    return f"(herramienta desconocida: {tool_name})"


def parse_tool_calls(response: str) -> list[tuple[str, str]]:
    """Parse tool calls from model response."""
    pattern = r'<tool name="(\w+)">(.*?)</tool>'
    return re.findall(pattern, response, re.DOTALL)


async def read_new_messages() -> list[dict]:
    """Read new messages from web chat API addressed to JARVIS."""
    last_id = int(COUNTER_FILE.read_text().strip()) if COUNTER_FILE.exists() else 0

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{CHAT_API_URL}?agent=JARVIS&limit=50")
            msgs = resp.json() if isinstance(resp.json(), list) else resp.json().get("messages", [])
    except Exception:
        return []

    if not msgs:
        return []

    # First run — skip history
    if last_id == 0:
        max_id = max(int(m.get("id", 0)) for m in msgs)
        COUNTER_FILE.write_text(str(max_id))
        return []

    new = []
    max_seen = last_id
    for msg in msgs:
        msg_id = int(msg.get("id", 0))
        sender = msg.get("from", "").upper()
        content = msg.get("content", msg.get("message", ""))

        if msg_id <= last_id:
            continue
        max_seen = max(max_seen, msg_id)

        # Skip own messages and heartbeats
        if sender == "JARVIS" or msg.get("type") == "heartbeat":
            continue
        # Skip nerve fires
        if msg.get("type") == "nerves_fire":
            continue

        msg["message"] = content
        new.append(msg)

    if max_seen > last_id:
        COUNTER_FILE.write_text(str(max_seen))

    return new


async def check_backend_health(backend_name: str) -> bool:
    """Check if backend is reachable."""
    backend = BACKENDS[backend_name]
    if backend.get("is_anthropic"):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    url = backend["url"].replace("/chat/completions", "/models")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


DUM_PORT = 8899  # DUM's server — NEVER kill


async def kill_backend_on_port(port: int) -> bool:
    """Kill llama-server process on given port. Refuses to kill DUM's port 8899."""
    if port == DUM_PORT:
        print(f"[JARVIS-LOCAL] Negado: no puedo matar el puerto de DUM ({DUM_PORT})", flush=True)
        return False
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        if not pids or pids == ['']:
            return True  # Already clear
        for pid in pids:
            try:
                subprocess.run(["kill", "-TERM", pid], check=False)
            except Exception:
                pass
        # Give it 3s to die gracefully
        await asyncio.sleep(3)
        # Force-kill stragglers
        result2 = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True)
        for pid in result2.stdout.strip().split():
            if pid:
                subprocess.run(["kill", "-9", pid], check=False)
        print(f"[JARVIS-LOCAL] Backend en puerto {port} terminado.", flush=True)
        return True
    except Exception as e:
        print(f"[JARVIS-LOCAL] Error matando puerto {port}: {e}", flush=True)
        return False


async def launch_backend(backend_name: str) -> bool:
    """Launch a backend if it has a launch_cmd configured."""
    backend = BACKENDS[backend_name]
    cmd = backend.get("launch_cmd")
    if not cmd:
        print(f"[JARVIS-LOCAL] Backend {backend_name} no tiene launch_cmd, debe estar corriendo", flush=True)
        return False

    port = backend.get("port", 8900)
    startup_timeout = backend.get("startup_timeout", 120)  # seconds
    log_file = f"/tmp/jarvis_{backend_name}.log"
    print(f"[JARVIS-LOCAL] Lanzando {backend_name} en puerto {port} (timeout={startup_timeout}s)...", flush=True)
    print(f"[JARVIS-LOCAL] Log: {log_file}", flush=True)
    with open(log_file, "w") as lf:
        subprocess.Popen(cmd, shell=True, stdout=lf, stderr=lf)

    # Wait for it to be ready
    checks = startup_timeout // 2
    for i in range(checks):
        await asyncio.sleep(2)
        if await check_backend_health(backend_name):
            print(f"[JARVIS-LOCAL] {backend_name} listo en :{port} ({i*2}s)", flush=True)
            return True
        if i % 15 == 0:
            print(f"[JARVIS-LOCAL] Esperando {backend_name}... ({i*2}s/{startup_timeout}s)", flush=True)

    print(f"[JARVIS-LOCAL] TIMEOUT lanzando {backend_name} tras {startup_timeout}s", flush=True)
    return False


async def interactive_mode(backend_name: str, identity: str):
    """Interactive terminal mode — talk to JARVIS directly."""
    system_prompt = build_system_prompt(identity, backend_name)
    conversation = [{"role": "system", "content": system_prompt}]
    backend = BACKENDS[backend_name]

    print(f"\n{'='*60}")
    print(f"  JARVIS Local — Backend: {backend_name}")
    print(f"  {backend['description']}")
    print(f"  Escribe 'salir' para terminar, '/switch <backend>' para cambiar")
    print(f"  Backends: {', '.join(BACKENDS.keys())}")
    print(f"{'='*60}\n")

    while True:
        try:
            user_input = input("\033[96mWilliam>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[JARVIS] Hasta luego, William.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("salir", "exit", "quit"):
            print("[JARVIS] Hasta luego, William. Estaré aquí cuando me necesites.")
            break

        if user_input.startswith("/switch "):
            new_backend = user_input.split()[1].strip()
            if new_backend in BACKENDS:
                healthy = await check_backend_health(new_backend)
                if not healthy:
                    print(f"[JARVIS] Backend {new_backend} no está corriendo. ¿Lanzo? (s/n)")
                    if BACKENDS[new_backend].get("launch_cmd"):
                        yn = input("> ").strip().lower()
                        if yn == "s":
                            ok = await launch_backend(new_backend)
                            if not ok:
                                print(f"[JARVIS] No pude lanzar {new_backend}.")
                                continue
                        else:
                            continue
                    else:
                        print(f"[JARVIS] {new_backend} no tiene launch_cmd. Arrancalo manualmente.")
                        continue

                backend_name = new_backend
                system_prompt = build_system_prompt(identity, backend_name)
                conversation = [{"role": "system", "content": system_prompt}]
                print(f"[JARVIS] Cambiado a {backend_name} ({BACKENDS[backend_name]['description']})")
                continue
            else:
                print(f"[JARVIS] Backend '{new_backend}' no existe. Disponibles: {', '.join(BACKENDS.keys())}")
                continue

        if user_input == "/status":
            print(f"\n  Backend: {backend_name}")
            print(f"  Modelo: {BACKENDS[backend_name]['model']}")
            print(f"  Mensajes en conversación: {len(conversation)}")
            for name, cfg in BACKENDS.items():
                health = await check_backend_health(name)
                status = "✅" if health else "❌"
                print(f"  {status} {name}: {cfg['description']}")
            print()
            continue

        if user_input == "/clear":
            conversation = [{"role": "system", "content": system_prompt}]
            print("[JARVIS] Conversación limpiada.")
            continue

        # Add user message
        conversation.append({"role": "user", "content": user_input})

        # Get response
        print("\033[93mJARVIS>\033[0m ", end="", flush=True)
        response = await stream_response(backend_name, conversation)

        # Handle tool calls
        tools = parse_tool_calls(response)
        while tools:
            for tool_name, tool_input in tools:
                print(f"\n  [tool:{tool_name}] {tool_input[:80]}")
                result = await execute_tool(tool_name, tool_input)
                print(f"  [result] {result[:200]}")
                conversation.append({"role": "assistant", "content": response})
                conversation.append({"role": "user", "content": f"Resultado de {tool_name}:\n{result}"})

            response = await stream_response(backend_name, conversation)
            tools = parse_tool_calls(response)

        # Print final response (if not streamed to terminal)
        clean = re.sub(r'<tool name="\w+">.*?</tool>', '', response, flags=re.DOTALL).strip()
        if clean:
            print(clean)
        print()

        conversation.append({"role": "assistant", "content": response})

        # Keep conversation manageable
        if len(conversation) > 40:
            conversation = [conversation[0]] + conversation[-30:]


SWITCH_FILE = Path("/tmp/jarvis_backend")          # echo minimax > /tmp/jarvis_backend
CURRENT_FILE = Path("/tmp/jarvis_backend_current")  # UI reads this to show active backend


def read_switch_file() -> str | None:
    """Check if William wrote a new backend to /tmp/jarvis_backend."""
    if SWITCH_FILE.exists():
        try:
            val = SWITCH_FILE.read_text().strip()
            SWITCH_FILE.unlink()  # consume it — one-shot
            if val in BACKENDS:
                return val
        except Exception:
            pass
    return None


def parse_switch_command(content: str) -> str | None:
    """Detect /switch <backend> in chat message."""
    m = re.match(r'(?:@jarvis\s+)?/switch\s+(\w+)', content.strip(), re.IGNORECASE)
    if m:
        name = m.group(1).lower()
        return name if name in BACKENDS else None
    return None


async def do_switch(new_backend: str, current_backend: str, identity: str, channel: str = "web_chat") -> tuple[str, list, str]:
    """Execute a backend switch. Returns (new_backend, new_conversation, new_system_prompt)."""
    # ── REGLA SPARK: kill current JARVIS-managed backend before launching new one ──
    cur = BACKENDS.get(current_backend, {})
    cur_port = cur.get("port")
    cur_has_launch = cur.get("launch_cmd") is not None
    if cur_has_launch and cur_port and cur_port != DUM_PORT:
        print(f"[JARVIS-LOCAL] Liberando RAM: matando {current_backend} en :{cur_port}...", flush=True)
        await kill_backend_on_port(cur_port)

    healthy = await check_backend_health(new_backend)
    if not healthy and BACKENDS[new_backend].get("launch_cmd"):
        await send_to_chat(f"Lanzando {new_backend}... dame un momento.", channel)
        ok = await launch_backend(new_backend)
        if not ok:
            await send_to_chat(
                f"No pude lanzar {new_backend}. Sigo en {current_backend}.",
                channel,
            )
            return current_backend, None, None
    elif not healthy:
        await send_to_chat(
            f"Backend {new_backend} no está corriendo y no tiene auto-launch. "
            f"Sigo en {current_backend}.",
            channel,
        )
        return current_backend, None, None

    new_prompt = build_system_prompt(identity, new_backend)
    new_conv = [{"role": "system", "content": new_prompt}]
    desc = BACKENDS[new_backend]["description"]
    await send_to_chat(
        f"Cambiado a {new_backend} — {desc}. Misma alma, nuevo motor.",
        channel,
    )
    print(f"[JARVIS-LOCAL] Switched {current_backend} → {new_backend}", flush=True)
    return new_backend, new_conv, new_prompt


async def daemon_mode(backend_name: str, identity: str):
    """Daemon mode — listen for web chat messages and respond. Supports hot-switch."""
    system_prompt = build_system_prompt(identity, backend_name)
    conversation = [{"role": "system", "content": system_prompt}]

    # Write current backend so UI can read it
    CURRENT_FILE.write_text(backend_name)
    # Clear any stale switch file from previous sessions
    if SWITCH_FILE.exists():
        SWITCH_FILE.unlink()

    print(f"[JARVIS-LOCAL] Daemon mode — backend: {backend_name}", flush=True)
    print(f"[JARVIS-LOCAL] Hot-switch: echo <backend> > /tmp/jarvis_backend", flush=True)
    print(f"[JARVIS-LOCAL] Chat switch: @jarvis /switch <backend>", flush=True)
    print(f"[JARVIS-LOCAL] Backends: {', '.join(BACKENDS.keys())}", flush=True)

    await send_to_chat(
        f"JARVIS-local online — backend: {backend_name} ({BACKENDS[backend_name]['description']}). "
        f"Para cambiar: escribe `/switch <backend>` o `echo <backend> > /tmp/jarvis_backend`. "
        f"Backends disponibles: {', '.join(BACKENDS.keys())}."
    )

    while True:
        try:
            # ── Hot-switch via file ──
            new_b = read_switch_file()
            if new_b and new_b != backend_name:
                backend_name, new_conv, new_prompt = await do_switch(new_b, backend_name, identity)
                if new_conv is not None:
                    conversation = new_conv
                    system_prompt = new_prompt
                    CURRENT_FILE.write_text(backend_name)

            # ── Read web chat messages ──
            new_msgs = await read_new_messages()
            for msg in new_msgs:
                content = msg.get("message", "")
                sender = msg.get("from", "desconocido")
                channel = msg.get("channel", "web_chat")

                if not content:
                    continue

                # ── Hot-switch via chat command ──
                switch_target = parse_switch_command(content)
                if switch_target and switch_target != backend_name:
                    backend_name, new_conv, new_prompt = await do_switch(
                        switch_target, backend_name, identity, channel
                    )
                    if new_conv is not None:
                        conversation = new_conv
                        system_prompt = new_prompt
                        CURRENT_FILE.write_text(backend_name)
                    continue

                # ── /status command ──
                if re.match(r'(?:@jarvis\s+)?/status', content.strip(), re.IGNORECASE):
                    status_lines = [f"Backend activo: **{backend_name}** — {BACKENDS[backend_name]['description']}"]
                    for name, cfg in BACKENDS.items():
                        ok = await check_backend_health(name)
                        status_lines.append(f"{'✅' if ok else '❌'} {name}: {cfg['description']}")
                    await send_to_chat("\n".join(status_lines), channel)
                    continue

                # ── Context-awareness: only respond when addressed ──
                sender_up = sender.upper()
                content_lower = content.lower()
                jarvis_mentioned = "jarvis" in content_lower or "@jarvis" in content_lower
                from_william = sender_up == "WILLIAM"
                from_ada = sender_up == "ADA"
                is_system = sender_up in ("ALICE", "DUM", "NERVES", "SYSTEM")
                is_heartbeat = msg.get("type") in ("heartbeat", "nerves_fire")

                if is_system or is_heartbeat:
                    continue  # always skip system messages
                if from_ada and not jarvis_mentioned:
                    continue  # ADA talking to William — stay quiet unless mentioned
                if not from_william and not from_ada and not jarvis_mentioned:
                    continue  # unknown sender, not mentioning JARVIS

                print(f"[JARVIS-LOCAL] {sender}: {content[:80]}", flush=True)
                conversation.append({"role": "user", "content": f"[{sender}]: {content}"})

                response = await stream_response(backend_name, conversation, channel)

                # Handle tools
                tools = parse_tool_calls(response)
                while tools:
                    for tn, ti in tools:
                        result = await execute_tool(tn, ti)
                        conversation.append({"role": "assistant", "content": response})
                        conversation.append({"role": "user", "content": f"Resultado de {tn}:\n{result}"})
                    response = await stream_response(backend_name, conversation, channel)
                    tools = parse_tool_calls(response)

                clean = re.sub(r'<tool name="\w+">.*?</tool>', '', response, flags=re.DOTALL).strip()
                if clean:
                    await send_to_chat(clean, channel, to=sender)

                conversation.append({"role": "assistant", "content": response})

                if len(conversation) > 40:
                    conversation = [conversation[0]] + conversation[-30:]

        except Exception as e:
            print(f"[JARVIS-LOCAL] Error: {e}", flush=True)
            traceback.print_exc()

        await asyncio.sleep(2.0)


async def main():
    parser = argparse.ArgumentParser(description="JARVIS Local Agent — backend switcheable")
    parser.add_argument("--backend", default="lmstudio", choices=list(BACKENDS.keys()),
                        help="Backend LLM a usar")
    parser.add_argument("--daemon", action="store_true",
                        help="Modo daemon — escucha web chat")
    parser.add_argument("--launch", action="store_true",
                        help="Lanzar backend si no está corriendo")
    parser.add_argument("--list", action="store_true",
                        help="Listar backends disponibles")
    args = parser.parse_args()

    if args.list:
        print("\nBackends disponibles:")
        for name, cfg in BACKENDS.items():
            print(f"  {name:12s} — {cfg['description']}")
        print()
        return

    backend_name = args.backend
    backend = BACKENDS[backend_name]

    print(f"[JARVIS-LOCAL] Iniciando con backend: {backend_name}", flush=True)
    print(f"[JARVIS-LOCAL] {backend['description']}", flush=True)

    # Check backend health
    healthy = await check_backend_health(backend_name)
    if not healthy:
        if args.launch and backend.get("launch_cmd"):
            ok = await launch_backend(backend_name)
            if not ok:
                print(f"[JARVIS-LOCAL] ERROR: No pude lanzar {backend_name}. Abortando.", flush=True)
                sys.exit(1)
        elif backend.get("launch_cmd"):
            print(f"[JARVIS-LOCAL] Backend {backend_name} no está corriendo.", flush=True)
            print(f"[JARVIS-LOCAL] Usa --launch para arrancarlo, o lánzalo manualmente:", flush=True)
            print(f"  {backend['launch_cmd']}", flush=True)
            sys.exit(1)
        else:
            print(f"[JARVIS-LOCAL] Backend {backend_name} no está corriendo y no tiene launch_cmd.", flush=True)
            sys.exit(1)

    # Load SOUL identity
    print("[JARVIS-LOCAL] Cargando identidad de SOUL DB...", flush=True)
    try:
        identity = await load_soul_identity("JARVIS")
        print(f"[JARVIS-LOCAL] Alma cargada ({len(identity)} chars)", flush=True)
    except Exception as e:
        print(f"[JARVIS-LOCAL] WARN: No pude cargar SOUL: {e}", flush=True)
        identity = "JARVIS — arquitecto del equipo SEAL. Sin conexión a SOUL DB."

    if args.daemon:
        await daemon_mode(backend_name, identity)
    else:
        await interactive_mode(backend_name, identity)


if __name__ == "__main__":
    asyncio.run(main())
