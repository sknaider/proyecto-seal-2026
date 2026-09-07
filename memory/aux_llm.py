"""Auxiliary LLM interface for context compression and session digest generation.

Backend selection (in priority order):
  1. Anthropic Haiku — fast, cheap, good summarization
  2. Ollama (qwen2.5:7b local) — offline fallback on DGX Spark
  3. Extractive fallback — pure Python, no LLM, uses first+last sentences
"""
from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("AUX_LLM_OLLAMA_MODEL", "qwen2.5:7b")
HAIKU_MODEL = "claude-haiku-4-5-20251001"


class AuxLLM(Protocol):
    def complete(self, prompt: str, max_tokens: int = 1500,
                 timeout: float | None = None) -> str: ...


class HaikuLLM:
    """Claude Haiku via Anthropic API."""

    def complete(self, prompt: str, max_tokens: int = 1500,
                 timeout: float | None = None) -> str:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": HAIKU_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout if timeout else 30.0,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


class OllamaLLM:
    """Ollama local inference — offline fallback."""

    def complete(self, prompt: str, max_tokens: int = 1500,
                 timeout: float | None = None) -> str:
        # El timeout HTTP debe ser <= al que espera quien llama.  Sin esto, un
        # `asyncio.wait_for(..., 15)` cancelaba el AWAIT pero el thread seguia
        # y Ollama generaba hasta 120 s: generacion HUERFANA que nadie lee y
        # que DUM reportaba como runaway (4 alertas el 2-sep-2026).
        # Cortar la peticion hace que Ollama pierda el cliente y aborte.
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=timeout if timeout else 120.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]


class ExtractiveFallback:
    """Pure Python fallback — no LLM. Extracts key sentences heuristically."""

    def complete(self, prompt: str, max_tokens: int = 1500) -> str:
        lines = [l.strip() for l in prompt.split("\n") if l.strip()]
        keywords = ["william", "decidimos", "error", "task", "fix", "urgente",
                    "critical", "importante", "decided", "problema", "done"]
        scored = []
        for line in lines:
            score = sum(1 for k in keywords if k in line.lower())
            if score:
                scored.append((score, line))
        scored.sort(reverse=True)
        selected = [l for _, l in scored[:30]]
        result = "\n".join(selected)
        budget = max_tokens * 4
        return result[:budget] if len(result) > budget else result


def get_aux_llm() -> AuxLLM:
    """Return best available AuxLLM in priority order."""
    if ANTHROPIC_API_KEY:
        try:
            llm = HaikuLLM()
            llm.complete("ping", max_tokens=1)
            return llm
        except Exception:
            pass
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            return OllamaLLM()
    except Exception:
        pass
    return ExtractiveFallback()
