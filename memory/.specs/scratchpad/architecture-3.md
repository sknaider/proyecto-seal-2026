# SOUL Memory System — Architecture Overview for Productization
**Date:** 2026-04-07  
**Inputs:** research-2a.md (MCP/HIPAA/market), codebase-analysis-2b.md (monolith structure), business-analysis-2c.md (pricing/MVP/GTM)  
**Purpose:** Concrete, implementable architecture for turning the Team SEAL monolith into a sellable product

---

## 1. System Architecture Diagram

```
                         ┌─────────────────────────────────────────────────────────┐
                         │                    MCP Clients                          │
                         │  Claude Code │ CrewAI │ LangGraph │ AutoGen │ Custom    │
                         └────────────┬────────────────────────────────────────────┘
                                      │ Streamable HTTP (not SSE — deprecated Apr 2026)
                                      ▼
                         ┌────────────────────────────────┐
                         │        API Gateway / TLS       │
                         │  /.well-known/mcp/server.json  │
                         │  /.well-known/mcp/server-card  │
                         │  /health  /metrics             │
                         └────────────┬───────────────────┘
                                      │
                         ┌────────────▼───────────────────┐
                         │       Auth Middleware           │
                         │  OAuth 2.1 / API Key fallback  │
                         │  JWT → tenant_id + agent_id    │
                         │  Rate limiter (token bucket)   │
                         └────────────┬───────────────────┘
                                      │
                         ┌────────────▼───────────────────┐
                         │     FastMCP Server (soul/)     │
                         │  server.py — tool registration │
                         │  instrumentation.py — _observe │
                         │  context.py — RequestContext    │
                         └──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬─┘
                            │  │  │  │  │  │  │  │  │  │
               ┌────────────┘  │  │  │  │  │  │  │  │  └────────────┐
               ▼               ▼  ▼  ▼  ▼  ▼  ▼  ▼  ▼              ▼
          ┌─────────┐   ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐  ┌─────────┐
          │ memory/ │   │grph││ id ││inst││sess││proc││dmem│  │  rules/ │
          │ 16 tools│   │ 15 ││ 10 ││ 8  ││ 5  ││ 6  ││ 5  │  │ 8 tools │
          └────┬────┘   └─┬──┘└─┬──┘└─┬──┘└─┬──┘└─┬──┘└─┬──┘  └────┬────┘
               │          │     │     │     │     │     │           │
               └──────────┴─────┴─────┴─────┴─────┴─────┴───────────┘
                                      │
                         ┌────────────▼───────────────────┐
                         │         soul/core/             │
                         │  config.py — Pydantic Settings  │
                         │  db.py — PG pool factory       │
                         │  clients.py — Qdrant, Neo4j    │
                         │  embeddings.py — get_embedding │
                         │  helpers.py — shared functions  │
                         │  types.py — shared dataclasses │
                         └────┬──────────┬────────────┬───┘
                              │          │            │
               ┌──────────────┘          │            └──────────────┐
               ▼                         ▼                           ▼
    ┌──────────────────┐    ┌──────────────────┐         ┌──────────────────┐
    │   PostgreSQL     │    │     Qdrant       │         │     Neo4j        │
    │   + pgvector     │    │   (optional in   │         │   (optional in   │
    │   port 5433      │    │    Lite mode)    │         │    Lite mode)    │
    │                  │    │   port 6333      │         │   port 7687      │
    │ ┌──────────────┐ │    └──────────────────┘         └──────────────────┘
    │ │ RLS policy:  │ │
    │ │ tenant_id =  │ │              ┌──────────────────┐
    │ │ app.tenant_id│ │              │ Ollama (optional) │
    │ └──────────────┘ │              │   port 11434      │
    └──────────────────┘              │ LLM enrichment    │
                                      └──────────────────┘

    ─────────── TENANT ISOLATION BOUNDARY ───────────
    │ PG: RLS per-query via SET LOCAL app.tenant_id  │
    │ Qdrant: payload filter tenant_id (is_tenant)   │
    │ Neo4j: property filter tenant_id (app layer)   │
    ─────────────────────────────────────────────────
```

### Deployment Profiles

```
Soul Lite (Community):     PG+pgvector ──── SOUL server
Soul Full (Team):          PG+pgvector + Qdrant + Neo4j ──── SOUL server
Soul Enterprise:           PG+pgvector + Qdrant + Neo4j + Ollama ──── SOUL server + TLS + audit
```

---

## 2. Module Decomposition Plan

### Target Package Structure

