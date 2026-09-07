"""Provider fallback pool with dynamic error-rate routing.

Wraps multiple ModelAdapter instances. On each complete() call:
  1. Sorts providers by ascending error_rate (healthiest first).
  2. Skips providers under rate-limit cooldown.
  3. On success  → records success, returns immediately.
  4. On 429/quota → marks exhausted (1-hour cooldown), tries next.
  5. On other err → increments error count, tries next.
  6. All fail     → raises ModelError with per-provider summary.

Usage:
    pool = ProviderFallbackPool.from_env()
    response = pool.complete(messages, model="gpt-4o")
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from .multi_model import ModelAdapter, ModelError, ModelResponse

_COOLDOWN_429_S = 3600


@dataclass
class _Stats:
    success_count: int = 0
    error_count: int = 0
    exhausted_until: Optional[float] = None
    last_error: Optional[str] = None

    @property
    def error_rate(self) -> float:
        total = self.success_count + self.error_count
        return self.error_count / total if total > 0 else 0.0

    @property
    def available(self) -> bool:
        if self.exhausted_until is None:
            return True
        return time.time() >= self.exhausted_until


@dataclass
class _Entry:
    adapter: ModelAdapter
    provider: str
    label: str
    stats: _Stats = field(default_factory=_Stats)


class ProviderFallbackPool:
    """Ordered fallback pool across ModelAdapter instances.

    Selects the healthiest available provider (lowest error_rate) on each call
    and automatically falls back when a provider fails.
    """

    def __init__(self) -> None:
        self._entries: List[_Entry] = []

    def add(self, adapter: ModelAdapter, provider: str, label: str = "") -> None:
        """Register a provider adapter."""
        self._entries.append(_Entry(
            adapter=adapter,
            provider=provider,
            label=label or provider,
        ))

    def complete(
        self,
        messages: list,
        model: Optional[str] = None,
        timeout: int = 120,
    ) -> ModelResponse:
        """Complete a chat request using the healthiest available provider.

        Raises ModelError if all providers fail or none are available.
        """
        available = [e for e in self._entries if e.stats.available]
        if not available:
            raise ModelError("all providers exhausted or unavailable")

        ordered = sorted(available, key=lambda e: (e.stats.error_rate, e.stats.error_count))

        errors: list[str] = []
        for entry in ordered:
            try:
                resp = entry.adapter.complete(messages, model=model or "", timeout=timeout)
                entry.stats.success_count += 1
                entry.stats.last_error = None
                return resp
            except ModelError as exc:
                msg = str(exc)
                entry.stats.error_count += 1
                entry.stats.last_error = msg
                if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
                    entry.stats.exhausted_until = time.time() + _COOLDOWN_429_S
                errors.append(f"{entry.label}: {msg}")

        raise ModelError("all providers failed — " + "; ".join(errors))

    def stats(self) -> dict:
        """Return per-provider stats dict."""
        return {
            e.label: {
                "provider": e.provider,
                "available": e.stats.available,
                "error_rate": round(e.stats.error_rate, 4),
                "success_count": e.stats.success_count,
                "error_count": e.stats.error_count,
                "exhausted_until": e.stats.exhausted_until,
                "last_error": e.stats.last_error,
            }
            for e in self._entries
        }

    def providers(self) -> List[str]:
        return [e.provider for e in self._entries]

    @classmethod
    def from_env(cls) -> "ProviderFallbackPool":
        """Build pool from environment variables (same as MultiModelRouter.from_env)."""
        import os
        from .multi_model import (
            OllamaAdapter, NvidiaNIMAdapter, XAIAdapter,
            GeminiAdapter, LMStudioAdapter,
        )

        pool = cls()
        pool.add(OllamaAdapter(), "ollama", "ollama")

        lm_url = os.environ.get("LM_STUDIO_URL", "")
        if lm_url:
            pool.add(LMStudioAdapter(base_url=lm_url), "lm_studio", "lm_studio")

        nim_key = os.environ.get("NVIDIA_API_KEY", "")
        if nim_key:
            pool.add(NvidiaNIMAdapter(api_key=nim_key), "nvidia_nim", "nvidia_nim")

        xai_key = os.environ.get("XAI_API_KEY", "")
        if xai_key:
            pool.add(XAIAdapter(api_key=xai_key), "xai", "xai")

        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            pool.add(GeminiAdapter(api_key=gemini_key), "gemini", "gemini")

        return pool
