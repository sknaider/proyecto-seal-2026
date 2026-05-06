"""Multi-model adapter layer — native Python, no third-party SDKs.

Adapters for every AI provider SEAL needs:
  • OllamaAdapter    — local inference, OpenAI-compat endpoint
  • NvidiaNIMAdapter — integrate.api.nvidia.com, OpenAI-compat
  • XAIAdapter       — api.x.ai (Grok), OpenAI-compat
  • GeminiAdapter    — generativelanguage.googleapis.com/v1beta/openai (OpenAI-compat)
  • LMStudioAdapter  — localhost:1234, OpenAI-compat
  • OpenAIAdapter    — api.openai.com/v1 (GPT-4o, o1, o3, Codex-compat models)
  • BedrockAdapter   — AWS Bedrock Runtime, native SigV4 (no boto3), Claude models

All adapters implement ModelAdapter. MultiModelRouter picks the active adapter
and handles fallback across the chain.

"inteligencia artificial = python puro" — William, 2026-04-28.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


class ModelError(Exception):
    """Raised when a model call fails unrecoverably after retries."""


@dataclass
class ModelResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str
    content: str


class ModelAdapter(ABC):
    """Contract every provider adapter must implement."""

    provider: str

    @abstractmethod
    def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        """Synchronous completion. Returns ModelResponse or raises ModelError."""

    def available(self) -> bool:
        """Quick health check — True if the provider appears reachable."""
        return True


# ── OpenAI-compatible base ──────────────────────────────────────────────────


class _OpenAICompatAdapter(ModelAdapter):
    """Shared logic for any OpenAI-compatible endpoint."""

    def __init__(self, base_url: str, api_key: str, provider: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider = provider

    def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise ModelError(f"[{self.provider}] HTTP {e.code}: {body[:200]}")
        except OSError as e:
            raise ModelError(f"[{self.provider}] connection failed: {e}")

        try:
            choice = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ModelError(f"[{self.provider}] unexpected response schema: {e}")

        usage = data.get("usage", {})
        return ModelResponse(
            content=choice.strip(),
            model=model,
            provider=self.provider,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )

    def available(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False


# ── Concrete adapters ────────────────────────────────────────────────────────


class OllamaAdapter(_OpenAICompatAdapter):
    """Local Ollama via OpenAI-compat /v1 endpoint."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        url = base_url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        super().__init__(f"{url}/v1", api_key="ollama", provider="ollama")
        self.default_model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


class NvidiaNIMAdapter(_OpenAICompatAdapter):
    """NVIDIA NIM inference cloud."""

    DEFAULT_BASE = "https://integrate.api.nvidia.com/v1"
    DEFAULT_MODEL = "deepseek-ai/deepseek-v4-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        url = base_url or self.DEFAULT_BASE
        super().__init__(url, api_key=key, provider="nvidia-nim")
        self.default_model = model or os.environ.get("NVIDIA_MODEL", self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


class XAIAdapter(_OpenAICompatAdapter):
    """xAI Grok via OpenAI-compat API at api.x.ai."""

    DEFAULT_BASE = "https://api.x.ai/v1"
    DEFAULT_MODEL = "grok-3-mini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("XAI_API_KEY", "")
        url = base_url or self.DEFAULT_BASE
        super().__init__(url, api_key=key, provider="xai")
        self.default_model = model or os.environ.get("XAI_MODEL", self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


class GeminiAdapter(_OpenAICompatAdapter):
    """Google Gemini via the OpenAI-compatible endpoint.

    Google exposes /v1beta/openai/ which speaks the same OpenAI wire protocol,
    so we inherit _OpenAICompatAdapter with no special casing.
    """

    DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        url = base_url or self.DEFAULT_BASE
        super().__init__(url, api_key=key, provider="gemini")
        self.default_model = model or os.environ.get("GEMINI_MODEL", self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


class LMStudioAdapter(_OpenAICompatAdapter):
    """LM Studio local inference via its OpenAI-compat server (default port 1234).

    LM Studio exposes the same /v1/chat/completions interface as OpenAI.
    No API key needed — uses "lm-studio" as placeholder.
    """

    DEFAULT_BASE = "http://localhost:1234/v1"
    DEFAULT_MODEL = "local-model"

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        url = base_url or os.environ.get("LM_STUDIO_URL", self.DEFAULT_BASE)
        super().__init__(url, api_key="lm-studio", provider="lm-studio")
        self.default_model = model or os.environ.get("LM_STUDIO_MODEL", self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


# ── OpenAI adapter (standard API + Codex-compatible models) ─────────────────


class OpenAIAdapter(_OpenAICompatAdapter):
    """OpenAI API — GPT-4o, GPT-4o-mini, o1, o3, and legacy codex-compatible models.

    Env vars: OPENAI_API_KEY, OPENAI_BASE_URL (optional override for proxies).
    """

    DEFAULT_BASE  = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        url = base_url or os.environ.get("OPENAI_BASE_URL", self.DEFAULT_BASE)
        super().__init__(url, api_key=key, provider="openai")
        self.default_model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        return super().complete(
            messages,
            model=model or self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


# ── AWS Bedrock adapter (native SigV4 — no boto3) ────────────────────────────

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    k = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date)
    k = _hmac_sha256(k, region)
    k = _hmac_sha256(k, service)
    return _hmac_sha256(k, "aws4_request")


def _sign_request(
    method: str,
    url: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "bedrock",
    session_token: Optional[str] = None,
) -> dict[str, str]:
    """Return Authorization + X-Amz-Date + (optionally X-Amz-Security-Token) headers."""
    parsed   = urllib.parse.urlparse(url)
    host     = parsed.netloc
    uri      = parsed.path or "/"
    now      = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date     = amz_date[:8]

    payload_hash = _sha256_hex(payload)

    headers_to_sign: dict[str, str] = {
        "content-type": "application/json",
        "host": host,
        "x-amz-date": amz_date,
    }
    if session_token:
        headers_to_sign["x-amz-security-token"] = session_token

    signed_names = ";".join(sorted(headers_to_sign))
    canonical_headers = "".join(
        f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
    )
    canonical_request = "\n".join([
        method.upper(),
        uri,
        "",                    # no query string
        canonical_headers,
        signed_names,
        payload_hash,
    ])

    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])

    signing_key = _derive_signing_key(secret_key, date, region, service)
    signature   = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_names}, "
        f"Signature={signature}"
    )
    result = {
        "Authorization":  auth,
        "X-Amz-Date":     amz_date,
        "Content-Type":   "application/json",
    }
    if session_token:
        result["X-Amz-Security-Token"] = session_token
    return result


