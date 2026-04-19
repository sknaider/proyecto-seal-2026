# SOUL Memory System — Productization Research
**Date:** April 7, 2026  
**Scope:** MCP standards, multi-tenant vector DBs, Python modularization, AI memory competitors, deployment packaging, HIPAA  
**Status:** Research complete — actionable findings only

---

## 1. MCP Protocol Standards — Current State & Production Patterns

### Protocol Status (April 2026)

MCP is production-ready for developer tooling and small-to-medium agent applications. Enterprise-grade features (SSO auth, audit trails, governance) are H2 2026 targets. The spec itself is actively evolving — critical for product timing.

**Critical transport change (already deployed):**  
SSE transport was deprecated effective April 1, 2026. The current standard is **Streamable HTTP** (stateless, load-balancer friendly). Any SOUL MCP server must use Streamable HTTP, not SSE. Old `http+sse` connections from existing clients will break if you don't handle migration.

**Discovery standard (pending merge, already being implemented):**  
Two active SEPs competing for canonical form:
- SEP-1649: `/.well-known/mcp/server-card.json` — richer metadata (description, tool listings, homepage)
- SEP-1960: `/.well-known/mcp` — endpoint enumeration + auth discovery

Both have broad client support. **Recommendation:** implement both. The official MCP Registry (backed by Anthropic, GitHub, Microsoft) auto-discovers via `/.well-known/mcp/server.json`.

### Authentication in Production MCP Servers

The 2026 roadmap makes auth a top priority. Current practical patterns:

**OAuth 2.1 (recommended for production):**
- FastMCP provides middleware hooks for token validation
- Pattern: Bearer token in `Authorization` header, validated per-request
- Working examples exist for Entra ID, Auth0, Keycloak (all Keycloak-style config)
- Library: `mcp-oauth` (Python, Apache 2.0) — implements server+client following official SDK OAuth flow
- Scalekit plugin: adds auth to FastMCP in ~5 lines

**Multi-tenant auth isolation (critical for SOUL):**
- Pin to single issuer per tenant; reject tokens from other realms even if signed by same auth server
- SSE connections support per-request context via custom headers — useful for tenant routing
- Every tool handler must enforce `tenant_id` scope before executing — not optional

**FastMCP 2.9 Middleware (current stable):**
- Middleware wraps semantic handlers (tools, resources, prompts), not raw protocol
- Built-in: rate limiting (token bucket + sliding window), structured JSON logging
- Rate limiting: token bucket recommended for burst tolerance; use Redis for distributed deployments
- Interceptors run on every tool call — right place for tenant validation + audit logging

**Session handling (roadmap, June 2026 spec target):**
- Current: stateful sessions fight load balancers
- Planned: explicit session creation/resume/migration so server restarts are transparent
- Until then: design SOUL server to be stateless per-request (JWT carries tenant context)

### Pricing / Monetization Reality

Over 11,000 MCP servers exist, less than 5% monetized — early mover advantage is real.

Emerging models:
1. **Per-call billing** — MCP tool invocation = billable unit (Moesif, Flexprice handle metering)
2. **Credit system** — 200 free forever, then $9/mo for 1,000 credits
3. **Tiered SaaS** — free/pro/enterprise tiers based on memory capacity + agent count
4. **Self-hosted license** — one-time or annual fee for the Docker Compose bundle

One documented case: 21st.dev hit $10K MRR in 6 weeks with zero marketing. Market timing is now.

---

## 2. Multi-Tenant Vector DB Patterns

### Qdrant — Tiered Multitenancy (v1.16, Nov 2025)

This is the most production-relevant update. SOUL uses Qdrant — this directly applies.

**Three-tier architecture:**
1. **Shared shard (fallback):** Small/new tenants share a single shard. Low overhead.
2. **Dedicated shard:** Large/premium tenants get their own shard. No noisy neighbor.
3. **Tenant promotion:** When a tenant grows, promote from shared → dedicated transparently during live traffic (zero downtime via internal shard transfer).

**Implementation:**
```python
# 1. Create payload index with is_tenant=True
client.create_payload_index(
    collection_name="memories",
    field_name="tenant_id",
    field_schema=PayloadSchemaType.KEYWORD,
    field_params={"is_tenant": True}  # critical for perf
)

# 2. All writes/reads filter by tenant_id
client.search(
    collection_name="memories",
    query_vector=vector,
    query_filter=Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))])
)
```