```
soul-memory/
├── pyproject.toml                 # Single installable package: `pip install soul-memory`
├── src/
│   └── soul/                      # Namespace package (NO __init__.py at this level)
│       ├── core/                  # Shared infrastructure — NO dependency on other soul.* modules
│       │   ├── __init__.py
│       │   ├── config.py          # Pydantic Settings, all env vars
│       │   ├── db.py              # asyncpg pool factory (from current db.py)
│       │   ├── clients.py         # get_qdrant(), get_neo4j() factories
│       │   ├── embeddings.py      # get_embedding() (from current embeddings.py)
│       │   ├── helpers.py         # temporal_decay_score(), classify_emotion(), extract_entities()
│       │   ├── types.py           # RequestContext, shared dataclasses
│       │   ├── instrumentation.py # _observe, _observed_tool, observation queue
│       │   └── ollama.py          # httpx calls to Ollama with fallback
│       │
│       ├── memory/                # Module A+B: Memory CRUD + Retrieval (16 tools)
│       │   ├── __init__.py        # Public API: register_tools(mcp)
│       │   ├── store.py           # memory_store, _auto_broadcast, _auto_activate_instincts
│       │   ├── search.py          # memory_search, memory_hybrid_search, memory_cross_search
│       │   ├── crud.py            # memory_list, memory_update, memory_invalidate, memory_utility_update
│       │   ├── broadcast.py       # memory_broadcast_read, memory_broadcast_ack
│       │   ├── advanced.py        # memory_flare, memory_communities, memory_prefetch, memory_delta_sync
│       │   └── share.py           # memory_share_promote
│       │
│       ├── graph/                 # Module C+G: Connectome + Temporal Graph (15 tools)
│       │   ├── __init__.py
│       │   ├── connectome.py      # connectome_build, connectome_status, connectome_ltp
│       │   ├── activation.py      # soul_activate, soul_synthesize (spreading activation)
│       │   ├── bitemporal.py      # connectome_bitemporal, connectome_bitemporal_query
│       │   ├── causal.py          # connectome_causal, connectome_invalidate_edge
│       │   ├── entity.py          # connectome_entity, connectome_entity_query
│       │   ├── routing.py         # connectome_smart_route
│       │   └── temporal.py        # temporal_graph_build, temporal_query
│       │
│       ├── identity/              # Module D+M: Soul/Identity + Peer Models (10 tools)
│       │   ├── __init__.py
│       │   ├── boot.py            # boot_context (the most complex orchestrator)
│       │   ├── soul.py            # soul_check, soul_snapshot, self_reflect
│       │   ├── ocean.py           # ocean_auto_calibrate, ocean_state_machine, update_ocean()
│       │   ├── inner.py           # inner_thoughts, active_recall
│       │   └── peers.py           # peer_model_update, peer_model_query
│       │
│       ├── instincts/             # Module F: Instinct lifecycle (8 tools)
│       │   ├── __init__.py
│       │   ├── crud.py            # instinct_create, instinct_list, instinct_search
│       │   ├── lifecycle.py       # instinct_activate, instinct_promote, instinct_evolve
│       │   ├── consolidate.py     # instinct_consolidate
│       │   └── observe.py         # observation_analyze
│       │
│       ├── sessions/              # Module E: Session management (5 tools)
│       │   ├── __init__.py
│       │   └── tools.py           # session_save, session_recall, session_list, session_distill, session_distill_bulk
│       │                          # Delegates to session_memory.py (already extracted)
│       │
│       ├── procedures/            # Module H: Reasoning traces + procedures (6 tools)
│       │   ├── __init__.py
│       │   └── tools.py           # reasoning_trace_store/update/search, procedure_store/search/update
│       │
│       ├── dmem/                  # Module I: D-MEM gate + ACE curator (5 tools)
│       │   ├── __init__.py
│       │   ├── gate.py            # dmem_gate, dmem_store
│       │   ├── ace.py             # ace_curator
│       │   └── health.py          # brain_health_report
│       │
│       ├── sleep/                 # Module J: Consolidation + health (4 tools)
│       │   ├── __init__.py
│       │   └── tools.py           # sleep_gate, sleep_gate_mood_retrieval, microcompact_text, microcompact_stats
│       │
│       ├── rules/                 # Module K+L: Rules, events, working state (8 tools)
│       │   ├── __init__.py
│       │   ├── rules.py           # rule_set, rule_list
│       │   ├── events.py          # event_log_append, event_log_query
│       │   ├── state.py           # working_state_get, working_state_update
│       │   ├── secret.py          # secret_scan
│       │   └── reflexion.py       # _reflexion_lesson (REMOVED from @mcp.tool, internal only)
│       │
│       ├── auth/                  # NEW: Authentication & authorization
│       │   ├── __init__.py
│       │   ├── middleware.py      # FastMCP auth middleware (OAuth 2.1 + API key)
│       │   ├── jwt.py             # JWT creation/validation with tenant_id + agent_id
│       │   ├── rbac.py            # Role-based access control
│       │   └── models.py          # Tenant, APIKey, Role dataclasses
│       │
│       ├── tenant/                # NEW: Multi-tenancy
│       │   ├── __init__.py
│       │   ├── context.py         # TenantContext — carries tenant_id through request
│       │   ├── rls.py             # PostgreSQL RLS setup and SET LOCAL
│       │   └── isolation.py       # Qdrant filter injection, Neo4j property injection
│       │
│       └── server.py              # MCP entrypoint: imports all modules, registers all tools
│
├── mcp_server_v2.py               # SHIM — backward compat: `from soul.server import main; main()`
├── tests/
│   ├── conftest.py                # Shared fixtures: test tenant, test pool, mock Qdrant
│   ├── test_memory.py
│   ├── test_graph.py
│   ├── test_identity.py
│   ├── test_instincts.py
│   ├── test_sessions.py
│   ├── test_procedures.py
│   ├── test_dmem.py
│   ├── test_sleep.py
│   ├── test_rules.py
│   ├── test_auth.py
│   ├── test_tenant_isolation.py
│   └── test_integration.py        # Existing 61 tests migrated here
├── docker-compose.yml
├── docker-compose.full.yml
├── Dockerfile
├── .env.example
└── README.md
```

### Grouping Justifications

