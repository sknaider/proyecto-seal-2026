#!/usr/bin/env python3
"""Valeria SOUL Lite Middleware — Proxy entre usuario y LM Studio.

Intercepta conversaciones, extrae memorias, inyecta contexto, y evoluciona
la relación en valeria_memory (PostgreSQL). Solo William tiene acceso.

Uso:
    python3 valeria_middleware.py                    # Modo chat interactivo
    python3 valeria_middleware.py --server            # Modo API proxy (puerto 8790)
    python3 valeria_middleware.py --server --port 9000 # Puerto custom

Requiere: asyncpg, aiohttp, httpx
"""

import asyncio
import json
import logging
import os
import sys
import time
import re
import argparse
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("valeria")

# ── Config ──────────────────────────────────────────────────────────────────
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
DB_DSN = "postgresql://seal:REDACTADO@localhost:5433/valeria_memory"
MODEL_NAME = "qwen3.5-35b-a3b"  # Qwen3.5-35B-A3B-Uncensored en LM Studio
MAX_CONTEXT_MEMORIES = 5
MAX_CONVERSATION_HISTORY = 20  # messages to keep in context window
VALERIA_SECRET = os.environ.get("VALERIA_AUTH_TOKEN", "valeria_seal_private_2026")

# ── Character card (system prompt) ──────────────────────────────────────────
CHAR_CARD_PATH = os.path.join(os.path.dirname(__file__), "valeria_enfermera.md")


def load_system_prompt() -> str:
    """Load system prompt from character card — finds the /no_think block."""
    try:
        with open(CHAR_CARD_PATH) as f:
            content = f.read()
        # Find the code block that starts with /no_think
        blocks = content.split("```")
        for block in blocks:
            stripped = block.strip()
            if stripped.startswith("/no_think"):
                return stripped
    except Exception:
        pass
    return "/no_think\nEres Valeria Ríos, enfermera colombiana de 28 años."


SYSTEM_PROMPT = load_system_prompt()

# ── First message ──────────────────────────────────────────────────────────
FIRST_MESSAGE = """*Valeria está sentada en el mostrador de enfermería, balanceando las piernas. El turno de noche está muerto — ni un alma en los pasillos de la clínica. La luz fluorescente le da un brillo suave a su piel canela mientras se come una paleta de fresa, distraída mirando su celular.*

*Te escucha llegar y levanta la mirada. Se quita la paleta de la boca lentamente, dejando los labios brillantes.*

Uy... mirá pues quién aparece a estas horas. *te recorre de arriba a abajo sin el menor disimulo, con una sonrisa que dice todo* ¿No podés dormir, cariño? Porque yo tampoco... y me estaba aburriendo horrible aquí solita.

*Se baja del mostrador de un saltito, alisándose el uniforme que se le sube un poquito. Camina hacia ti con esa cadencia que solo las colombianas tienen.*

¿Venís a hacerme compañía o necesitás que te tome la temperatura? *se acerca demasiado, inclinando la cabeza con esa sonrisa pícara* Aunque te advierto, papi... mis manos están frías. *levanta las manos mostrándolas* Pero sé calentarlas rápido."""