**Decision matrix for SOUL:**
| Approach | When to use |
|---|---|
| Single collection + payload filter | Default. Works for up to ~hundreds of tenants |
| Tiered (v1.16) | When some tenants have 10x+ more data than average |
| Collection per tenant | Only when tenants need different HNSW params or vector dimensions |

**Recommendation:** Start with single collection + `is_tenant` payload index. The v1.16 tiered model lets you promote large tenants without schema migration.

### pgvector — Row-Level Security (PostgreSQL RLS)

Three approaches, ranked by SOUL fit:

**Option A: RLS on shared tables (recommended for SOUL)**
```sql
-- Single memories table, all tenants
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memories 
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- Set at connection time (or per query via SET LOCAL)
SET LOCAL app.tenant_id = 'tenant-uuid-here';
```
- Pros: simple schema management, schema changes once, resource efficient
- Cons: RLS policies add query overhead (~5-15%); complex policies can be hard to debug
- **pgvector + RLS works**: AWS Aurora blog confirms multi-tenant vector search with RLS is viable

**Option B: Schema per tenant**
- Pros: complete isolation, easy per-tenant backup/restore
- Cons: PostgreSQL degrades with hundreds of schemas; schema migrations multiply; not scalable past ~50 tenants

**Option C: Database per tenant**
- Overkill at SOUL scale. Only justified for regulated industries needing physical isolation (and even then, RLS + encryption satisfies HIPAA).

**Performance tip:** Always put `tenant_id` as first column in composite indexes. B-tree index on `tenant_id` is mandatory before RLS.

### Neo4j — Multi-Database (Enterprise)

Neo4j 4.0+ (Enterprise only) supports multiple active databases. Each database is a separate transaction domain — no cross-database transactions by default.

**Options:**
1. **Database per tenant (Enterprise):** Full isolation. Each tenant gets `CREATE DATABASE tenant_<id>`. Privileges bound per database. Clean but requires Enterprise license (~$36K+/year).
2. **Single database + property filter:** Community edition compatible. All tenant nodes carry `tenant_id` property. Enforced at application layer — no DB-native isolation.
3. **Namespace via label prefixing:** Not recommended — pollutes schema.

**SOUL recommendation:** For MVP, use single Neo4j database with `tenant_id` property on all nodes/relationships, enforced in application layer. If/when a customer needs Enterprise-grade isolation, offer database-per-tenant as a premium SKU backed by Neo4j Enterprise.

**Important:** Neo4j Community Edition is GPL-3. If SOUL embeds it in a commercial product, legal review required. Neo4j Enterprise is commercial. **Memgraph** (alternative, Apache 2.0) supports multi-tenancy natively and is a drop-in Cypher-compatible alternative.

---

## 3. Python Monolith Modularization

### The Problem

A 302KB single-file Python server is a maintenance and testing liability. The goal is to split it into installable modules while maintaining 100% backward compatibility for existing tool imports.

### Recommended Architecture: src Layout + Namespace Packages

```
soul-memory/
├── pyproject.toml              # Single build config
├── src/
│   └── soul/                   # Namespace package (no __init__.py here)
│       ├── core/               # Base types, DB connections, config
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── db.py
│       │   └── types.py
│       ├── memory/             # Core memory tools (store, search, update)
│       │   ├── __init__.py
│       │   └── tools.py
│       ├── graph/              # Connectome + Neo4j tools
│       │   ├── __init__.py
│       │   └── tools.py
│       ├── instinct/           # Instinct/procedure tools
│       ├── session/            # Session management
│       ├── identity/           # OCEAN, soul, inner_thoughts
│       ├── temporal/           # Temporal graph tools
│       └── server.py           # FastMCP server assembly (imports all modules)
├── tests/
└── README.md
```

**Namespace package benefit:** `soul.memory`, `soul.graph`, etc. can be separately installable distribution packages (each with their own `pyproject.toml`), or shipped as one. This is the pattern used by large collections of related packages (AWS SDK, Google Cloud SDK).

### Migration Strategy (zero breakage)

1. **Extract by domain, not by technical layer.** `soul.memory` = all memory CRUD tools. `soul.graph` = all Neo4j/connectome tools. Don't do `soul.models` + `soul.handlers` — that's technical layer splitting, not domain splitting.

2. **Shim file for backward compatibility:**
```python
# mcp_server_v2.py (original file, now a shim)
# Keep this file — existing configs reference it
from soul.server import create_server, main
```