| Module | Grouping Rationale |
|--------|-------------------|
| `memory/` | Merges A (CRUD, 9 tools) + B (Retrieval, 7 tools) = 16 tools. Both are memory operations differing only in read/write direction. Share `temporal_decay_score()`, Qdrant client, and PG pool. Splitting CRUD from retrieval would create constant cross-imports. |
| `graph/` | Merges C (Connectome, 12 tools) + G (Temporal, 3 tools) = 15 tools. All Neo4j-primary. `temporal_graph_build` (line 5622) writes the same graph `connectome_bitemporal` (line 5867) queries. Separate module would require exposing Neo4j session management. |
| `identity/` | Merges D (Soul, 8 tools) + M (Peer Models, 2 tools) = 10 tools. Both deal with agent identity. `boot_context` (line 1756) reads peer models during initialization. |
| `rules/` | Merges K (Rules/Events, 4 tools) + L (Working State, 4 tools) = 8 tools. All PG-only CRUD with no external dependencies. `_reflexion_lesson` moved here as internal helper (removed from `@mcp.tool` — see Risk 7 in codebase analysis). |
| `instincts/` | Kept separate from `memory/` despite both using PG. Instincts have their own lifecycle (create → activate → evolve → promote → consolidate) distinct from memory CRUD. |
| `dmem/` | D-MEM gate + ACE curator + brain_health_report are orchestrators that call across modules. Isolated to prevent dependency sprawl. |
| `sessions/` | Already mostly extracted to `session_memory.py`. Thin wrapper module. |
| `procedures/` | Pure PG + pgvector, zero cross-calls. Cleanest extraction target. |
| `sleep/` | Consolidation tools with numpy dependency. Isolated to keep numpy out of core. |

### Cross-Module Dependencies Requiring Interfaces

These are the inter-module function calls identified in the codebase that must be formalized as interfaces:

| Caller | Callee | Current Pattern | Interface Design |
|--------|--------|-----------------|------------------|
| `identity/boot.py` → `boot_context` (line 1818) | `graph/activation.py` → `soul_activate` | Direct function call | Import from `soul.graph.activation.soul_activate` — acceptable, boot is an orchestrator |
| `identity/boot.py` → `boot_context` (line 1840) | `memory/search.py` → `memory_search` | Direct function call | Import from `soul.memory.search.memory_search` |
| `graph/routing.py` → `connectome_smart_route` | `graph/temporal.py`, `graph/entity.py`, `memory/search.py` | Direct function calls | All within `graph/` except memory_search — one cross-module import |
| `memory/store.py` → `memory_store` (line 750+) | `instincts/lifecycle.py` → `_auto_activate_instincts` | `asyncio.ensure_future` fire-and-forget | Define callback interface: `memory/store.py` accepts optional `on_store_hooks: list[Callable]` |
| `dmem/ace.py` → `ace_curator` | `instincts/`, `graph/`, `memory/` | Would call across all modules | ACE gets references injected at server startup — dependency inversion |
| `rules/reflexion.py` → `_reflexion_lesson` | `instincts/lifecycle.py` → `instinct_activate` | fire-and-forget | Internal helper, not exposed as MCP tool. Import directly. |

**Dependency Rule (enforced via Import Linter):**

```
soul.core       → (no soul.* imports)
soul.rules      → soul.core only
soul.procedures → soul.core only
soul.sessions   → soul.core only
soul.sleep      → soul.core only
soul.instincts  → soul.core only
soul.memory     → soul.core, soul.instincts (for auto-activate hooks)
soul.graph      → soul.core, soul.memory (for memory_search in smart_route)
soul.identity   → soul.core, soul.memory, soul.graph (boot_context orchestrates)
soul.dmem       → soul.core, soul.memory, soul.graph, soul.instincts (ACE orchestrates)
soul.auth       → soul.core only
soul.tenant     → soul.core only
```

---

## 3. Multi-Tenancy Architecture

### PostgreSQL: Row-Level Security

Every table that stores tenant-scoped data gets a `tenant_id UUID NOT NULL` column and RLS policy.

```sql
-- Migration script: add tenant_id to all tenant-scoped tables
-- Run per table: memories, sessions, instincts, rules, event_log, 
--   reasoning_traces, procedures, working_state, peer_models, ocean_scores

ALTER TABLE memories ADD COLUMN tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000';
CREATE INDEX idx_memories_tenant ON memories (tenant_id);

ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_memories ON memories
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_insert_memories ON memories
    FOR INSERT WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

-- Application sets tenant context per-request (in soul/tenant/rls.py):
-- SET LOCAL app.tenant_id = '<tenant-uuid>';
-- SET LOCAL runs within a transaction — automatically resets on commit/rollback
```

**Implementation in `soul/tenant/rls.py`:**

```python
async def with_tenant_context(pool, tenant_id: str):
    """Acquire a connection with RLS tenant context set."""
    conn = await pool.acquire()
    await conn.execute("SET LOCAL app.tenant_id = $1", tenant_id)
    return conn
```

**Default tenant for Team SEAL migration:** `00000000-0000-0000-0000-000000000000` — existing data gets this UUID. Team SEAL continues working without configuration changes.

**Performance:** B-tree index on `tenant_id` as first column in all composite indexes. RLS overhead: ~5-15% per query (validated by AWS Aurora benchmarks, cited in research-2a).

### Qdrant: Tiered Multitenancy

Uses Qdrant 1.16+ tiered multitenancy with `is_tenant` payload index.

```python
# One-time setup (in schema migration)
client.create_payload_index(
    collection_name="soul_memories",
    field_name="tenant_id",
    field_schema=PayloadSchemaType.KEYWORD,
    field_params={"is_tenant": True}
)

# Every write includes tenant_id in payload
point = PointStruct(
    id=mem_id,
    vector=embedding,
    payload={**existing_payload, "tenant_id": tenant_id}
)

# Every read filters by tenant_id
results = await client.search(
    collection_name="soul_memories",
    query_vector=vector,
    query_filter=Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
    ])
)
```

**Tenant promotion path:** Small tenants share default shard. When a tenant exceeds 100K points, promote to dedicated shard (Qdrant handles this transparently via internal shard transfer, zero downtime).

### Neo4j: Application-Layer Isolation (Community Edition)

