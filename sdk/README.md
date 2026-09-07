# seal-memory

Tenant-safe Python client for the SOUL Memory Contract Baseline v0.2.

The public contract is intentionally narrow: API keys resolve tenant identity
server-side, PostgreSQL RLS enforces isolation, and clients cannot submit a
tenant identifier.

## Install

```bash
pip install seal-memory
```

The synchronous client uses only the Python standard library. For the async
client:

```bash
pip install "seal-memory[async]"
```

## Supported operations

```python
from seal_memory import SealMemory

memory = SealMemory(
    api_key="soul_live_...",
    base_url="https://memory.example.com",
)

created = memory.store(
    "support_bot",
    "The user prefers examples first.",
    category="preference",
    importance=7,
)

matches = memory.recall(
    "How should I explain this?",
    agent_id="support_bot",
    limit=5,
)

all_memories = memory.get_all("support_bot", limit=50)
one_memory = memory.get(created["memory"]["id"])
```

`search(agent_id, query)` remains as a compatibility alias for `recall`.
Query expansion is not part of v0.2 and fails locally when `expand=True`.

The following historical methods remain present only to produce an explicit
`501 unsupported_operation` error without making a request: `add`, `update`,
`delete`, `register`, `signup`, `boot`, `snapshot`, `reflect`, `thoughts`,
`entities`, `chat`, and `summary`.

## Async

```python
from seal_memory import AsyncSealMemory

async with AsyncSealMemory(api_key="soul_live_...", base_url="https://memory.example.com") as memory:
    await memory.store("support_bot", "The user prefers concise answers.")
    matches = await memory.recall("response style", agent_id="support_bot")
```

## OpenAPI

The typed OpenAPI 3.1 contract is available from `/openapi.json`. It declares
HTTP Bearer authentication, request bodies, response models, validation limits,
and the canonical routes:

- `POST /v1/memories`
- `GET /v1/memories`
- `GET /v1/memories/{memory_id}`
- `POST /v1/recall`
- tenant-owner audited reads under `/v1/tenant/*`

## Mem0 compatibility

`MemoryClient` is imported lazily and is REST-only. Direct PostgreSQL mode is
disabled because it would bypass the API-key-to-tenant identity boundary.

```python
from seal_memory import MemoryClient

memory = MemoryClient(
    api_key="soul_live_...",
    base_url="https://memory.example.com",
)
```

Mutating Mem0 methods without a tenant-safe REST equivalent fail locally.

## Security contract

- Raw API keys are sent only as `Authorization: Bearer ...`.
- Tenant IDs are never accepted from request headers, queries, or bodies.
- Unsupported operations fail locally; the SDK does not fall back to an
  internal MCP server or direct database access.
- External callers receive only the public REST surface documented above.
