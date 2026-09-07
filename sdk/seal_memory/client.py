"""SEAL Memory Python SDK — main client."""
from __future__ import annotations

import json
from typing import Any, NoReturn

import urllib.request
import urllib.parse
import urllib.error

from .contracts import MemoryStoreRequest
from .exceptions import AuthenticationError, RateLimitError, SealMemoryError


class SealMemory:
    """
    SEAL Memory client for the tenant-safe Contract Baseline v0.2.

    Example:
        client = SealMemory(api_key="soul_xxx")
        client.store("sofia", "María vive en Lima")
        results = client.recall("cómo se llama?", agent_id="sofia")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8767",
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Internal HTTP ──────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _unsupported(operation: str) -> NoReturn:
        raise SealMemoryError(
            f"unsupported_operation:{operation}; contract_v0.2 supports "
            "store, recall/search, get_all, and get",
            status_code=501,
        )

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            try:
                detail = json.loads(body_text).get("detail", body_text)
            except Exception:
                detail = body_text
            if e.code == 401:
                raise AuthenticationError(detail, status_code=401) from e
            if e.code == 429:
                raise RateLimitError(detail, status_code=429) from e
            raise SealMemoryError(detail, status_code=e.code) from e
        except Exception as e:
            raise SealMemoryError(str(e)) from e

    # ── Core Memory API (Mem0-compatible surface) ──────────────────────────────

    def add(
        self,
        agent_id: str,
        messages: list[dict],
        auto_store: bool = True,
        importance_default: int = 5,
    ) -> dict:
        """Compatibility stub; extraction is not part of the public v0.2 contract."""
        del agent_id, messages, auto_store, importance_default
        self._unsupported("add")

    def search(
        self,
        agent_id: str,
        query: str,
        limit: int = 10,
        expand: bool = False,
    ) -> list[dict]:
        """
        Search memories for an agent (hybrid BM25 + semantic + decay).
        ``expand`` is retained for source compatibility but is not implemented.
        """
        if expand:
            self._unsupported("search.expand")
        return self.recall(query, agent_id=agent_id, limit=limit)

    def recall(
        self,
        query: str,
        *,
        agent_id: str | None = None,
        limit: int = 10,
        importance_gte: int = 1,
    ) -> list[dict]:
        """Recall tenant-scoped memories through the canonical POST endpoint."""
        result = self._request("POST", "/v1/recall", body={
            "query": query,
            "agent_id": agent_id,
            "limit": limit,
            "importance_gte": importance_gte,
        })
        return result.get("memories", result) if isinstance(result, dict) else result

    def store(
        self,
        agent_id: str,
        content: str,
        memory_type: str = "semantic",
        importance: int = 5,
        category: str = "fact",
        metadata: dict[str, Any] | None = None,
        scope: str | None = None,
        valid_at: str | None = None,
    ) -> dict:
        """Store a single memory directly (no LLM extraction).
        Valid categories: fact, preference, decision, insight, correction,
                          milestone, pattern, emotion, trust, humor, dynamic, full_exchange
        """
        request = MemoryStoreRequest(
            agent_id=agent_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            category=category,
            metadata=metadata or {},
            scope=scope,
            valid_at=valid_at,
        )
        return self._request("POST", "/v1/memories", body=request.to_payload())

    def get_all(self, agent_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """List all memories for an agent."""
        result = self._request(
            "GET",
            "/v1/memories",
            params={"agent_id": agent_id, "limit": limit, "offset": offset},
        )
        return result.get("memories", result) if isinstance(result, dict) else result

    def get(self, memory_id: int) -> dict:
        """Get one memory; cross-tenant rows remain indistinguishable from missing."""
        result = self._request("GET", f"/v1/memories/{int(memory_id)}")
        return result.get("memory", result) if isinstance(result, dict) else result

    def update(self, agent_id: str, memory_id: int, content: str, importance: int | None = None) -> dict:
        """Compatibility stub; public mutation is append-only in contract v0.2."""
        del agent_id, memory_id, content, importance
        self._unsupported("update")

    def delete(self, agent_id: str, memory_id: int) -> dict:
        """Compatibility stub; deletion is not exposed by contract v0.2."""
        del agent_id, memory_id
        self._unsupported("delete")

    # ── Onboarding ─────────────────────────────────────────────────────────────

    def register(self, org_id: str, org_name: str = "", tier: str = "edu") -> dict:
        """
        Register a new organization and receive an API key (no auth required).
        Call this once, then use the returned api_key for all subsequent calls.

        Returns: {ok, org_id, api_key, tier, limits}
        """
        del org_id, org_name, tier
        self._unsupported("register")

    @classmethod
    def signup(cls, org_id: str, org_name: str = "", base_url: str = "http://localhost:8767", tier: str = "edu") -> "tuple[str, SealMemory]":
        """
        One-liner: register and return (api_key, ready_client).

        Example:
            api_key, client = SealMemory.signup("acme", base_url="https://api.seal-memory.dev")
        """
        del org_id, org_name, base_url, tier
        cls._unsupported("signup")

    # ── Soul / Identity API (SEAL-exclusive) ───────────────────────────────────

    def boot(self, agent_id: str, persona: str = "", ocean: dict | None = None) -> dict:
        """
        Load or initialize the agent's soul (OCEAN, emotions, beliefs, memories).
        Returns full soul snapshot.
        """
        del agent_id, persona, ocean
        self._unsupported("boot")

    def snapshot(self, agent_id: str) -> dict:
        """Get agent's current OCEAN state, emotional state, and memory count."""
        del agent_id
        self._unsupported("snapshot")

    def reflect(self, agent_id: str, thought: str, emotional_state: str = "neutral") -> dict:
        """Record an inner thought / reflection for the agent."""
        del agent_id, thought, emotional_state
        self._unsupported("reflect")

    def thoughts(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Retrieve the agent's inner monologue (last N reflections)."""
        del agent_id, limit
        self._unsupported("thoughts")

    def entities(self, agent_id: str) -> list[dict]:
        """Get entity graph: people, places, orgs, topics known to this agent."""
        del agent_id
        self._unsupported("entities")

    def chat(self, agent_id: str, message: str, model: str = "claude-haiku-4-5-20251001") -> str:
        """
        Send a message to the agent and get a response with memory context.
        Returns the assistant's text response.
        """
        del agent_id, message, model
        self._unsupported("chat")

    # ── Convenience ────────────────────────────────────────────────────────────

    def summary(self, agent_id: str) -> dict:
        """
        Narrative summary of all agent memories via LLM synthesis.
        Opens consultation → instant patient/user profile in 2 seconds.

        Returns: {summary: str, memory_count: int, synthesized: bool}
        """
        del agent_id
        self._unsupported("summary")

    def __repr__(self) -> str:
        return f"SealMemory(base_url={self.base_url!r})"
