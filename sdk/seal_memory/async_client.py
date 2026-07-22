"""SEAL Memory async client — requires httpx (pip install seal-memory[async])."""
from __future__ import annotations

from typing import Any

from .contracts import MemoryStoreRequest
from .exceptions import AuthenticationError, RateLimitError, SealMemoryError

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


class AsyncSealMemory:
    """
    Async SEAL Memory client for use with asyncio / FastAPI.

    Requires: pip install seal-memory[async]  (installs httpx)

    Example:
        async with AsyncSealMemory(api_key="soul_xxx") as client:
            await client.add("sofia", messages=[...])
            results = await client.search("sofia", "qué estudia?", expand=True)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8767",
        timeout: float = 30.0,
    ):
        if not _HAS_HTTPX:
            raise ImportError("AsyncSealMemory requires httpx. Install with: pip install seal-memory[async]")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "AsyncSealMemory":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    def _get_client(self) -> "httpx.AsyncClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        return self._client

    async def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> Any:
        client = self._get_client()
        try:
            r = await client.request(method, path, params=params, json=json)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", str(e)) if e.response.headers.get("content-type", "").startswith("application/json") else str(e)
            if e.response.status_code == 401:
                raise AuthenticationError(detail, 401) from e
            if e.response.status_code == 429:
                raise RateLimitError(detail, 429) from e
            raise SealMemoryError(detail, e.response.status_code) from e
        except Exception as e:
            raise SealMemoryError(str(e)) from e

    async def add(self, agent_id: str, messages: list[dict], auto_store: bool = True, importance_default: int = 5) -> dict:
        return await self._request("POST", f"/v1/agents/{agent_id}/extract", json={
            "messages": messages, "auto_store": auto_store, "importance_default": importance_default,
        })

    async def search(self, agent_id: str, query: str, limit: int = 10, expand: bool = False) -> list[dict]:
        result = await self._request("GET", "/v1/memory/search", params={
            "agent_id": agent_id, "query": query, "limit": limit, "expand": str(expand).lower(),
        })
        return result.get("memories", result) if isinstance(result, dict) else result

    async def store(
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
        return await self._request("POST", "/v1/memory", json=request.to_payload())

    async def get_all(self, agent_id: str, limit: int = 50) -> list[dict]:
        result = await self._request("GET", "/v1/memory", params={"agent_id": agent_id, "limit": limit})
        return result.get("memories", result) if isinstance(result, dict) else result

    async def register(self, org_id: str, org_name: str = "", tier: str = "edu") -> dict:
        """Register a new org and receive an API key (no auth required)."""
        return await self._request("POST", "/v1/tenants/register", json={
            "org_id": org_id,
            "org_name": org_name or org_id,
            "tier": tier,
        })

    async def boot(self, agent_id: str, persona: str = "", ocean: dict | None = None) -> dict:
        body: dict = {}
        if persona:
            body["persona"] = persona
        if ocean:
            body["ocean"] = ocean
        return await self._request("POST", f"/v1/agents/{agent_id}/boot", json=body or None)

    async def snapshot(self, agent_id: str) -> dict:
        return await self._request("GET", f"/v1/agents/{agent_id}/snapshot")

    async def reflect(self, agent_id: str, thought: str, emotional_state: str = "neutral") -> dict:
        return await self._request("POST", f"/v1/agents/{agent_id}/reflect", json={
            "thought": thought, "emotional_state": emotional_state,
        })

    async def thoughts(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Retrieve the agent's inner monologue."""
        result = await self._request("GET", f"/v1/agents/{agent_id}/thoughts", params={"limit": limit})
        return result.get("thoughts", result) if isinstance(result, dict) else result

    async def entities(self, agent_id: str) -> list[dict]:
        result = await self._request("GET", f"/v1/agents/{agent_id}/entities")
        return result.get("entities", result) if isinstance(result, dict) else result

    async def chat(self, agent_id: str, message: str, model: str = "claude-haiku-4-5-20251001") -> str:
        result = await self._request("POST", f"/v1/agents/{agent_id}/chat", json={
            "message": message, "model": model,
        })
        return result.get("response", result.get("message", "")) if isinstance(result, dict) else str(result)

    async def summary(self, agent_id: str) -> dict:
        """Narrative summary of agent memories via LLM synthesis."""
        return await self._request("GET", f"/v1/agents/{agent_id}/summary")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