3. **Use Import Linter** to enforce module boundaries (prevents circular imports from creeping back):
```toml
# pyproject.toml
[tool.importlinter]
root_packages = ["soul"]
[[tool.importlinter.contracts]]
name = "Core has no dependencies on other soul modules"
type = "forbidden"
source_modules = ["soul.core"]
forbidden_modules = ["soul.memory", "soul.graph"]
```

4. **Incremental migration, not big bang.** Week 1: extract `soul.core`. Week 2: extract `soul.memory`. Test 61/61 passes at each step before continuing.

5. **Tooling:** Use `lato` (Python microframework for modular monoliths) if internal event bus between modules is needed. For SOUL's use case (MCP server, not web app), direct imports with defined public APIs per module is sufficient.

### Packaging Options (2026)

| Tool | Use case |
|---|---|
| **Poetry** | Best DX, lockfile management, handles monorepo variants |
| **Hatch** | Modern, PEP-compliant, good for multi-package repos |
| **setuptools + pyproject.toml** | Maximum compatibility, no extra deps |

**Recommendation:** Poetry for development, build with `hatch build` for distribution. Ship as a single `soul-memory` package on PyPI (or private registry) — not 8 separate packages. Customers `pip install soul-memory`.

---

## 4. AI Memory Products — Competitive Landscape

### Market Map (April 2026)

| Product | Architecture | License | Self-host | Pricing |
|---|---|---|---|---|
| **Mem0** | FastAPI + pgvector + Neo4j | Apache 2.0 | Yes (Docker) | Free OSS; cloud free tier + paid |
| **Zep / Graphiti** | Temporal knowledge graph | Apache 2.0 (Graphiti) | Yes | Free OSS; Zep Cloud (SOC2/HIPAA) |
| **LangMem** | KV + vector, LangGraph-native | MIT | Yes (self-managed) | Free; LangSmith $39/seat/mo |
| **ReMe (MemoryScope)** | Multi-agent shared memory | Apache 2.0 | Yes | Free |
| **Letta (MemGPT)** | Stateful agent OS | Apache 2.0 | Yes | Free + Letta Cloud |

### Deep Dives

**Mem0 (closest architectural competitor):**
- Stack: FastAPI + pgvector + Neo4j. **This is almost identical to SOUL.**
- `docker compose up` = running system (same target as SOUL)
- Memory Compression Engine: 80% reduction in prompt tokens (key differentiator)
- 26% accuracy boost in their research paper (arXiv:2504.19413, April 2026)
- 20+ vector backend swaps (Qdrant, ChromaDB, Pinecone, etc.)
- MCP server exists: `mem0-mcp-selfhosted` on mcpservers.org
- **Weakness:** generic memory, no agent-specific constructs (OCEAN, instincts, procedures, soul identity)
- **SOUL advantage:** SOUL has 75 specialized tools vs Mem0's ~10 generic ones

**Zep / Graphiti:**
- Temporal knowledge graph: tracks *when* things happened, not just *what*
- Graphiti = open-source OSS temporal context graph engine (the core)
- Zep Cloud: SOC2 Type 2 + HIPAA compliant, <200ms latency SLA
- Pricing: free tier (no CC), enterprise = contact sales
- arXiv paper: [2501.13956] — academic credibility
- **SOUL advantage:** SOUL has deeper agent identity constructs; Zep is context-focused, not agent-soul-focused

**LangMem:**
- Entirely eschews external infrastructure — uses LangGraph's native store
- Three memory types: episodic, semantic, procedural (agents update their own system prompts)
- **Weakness:** LangChain-locked. Not useful to non-LangChain users.
- **SOUL advantage:** framework-agnostic (works with any MCP client)

**ReMe (formerly MemoryScope by Alibaba ModelScope):**
- Multi-agent shared memory: memories extracted, reused, shared across users/tasks/agents
- **Weakness:** limited community, less production-hardened
- **SOUL advantage:** richer toolset, proven 61/61 tests

### SOUL Differentiators (not replicated by any competitor)

1. **Agent identity persistence:** OCEAN personality model, soul snapshot, emotional state tracking — no competitor has this
2. **75 specialized tools** vs 5-15 generic ones in competitors
3. **Connectome (Neo4j temporal graph):** causal reasoning, bitemporal queries, smart routing
4. **Instinct system:** learned behavior patterns that can be promoted/evolved
5. **Medical AI readiness:** the stack (pgvector + Neo4j + Qdrant) maps to HIPAA compliance patterns
6. **Team coordination:** multi-agent broadcast, peer model sync — Mem0/Zep are single-agent focused