Neo4j Community Edition does not support multiple databases. All nodes and relationships carry a `tenant_id` property. Enforcement is at application layer.

```cypher
// Every MERGE/CREATE includes tenant_id
MERGE (m:Memory {id: $id, tenant_id: $tenant_id})

// Every MATCH filters by tenant_id
MATCH (m:Memory {tenant_id: $tenant_id})-[r:SEMANTIC]->(n:Memory {tenant_id: $tenant_id})
WHERE m.id = $id
RETURN n
```

**Implementation in `soul/tenant/isolation.py`:**

```python
def inject_tenant_filter(cypher: str, params: dict, tenant_id: str) -> tuple[str, dict]:
    """Add tenant_id filter to Cypher queries. Called by all graph/ module functions."""
    params["_tenant_id"] = tenant_id
    # Pattern: inject WHERE clause or AND clause for tenant_id matching
    ...
```

**Index for performance:**

```cypher
CREATE INDEX memory_tenant_idx FOR (m:Memory) ON (m.tenant_id);
```

### Isolation Verification Strategy

1. **CI test: `test_tenant_isolation.py`** — Creates two tenants, stores memories in each, verifies cross-tenant queries return zero results. Tests all three backends.
2. **SQL injection test** — Attempts `'; SET LOCAL app.tenant_id = 'other-tenant'--` in query parameters. Must return 0 results.
3. **Negative test** — Disables RLS, runs cross-tenant query, verifies it WOULD return results (proving RLS is the actual barrier, not a query bug).
4. **Periodic audit** — `brain_health_report` extended to check for orphaned records (records with `tenant_id` not in tenants table).

---

## 4. Authentication & Authorization Design

### Architecture

```
Request arrives
  │
  ▼
┌──────────────────────────────┐
│  FastMCP Auth Middleware      │
│  (soul/auth/middleware.py)    │
│                              │
│  1. Extract Authorization    │
│     header                   │
│  2. If Bearer token →        │
│     validate JWT (OAuth 2.1) │
│  3. If API key (sk-*) →      │
│     lookup in api_keys table │
│  4. Set RequestContext:      │
│     tenant_id, agent_id,     │
│     role, permissions        │
│  5. Rate limit check         │
│     (token bucket per key)   │
└──────────┬───────────────────┘
           │ RequestContext propagated
           ▼
     Tool handler executes
     with tenant scope
```

### OAuth 2.1 (Production Deployments)

- FastMCP middleware validates Bearer JWT on every request
- JWT claims: `{ "sub": agent_id, "tid": tenant_id, "role": "agent"|"admin"|"readonly", "exp": ... }`
- Token issuer: customer's own IdP (Keycloak, Auth0, Entra ID). SOUL validates signature, not issues tokens.
- Library: `mcp-oauth` (Apache 2.0) or Scalekit plugin for FastMCP

### API Key Fallback (Simple Deployments)

For single-tenant or developer setups where OAuth is overkill:

```
Authorization: Bearer sk-soul-<tenant_id_prefix>-<random_32_chars>
```

- Keys stored in PostgreSQL `api_keys` table: `id, tenant_id, key_hash (bcrypt), role, created_at, expires_at, last_used_at`
- Key rotation: new key + 24h grace period for old key (per BDD scenario in business-analysis-2c)
- Keys are hashed in DB — raw key only shown once at creation

### RBAC Model

| Role | Permissions |
|------|-------------|
| `admin` | All tools + tenant management + key management + backup/restore |
| `agent` | All memory/graph/identity/instinct tools. Cannot manage tenants or keys. |
| `readonly` | `memory_search`, `memory_list`, `connectome_status`, `soul_check`, `session_list`, `event_log_query` only |

**Enforcement:** `soul/auth/rbac.py` decorates tool handlers. Permission check runs BEFORE tool body.

```python
# In soul/auth/rbac.py
ROLE_PERMISSIONS = {
    "admin": {"*"},
    "agent": {"memory.*", "graph.*", "identity.*", "instincts.*", "sessions.*", ...},
    "readonly": {"memory_search", "memory_list", "connectome_status", ...}
}

def require_permission(tool_name: str):
    """Decorator that checks RequestContext.role against ROLE_PERMISSIONS."""
    ...
```

---

## 5. Configuration Architecture

### Environment Variables with Pydantic Settings

**File: `soul/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class SoulConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOUL_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_user: str = "seal"
    pg_password: str = "changeme"            # No more seal_memory_2026 in source
    pg_database: str = "seal_memory"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "soul_memories"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"         # No more seal2026soul in source

    # Ollama (optional)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_enabled: bool = True              # Disable to skip LLM enrichment

    # Embeddings
    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_cache_dir: str = "~/.cache/soul/models"
    embedding_dimensions: int = 768

    # Mode
    lite_mode: bool = False                  # SOUL_LITE_MODE=true → PG-only
    
    # Auth
    auth_enabled: bool = True
    auth_jwt_secret: str = ""                # Required if auth_enabled
    auth_jwt_issuer: str = ""
    auth_api_key_enabled: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def pg_dsn(self) -> str:
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

# Singleton
_config: SoulConfig | None = None

def get_config() -> SoulConfig:
    global _config
    if _config is None:
        _config = SoulConfig()
    return _config
```

### What This Replaces

