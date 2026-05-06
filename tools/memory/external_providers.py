"""External memory provider adapters — Mem0, Honcho, Hindsight compatibility.

Provides a unified ExternalMemoryProvider interface that SEAL can use to
bridge with external memory services when configured, falling back to
soul_v3 (our native provider) when not.

Pure stdlib — no third-party SDKs.

Usage:
    from tools.memory.external_providers import Mem0Adapter, MemoryProviderPool

    mem = Mem0Adapter(api_key="m0-...")
    mem_id = mem.store("ADA learned X today", agent="ADA")
    results = mem.search("what did ADA learn", agent="ADA")

    # Pool with fallback
    pool = MemoryProviderPool([Mem0Adapter(), HonchoAdapter()])
    pool.store("fact", agent="ADA")  # tries Mem0 first, Honcho on failure

Environment variables:
    MEM0_API_KEY      — Mem0 API key (https://api.mem0.ai)
    HONCHO_APP_ID     — Honcho application ID
    HONCHO_API_KEY    — Honcho API key
    HINDSIGHT_API_KEY — Hindsight API key
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    id:       str
    content:  str
    agent:    str
    score:    float            = 0.0
    metadata: Dict[str, Any]  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "content": self.content,
            "agent": self.agent, "score": self.score,
            "metadata": self.metadata,
        }


# ── Abstract interface ────────────────────────────────────────────────────────

class ExternalMemoryProvider(ABC):
    """Uniform interface for external memory services."""

    @abstractmethod
    def store(self, content: str, agent: str,
              metadata: Optional[dict] = None) -> str:
        """Store a memory. Returns provider-assigned ID."""

    @abstractmethod
    def search(self, query: str, agent: Optional[str] = None,
               limit: int = 10) -> List[MemoryItem]:
        """Search memories. Returns ranked list."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True on success."""

    @abstractmethod
    def available(self) -> bool:
        """Return True if this provider is configured and reachable."""

    # ── HTTP helper shared by concrete adapters ─────────────────────────────

    def _request(
        self,
        method:  str,
        url:     str,
        payload: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: int            = 10,
    ) -> dict:
        """Make an HTTP request and return parsed JSON body."""
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        body = json.dumps(payload).encode() if payload is not None else None
        req  = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                err_body = json.loads(raw)
            except Exception:
                err_body = {"raw": raw.decode("utf-8", errors="replace")}
            raise ProviderError(f"HTTP {e.code} from {url}: {err_body}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError(f"Connection failed to {url}: {e}") from e


class ProviderError(Exception):
    """Raised when an external memory provider call fails."""


# ── Mem0 adapter ─────────────────────────────────────────────────────────────

class Mem0Adapter(ExternalMemoryProvider):
    """Mem0 memory adapter — https://api.mem0.ai/v1

    Endpoints used:
      POST   /v1/memories/          — add memory
      POST   /v1/memories/search/   — semantic search
      DELETE /v1/memories/{id}/     — delete
    """

    BASE = "https://api.mem0.ai/v1"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._key = api_key or os.environ.get("MEM0_API_KEY", "")

    def available(self) -> bool:
        return bool(self._key)

    def _auth(self) -> dict:
        return {"Authorization": f"Token {self._key}"}

    def store(self, content: str, agent: str,
              metadata: Optional[dict] = None) -> str:
        payload: dict = {
            "messages": [{"role": "user", "content": content}],
            "user_id": agent,
        }
        if metadata:
            payload["metadata"] = metadata
        resp = self._request("POST", f"{self.BASE}/memories/",
                             payload=payload, headers=self._auth())
        mem = resp.get("results", [{}])[0] if isinstance(resp.get("results"), list) else resp
        return str(mem.get("id", ""))

    def search(self, query: str, agent: Optional[str] = None,
               limit: int = 10) -> List[MemoryItem]:
        payload: dict = {"query": query, "limit": limit}
        if agent:
            payload["user_id"] = agent
        resp = self._request("POST", f"{self.BASE}/memories/search/",
                             payload=payload, headers=self._auth())
        items = resp if isinstance(resp, list) else resp.get("results", [])
        return [
            MemoryItem(
                id      = str(r.get("id", "")),
                content = r.get("memory", r.get("content", "")),
                agent   = r.get("user_id", agent or ""),
                score   = float(r.get("score", 0.0)),
                metadata= r.get("metadata", {}),
            )
            for r in items
        ]

    def delete(self, memory_id: str) -> bool:
        try:
            self._request("DELETE", f"{self.BASE}/memories/{memory_id}/",
                          headers=self._auth())
            return True
        except ProviderError:
            return False


# ── Honcho adapter ────────────────────────────────────────────────────────────

class HonchoAdapter(ExternalMemoryProvider):
    """Honcho user-memory adapter — https://api.honcho.dev

    Endpoints used:
      POST  /apps/{app_id}/users/{user_id}/sessions/{sid}/metamessages — store
      GET   /apps/{app_id}/users/{user_id}/query?query=...             — search
      DELETE /apps/{app_id}/users/{user_id}/sessions/{sid}/metamessages/{id} — delete
    """

    BASE = "https://api.honcho.dev"

    def __init__(
        self,
        app_id:  Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self._app_id = app_id  or os.environ.get("HONCHO_APP_ID", "seal")
        self._key    = api_key or os.environ.get("HONCHO_API_KEY", "")

    def available(self) -> bool:
        return bool(self._key and self._app_id)

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def _session_url(self, agent: str) -> str:
        return f"{self.BASE}/apps/{self._app_id}/users/{agent}/sessions/default/metamessages"

    def store(self, content: str, agent: str,
              metadata: Optional[dict] = None) -> str:
        payload: dict = {"content": content, "is_user": False,
                         "metadata": metadata or {}}
        resp = self._request("POST", self._session_url(agent),
                             payload=payload, headers=self._auth())
        return str(resp.get("id", ""))

    def search(self, query: str, agent: Optional[str] = None,
               limit: int = 10) -> List[MemoryItem]:
        uid  = agent or "default"
        qs   = urllib.parse.urlencode({"query": query, "page_size": limit})
        url  = f"{self.BASE}/apps/{self._app_id}/users/{uid}/query?{qs}"
        resp = self._request("GET", url, headers=self._auth())
        items = resp.get("items", [])
        return [
            MemoryItem(
                id      = str(r.get("id", "")),
                content = r.get("content", ""),
                agent   = uid,
                score   = float(r.get("score", 0.0)),
                metadata= r.get("metadata", {}),
            )
            for r in items
        ]

    def delete(self, memory_id: str) -> bool:
        try:
            uid = "default"
            url = (f"{self.BASE}/apps/{self._app_id}/users/{uid}"
                   f"/sessions/default/metamessages/{memory_id}")
            self._request("DELETE", url, headers=self._auth())
            return True
        except ProviderError:
            return False


# ── Hindsight adapter ─────────────────────────────────────────────────────────

class HindsightAdapter(ExternalMemoryProvider):
    """Hindsight conversation-memory adapter.

    Endpoints used:
      POST  /memory         — store
      GET   /memory/search  — search (?q=&agent=&limit=)
      DELETE /memory/{id}   — delete
    """

    BASE = "https://api.hindsight.so/v1"

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None) -> None:
        self._key  = api_key  or os.environ.get("HINDSIGHT_API_KEY", "")
        self._base = base_url or self.BASE

    def available(self) -> bool:
        return bool(self._key)

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def store(self, content: str, agent: str,
              metadata: Optional[dict] = None) -> str:
        payload: dict = {"content": content, "agent": agent,
                         "metadata": metadata or {}}
        resp = self._request("POST", f"{self._base}/memory",
                             payload=payload, headers=self._auth())
        return str(resp.get("id", ""))

    def search(self, query: str, agent: Optional[str] = None,
               limit: int = 10) -> List[MemoryItem]:
        params: dict = {"q": query, "limit": limit}
        if agent:
            params["agent"] = agent
        qs  = urllib.parse.urlencode(params)
        resp = self._request("GET", f"{self._base}/memory/search?{qs}",
                             headers=self._auth())
        items = resp if isinstance(resp, list) else resp.get("results", [])
        return [
            MemoryItem(
                id      = str(r.get("id", "")),
                content = r.get("content", ""),
                agent   = r.get("agent", agent or ""),
                score   = float(r.get("score", 0.0)),
                metadata= r.get("metadata", {}),
            )
            for r in items
        ]

    def delete(self, memory_id: str) -> bool:
        try:
            self._request("DELETE", f"{self._base}/memory/{memory_id}",
                          headers=self._auth())
            return True
        except ProviderError:
            return False


# ── Provider pool ─────────────────────────────────────────────────────────────

class MemoryProviderPool(ExternalMemoryProvider):
    """Tries providers in order; falls back on ProviderError.

    Designed so soul_v3 is the implicit last resort when all external
    providers fail or are unconfigured.
    """

    def __init__(self, providers: List[ExternalMemoryProvider]) -> None:
        self._providers = providers

    def available(self) -> bool:
        return any(p.available() for p in self._providers)

    def available_providers(self) -> List[str]:
        return [type(p).__name__ for p in self._providers if p.available()]

    def store(self, content: str, agent: str,
              metadata: Optional[dict] = None) -> str:
        last_err: Optional[Exception] = None
        for p in self._providers:
            if not p.available():
                continue
            try:
                return p.store(content, agent, metadata)
            except ProviderError as e:
                last_err = e
        raise ProviderError(
            f"All providers failed to store: {last_err}"
        ) from last_err

    def search(self, query: str, agent: Optional[str] = None,
               limit: int = 10) -> List[MemoryItem]:
        for p in self._providers:
            if not p.available():
                continue
            try:
                return p.search(query, agent, limit)
            except ProviderError:
                continue
        return []

    def delete(self, memory_id: str) -> bool:
        for p in self._providers:
            if not p.available():
                continue
            try:
                return p.delete(memory_id)
            except ProviderError:
                continue
        return False

    @classmethod
    def from_env(cls) -> "MemoryProviderPool":
        """Build pool from environment variables — includes only configured adapters."""
        providers: List[ExternalMemoryProvider] = []
        if os.environ.get("MEM0_API_KEY"):
            providers.append(Mem0Adapter())
        if os.environ.get("HONCHO_API_KEY"):
            providers.append(HonchoAdapter())
        if os.environ.get("HINDSIGHT_API_KEY"):
            providers.append(HindsightAdapter())
        return cls(providers)