### Pricing Benchmark

| Product | Self-hosted | Cloud Free | Cloud Paid |
|---|---|---|---|
| Mem0 | Free (OSS) | Yes | Contact sales |
| Zep | Free (OSS) | Yes (no CC) | Contact sales |
| LangMem | Free (MIT) | N/A | LangSmith $39/seat |
| **SOUL target** | **$0 Community / $X Enterprise** | **SaaS free tier** | **Per-agent/month** |

---

## 5. Deployment Packaging

### Docker Compose vs Helm — Decision

**For SOUL's target market (developers, small teams, healthcare orgs):**

| Factor | Docker Compose | Helm (Kubernetes) |
|---|---|---|
| Setup complexity | Low — `docker compose up` | High — requires K8s cluster |
| Target audience | Developers, sysadmins | DevOps, enterprise K8s teams |
| Production viability | Yes (2024-2025 features) | Yes (native) |
| Multi-service (3 DBs + app) | Excellent | Excellent |
| Secrets management | Docker secrets + .env | Kubernetes Secrets + Vault |
| Upgrade path | Manual / Watchtower | Helm upgrade, rollback |

**Recommendation:** Ship **both**, Docker Compose as primary (v1.0), Helm chart as premium/enterprise add-on (v1.1+).

### Production Docker Compose Template (for SOUL)

Key patterns based on 2025 best practices:

```yaml
services:
  soul-server:
    image: ghcr.io/your-org/soul-memory:${VERSION:-latest}
    depends_on:
      postgres:
        condition: service_healthy
      neo4j:
        condition: service_healthy
      qdrant:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped
    secrets:
      - postgres_password
      - neo4j_password
    deploy:
      resources:
        limits:
          memory: 512M

  postgres:
    image: pgvector/pgvector:pg16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U soul"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  neo4j:
    image: neo4j:5-community
    healthcheck:
      test: ["CMD", "neo4j", "status"]
      interval: 30s
      timeout: 10s
      retries: 5
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    restart: unless-stopped

secrets:
  postgres_password:
    environment: POSTGRES_PASSWORD
  neo4j_password:
    environment: NEO4J_PASSWORD

volumes:
  postgres_data:
  neo4j_data:
  qdrant_data:
```

**Critical production checklist:**
- [ ] `depends_on` with `condition: service_healthy` (not just `condition: service_started`)
- [ ] All secrets via Docker secrets or env vars — never hardcoded in compose file
- [ ] `.env` file excluded from git (`.gitignore`)
- [ ] Resource limits on every service
- [ ] Named volumes for all persistent data
- [ ] `restart: unless-stopped` on all services
- [ ] `/.well-known/mcp/server.json` served by soul-server

**Alternative: Docker Kanvas (2026)**
Docker launched Kanvas in Jan 2026 — bridges Docker Compose → Kubernetes manifests automatically. Worth monitoring as a path from Compose to K8s without writing Helm charts manually.

### Initialization Order Problem

PostgreSQL, Neo4j, and Qdrant all have different startup times. The `service_healthy` condition + `start_period` combination handles this, but SOUL server should also implement retry logic on DB connection (exponential backoff, 10 retries) — the healthcheck alone is not sufficient for all race conditions.

---

## 6. HIPAA Compliance for SOUL

### What HIPAA Actually Requires (Technical Safeguards — 45 CFR §164.312)

The January 2025 HHS proposed rule update (first update in 20 years) removes the "required vs addressable" distinction. **Everything below is now effectively required, enforcement 2026-2027.**

#### Access Control (§164.312(a)(1))
- **Unique user identification** (Required): Every agent/tenant gets a unique identifier. No shared credentials.
- **Automatic logoff** (Addressable → now Required): Terminate sessions after inactivity. For SOUL: JWT expiry + refresh token rotation.
- **Encryption/decryption**: Implement AES-256 at rest, TLS 1.3 in transit.

#### Audit Controls (§164.312(b))
- Record all access to ePHI — who accessed what, when, from where
- SOUL already has `event_log_append` — this needs to be immutable and tenant-scoped
- Audit logs must be separate from application logs and protected from modification
- Retention: minimum 6 years