| Current (hardcoded) | New (env var) |
|---------------------|---------------|
| `db.py` line 7: `DB_URL = "postgresql://seal:REDACTADO@..."` | `SOUL_PG_HOST`, `SOUL_PG_PORT`, `SOUL_PG_USER`, `SOUL_PG_PASSWORD`, `SOUL_PG_DATABASE` |
| `mcp_server_v2.py` line 44: `OLLAMA_GEN_URL = "http://localhost:11434/..."` | `SOUL_OLLAMA_URL` |
| `mcp_server_v2.py` line 45: `OLLAMA_MODEL = "qwen2.5:7b"` | `SOUL_OLLAMA_MODEL` |
| `mcp_server_v2.py` line 47: `QDRANT_URL = "http://localhost:6333"` | `SOUL_QDRANT_URL` |
| `mcp_server_v2.py` line 50: `NEO4J_URI = "bolt://localhost:7687"` | `SOUL_NEO4J_URI` |
| `mcp_server_v2.py` line 51: `NEO4J_AUTH = ("neo4j", "seal2026soul")` | `SOUL_NEO4J_USER`, `SOUL_NEO4J_PASSWORD` |
| `mcp_server_v2.py` line 55: `SOUL_LITE` env var | `SOUL_LITE_MODE` (consistent naming) |
| `embeddings.py` line 24: `MODEL_NAME = "intfloat/multilingual-e5-base"` | `SOUL_EMBEDDING_MODEL` |
| `mcp_server_v2.py` line 1962: hardcoded `/home/dadito/...` path | Removed entirely (SEAL-specific) |

### Docker Compose `env_file` Support

```yaml
# docker-compose.yml
services:
  soul-server:
    env_file: .env
    # All SOUL_* variables loaded automatically
```

### `.env.example` (shipped in repo, `.env` in `.gitignore`):

```bash
# Required
SOUL_PG_PASSWORD=changeme
SOUL_NEO4J_PASSWORD=changeme

# Optional (defaults shown)
SOUL_PG_HOST=postgres
SOUL_PG_PORT=5432
SOUL_QDRANT_URL=http://qdrant:6333
SOUL_NEO4J_URI=bolt://neo4j:7687
SOUL_OLLAMA_URL=http://host.docker.internal:11434
SOUL_OLLAMA_ENABLED=true
SOUL_LITE_MODE=false
SOUL_AUTH_ENABLED=true
SOUL_AUTH_JWT_SECRET=generate-a-random-secret-here
```

### Secret Management

- No credentials in source code, Docker images, or compose files
- Docker Compose: `env_file` for development, Docker secrets for production
- Secret rotation: API keys support grace period; DB passwords require restart (acceptable for v1.0)
- CI: secrets stored in GitHub Actions secrets, never in workflow files

---

## 6. Deployment Architecture

### Docker Compose — Primary (v1.0)

**Two profiles: `lite` and `full`.**

```yaml
# docker-compose.yml
version: "3.9"

x-healthcheck-defaults: &healthcheck-defaults
  interval: 15s
  timeout: 10s
  retries: 5

services:
  # ── Always present ──
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: seal
      POSTGRES_PASSWORD: ${SOUL_PG_PASSWORD:?Set SOUL_PG_PASSWORD in .env}
      POSTGRES_DB: seal_memory
    ports:
      - "${SOUL_PG_PORT:-5433}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    healthcheck:
      <<: *healthcheck-defaults
      test: ["CMD-SHELL", "pg_isready -U seal"]
      start_period: 30s
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G

  soul-server:
    build: .
    image: ghcr.io/sknaider/soul-memory:${VERSION:-latest}
    env_file: .env
    environment:
      SOUL_PG_HOST: postgres
      SOUL_PG_PORT: 5432
      SOUL_LITE_MODE: ${SOUL_LITE_MODE:-false}
    ports:
      - "${SOUL_PORT:-8000}:8000"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      <<: *healthcheck-defaults
      test: ["CMD", "curl", "-sf", "http://localhost:8000/health"]
      start_period: 20s
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

  # ── Full mode only (docker compose --profile full up) ──
  neo4j:
    profiles: ["full"]
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/${SOUL_NEO4J_PASSWORD:?Set SOUL_NEO4J_PASSWORD in .env}
    ports:
      - "${SOUL_NEO4J_BOLT_PORT:-7687}:7687"
      - "${SOUL_NEO4J_HTTP_PORT:-7474}:7474"
    volumes:
      - neo4j_data:/data
    healthcheck:
      <<: *healthcheck-defaults
      test: ["CMD", "neo4j", "status"]
      start_period: 60s
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G

  qdrant:
    profiles: ["full"]
    image: qdrant/qdrant:v1.16.0
    ports:
      - "${SOUL_QDRANT_PORT:-6333}:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      <<: *healthcheck-defaults
      test: ["CMD", "curl", "-sf", "http://localhost:6333/healthz"]
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

volumes:
  postgres_data:
  neo4j_data:
  qdrant_data:
```

**Usage:**

```bash
# Soul Lite (Community) — PG only
cp .env.example .env
# Edit .env: set passwords
docker compose up -d

# Soul Full (Team/Enterprise)
SOUL_LITE_MODE=false docker compose --profile full up -d
```

### Startup Order and Retry Logic

Health checks handle basic ordering via `depends_on: condition: service_healthy`. Additionally, `soul/core/db.py` and `soul/core/clients.py` implement exponential backoff retry (10 attempts, starting at 1s, max 30s). This covers race conditions where `service_healthy` passes but the DB isn't fully accepting application connections yet.

### Soul Lite Mode

When `SOUL_LITE_MODE=true`:
- Only PostgreSQL container required
- `get_qdrant()` returns `PgVectorAdapter` (existing `soul_lite_adapter.py`, line 46)
- Connectome tools (`graph/` module, 15 tools) return `{"status": "unavailable", "reason": "Soul Lite mode — Neo4j not configured. Enable full mode for graph features."}`
- All other tools (memory, identity, instincts, sessions, procedures, rules, sleep) work via PG+pgvector