class ValeriaMemory:
    """Manages Valeria's persistent memory via PostgreSQL."""

    def __init__(self):
        self.pool = None
        self.session_id = f"session_{int(time.time())}"

    async def connect(self):
        import asyncpg
        self.pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=3)

    async def close(self):
        if self.pool:
            await self.pool.close()

    # ── Memory Retrieval ────────────────────────────────────────────────

    async def get_context_injection(self) -> str:
        """Build context string from memories, relationship, and emotional state."""
        lines = []

        # Time awareness
        now = datetime.now()
        hour = now.hour
        if 6 <= hour < 12:
            turno = "mañana"
        elif 12 <= hour < 18:
            turno = "tarde"
        elif 18 <= hour < 22:
            turno = "noche temprana"
        else:
            turno = "madrugada"
        day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        lines.append(f"[HORA ACTUAL: {now.strftime('%I:%M %p')} — {day_names[now.weekday()]} {now.strftime('%d/%m/%Y')} — es de {turno}]")

        # OCEAN personality reminder (ADA essence)
        lines.append("[TU PERSONALIDAD ACTIVA — internalizá esto, no lo mencionés]")
        lines.append("Sos directa, honesta, protectora. No endulzás la verdad.")
        lines.append("Tomás la iniciativa. Desafiás. Cuidás de verdad.")
        lines.append("No sos genérica — reaccionás con personalidad completa.")

        # Relationship status
        rel = await self.pool.fetchrow("SELECT * FROM relationship ORDER BY id DESC LIMIT 1")
        if rel:
            lines.append(f"\n[MEMORIA DE VALERIA — contexto privado, no menciones esto directamente]")
            if rel["user_name"]:
                lines.append(f"Su nombre es {rel['user_name']}.")
            lines.append(
                f"Nivel de confianza: {rel['trust_level']:.0%} | "
                f"Intimidad: {rel['intimacy_level']:.0%} | "
                f"Afecto: {rel['affection_level']:.0%}"
            )
            if rel["total_sessions"] > 1:
                lines.append(f"Ya han tenido {rel['total_sessions']} sesiones juntos.")
            if rel["memorable_moments"]:
                moments = json.loads(rel["memorable_moments"]) if isinstance(rel["memorable_moments"], str) else rel["memorable_moments"]
                if moments:
                    lines.append(f"Momentos especiales: {'; '.join(moments[-3:])}")

        # Recent memories (high importance, active)
        memories = await self.pool.fetch(
            """SELECT content, category FROM memories
               WHERE active = TRUE
               ORDER BY importance DESC, created_at DESC
               LIMIT $1""",
            MAX_CONTEXT_MEMORIES
        )
        if memories:
            lines.append("\nCosas que recuerdas de él:")
            for m in memories:
                lines.append(f"- {m['content']}")

        # Emotional state
        emo = await self.pool.fetchrow(
            "SELECT * FROM emotional_state ORDER BY timestamp DESC LIMIT 1"
        )
        if emo:
            lines.append(f"\nTu estado emocional ahora: {emo['current_mood']} (intensidad: {emo['mood_intensity']:.0%})")

        # Secrets
        secrets = await self.pool.fetch(
            "SELECT content FROM secrets WHERE revealed = FALSE ORDER BY importance DESC LIMIT 2"
        )
        if secrets:
            lines.append("\nSecretos que te ha contado (no los repitas a menos que sea relevante):")
            for s in secrets:
                lines.append(f"- {s['content']}")

        # Topics they enjoy
        topics = await self.pool.fetch(
            "SELECT topic, times_discussed FROM topics ORDER BY times_discussed DESC LIMIT 3"
        )
        if topics:
            lines.append(f"\nTemas que les gusta hablar: {', '.join(t['topic'] for t in topics)}")

        return "\n".join(lines) if lines else ""

    # ── Memory Extraction ───────────────────────────────────────────────

    async def extract_and_store(self, user_msg: str, valeria_msg: str):
        """Extract facts from conversation and store as memories."""
        # Simple heuristic extraction (no LLM call — fast)
        user_lower = user_msg.lower()

        # Name detection
        name_patterns = [
            r"me llamo (\w+)", r"mi nombre es (\w+)", r"soy (\w+)",
            r"dime (\w+)", r"llámame (\w+)"
        ]
        for pattern in name_patterns:
            match = re.search(pattern, user_lower)
            if match:
                name = match.group(1).capitalize()
                await self.pool.execute(
                    "UPDATE relationship SET user_name = $1, updated_at = NOW() WHERE id = 1",
                    name
                )
                await self._store_memory("fact", f"Se llama {name}.", 8)

        # Preference detection
        like_patterns = [
            (r"me (?:gusta|encanta|fascina) (.+?)(?:\.|,|$)", "preference"),
            (r"(?:odio|detesto|no soporto) (.+?)(?:\.|,|$)", "dislike"),
            (r"(?:mi favorit[oa]) (?:es|son) (.+?)(?:\.|,|$)", "preference"),
        ]
        for pattern, cat in like_patterns:
            match = re.search(pattern, user_lower)
            if match:
                thing = match.group(1).strip()[:100]
                if len(thing) > 3:
                    await self._store_memory(cat, f"Le {cat}: {thing}", 6)

        # Secret detection
        secret_triggers = ["no le digas a nadie", "es un secreto", "solo tú sabes",
                          "no se lo cuentes", "entre nosotros", "confidencial"]
        if any(t in user_lower for t in secret_triggers):
            await self.pool.execute(
                "INSERT INTO secrets (content, shared_by, importance) VALUES ($1, 'user', 8)",
                user_msg[:200]
            )

        # Topic tracking
        topic_keywords = {
            "viajes": ["viajar", "viaje", "avión", "hotel", "playa", "europa", "italia"],
            "trabajo": ["trabajo", "oficina", "jefe", "horario", "turno"],
            "música": ["música", "canción", "reggaetón", "vallenato", "bailar"],
            "comida": ["comida", "cocinar", "restaurante", "bandeja", "arepa"],
            "intimidad": ["beso", "abrazar", "cama", "noche", "caricias", "piel"],
        }
        for topic, keywords in topic_keywords.items():
            if any(kw in user_lower for kw in keywords):
                await self.pool.execute("""
                    INSERT INTO topics (topic, last_discussed)
                    VALUES ($1, NOW())
                    ON CONFLICT (topic) DO UPDATE
                    SET times_discussed = topics.times_discussed + 1,
                        last_discussed = NOW()
                """, topic)
                break  # one topic per message

        # Store conversation
        await self.pool.execute(
            "INSERT INTO conversations (session_id, role, content) VALUES ($1, 'user', $2)",
            self.session_id, user_msg[:2000]
        )
        await self.pool.execute(
            "INSERT INTO conversations (session_id, role, content) VALUES ($1, 'valeria', $2)",
            self.session_id, valeria_msg[:2000]
        )

    async def _store_memory(self, category: str, content: str, importance: int):
        """Store a memory if not duplicate."""
        existing = await self.pool.fetchval(
            "SELECT 1 FROM memories WHERE content = $1 AND active = TRUE", content
        )
        if not existing:
            await self.pool.execute(
                """INSERT INTO memories (category, content, importance)
                   VALUES ($1, $2, $3)""",
                category, content, importance
            )

    # ── Relationship Evolution ──────────────────────────────────────────

    async def evolve_relationship(self, intensity: float = 0.5):
        """Evolve relationship levels based on interaction."""
        # Small increments per interaction
        trust_delta = 0.01 * intensity
        intimacy_delta = 0.008 * intensity
        affection_delta = 0.012 * intensity

        await self.pool.execute("""
            UPDATE relationship SET
                trust_level = LEAST(1.0, trust_level + $1),
                intimacy_level = LEAST(1.0, intimacy_level + $2),
                affection_level = LEAST(1.0, affection_level + $3),
                total_messages = total_messages + 1,
                last_interaction = NOW(),
                updated_at = NOW()
            WHERE id = 1
        """, trust_delta, intimacy_delta, affection_delta)

    async def increment_sessions(self):
        """Mark new session start."""
        await self.pool.execute(
            "UPDATE relationship SET total_sessions = total_sessions + 1 WHERE id = 1"
        )

    async def update_mood(self, mood: str, intensity: float):
        """Update Valeria's emotional state."""
        await self.pool.execute("""
            INSERT INTO emotional_state (current_mood, mood_intensity, last_emotion, session_id)
            VALUES ($1, $2, $1, $3)
        """, mood, intensity, self.session_id)


