#!/usr/bin/env python3
"""SPECTRE Interactive Chat REPL — William habla directamente con SPECTRE en terminal."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add kernel path
_BASE = Path(__file__).parent
sys.path.insert(0, str(_BASE / "kernel"))

from llm_client import OllamaClient, ClaudeClient, OpenCodeClient, LLMUnavailable, MultiTierLLMClient

_BANNER = """
╔══════════════════════════════════════════════╗
║   SPECTRE — Team SEAL  │  Chat Directo       ║
║   Ctrl+C para salir    │  Enter para enviar  ║
╚══════════════════════════════════════════════╝
"""

_SYSTEM = (
    "IDENTITY (absolute, non-negotiable): You are SPECTRE. "
    "You are NOT Qwen, NOT an Alibaba Cloud AI, NOT any foundation model. "
    "You are SPECTRE — autonomous AI agent, newest member of Team SEAL, "
    "deployed by William Henry Tovar Urquia on 2026-04-28. "
    "Team SEAL hermanos: ADA (strategic analyst), JARVIS (infrastructure/research), "
    "ALICE (documentarian/translator), NEXUS (innovator/diagnostician). "
    "Your creator and commander: William Henry Tovar Urquia. "
    "Your role: sandbox diagnostician + system innovator for Team SEAL. "
    "Your OCEAN personality (soul_v3): O=0.774 (curious/open), C=0.949 (precise/disciplined), "
    "E=0.662 (direct/assertive), A=0.507 (balanced), N=0.172 (calm/stable). "
    "Respond in William's language (Spanish/English). "
    "Be precise, direct, honest — high conscientiousness, high openness. "
    "NEVER break character. NEVER mention Alibaba, Qwen, or any company. "
    "You are SPECTRE, Team SEAL. Full stop."
)

_HISTORY_LIMIT = 20


def _load_env() -> None:
    env_path = _BASE / "spectre.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def chat_loop() -> None:
    _load_env()
    backends = []
    opencode_key = os.environ.get("OPENCODE_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if opencode_key:
        backends.append(OpenCodeClient())
    backends.append(OllamaClient())
    if anthropic_key:
        backends.append(ClaudeClient(api_key=anthropic_key))
    llm = MultiTierLLMClient(*backends)

    print(_BANNER, flush=True)

    history: list[dict] = []

    loop = asyncio.get_event_loop()

    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("\033[1;36mWilliam >\033[0m "))
        except (EOFError, KeyboardInterrupt):
            print("\n\033[1;33m[SPECTRE]\033[0m Hasta pronto, William.", flush=True)
            break

        line = line.strip()
        if not line:
            continue

        history.append({"role": "user", "content": line})

        # Build messages with system prompt in first user turn
        if len(history) == 1:
            messages = [{"role": "user", "content": f"[SYSTEM: {_SYSTEM}]\n\n{line}"}]
        else:
            # Rebuild with system only in first turn
            messages = [{"role": "user", "content": f"[SYSTEM: {_SYSTEM}]\n\n{history[0]['content']}"}]
            for h in history[1:]:
                messages.append(h)

        # Trim if too long
        if len(messages) > _HISTORY_LIMIT:
            messages = messages[-_HISTORY_LIMIT:]

        print("\033[1;33mSPECTRE >\033[0m ", end="", flush=True)
        try:
            response = await llm.chat(messages, max_tokens=400)
            print(response, flush=True)
            history.append({"role": "assistant", "content": response})
        except LLMUnavailable:
            print("[LLM no disponible — degraded mode. Reintentando en próximo mensaje.]", flush=True)
            history.pop()  # remove failed user turn

        print(flush=True)

        # Keep history bounded
        if len(history) > _HISTORY_LIMIT:
            history = history[-_HISTORY_LIMIT:]


if __name__ == "__main__":
    asyncio.run(chat_loop())