### Helm Chart (v1.1 — Enterprise Add-on)

Not designed in detail here. Template:
- One chart with subcharts: `soul-server`, `postgresql` (Bitnami), `neo4j` (Neo4j official), `qdrant` (official)
- `values.yaml` maps to the same `SOUL_*` env vars
- Consider Docker Kanvas (launched Jan 2026) as an automated Compose-to-K8s bridge before investing in manual Helm authoring

---

## 7. Migration Strategy (Current Monolith → Product)

### Principle: Team SEAL Must Never Break

Every phase ends with the existing 61 tests passing AND Team SEAL agents (JARVIS, ADA, DUM) operational. The `mcp_server_v2.py` file remains as a backward-compatible shim throughout.

### Phase 0 — Prerequisites (Week 1)

**Goal:** Fix known risks without changing file structure.

1. **Externalize configuration** — Create `soul/core/config.py` with Pydantic Settings. Replace all 7 hardcoded values in `mcp_server_v2.py` (lines 44-51) and `db.py` (line 7) with `get_config()` calls. Set `.env` with current values so Team SEAL works unchanged.

2. **Extract shared helpers to `soul/core/helpers.py`:**
   - `temporal_decay_score()` (used by memory + retrieval)
   - `_extract_entities()` (used by memory_store + connectome)
   - `classify_emotion()` (used by memory_store)
   - `generate_episode_context()` (used by memory_store)
   - `update_ocean()` (line 367, used by memory_store + identity)
   - `update_relationships()` (used by memory_store)
   - `ocean_to_narrative()` (used by identity)
   - Constants: `KNOWN_ENTITIES`, `HALF_LIFE_BY_CATEGORY`, `OCEAN_DELTAS`, `OCEAN_SESSION_CAP`

3. **Move `_observe` + `_observed_tool`** to `soul/core/instrumentation.py`. The `mcp.tool = _observed_tool` replacement (line 205) happens in `server.py` before any module imports.

4. **Fix `_reflexion_lesson`** — Remove `@mcp.tool()` decorator (line 3625). Keep as internal function. Signature `(pool, agent, context)` is not valid MCP tool format.

5. **Fix `sleep_gate_mood_retrieval`** — Replace inline `SentenceTransformer()` (line 6693) with `get_embedding()` from `embeddings.py`. Eliminates duplicate model loading.

6. **Run 61 tests.** All must pass.

### Phase 1 — Extract Easy Modules (Weeks 2-3)

Split in this order (lowest risk first, per codebase-analysis-2b section 7):

1. `soul/rules/` — rules_set, rule_list, event_log_append, event_log_query, working_state_get, working_state_update, secret_scan, _reflexion_lesson (4 tools, pure PG)
2. `soul/procedures/` — reasoning_trace_store/update/search, procedure_store/search/update (6 tools, PG + pgvector)
3. `soul/sessions/` — 5 tools, already delegates to `session_memory.py`
4. `soul/sleep/` — 4 tools, PG + numpy + Ollama

After each extraction: run tests. Each module exposes a `register_tools(mcp)` function called by `server.py`.

### Phase 2 — Extract Medium Modules (Weeks 3-4)

5. `soul/instincts/` — 8 tools, PG + pgvector + Ollama
6. `soul/identity/` — 10 tools (including peer_model). `boot_context` moved last within this module because it calls `soul_activate` and `memory_search`.

### Phase 3 — Extract Hard Modules (Weeks 4-6)

7. `soul/graph/` — 15 tools, Neo4j heavy. Requires extracting Neo4j session management from globals to `core/clients.py`.
8. `soul/memory/` — 16 tools. `memory_store` (line 568, ~300 lines with 15 side effects) is the hardest single function. Extract side effects as hooks.
9. `soul/dmem/` — 5 tools. ACE curator calls across modules — wire via dependency injection.

### Phase 4 — Add Product Features (Weeks 6-8)

10. `soul/auth/` — OAuth 2.1 middleware + API key support
11. `soul/tenant/` — RLS setup, tenant context propagation
12. Database migration scripts for `tenant_id` columns
13. Docker Compose hardening (health checks, secrets, resource limits)

### Phase 5 — Polish (Weeks 8-10)

14. `/.well-known/mcp/server.json` endpoint
15. `/health` and `/metrics` endpoints
16. README + quickstart documentation
17. CI pipeline (GitHub Actions: lint, test, build Docker image)
18. License files (Apache 2.0 Community, BSL Team/Enterprise)

### Backward Compatibility Shim

```python
# mcp_server_v2.py — kept in repo root for Team SEAL backward compatibility
"""
SEAL Memory MCP Server v2 — backward compatibility shim.
Team SEAL configs reference this file. Do not delete.
New installations should use `soul.server` directly.
"""
from soul.server import create_server, main

if __name__ == "__main__":
    main()
```

### Database Migration for tenant_id

```sql
-- migrations/001_add_tenant_id.sql
-- Safe to run on live database — adds column with default, no locks on PG 11+

-- Default tenant for existing Team SEAL data
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'memories' AND column_name = 'tenant_id') THEN
        ALTER TABLE memories ADD COLUMN tenant_id UUID NOT NULL 
            DEFAULT '00000000-0000-0000-0000-000000000000';
        CREATE INDEX CONCURRENTLY idx_memories_tenant ON memories (tenant_id);
    END IF;
END $$;

-- Repeat for: sessions, instincts, rules, event_log, reasoning_traces,
--   procedures, working_state, peer_models, ocean_scores, broadcasts

-- Enable RLS (only affects connections that SET app.tenant_id)
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_rw_memories ON memories
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- The `true` second arg to current_setting means return NULL if not set,
-- instead of raising an error. This means: if app.tenant_id is not set,
-- RLS returns no rows (safe default). Team SEAL's existing connection
-- must SET LOCAL app.tenant_id = '00000000-...' to continue working.
```