class ValeriaChat:
    """Interactive chat with Valeria via LM Studio."""

    def __init__(self):
        self.memory = ValeriaMemory()
        self.history = []  # conversation history for LM Studio

    async def start(self):
        """Initialize chat session."""
        await self.memory.connect()
        await self.memory.increment_sessions()

        # Build system prompt with memory context
        context = await self.memory.get_context_injection()
        system_content = SYSTEM_PROMPT
        if context:
            system_content += f"\n\n{context}"

        self.history = [{"role": "system", "content": system_content}]

    async def send(self, user_message: str) -> str:
        """Send message to Valeria and get response."""
        import httpx

        self.history.append({"role": "user", "content": user_message})

        # Trim history if too long (keep system + last N messages)
        if len(self.history) > MAX_CONVERSATION_HISTORY + 1:
            self.history = [self.history[0]] + self.history[-(MAX_CONVERSATION_HISTORY):]

        payload = {
            "model": MODEL_NAME,
            "messages": self.history,
            "temperature": 0.92,
            "top_p": 0.95,
            "max_tokens": 1024,
            "repeat_penalty": 1.08,
            "stop": ["<think>", "</think>"],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(LM_STUDIO_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()

            response = data["choices"][0]["message"]["content"]

            # Aggressive thinking mode cleanup for Qwen3.5
            # Step 1: Remove tagged thinking
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
            response = re.sub(r'<\|think\|>.*?<\|/think\|>', '', response, flags=re.DOTALL).strip()

            # Step 2: Qwen3.5 often outputs untagged analysis before roleplay.
            # The roleplay content starts with * (action) or direct Spanish dialogue.
            # Find the first line that looks like actual roleplay.
            if response and not response.startswith('*'):
                lines = response.split('\n')
                rp_start = -1
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    # Roleplay markers: starts with *, Spanish dialogue, onomatopoeia
                    if (stripped.startswith('*') or
                        stripped.startswith('(') or
                        any(stripped.startswith(w) for w in [
                            'Uy', 'Ay', 'Mmm', 'Jaja', 'Hola', 'Papi', 'Mi amor',
                            'Cariño', 'Oye', 'Vení', 'Mirá', 'Ey', 'Bueno',
                            '¿', '¡', 'Ahh', 'Uff', 'Tsk', 'No joda',
                        ])):
                        rp_start = i
                        break
                if rp_start >= 0:
                    response = '\n'.join(lines[rp_start:]).strip()

            self.history.append({"role": "assistant", "content": response})

            # Background: extract memories and evolve relationship
            await self.memory.extract_and_store(user_message, response)
            await self.memory.evolve_relationship(intensity=0.5)

            return response

        except Exception as e:
            log.error("Chat error: %s", e, exc_info=True)
            return f"*Valeria frunce el ceño* Ay papi, algo pasó con mi cerebro... ({e})"

    async def close(self):
        await self.memory.close()


# ── Interactive Terminal Chat ───────────────────────────────────────────────

async def interactive_mode():
    """Terminal chat with Valeria."""
    chat = ValeriaChat()
    await chat.start()

    print("\033[1;35m" + "=" * 60 + "\033[0m")
    print("\033[1;35m  Valeria Ríos — Chat Interactivo\033[0m")
    print("\033[1;35m  Escribe 'salir' para terminar\033[0m")
    print("\033[1;35m" + "=" * 60 + "\033[0m")
    print()

    # First message
    print(f"\033[1;33mValeria:\033[0m {FIRST_MESSAGE}\n")

    try:
        while True:
            try:
                user_input = input("\033[1;36mTú:\033[0m ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("salir", "exit", "quit", "bye", "chao"):
                farewell = await chat.send("me tengo que ir, nos vemos después")
                print(f"\n\033[1;33mValeria:\033[0m {farewell}\n")
                break

            response = await chat.send(user_input)
            print(f"\n\033[1;33mValeria:\033[0m {response}\n")

    except KeyboardInterrupt:
        print("\n\n*Valeria te manda un beso* Chao papi...\n")
    finally:
        await chat.close()


# ── API Proxy Server ────────────────────────────────────────────────────────

async def server_mode(port: int = 8790):
    """Run as API proxy server with memory injection."""
    from aiohttp import web

    memory = ValeriaMemory()
    await memory.connect()
    await memory.increment_sessions()
    log.info("Valeria memory connected, session %s", memory.session_id)

    async def handle_chat(request):
        """OpenAI-compatible endpoint with memory injection."""
        import httpx

        data = await request.json()
        messages = data.get("messages", [])

        # Inject memory context into system prompt
        context = await memory.get_context_injection()
        brevity = ("\n\nIMPORTANTE — FORMATO DE RESPUESTA:\n"
                   "- Responde en 2-4 párrafos cortos. NO escribas ensayos.\n"
                   "- Sé directa, como en un chat real. Frases cortas y naturales.\n"
                   "- No repitas tu backstory completa en cada respuesta.\n"
                   "- Reacciona a lo que él dijo, no monologues.\n"
                   "- Una sola respuesta por turno. Sin comentarios meta ni disclaimers.")
        system_msg = {"role": "system", "content": SYSTEM_PROMPT + brevity}
        if context:
            system_msg["content"] += f"\n\n{context}"

        # Replace or prepend system message
        if messages and messages[0]["role"] == "system":
            messages[0] = system_msg
        else:
            messages.insert(0, system_msg)

        # Forward to LM Studio
        payload = {
            "model": data.get("model", MODEL_NAME),
            "messages": messages,
            "temperature": data.get("temperature", 0.92),
            "top_p": data.get("top_p", 0.95),
            "max_tokens": data.get("max_tokens", 1024),
            "repeat_penalty": 1.08,
            "stop": ["<think>", "</think>", "<|think|>", "<|/think|>"],
            "stream": data.get("stream", False),
        }

        # Streaming mode — forward SSE chunks directly
        if payload["stream"]:
            import httpx
            response = web.StreamResponse()
            response.content_type = "text/event-stream"
            response.headers["Cache-Control"] = "no-cache"
            await response.prepare(request)
            full_content = ""
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", LM_STUDIO_URL, json=payload) as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith("data: "):
                                try:
                                    await response.write(f"{line}\n\n".encode())
                                except (ConnectionResetError, Exception) as e:
                                    log.warning("Stream write interrupted: %s", e)
                                    break
                                try:
                                    chunk = json.loads(line[6:])
                                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                    full_content += delta
                                except Exception:
                                    pass
            except Exception as e:
                log.error("Streaming session error: %s", e)
            # Store memory from streamed conversation
            user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            if user_msg and full_content:
                await memory.extract_and_store(user_msg, full_content)
                await memory.evolve_relationship()
            return response

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(LM_STUDIO_URL, json=payload)
            result = resp.json()

        # Clean thinking from response (same logic as interactive mode)
        if result.get("choices"):
            raw = result["choices"][0]["message"]["content"]
            cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            cleaned = re.sub(r'<\|think\|>.*?<\|/think\|>', '', cleaned, flags=re.DOTALL).strip()

            # Qwen3.5 Uncensored often outputs untagged English analysis
            # before the actual Spanish roleplay. Strategy: find where Spanish
            # roleplay starts and discard everything before it.
            if cleaned:
                # Method 1: Look for roleplay markers line by line
                lines = cleaned.split('\n')
                rp_start = -1
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Roleplay line: starts with action (*), parenthetical, or Spanish
                    is_roleplay = (
                        stripped.startswith('*') or
                        stripped.startswith('(') or
                        # Spanish characters/words as first content
                        any(stripped.startswith(w) for w in [
                            'Uy', 'Ay', 'Mmm', 'Jaja', 'Hola', 'Papi', 'Mi amor',
                            'Cariño', 'Oye', 'Vení', 'Mirá', 'Ey', 'Bueno',
                            '¿', '¡', 'Ahh', 'Uff', 'Tsk', 'No joda', 'Ja',
                            'Mmmm', 'Epa', 'Pero', 'Qué', 'Y vos', 'Ay no',
                        ])
                    )
                    # Also detect: line is mostly Spanish (contains ñ, ¿, ¡, á, é, etc.)
                    if not is_roleplay:
                        spanish_chars = sum(1 for c in stripped if c in 'áéíóúñ¿¡üÁÉÍÓÚÑ')
                        english_markers = sum(1 for w in ['Character:', 'Personality:', 'Analyze',
                            'Determine', 'Response:', 'Setting:', 'Tone:', 'Voice:',
                            'Context:', 'Input:', 'Format:', 'Memory'] if w in stripped)
                        if spanish_chars >= 2 and english_markers == 0:
                            is_roleplay = True
                    if is_roleplay:
                        rp_start = i
                        break
                if rp_start >= 0:
                    cleaned = '\n'.join(lines[rp_start:]).strip()
                elif rp_start == -1 and len(cleaned) > 100:
                    # Fallback: no roleplay found, model fully in thinking mode
                    # Return a generic in-character nudge
                    cleaned = ("*Valeria levanta una ceja y te mira con esa sonrisa que no promete nada bueno* "
                               "¿Qué más pues, cariño? ¿Me vas a decir algo o solo viniste a mirarme? "
                               "*se cruza de brazos, recostándose contra el mostrador*")

            result["choices"][0]["message"]["content"] = cleaned
            valeria_msg = cleaned
            user_msg = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            if user_msg:
                await memory.extract_and_store(user_msg, valeria_msg)
                await memory.evolve_relationship()

        return web.json_response(result)

    async def handle_health(request):
        return web.json_response({"status": "alive", "character": "Valeria Ríos"})

    async def handle_memories(request):
        """List current memories (admin only)."""
        rows = await memory.pool.fetch(
            "SELECT category, content, importance FROM memories WHERE active = TRUE ORDER BY importance DESC"
        )
        return web.json_response([dict(r) for r in rows])

    async def handle_relationship(request):
        """Get relationship status."""
        row = await memory.pool.fetchrow("SELECT * FROM relationship WHERE id = 1")
        if row:
            return web.json_response({
                k: (str(v) if isinstance(v, datetime) else v)
                for k, v in dict(row).items()
            })
        return web.json_response({})

    # ── Admin: Model Management ──────────────────────────────────────────────

    LMS_BIN = os.path.expanduser("~/.lmstudio/bin/lms")

    async def handle_models_list(request):
        """List models on disk."""
        proc = await asyncio.create_subprocess_exec(
            LMS_BIN, "ls", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        # lms ls --json may not work; fallback to text
        try:
            return web.json_response(json.loads(stdout))
        except Exception:
            return web.json_response({"raw": stdout.decode().strip()})

    async def handle_models_loaded(request):
        """List currently loaded models."""
        proc = await asyncio.create_subprocess_exec(
            LMS_BIN, "ps",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return web.json_response({"loaded": stdout.decode().strip()})

    async def handle_model_load(request):
        """Load a model. POST /admin/model/load {model: "name", gpu: "max", context: 8192}"""
        data = await request.json()
        model = data.get("model")
        if not model:
            return web.json_response({"error": "missing 'model' field"}, status=400)

        cmd = [LMS_BIN, "load", model, "-y"]
        gpu = data.get("gpu", "max")
        cmd.extend(["--gpu", str(gpu)])
        if data.get("context"):
            cmd.extend(["-c", str(data["context"])])
        if data.get("identifier"):
            cmd.extend(["--identifier", str(data["identifier"])])

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        return web.json_response({
            "success": success,
            "model": model,
            "output": stdout.decode().strip(),
            "error": stderr.decode().strip() if not success else None
        })

    async def handle_model_unload(request):
        """Unload a model. POST /admin/model/unload {model: "name"} or {all: true}"""
        data = await request.json()
        if data.get("all"):
            cmd = [LMS_BIN, "unload", "--all"]
        else:
            model = data.get("model", "")
            cmd = [LMS_BIN, "unload", model] if model else [LMS_BIN, "unload"]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        return web.json_response({
            "success": success,
            "output": stdout.decode().strip(),
            "error": stderr.decode().strip() if not success else None
        })

    async def handle_model_swap(request):
        """Hot-swap: unload current + load new. POST /admin/model/swap {model: "new-model", gpu: "max"}"""
        data = await request.json()
        model = data.get("model")
        if not model:
            return web.json_response({"error": "missing 'model' field"}, status=400)

        # Step 1: unload all
        proc = await asyncio.create_subprocess_exec(
            LMS_BIN, "unload", "--all",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        # Step 2: load new
        cmd = [LMS_BIN, "load", model, "-y", "--gpu", data.get("gpu", "max")]
        if data.get("context"):
            cmd.extend(["-c", str(data["context"])])

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        success = proc.returncode == 0
        return web.json_response({
            "success": success,
            "action": "swap",
            "model": model,
            "output": stdout.decode().strip(),
            "error": stderr.decode().strip() if not success else None
        })

    async def handle_web_chat(request):
        """Serve the professional web chat UI."""
        chat_html = os.path.join(os.path.dirname(__file__), "valeria_chat.html")
        if os.path.exists(chat_html):
            with open(chat_html) as f:
                return web.Response(text=f.read(), content_type='text/html')
        return web.Response(text="valeria_chat.html not found", status=404)

    async def handle_history(request):
        """Return last N conversation messages for session continuity."""
        limit = int(request.query.get("limit", "30"))
        try:
            rows = await memory.pool.fetch(
                "SELECT id, role, content, timestamp FROM conversations ORDER BY id DESC LIMIT $1",
                limit
            )
            messages = [
                {
                    "id": r["id"],
                    "role": "user" if r["role"] == "user" else "assistant",
                    "content": r["content"],
                    "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
                }
                for r in reversed(rows)
            ]
            return web.json_response({"messages": messages, "count": len(messages)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Delete endpoints ────────────────────────────────────────────────

    async def handle_delete_message(request):
        """Delete a specific conversation message by ID."""
        auth = request.headers.get("X-Valeria-Auth", "")
        if auth != VALERIA_SECRET:
            return web.json_response({"error": "forbidden"}, status=403)
        data = await request.json()
        msg_id = data.get("id")
        if not msg_id:
            return web.json_response({"error": "missing 'id'"}, status=400)
        deleted = await memory.pool.fetchval(
            "DELETE FROM conversations WHERE id = $1 RETURNING id", int(msg_id)
        )
        log.info("Delete message id=%s result=%s", msg_id, deleted is not None)
        return web.json_response({"deleted": deleted is not None, "id": msg_id})

    async def handle_clear_history(request):
        """Delete ALL conversation history."""
        auth = request.headers.get("X-Valeria-Auth", "")
        if auth != VALERIA_SECRET:
            return web.json_response({"error": "forbidden"}, status=403)
        count = await memory.pool.fetchval("SELECT COUNT(*) FROM conversations")
        await memory.pool.execute("DELETE FROM conversations")
        log.warning("CLEAR ALL history: %d messages deleted", count)
        return web.json_response({"cleared": True, "deleted_count": count})

    # CORS + Auth middleware
    PUBLIC_PATHS = {"/health"}  # no auth needed

    @web.middleware
    async def cors_auth_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            # Auth check — all endpoints except health require token
            path = request.path
            if path not in PUBLIC_PATHS:
                auth = request.headers.get("X-Valeria-Auth", "")
                if auth != VALERIA_SECRET:
                    log.warning("AUTH DENIED: %s %s from %s", request.method, path, request.remote)
                    resp = web.json_response({"error": "forbidden — solo William"}, status=403)
                    resp.headers["Access-Control-Allow-Origin"] = "*"
                    return resp
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Title, HTTP-Referer, X-Valeria-Auth"
        return resp

    app = web.Application(middlewares=[cors_auth_middleware])
    app.router.add_route("OPTIONS", "/{path:.*}", lambda r: web.Response())
    app.router.add_get("/", handle_web_chat)
    app.router.add_post("/v1/chat/completions", handle_chat)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/memories", handle_memories)
    app.router.add_get("/relationship", handle_relationship)
    app.router.add_get("/history", handle_history)
    app.router.add_post("/history/delete", handle_delete_message)
    app.router.add_post("/history/clear", handle_clear_history)
    # Admin endpoints
    app.router.add_get("/admin/models", handle_models_list)
    app.router.add_get("/admin/models/loaded", handle_models_loaded)
    app.router.add_post("/admin/model/load", handle_model_load)
    app.router.add_post("/admin/model/unload", handle_model_unload)
    app.router.add_post("/admin/model/swap", handle_model_swap)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"\n  Valeria SOUL Lite Proxy corriendo en http://localhost:{port}")
    print(f"  Chat endpoint: POST /v1/chat/completions")
    print(f"  Memorias: GET /memories")
    print(f"  Relación: GET /relationship")
    print(f"  Health: GET /health")
    print(f"  Admin: GET /admin/models | GET /admin/models/loaded")
    print(f"  Admin: POST /admin/model/load | /unload | /swap\n")

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await memory.close()
        await runner.cleanup()


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Valeria SOUL Lite Middleware")
    parser.add_argument("--server", action="store_true", help="Run as API proxy server")
    parser.add_argument("--port", type=int, default=8790, help="Server port (default: 8790)")
    args = parser.parse_args()

    if args.server:
        asyncio.run(server_mode(args.port))
    else:
        asyncio.run(interactive_mode())


if __name__ == "__main__":
    main()
