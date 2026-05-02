"""SPECTRE LLMClient — Nivel 4, Semana 3.

Ref: spec_spectre_contract_v2_1_addendum.md F4
Sandbox v1: 2 backends (Claude API + Ollama local).
Multi-tier 4-backends → v2 sandbox, no sandbox v1.

Switch via env var SPECTRE_LLM_BACKEND (default: "ollama").
Fallback chain: primary → secondary → raise LLMUnavailable.
"""
from __future__ import annotations

import os
import asyncio
from abc import ABC, abstractmethod
from typing import Any

import httpx


class LLMUnavailable(Exception):
    """Raised when no LLM backend can handle the request."""


# ── Abstract Base ─────────────────────────────────────────────────────────────

class LLMClient(ABC):
    """Abstract LLM backend. Implement chat() and health()."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send messages and return assistant reply as string."""

    @abstractmethod
    async def health(self) -> bool:
        """Return True if backend is reachable."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend identifier."""


# ── ClaudeClient ──────────────────────────────────────────────────────────────

class ClaudeClient(LLMClient):
    """Slow/deep backend via Anthropic API. For complex reasoning."""

    DEFAULT_MODEL = "claude-haiku-4-5-20251001"
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def backend_name(self) -> str:
        return f"claude/{self._model}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if not self._api_key:
            raise LLMUnavailable("ClaudeClient: ANTHROPIC_API_KEY not set")

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "messages": messages,
        }
        system = kwargs.get("system")
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(
                self.API_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "")
            return ""

    async def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            # Minimal probe — list models endpoint (cheap)
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False


# ── OllamaClient ──────────────────────────────────────────────────────────────

class OllamaClient(LLMClient):
    """Fast/local backend via Ollama. Precedente: qwen2.5:7b en DGX Spark."""

    DEFAULT_MODEL = "qwen2.5:7b"
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = (base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def backend_name(self) -> str:
        return f"ollama/{self._model}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                resp = await c.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False


# ── Factory + Fallback Chain ──────────────────────────────────────────────────

def make_client(backend: str | None = None) -> LLMClient:
    """
    Create LLMClient from env var or explicit backend name.

    SPECTRE_LLM_BACKEND: "ollama" (default) | "claude"
    """
    name = backend or os.getenv("SPECTRE_LLM_BACKEND", "ollama")
    registry: dict[str, type[LLMClient]] = {
        "claude": ClaudeClient,
        "ollama": OllamaClient,
    }
    cls = registry.get(name)
    if cls is None:
        raise ValueError(f"Unknown backend '{name}'. Choose: {list(registry)}")
    return cls()


class FallbackLLMClient(LLMClient):
    """Try primary, fall back to secondary on failure.

    Usage: FallbackLLMClient(OllamaClient(), ClaudeClient())
    """

    def __init__(self, primary: LLMClient, secondary: LLMClient) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def backend_name(self) -> str:
        return f"{self._primary.backend_name}→{self._secondary.backend_name}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            return await self._primary.chat(messages, **kwargs)
        except Exception as primary_err:
            print(f"[SPECTRE/llm] primary {self._primary.backend_name} failed: {primary_err} — trying secondary", flush=True)
            try:
                return await self._secondary.chat(messages, **kwargs)
            except Exception as secondary_err:
                raise LLMUnavailable(
                    f"Both backends failed. Primary: {primary_err}. Secondary: {secondary_err}"
                ) from secondary_err

    async def health(self) -> bool:
        primary_ok = await self._primary.health()
        if primary_ok:
            return True
        return await self._secondary.health()


def make_fallback_client() -> FallbackLLMClient:
    """Default SPECTRE fallback: Ollama fast → Claude deep."""
    return FallbackLLMClient(
        primary=OllamaClient(),
        secondary=ClaudeClient(),
    )
