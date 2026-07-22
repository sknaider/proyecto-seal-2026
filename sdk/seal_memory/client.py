"""SEAL Memory Python SDK — main client."""
from __future__ import annotations

import json
from typing import Any

import urllib.request
import urllib.parse
import urllib.error

from .contracts import MemoryStoreRequest
from .exceptions import AuthenticationError, RateLimitError, SealMemoryError


class SealMemory:
    """
    SEAL Memory client — mirrors Mem0 API surface with OCEAN/soul extensions.

    Example:
        client = SealMemory(api_key="soul_xxx")
        client.add("sofia", messages=[{"role": "user", "content": "Me llamo María"}])
        results = client.search("sofia", query="cómo se llama?")
        soul = client.boot("sofia")
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
        """
        Auto-extract facts from a conversation and store them as memories.
        Equivalent to Mem0's memory.add(messages, user_id=agent_id).

        Returns: {ok, extracted: int, stored: int, memories: [...]}
        """
        return self._request("POST", f"/v1/agents/{agent_id}/extract", body={
            "messages": messages,
            "auto_store": auto_store,
            "importance_default": importance_default,
        })

    def search(
        self,
        agent_id: str,
        query: str,
        limit: int = 10,
        expand: bool = False,
    ) -> list[dict]:
        """
        Search memories for an agent (hybrid BM25 + semantic + decay).
        Use expand=True for LLM query expansion on short/conversational queries.

        Returns list of memory dicts with semantic_score when available.
        """
        result = self._request("GET", "/v1/memory/search", params={
            "agent_id": agent_id,
            "query": query,
            "limit": limit,
            "expand": str(expand).lower(),
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
        return self._request("POST", "/v1/memory", body=request.to_payload())

    def get_all(self, agent_id: str, limit: int = 50) -> list[dict]:
        """List all memories for an agent."""
        result = self._request("GET", "/v1/memory", params={"agent_id": agent_id, "limit": limit})
        return result.get("memories", result) if isinstance(result, dict) else result

    def update(self, agent_id: str, memory_id: int, content: str, importance: int | None = None) -> dict:
        """Update a memory's content (and optionally importance)."""
        return self._request("PATCH", f"/v1/memory/{memory_id}", params={"agent_id": agent_id}, body={
            "content": content,
            **({"importance": importance} if importance is not None else {}),
        })

    def delete(self, agent_id: str, memory_id: int) -> dict:
        """Delete a memory."""
        return self._request("DELETE", f"/v1/memory/{memory_id}", params={"agent_id": agent_id})

    # ── Onboarding ─────────────────────────────────────────────────────────────

    def register(self, org_id: str, org_name: str = "", tier: str = "edu") -> dict:
        """
        Register a new organization and receive an API key (no auth required).
        Call this once, then use the returned api_key for all subsequent calls.

        Returns: {ok, org_id, api_key, tier, limits}
        """
        return self._request("POST", "/v1/tenants/register", body={
            "org_id": org_id,
            "org_name": org_name or org_id,
            "tier": tier,
        })

    @classmethod
    def signup(cls, org_id: str, org_name: str = "", base_url: str = "http://localhost:8767", tier: str = "edu") -> "tuple[str, SealMemory]":
        """
        One-liner: register and return (api_key, ready_client).

        Example:
            api_key, client = SealMemory.signup("acme", base_url="https://api.seal-memory.dev")
        """
        temp = cls(api_key="", base_url=base_url)
        result = temp.register(org_id=org_id, org_name=org_name, tier=tier)
        api_key = result["api_key"]
        return api_key, cls(api_key=api_key, base_url=base_url)

    # ── Soul / Identity API (SEAL-exclusive) ───────────────────────────────────

    def boot(self, agent_id: str, persona: str = "", ocean: dict | None = None) -> dict:
        """
        Load or initialize the agent's soul (OCEAN, emotions, beliefs, memories).
        Returns full soul snapshot.
        """
        body: dict = {}
        if persona:
            body["persona"] = persona
        if ocean:
            body["ocean"] = ocean
        return self._request("POST", f"/v1/agents/{agent_id}/boot", body=body or None)

    def snapshot(self, agent_id: str) -> dict:
        """Get agent's current OCEAN state, emotional state, and memory count."""
        return self._request("GET", f"/v1/agents/{agent_id}/snapshot")

    def reflect(self, agent_id: str, thought: str, emotional_state: str = "neutral") -> dict:
        """Record an inner thought / reflection for the agent."""
        return self._request("POST", f"/v1/agents/{agent_id}/reflect", body={
            "thought": thought,
            "emotional_state": emotional_state,
        })

    def thoughts(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Retrieve the agent's inner monologue (last N reflections)."""
        result = self._request("GET", f"/v1/agents/{agent_id}/thoughts", params={"limit": limit})
        return result.get("thoughts", result) if isinstance(result, dict) else result

    def entities(self, agent_id: str) -> list[dict]:
        """Get entity graph: people, places, orgs, topics known to this agent."""
        result = self._request("GET", f"/v1/agents/{agent_id}/entities")
        return result.get("entities", result) if isinstance(result, dict) else result

    def chat(self, agent_id: str, message: str, model: str = "claude-haiku-4-5-20251001") -> str:
        """
        Send a message to the agent and get a response with memory context.
        Returns the assistant's text response.
        """
        result = self._request("POST", f"/v1/agents/{agent_id}/chat", body={
            "message": message,
            "model": model,
        })
        return result.get("response", result.get("message", "")) if isinstance(result, dict) else str(result)

    # ── Convenience ────────────────────────────────────────────────────────────

    def summary(self, agent_id: str) -> dict:
        """
        Narrative summary of all agent memories via LLM synthesis.
        Opens consultation → instant patient/user profile in 2 seconds.

        Returns: {summary: str, memory_count: int, synthesized: bool}
        """
        return self._request("GET", f"/v1/agents/{agent_id}/summary")

    def __repr__(self) -> str:
        return f"SealMemory(base_url={self.base_url!r})"
