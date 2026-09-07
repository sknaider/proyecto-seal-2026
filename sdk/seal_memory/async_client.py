"""SEAL Memory async client — requires httpx (pip install seal-memory[async])."""
from __future__ import annotations

from typing import Any, NoReturn

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

    @staticmethod
    def _unsupported(operation: str) -> NoReturn:
        raise SealMemoryError(
            f"unsupported_operation:{operation}; contract_v0.2 supports "
            "store, recall/search, get_all, and get",
            status_code=501,
        )

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
        del agent_id, messages, auto_store, importance_default
        self._unsupported("add")

    async def search(self, agent_id: str, query: str, limit: int = 10, expand: bool = False) -> list[dict]:
        if expand:
            self._unsupported("search.expand")
        return await self.recall(query, agent_id=agent_id, limit=limit)

    async def recall(
        self,
        query: str,
        *,
        agent_id: str | None = None,
        limit: int = 10,
        importance_gte: int = 1,
    ) -> list[dict]:
        result = await self._request("POST", "/v1/recall", json={
            "query": query,
            "agent_id": agent_id,
            "limit": limit,
            "importance_gte": importance_gte,
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
        return await self._request("POST", "/v1/memories", json=request.to_payload())

    async def get_all(self, agent_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        result = await self._request(
            "GET",
            "/v1/memories",
            params={"agent_id": agent_id, "limit": limit, "offset": offset},
        )
        return result.get("memories", result) if isinstance(result, dict) else result

    async def get(self, memory_id: int) -> dict:
        result = await self._request("GET", f"/v1/memories/{int(memory_id)}")
        return result.get("memory", result) if isinstance(result, dict) else result

    async def register(self, org_id: str, org_name: str = "", tier: str = "edu") -> dict:
        del org_id, org_name, tier
        self._unsupported("register")

    async def boot(self, agent_id: str, persona: str = "", ocean: dict | None = None) -> dict:
        del agent_id, persona, ocean
        self._unsupported("boot")

    async def snapshot(self, agent_id: str) -> dict:
        del agent_id
        self._unsupported("snapshot")

    async def reflect(self, agent_id: str, thought: str, emotional_state: str = "neutral") -> dict:
        del agent_id, thought, emotional_state
        self._unsupported("reflect")

    async def thoughts(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Retrieve the agent's inner monologue."""
        del agent_id, limit
        self._unsupported("thoughts")

    async def entities(self, agent_id: str) -> list[dict]:
        del agent_id
        self._unsupported("entities")

    async def chat(self, agent_id: str, message: str, model: str = "claude-haiku-4-5-20251001") -> str:
        del agent_id, message, model
        self._unsupported("chat")

    async def summary(self, agent_id: str) -> dict:
        """Narrative summary of agent memories via LLM synthesis."""
        del agent_id
        self._unsupported("summary")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