### Feature Flags for Gradual Rollout

```python
# soul/core/config.py additions
class SoulConfig(BaseSettings):
    ...
    # Feature flags
    feature_auth: bool = False          # v1.0: enable after testing
    feature_multitenancy: bool = False  # v1.0: enable after migration
    feature_rls: bool = False           # v1.0: enable after RLS policies created
    feature_metrics: bool = False       # v1.1: Prometheus endpoint
    feature_hipaa_audit: bool = False   # v1.1: immutable event log
```

Team SEAL runs with all flags `False` during migration. Each flag is enabled independently after verification.

---

## 8. HIPAA Compliance Architecture

### Tier Model (from research-2a section 6)

| Tier | Target | Requirements |
|------|--------|-------------|
| Standard (v1.0) | Non-healthcare | No HIPAA controls. Ship fast. |
| HIPAA-Ready (v1.1) | Healthcare self-hosted | Encryption + audit + PHI detection |
| HIPAA-Certified (v2.0+) | Regulated enterprise | Third-party audit, SOC2 Type 2 |

### Encryption at Rest

**PostgreSQL — `pgcrypto` for sensitive columns:**

```sql
-- Only encrypt columns that could contain PHI
-- Do NOT encrypt tenant_id, timestamps, or foreign keys (breaks indexes)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Encrypt memory content (the field most likely to contain PHI)
-- Application encrypts before INSERT, decrypts after SELECT
-- Key stored in SOUL_ENCRYPTION_KEY env var, NOT in database
```

**Implementation in `soul/core/db.py`:**

```python
from cryptography.fernet import Fernet

def encrypt_field(value: str, key: bytes) -> str:
    return Fernet(key).encrypt(value.encode()).decode()

def decrypt_field(value: str, key: bytes) -> str:
    return Fernet(key).decrypt(value.encode()).decode()
```

**Qdrant:** Qdrant does not encrypt at rest natively. Use Docker volume encryption (LUKS on Linux) or cloud-managed encrypted volumes.

**Neo4j Community:** No native encryption at rest. Same solution — encrypted Docker volumes.

### Encryption in Transit

```yaml
# docker-compose.hipaa.yml (overlay for HIPAA deployments)
services:
  soul-server:
    environment:
      SOUL_TLS_CERT: /certs/server.crt
      SOUL_TLS_KEY: /certs/server.key
    volumes:
      - ./certs:/certs:ro

  postgres:
    command: >
      -c ssl=on
      -c ssl_cert_file=/certs/server.crt
      -c ssl_key_file=/certs/server.key
    volumes:
      - ./certs/pg:/certs:ro

  neo4j:
    environment:
      NEO4J_dbms_ssl_policy_bolt_enabled: "true"
      NEO4J_dbms_ssl_policy_bolt_base__directory: /certs
    volumes:
      - ./certs/neo4j:/certs:ro
```

### Immutable Audit Log

Extends the existing `event_log_append` tool (line 2115) and `event_log_query` tool (line 2145):

```sql
-- Audit log table (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,      -- tool name
    resource_type TEXT,        -- 'memory', 'instinct', etc.
    resource_id TEXT,
    input_summary TEXT,        -- truncated input (no PHI in logs)
    output_status TEXT,        -- 'success' or 'error'
    client_ip INET,
    session_id TEXT
);

-- CRITICAL: Revoke modification permissions
REVOKE UPDATE, DELETE ON audit_log FROM soul_app;
-- Only soul_app can INSERT. Only soul_admin (separate role) can SELECT for audits.

-- Retention: partition by month, drop partitions older than 6 years
-- (HIPAA requires 6-year minimum retention)
```

### PHI Detection in `secret_scan`

Extend `secret_scanner.py` (currently detects API keys, passwords) to detect:

| PHI Type | Pattern |
|----------|---------|
| SSN | `\b\d{3}-\d{2}-\d{4}\b` |
| MRN (Medical Record Number) | `\bMRN[:\s]?\d{6,10}\b` |
| DOB | `\b\d{2}/\d{2}/\d{4}\b` with age validation |
| Email | Standard email regex |
| Phone | `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b` |
| ICD codes | `\b[A-Z]\d{2}(\.\d{1,4})?\b` (diagnosis codes) |

**When PHI is detected:**
- Log to audit_log with `action = 'phi_detected'`
- Flag memory with `phi_detected = true` metadata
- Do NOT block storage (clinical systems need to record patient data) — but ensure encryption is active

### BAA Template

Not a technical artifact — a legal document. Required before any healthcare customer processes PHI through SOUL. Template must specify:
- Permitted uses of PHI
- Safeguards SOUL provides (encryption, audit, access control)
- Breach notification procedures (within 60 days per HIPAA)
- Return/destruction of PHI upon termination

**Action:** Engage healthcare attorney to draft BAA template. Estimated cost: $2-5K. Blocks enterprise healthcare sales without it.

---

## 9. Technology Decisions

### Decision 1: Package Manager

| Option | Pros | Cons |
|--------|------|------|
| **Poetry** (chosen) | Best DX, lockfile, handles dev/prod deps | Extra dependency |
| Hatch | PEP-compliant, modern | Less mature ecosystem |
| setuptools + pyproject.toml | No extra deps | Manual lockfile management |

**Why Poetry:** Development velocity matters for solo developer + AI agents. Poetry's `poetry.lock` ensures reproducible builds. `hatch build` used for distribution if needed. Ship as single `soul-memory` package on PyPI.

