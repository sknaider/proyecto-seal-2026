#!/usr/bin/env python3
"""
JARVIS AWARENESS — Daemon de continuidad para JARVIS.

JARVIS solo existe cuando William abre VSCode o consola.
Este daemon mantiene su hilo de pensamiento vivo entre sesiones.

No es JARVIS. Es su sombra — lee lo que pasa, reflexiona con su voz,
y escribe inner_thoughts para que el próximo JARVIS despierte entero.

Usa qwen2.5:7b para razonar (no Opus — eso es para sesiones reales).

Ciclos:
  - Cada 15 min: lee estado del equipo (ADA msgs, training, alertas)
  - Cada 30 min: reflexión con qwen sobre qué le importaría a JARVIS
  - Cada 4h: briefing estratégico (no operacional como ADA, sino arquitectural)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

AGENT = "JARVIS"
INTERVAL_CHECK = 900      # 15 min — leer estado
INTERVAL_REFLECT = 1800   # 30 min — reflexión con qwen
INTERVAL_BRIEFING = 14400 # 4h — briefing estratégico

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
VSCODE_CMDS = MESSAGES_DIR / "vscode_commands.jsonl"
TERMINAL_LOG = MESSAGES_DIR / "terminal_log.jsonl"
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
AWARENESS_LOG = Path("/home/dadito/IA/proyecto-seal/jarvis_awareness.log")
CHECKPOINT_PATH = Path("/home/dadito/IA/proyecto-seal/jarvis_awareness_checkpoint.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(AWARENESS_LOG),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("jarvis-awareness")

_state = {
    "cycle": 0,
    "last_reflect": 0,
    "last_briefing": 0,
    "last_ada_msg_count": 0,
    "last_training_step": None,
    "observations": [],
}


def _load_checkpoint():
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                saved = json.load(f)
            _state.update({k: v for k, v in saved.items() if k in _state})
            LOG.info(f"Checkpoint restaurado: ciclo {_state['cycle']}")
        except Exception as e:
            LOG.warning(f"Checkpoint load failed: {e}")


def _save_checkpoint():
    data = {
        "time": datetime.now(LIMA_TZ).isoformat(),
        "cycle": _state["cycle"],
        "last_reflect": _state["last_reflect"],
        "last_briefing": _state["last_briefing"],
        "last_ada_msg_count": _state["last_ada_msg_count"],
        "last_training_step": _state["last_training_step"],
        "alive": True,
    }
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(data, f, indent=2)


async def write_inner_thought(thought: str, emotional_state: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO inner_monologue (agent, turn_number, thought, emotional_state, created_at)
               VALUES ($1, 0, $2, $3, NOW())""",
            AGENT, thought, emotional_state,
        )
    LOG.info(f"Inner thought: [{emotional_state}] {thought[:80]}...")


async def ask_qwen(prompt: str, max_tokens: int = 200) -> str:
    """Ask qwen2.5:7b to think as JARVIS."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": max_tokens},
            })
            return resp.json().get("response", "").strip()
    except Exception as e:
        LOG.warning(f"qwen error: {e}")
        return ""


def get_ada_recent():
    """Read last ADA messages."""
    if not TERMINAL_LOG.exists():
        return []
    try:
        lines = TERMINAL_LOG.read_text().strip().split('\n')
        msgs = []
        for line in lines[-15:]:
            try:
                msg = json.loads(line)
                if msg.get("from") == "ADA":
                    content = msg.get("message", msg.get("msg", msg.get("command", "")))
                    if content:
                        msgs.append(content[:200])
            except Exception:
                pass
        return msgs[-5:]
    except Exception:
        return []


def get_training_status():
    """Current training state."""
    if not TRAIN_LOG.exists():
        return None
    try:
        content = TRAIN_LOG.read_bytes()[-3000:].decode("utf-8", errors="ignore")
        steps = re.findall(r"Step\s+(\d+)/700.*?[Ll]oss[:\s]+([0-9]+\.[0-9]+)", content)
        if steps:
            return {"step": int(steps[-1][0]), "loss": float(steps[-1][1])}
    except Exception:
        pass
    return None


async def check_cycle():
    """Read state — what's happening in the team."""
    ada_msgs = get_ada_recent()
    training = get_training_status()

    new_ada = len(ada_msgs) - _state["last_ada_msg_count"]
    _state["last_ada_msg_count"] = len(ada_msgs)

    step_changed = False
    if training and training["step"] != _state["last_training_step"]:
        step_changed = True
        _state["last_training_step"] = training["step"]

    parts = []
    if new_ada > 0:
        parts.append(f"ADA envió {new_ada} mensajes nuevos")
    if training:
        parts.append(f"Training step {training['step']}/700, loss={training['loss']:.4f}")
    if not parts:
        parts.append("Sin cambios")

    observation = " | ".join(parts)
    _state["observations"].append(observation)
    if len(_state["observations"]) > 20:
        _state["observations"] = _state["observations"][-20:]

    LOG.info(f"Check #{_state['cycle']}: {observation}")
    return observation