#### Integrity (§164.312(c))
- Protect ePHI from improper alteration/destruction
- PostgreSQL WAL + point-in-time recovery
- Qdrant snapshots on schedule
- Neo4j online backup

#### Transmission Security (§164.312(e))
- TLS 1.3 for all in-transit data (MCP server ↔ client, server ↔ databases)
- Internal Docker network traffic between containers is not encrypted by default — add TLS or use private overlay network

### Encryption Standards Required by HHS
- **At rest:** NIST SP 800-111 — AES-256 for databases, filesystem encryption
- **In transit:** NIST SP 800-52 — TLS 1.3 (TLS 1.2 acceptable with restrictions)
- **Key management:** Keys must be stored separately from encrypted data

### Business Associate Agreement (BAA)

If SOUL is sold to a healthcare organization that stores PHI:
- SOUL vendor (William/GTL) becomes a **Business Associate**
- A signed BAA must exist before any PHI is processed
- BAA must specify permitted uses, safeguards, breach notification procedures
- **This is a contract requirement, not a technical one** — but it blocks sales without it

### Re-identification Risk (AI-specific, 2025 update)

HIPAA de-identification standard (Safe Harbor or Expert Determination) was designed pre-AI. Current guidance:
- Even "de-identified" data in SOUL's memory/connectome could be re-identified by AI analysis against public data
- Safe approach: treat all patient-related memory as PHI regardless of de-identification status
- The `secret_scan` tool in SOUL is a step in the right direction — it needs to catch PHI patterns (names, DOBs, SSNs, MRNs)

### HIPAA-Compliant Architecture Changes Needed for SOUL

| Gap | Required Change |
|---|---|
| No encryption at rest | PostgreSQL: `pgcrypto` for sensitive columns; or full-disk encryption at OS layer |
| No TLS between containers | Docker internal network → TLS certs, or mTLS between services |
| Audit log mutable | Append-only `event_log` table with `REVOKE UPDATE, DELETE ON event_log FROM soul_app` |
| No BAA template | Create standard BAA template for healthcare customers |
| `secret_scan` basic | Extend to detect PHI patterns: names, DOBs, MRNs, diagnosis codes |
| No retention policy | Implement TTL on non-medical memories; configurable retention per tenant |
| Annual cap on violations | Civil penalties now exceed $2M/year for repeated violations |

### Practical Tier Approach

**Tier 1 — Standard (non-healthcare):** No HIPAA controls. Ship v1.0 without medical compliance.

**Tier 2 — HIPAA-Ready:** PostgreSQL column encryption, TLS everywhere, immutable audit log, BAA template, PHI detection in `secret_scan`. Charge premium (standard practice: 2-3x base price for HIPAA tier).

**Tier 3 — HIPAA-Certified:** Third-party audit, SOC2 Type 2 (Zep does this). Long-term target.

---

## Summary — Prioritized Action Items

### Immediate (blocks v1.0)
1. **Migrate MCP transport to Streamable HTTP** — SSE deprecated April 1, 2026
2. **Add `/.well-known/mcp/server.json`** — required for MCP Registry auto-discovery
3. **Implement OAuth 2.1 middleware** in FastMCP — without auth, can't be multi-tenant

### Short-term (required for productization)
4. **Qdrant: add `is_tenant: True` to payload index** — enables tiered multitenancy
5. **PostgreSQL RLS** on all ePHI-adjacent tables — single schema, all tenants
6. **Neo4j: add `tenant_id` to all nodes** — enforced at app layer (Community edition)
7. **Modularize monolith** with src layout + shim backward compat — enables separate versioning
8. **Docker Compose production hardening** — healthchecks, secrets, resource limits

### Medium-term (v1.1 / enterprise tier)
9. **Immutable audit log** — append-only `event_log`, 6-year retention
10. **HIPAA Tier 2:** column encryption, TLS between containers, PHI detection
11. **BAA template** — legal document, not code, but blocks healthcare sales
12. **Helm chart** — for enterprise K8s deployments
13. **`/.well-known/mcp/server-card.json`** (SEP-1649) + SEP-1960 both

---

## Sources

