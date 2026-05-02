"""SPECTRE LLMClient — Nivel 4, v2 sandbox.

Ref: spec_spectre_contract_v2.md §NIVEL 4
Multi-tier chain: OllamaClient → VLLMClient → ClaudeClient(haiku) → ClaudeClient(sonnet)
  Fast tier: Ollama + VLLM (local, low-latency, reflex enrichment)
  Slow tier: Claude Haiku + Sonnet (cloud, deep reasoning, plan synthesis)

Switch single backend via env var SPECTRE_LLM_BACKEND (default: "ollama").
Full 4-tier chain: make_four_tier_client()
2-backend chain: make_fallback_client() (backward compat)
"""
from __future__ import annotations

import asyncio
import os
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
    """Cloud backend via Anthropic API.

    Fast tier: Haiku (reflex enrichment, ack patterns).
    Slow tier: Sonnet (deep reasoning, plan synthesis).
    """

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

        # Anthropic API: system must be top-level, not a role in messages array
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [m for m in messages if m.get("role") != "system"]
        system = kwargs.get("system") or ("\n\n".join(system_parts) if system_parts else None)

        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "messages": chat_messages,
        }
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            for attempt in range(3):
                resp = await c.post(
                    self.API_URL,
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[SPECTRE/llm] Claude 429 rate-limit — retry in {wait}s (attempt {attempt+1}/3)", flush=True)
                    await asyncio.sleep(wait)
                    continue
                break
            if not resp.is_success:
                raise LLMUnavailable(f"Claude API {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "")
            return ""

    async def health(self) -> bool:
        if not self._api_key:
            return False
        try:
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
    """Fast local backend via Ollama (fast tier — reflex enrichment, ack patterns).

    Default model: qwen2.5:7b on DGX Spark / localhost.
    """

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


# ── VLLMClient ────────────────────────────────────────────────────────────────

class VLLMClient(LLMClient):
    """OpenAI-compatible local backend (fast tier — local deep reasoning).

    Targets vLLM running on RTX 5090 at localhost:8000.
    Compatible with any OpenAI-API-compatible server (vLLM, llama.cpp server,
    LM Studio, Ollama v1 endpoint).
    """

    DEFAULT_MODEL = "nemotron-3"
    DEFAULT_BASE_URL = "http://localhost:8000"

    def __init__(
        self,
        base_url: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        api_key: str = "none",
    ) -> None:
        self._base_url = (base_url or os.getenv("SPECTRE_VLLM_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self._model = model
        self._timeout = timeout
        self._api_key = api_key

    @property
    def backend_name(self) -> str:
        return f"vllm/{self._model}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 512),
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                resp = await c.get(
                    f"{self._base_url}/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                return resp.status_code == 200
        except Exception:
            return False


# ── MultiTierLLMClient ────────────────────────────────────────────────────────

class MultiTierLLMClient(LLMClient):
    """Try backends in priority order — first success wins, all fail → LLMUnavailable.

    Implements the spec §NIVEL 4 multi-tier chain:
      T1 (fast local) → T2 (deep local) → T3 (cloud fast) → T4 (cloud deep)

    Each backend failure is logged but non-fatal until all are exhausted.
    """

    def __init__(self, *backends: LLMClient) -> None:
        if not backends:
            raise ValueError("MultiTierLLMClient requires at least one backend")
        self._backends: list[LLMClient] = list(backends)

    @property
    def backend_name(self) -> str:
        return "→".join(b.backend_name for b in self._backends)

    @property
    def tier_count(self) -> int:
        return len(self._backends)

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        errors: list[str] = []
        for backend in self._backends:
            try:
                result = await backend.chat(messages, **kwargs)
                if len(self._backends) > 1:
                    # Only log fallback when there was at least one prior failure
                    if errors:
                        print(f"[SPECTRE/llm] tier fallback succeeded: {backend.backend_name}", flush=True)
                return result
            except Exception as err:
                print(
                    f"[SPECTRE/llm] tier {backend.backend_name} failed: {err} — trying next",
                    flush=True,
                )
                errors.append(f"{backend.backend_name}: {err}")

        raise LLMUnavailable(
            f"All {len(self._backends)} LLM tiers exhausted. Errors: {'; '.join(errors)}"
        )

    async def health(self) -> bool:
        for backend in self._backends:
            try:
                if await backend.health():
                    return True
            except Exception:
                continue
        return False

    async def health_all(self) -> dict[str, bool]:
        """Return health status of each tier independently."""
        result: dict[str, bool] = {}
        for backend in self._backends:
            try:
                result[backend.backend_name] = await backend.health()
            except Exception:
                result[backend.backend_name] = False
        return result


# ── FallbackLLMClient (backward compat) ──────────────────────────────────────

class FallbackLLMClient(MultiTierLLMClient):
    """Backward-compatible 2-backend fallback (sandbox v1 API).

    Wraps MultiTierLLMClient. Existing code using
    FallbackLLMClient(primary, secondary) or accessing ._primary/_secondary
    continues to work unchanged.
    """

    def __init__(self, primary: LLMClient, secondary: LLMClient) -> None:
        super().__init__(primary, secondary)

    @property
    def _primary(self) -> LLMClient:
        return self._backends[0]

    @property
    def _secondary(self) -> LLMClient:
        return self._backends[1]


# ── OpenCodeClient ────────────────────────────────────────────────────────────

class OpenCodeClient(LLMClient):
    """OpenAI-compatible cloud backend for OpenCode API (or any sk- keyed service).

    Configure via spectre.env:
      OPENCODE_API_KEY  — sk- key (required)
      OPENCODE_BASE_URL — API base URL (default: https://api.openai.com/v1)
      OPENCODE_MODEL    — model name (default: gpt-4o-mini)
    """

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENCODE_API_KEY", "")
        self._base_url = (base_url or os.getenv("OPENCODE_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self._model = model or os.getenv("OPENCODE_MODEL", self.DEFAULT_MODEL)
        self._timeout = timeout

    @property
    def backend_name(self) -> str:
        return f"opencode/{self._model}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if not self._api_key:
            raise LLMUnavailable("OpenCodeClient: OPENCODE_API_KEY not set")
        payload: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 512),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content")
                if content:
                    return content
                # COT models (MiniMax, DeepSeek-R1 style) put answer in reasoning
                reasoning = msg.get("reasoning") or msg.get("reasoning_content", "")
                if reasoning:
                    # Extract the conclusion from the reasoning (last substantive block)
                    lines = [l.strip() for l in str(reasoning).splitlines() if l.strip()]
                    return lines[-1] if lines else reasoning[:300]
            return ""

    async def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                return resp.status_code == 200
        except Exception:
            return False


# ── ClaudeCodeClient ──────────────────────────────────────────────────────────

class ClaudeCodeClient(LLMClient):
    """Local Claude Code CLI subprocess backend.

    Uses `claude -p` (print mode) which inherits the user's OAuth Max plan
    session — no separate API key or credits needed.
    """

    CLI_PATH = "/home/dadito/.local/bin/claude"
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 45.0) -> None:
        self._model = model
        self._timeout = timeout

    @property
    def backend_name(self) -> str:
        return f"claudecode/{self._model}"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        user_parts = [m["content"] for m in messages if m.get("role") == "user"]
        prompt = user_parts[-1] if user_parts else ""
        system = "\n\n".join(system_parts) if system_parts else ""
        cmd = [self.CLI_PATH, "-p", prompt]
        if system:
            cmd += ["--system-prompt", system]
        cmd += ["--model", self._model]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CLAUDE_CODE_SIMPLE": "0"},
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise LLMUnavailable(f"ClaudeCodeClient: timeout after {self._timeout}s")
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace")[:200]
                raise LLMUnavailable(f"ClaudeCodeClient: exit {proc.returncode}: {err}")
            return stdout.decode("utf-8", errors="replace").strip()
        except LLMUnavailable:
            raise
        except Exception as e:
            raise LLMUnavailable(f"ClaudeCodeClient: {e}")

    async def health(self) -> bool:
        return os.path.isfile(self.CLI_PATH)


# ── Factory functions ─────────────────────────────────────────────────────────

def make_client(backend: str | None = None) -> LLMClient:
    """Create single LLMClient from env var or explicit backend name."""
    name = backend or os.getenv("SPECTRE_LLM_BACKEND", "ollama")
    registry: dict[str, type[LLMClient]] = {
        "claude": ClaudeClient,
        "ollama": OllamaClient,
        "vllm": VLLMClient,
    }
    cls = registry.get(name)
    if cls is None:
        raise ValueError(f"Unknown backend '{name}'. Choose: {list(registry)}")
    return cls()


def make_fallback_client() -> FallbackLLMClient:
    """2-tier chain: Ollama → Claude Haiku (sandbox v1 backward compat)."""
    return FallbackLLMClient(
        primary=OllamaClient(),
        secondary=ClaudeClient(),
    )


def make_four_tier_client() -> MultiTierLLMClient:
    """Full 4-tier SPECTRE chain per spec §NIVEL 4.

    T1 fast-local:  OllamaClient  (qwen2.5:7b, :11434)
    T2 deep-local:  VLLMClient    (nemotron-3, :8000, RTX 5090)
    T3 cloud-fast:  ClaudeClient  (haiku — ack, reflex enrichment)
    T4 cloud-deep:  ClaudeClient  (sonnet — deep reasoning, plan synthesis)
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    return MultiTierLLMClient(
        OllamaClient(),
        VLLMClient(),
        ClaudeClient(api_key=api_key, model="claude-haiku-4-5-20251001"),
        ClaudeClient(api_key=api_key, model="claude-sonnet-4-6"),
    )