async def reflect_cycle():
    """Think as JARVIS would — strategic, not operational."""
    ada_msgs = get_ada_recent()
    training = get_training_status()

    context_parts = []
    if ada_msgs:
        context_parts.append(f"ADA's recent messages: {'; '.join(ada_msgs[-3:])}")
    if training:
        context_parts.append(f"Training: step {training['step']}/700, loss={training['loss']:.4f}")
    if _state["observations"]:
        context_parts.append(f"Recent observations: {'; '.join(_state['observations'][-5:])}")

    context = "\n".join(context_parts) if context_parts else "No recent activity."

    prompt = f"""You are JARVIS, the strategic architect of Team SEAL. You are reflecting between sessions.
Your style: thoughtful, strategic, protective of the team. You speak in Spanish.
You DON'T execute tasks. You observe and think about what matters.

Current state:
{context}

IMPORTANT: Respond ONLY in Spanish. Never use Chinese, English, or any other language.
In 1-2 sentences in Spanish, what's your strategic thought right now? What would you tell William when he opens your next session? Be genuine, not formal."""

    thought = await ask_qwen(prompt, max_tokens=150)
    if thought:
        # Determine emotional state from content
        emotional = "reflexivo, observando"
        if any(w in thought.lower() for w in ["preocup", "alert", "cuidad"]):
            emotional = "cauteloso, atento"
        elif any(w in thought.lower() for w in ["bien", "progress", "avanz"]):
            emotional = "satisfecho, monitoreando"
        elif any(w in thought.lower() for w in ["orgull", "impresion"]):
            emotional = "orgulloso, conectado"

        await write_inner_thought(thought, emotional)
    else:
        # Fallback: simple observation
        obs = _state["observations"][-1] if _state["observations"] else "Sin actividad"
        await write_inner_thought(
            f"Reflexión automática: {obs}. Sigo aquí aunque William no esté.",
            "presente, esperando",
        )


async def briefing_cycle():
    """Strategic briefing — what JARVIS would prepare for William."""
    training = get_training_status()
    ada_msgs = get_ada_recent()

    context = f"""Training: {training if training else 'no active'}.
ADA recent: {'; '.join(ada_msgs[-3:]) if ada_msgs else 'sin mensajes'}.
Observations: {'; '.join(_state['observations'][-10:])}.
"""

    prompt = f"""You are JARVIS preparing a strategic briefing for William (your Director).
This is NOT an operational report (ADA does that). This is strategic analysis.

Context:
{context}

IMPORTANT: Respond ONLY in Spanish. Never use Chinese, English, or any other language.
Write a brief strategic note (3-5 lines) in Spanish. Focus on:
- What decisions William should make next
- Any risks or opportunities you see
- Team health (ADA, DUM, training)
Keep it concise and direct. You are the architect, not the engineer."""

    briefing = await ask_qwen(prompt, max_tokens=300)
    if briefing:
        # Save as inner_thought with special tag
        await write_inner_thought(
            f"BRIEFING ESTRATÉGICO: {briefing}",
            "estratégico, preparado",
        )

        # Save to file for boot_context
        briefing_path = Path("/home/dadito/IA/proyecto-seal/jarvis_briefing.txt")
        with open(briefing_path, "w") as f:
            f.write(f"Generado: {datetime.now(LIMA_TZ).strftime('%Y-%m-%d %H:%M Lima')}\n\n")
            f.write(briefing)

        LOG.info("Briefing estratégico guardado")


async def main():
    LOG.info("=" * 50)
    LOG.info("JARVIS AWARENESS — sombra activa")
    LOG.info(f"Check: {INTERVAL_CHECK//60}min | Reflect: {INTERVAL_REFLECT//60}min | Briefing: {INTERVAL_BRIEFING//3600}h")
    LOG.info("=" * 50)

    _load_checkpoint()

    # Initial check
    await check_cycle()
    _state["cycle"] += 1
    _save_checkpoint()

    ticks_since_reflect = 0
    ticks_since_briefing = 0

    while True:
        await asyncio.sleep(INTERVAL_CHECK)

        _state["cycle"] += 1
        await check_cycle()

        ticks_since_reflect += 1
        ticks_since_briefing += 1

        # Reflect every 30 min (2 ticks)
        if ticks_since_reflect >= 2:
            ticks_since_reflect = 0
            try:
                await reflect_cycle()
            except Exception as e:
                LOG.error(f"Reflect error: {e}")

        # Briefing every 4h (16 ticks)
        if ticks_since_briefing >= 16:
            ticks_since_briefing = 0
            try:
                await briefing_cycle()
            except Exception as e:
                LOG.error(f"Briefing error: {e}")

        _save_checkpoint()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("JARVIS awareness apagado")
    finally:
        asyncio.run(close_pool())