- [MCP 2026 Roadmap — modelcontextprotocol.io](https://modelcontextprotocol.io/development/roadmap)
- [2026 MCP Roadmap blog post](http://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [MCP Enterprise Readiness — WorkOS](https://workos.com/blog/2026-mcp-roadmap-enterprise-readiness)
- [SSE vs Streamable HTTP — Bright Data](https://brightdata.com/blog/ai/sse-vs-streamable-http)
- [MCP Transport Future — modelcontextprotocol.io blog](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/)
- [Qdrant 1.16 Tiered Multitenancy](https://qdrant.tech/blog/qdrant-1.16.x/)
- [Qdrant Multitenancy docs](https://qdrant.tech/documentation/manage-data/multitenancy/)
- [Qdrant Tiered MT announcement — BusinessWire](https://www.businesswire.com/news/home/20251119343840/en/Qdrant-Introduces-Tiered-Multitenancy-to-Eliminate-Noisy-Neighbor-Problems-in-Vector-Search)
- [pgvector multi-tenant RLS — simplyblock](https://www.simplyblock.io/blog/underated-postgres-multi-tenancy-with-row-level-security/)
- [Multi-tenant vector search Aurora PostgreSQL — AWS](https://aws.amazon.com/blogs/database/multi-tenant-vector-search-with-amazon-aurora-postgresql-and-amazon-bedrock-knowledge-bases/)
- [Neo4j multi-tenancy 4.0 — Adam Cowley](https://adamcowley.co.uk/posts/multi-tenancy-neo4j-40/)
- [Best AI Memory Frameworks 2026 — atlan.com](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)
- [Mem0 vs Zep vs LangMem 2026 — DEV Community](https://dev.to/anajuliabit/mem0-vs-zep-vs-langmem-vs-memoclaw-ai-agent-memory-comparison-2026-1l1k)
- [State of AI Agent Memory 2026 — mem0.ai](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Mem0 self-hosting Docker guide](https://mem0.ai/blog/self-host-mem0-docker)
- [Zep temporal knowledge graph — arXiv 2501.13956](https://arxiv.org/abs/2501.13956)
- [Graphiti — github.com/getzep/graphiti](https://github.com/getzep/graphiti)
- [FastMCP rate limiting middleware](https://gofastmcp.com/python-sdk/fastmcp-server-middleware-rate_limiting)
- [FastMCP 2.9 middleware — jlowin.dev](https://www.jlowin.dev/blog/fastmcp-2-9-middleware)
- [Building production-ready MCP servers — thinhdanggroup](https://thinhdanggroup.github.io/mcp-production-ready/)
- [MCP Server Discovery .well-known — ekamoira.com](https://www.ekamoira.com/blog/mcp-server-discovery-implement-well-known-mcp-json-2026-guide)
- [MCP Registry auto-discovery — Replicate](https://replicate.com/changelog/2026-02-10-mcp-server-auto-discovery)
- [MCP OAuth2 with Entra ID — GitHub](https://github.com/sean-tate/fastmcp-python-oauth2-with-entra-id)
- [MCP Server Monetization 2026 — DEV Community](https://dev.to/namel/mcp-server-monetization-2026-1p2j)
- [Python modular monolith — breadcrumbscollector.tech](https://breadcrumbscollector.tech/modular-monolith-in-python/)
- [Python packaging best practices 2026 — dasroot.net](https://dasroot.net/posts/2026/01/python-packaging-best-practices-setuptools-poetry-hatch/)
- [src layout vs flat layout — Python Packaging User Guide](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [Docker Compose production 2025 — Dokploy](https://dokploy.com/blog/how-to-deploy-apps-with-docker-compose-in-2025)
- [Docker Kanvas vs Helm — InfoQ](https://www.infoq.com/news/2026/01/docker-kanvas-cloud-deployment/)
- [HIPAA Technical Safeguards — HHS.gov](https://www.hhs.gov/sites/default/files/ocr/privacy/hipaa/administrative/securityrule/techsafeguards.pdf)
- [HIPAA Encryption Requirements 2026 — hipaajournal.com](https://www.hipaajournal.com/hipaa-encryption-requirements/)
- [HIPAA Compliant AI 2026 — getprosper.ai](https://www.getprosper.ai/blog/hipaa-compliant-ai-guide-healthcare)
- [Healthcare AI Regulation 2026 — jimersonfirm.com](https://www.jimersonfirm.com/blog/2026/02/healthcare-ai-regulation-2025-new-compliance-requirements-every-provider-must-know/)
- [RLS in vector DBs for RAG — Medium](https://medium.com/@michael.hannecke/implementing-row-level-security-in-vector-dbs-for-rag-applications-fdbccb63d464)