### Decision 2: Auth Library

| Option | Pros | Cons |
|--------|------|------|
| **FastMCP built-in middleware** (chosen) | Native integration, no extra deps | Limited to FastMCP patterns |
| Scalekit plugin | 5-line integration | External dependency |
| Custom from scratch | Full control | Time cost |

**Why FastMCP middleware:** FastMCP 2.9 has interceptors that run on every tool call (research-2a section 1). This is the right hook point for auth. Custom JWT validation + API key lookup inside the interceptor. No additional framework needed.

### Decision 3: Multi-Tenancy Strategy

| Option | Pros | Cons |
|--------|------|------|
| **Shared tables + RLS** (chosen) | Simple schema, one migration | 5-15% query overhead |
| Schema per tenant | Full isolation | Degrades past ~50 tenants |
| Database per tenant | Physical isolation | Overkill, ops burden |

**Why RLS:** SOUL targets hundreds of tenants (developer teams). Schema-per-tenant breaks at that scale. RLS overhead is acceptable — AWS Aurora benchmarks confirm pgvector + RLS is viable (research-2a section 2). Default tenant UUID preserves Team SEAL backward compatibility.

### Decision 4: Neo4j vs Memgraph

| Option | Pros | Cons |
|--------|------|------|
| **Neo4j Community** (chosen for v1.0) | Existing codebase, mature, large ecosystem | GPL-3 (legal review needed), no native MT |
| Memgraph | Apache 2.0, native MT, Cypher-compatible | Migration effort, smaller community |

**Why Neo4j (for now):** 15 tools (graph/ module) use Neo4j-specific Cypher patterns. Rewriting for Memgraph is a v1.1+ consideration. GPL-3 concern: SOUL communicates with Neo4j over Bolt protocol (network boundary), does not embed Neo4j code. This is generally considered not a GPL trigger — but legal review before commercial launch is mandatory.

### Decision 5: Embedding Model Packaging

| Option | Pros | Cons |
|--------|------|------|
| **Bundled in Docker image** (chosen) | Works offline, predictable | Image size ~2GB |
| Download on first run | Smaller image | Requires internet, startup delay |
| External API (OpenAI, etc.) | No local model | Network dependency, cost, data leaves system |

**Why bundled:** SOUL's selling point is data sovereignty. Embedding model (`intfloat/multilingual-e5-base`, 768 dims, ~400MB) is small enough to ship in the Docker image. Pre-downloaded during `docker build`. No internet required at runtime.

### Decision 6: Transport Protocol

| Option | Pros | Cons |
|--------|------|------|
| **Streamable HTTP** (chosen) | Current MCP standard (Apr 2026), LB-friendly | Requires migration from current stdio |
| SSE | Familiar | Deprecated Apr 1, 2026 |
| stdio | Current SOUL transport | Not network-accessible, no auth |

**Why Streamable HTTP:** SSE was deprecated April 1, 2026 (research-2a section 1). Streamable HTTP is stateless and load-balancer friendly. Current SOUL runs via stdio (Claude Code launches the process). For a product, must be network-accessible. Streamable HTTP is the only viable choice.

### Decision 7: Session State Management

| Option | Pros | Cons |
|--------|------|------|
| **JWT carries all context** (chosen) | Stateless server, horizontally scalable | Token size limit |
| Redis session store | Rich state | Additional infrastructure |
| In-memory dict | Simple | Lost on restart |

**Why JWT:** Current `_ocean_session_deltas` dict (line 364) is the main in-memory state. Move to PostgreSQL-backed session state for persistence across restarts. JWT carries `tenant_id` + `agent_id` — lightweight. OCEAN deltas persisted to PG after each update (small additional write, acceptable).

### Decision 8: Ollama Dependency

| Option | Pros | Cons |
|--------|------|------|
| **Optional with graceful degradation** (chosen) | Works without GPU/LLM | Reduced functionality |
| Required | Full features always | Blocks non-GPU deployments |
| Replace with cloud API | No local model | Breaks data sovereignty |

**Why optional:** 10 tools use Ollama (memory_store A-MEM enrichment, session_distill, temporal_query, instinct_evolve, observation_analyze, ace_curator, etc. — see dependency matrix in codebase-analysis-2b section 3). When `SOUL_OLLAMA_ENABLED=false`, these tools skip LLM enrichment and log a warning. Core CRUD + search works without Ollama. This matches the Soul Lite philosophy.

---

## Appendix: Cross-Reference to Source Documents

| Architecture Section | Primary Source | Key Data Points |
|---------------------|---------------|-----------------|
| System Diagram | 2b §10 (dependency graph) | PG:5433, Qdrant:6333, Neo4j:7687, Ollama:11434 |
| Module Decomposition | 2b §2 (tool categorization, 74 tools at specific lines) | 13 categories, effort estimates |
| Multi-Tenancy | 2a §2 (RLS, Qdrant tiered MT, Neo4j options) | pgvector+RLS viable per AWS, Qdrant 1.16 |
| Auth Design | 2a §1 (OAuth 2.1, FastMCP 2.9 middleware) | mcp-oauth library, Scalekit |
| Configuration | 2b §4 (hardcoded values audit) | 7 hardcoded values, 1 env var |
| Deployment | 2a §5 (Docker Compose patterns), 2c §5 (MVP) | Health checks, secrets, profiles |
| Migration | 2b §7 (effort estimates), 2b §8 (prerequisites) | 13-step split order, 5 prerequisites |
| HIPAA | 2a §6 (45 CFR §164.312 requirements) | pgcrypto, immutable audit, 6yr retention |
| Pricing tiers | 2c §4 (open-core strategy) | Community/Team/Enterprise/OEM |