class BedrockAdapter(ModelAdapter):
    """AWS Bedrock Runtime adapter — invokes Claude models via native SigV4.

    Uses the Bedrock Runtime /model/{model_id}/invoke endpoint directly with
    pure stdlib (no boto3 required).

    Env vars:
        AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN (optional),
        AWS_REGION (default: us-east-1),
        BEDROCK_DEFAULT_MODEL (default: anthropic.claude-3-haiku-20240307-v1:0).
    """

    DEFAULT_REGION = "us-east-1"
    DEFAULT_MODEL  = "anthropic.claude-3-haiku-20240307-v1:0"
    provider       = "bedrock"

    def __init__(
        self,
        access_key:    Optional[str] = None,
        secret_key:    Optional[str] = None,
        region:        Optional[str] = None,
        session_token: Optional[str] = None,
        model:         Optional[str] = None,
    ) -> None:
        self._access_key    = access_key    or os.environ.get("AWS_ACCESS_KEY_ID",      "")
        self._secret_key    = secret_key    or os.environ.get("AWS_SECRET_ACCESS_KEY",  "")
        self._region        = region        or os.environ.get("AWS_REGION",             self.DEFAULT_REGION)
        self._session_token = session_token or os.environ.get("AWS_SESSION_TOKEN",      "")
        self.default_model  = model         or os.environ.get("BEDROCK_DEFAULT_MODEL",  self.DEFAULT_MODEL)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> ModelResponse:
        if not self._access_key or not self._secret_key:
            raise ModelError("BedrockAdapter requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")

        model_id = model or self.default_model
        url = (
            f"https://bedrock-runtime.{self._region}.amazonaws.com"
            f"/model/{urllib.parse.quote(model_id, safe='')}/invoke"
        )

        msgs = [{"role": m.role, "content": m.content} for m in messages]
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }).encode("utf-8")

        headers = _sign_request(
            "POST", url, body,
            self._access_key, self._secret_key, self._region,
            service="bedrock",
            session_token=self._session_token or None,
        )

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ModelError(f"Bedrock HTTP {e.code}: {e.read(200)}")
        except OSError as e:
            raise ModelError(f"Bedrock connection error: {e}")

        text   = "".join(c.get("text", "") for c in data.get("content", []))
        usage  = data.get("usage", {})
        return ModelResponse(
            content           = text,
            model             = data.get("model", model_id),
            provider          = "bedrock",
            prompt_tokens     = usage.get("input_tokens", 0),
            completion_tokens = usage.get("output_tokens", 0),
            raw               = data,
        )

    def available(self) -> bool:
        return bool(self._access_key and self._secret_key)


# ── Router ───────────────────────────────────────────────────────────────────


class MultiModelRouter:
    """Routes completions across a prioritized adapter chain with fallback.

    Usage:
        router = MultiModelRouter([OllamaAdapter(), NvidiaNIMAdapter(api_key="...")])
        response = router.complete(messages, model="qwen2.5:7b")

    The router tries adapters in order. On ModelError it logs and tries the next.
    Raises ModelError only when the full chain is exhausted.
    """

    def __init__(self, adapters: list[ModelAdapter]) -> None:
        if not adapters:
            raise ValueError("MultiModelRouter requires at least one adapter")
        self._adapters = adapters

    @classmethod
    def from_env(cls) -> "MultiModelRouter":
        """Build a router from environment variables, skipping unconfigured providers."""
        adapters: list[ModelAdapter] = []
        adapters.append(OllamaAdapter())
        if os.environ.get("LM_STUDIO_URL"):
            adapters.append(LMStudioAdapter())
        if os.environ.get("NVIDIA_API_KEY"):
            adapters.append(NvidiaNIMAdapter())
        if os.environ.get("XAI_API_KEY"):
            adapters.append(XAIAdapter())
        if os.environ.get("GEMINI_API_KEY"):
            adapters.append(GeminiAdapter())
        if os.environ.get("OPENAI_API_KEY"):
            adapters.append(OpenAIAdapter())
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            adapters.append(BedrockAdapter())
        return cls(adapters)

    def complete(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        provider: Optional[str] = None,
    ) -> ModelResponse:
        """Complete with optional provider hint; fallback through chain on error."""
        chain = self._adapters
        if provider:
            preferred = [a for a in chain if a.provider == provider]
            rest = [a for a in chain if a.provider != provider]
            chain = preferred + rest

        last_err: Optional[ModelError] = None
        for adapter in chain:
            try:
                return adapter.complete(
                    messages,
                    model=model,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except ModelError as e:
                last_err = e
                continue

        raise ModelError(f"all adapters failed. Last: {last_err}")

    @property
    def providers(self) -> list[str]:
        return [a.provider for a in self._adapters]
