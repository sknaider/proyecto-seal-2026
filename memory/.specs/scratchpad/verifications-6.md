# SOUL Memory System — Verification Rubrics
**Date:** 2026-04-07
**Author:** Claude Code (Opus 4.6) — QA Engineer
**Inputs:** decomposition-4.md (30 steps), architecture-3.md (architecture), business-analysis-2c.md (acceptance criteria)
**Purpose:** Exhaustive, automatable verification rubrics for every phase gate, security boundary, performance target, and launch requirement

---

## 1. Phase Gate Verifications

Each gate is a hard stop. Do not proceed to the next phase until the gate passes. If a gate fails, the fail action is mandatory before retrying.

---

#### Gate 0: Prerequisites Complete (Steps 1-4)
**Precondition:** `mcp_server_v2.py` monolith is running and all 61 tests pass on current codebase (baseline snapshot taken).

**Verification steps:**
1. `grep -rn "seal2026soul" src/ soul/ *.py` returns 0 matches (no hardcoded Neo4j password in source)
2. `grep -rn "seal_memory_2026" src/ soul/ *.py` returns 0 matches (no hardcoded PG password in source)
3. `.env.example` exists and contains at least 15 `SOUL_*` variables with descriptions
4. `.env` exists, is in `.gitignore`, and contains the current Team SEAL values
5. `python -c "from soul.core.config import get_config; c = get_config(); assert c.pg_host"` succeeds
6. `python -c "from soul.core.helpers import temporal_decay_score, classify_emotion, _extract_entities"` succeeds
7. `grep -c "def temporal_decay_score" mcp_server_v2.py` returns 0 (function moved out of monolith)
8. `_reflexion_lesson` does NOT appear in MCP tool listing: `python -c "from mcp_server_v2 import mcp; assert '_reflexion_lesson' not in [t.name for t in mcp.list_tools()]"`
9. `poetry install` succeeds without errors
10. `poetry build` produces a `.whl` file in `dist/`
11. `python mcp_server_v2.py` launches the server (backward compatibility shim)
12. `python -m soul.core.config` does not crash
13. All 61 existing tests pass: `poetry run pytest --tb=short -q` exits 0
14. `ada_boot_test.py` returns 10/10 ALMA CONECTADA

**Pass criteria:** ALL 14 checks pass. Zero exceptions.

**Fail action:** Identify which check failed. If checks 1-2 fail, secrets are still in source — fix immediately (security blocker). If checks 13-14 fail, regression introduced — `git diff` the failing test, fix before proceeding. Do NOT proceed with module extraction if the foundation is broken.

---

#### Gate 1: Easy Extraction Complete (Steps 5-8)
**Precondition:** Gate 0 passed. `soul/core/` package is installed and importable.

**Verification steps:**
1. Tool count verification — 21 tools extracted across 4 modules:
   - `soul.rules`: `rule_set`, `rule_list`, `event_log_append`, `event_log_query`, `working_state_get`, `working_state_update`, `secret_scan` (7 tools)
   - `soul.procedures`: `reasoning_trace_store`, `reasoning_trace_update`, `reasoning_trace_search`, `procedure_store`, `procedure_search`, `procedure_update` (6 tools)
   - `soul.sessions`: `session_save`, `session_recall`, `session_list`, `session_distill`, `session_distill_bulk` (5 tools)
   - `soul.peers`: `peer_model_update`, `peer_model_query` (2 tools, if separated) or in `soul.identity` (per architecture-3.md)
2. All 21 extracted tools appear in `mcp.list_tools()` output
3. `_reflexion_lesson` does NOT appear in `mcp.list_tools()` but IS importable: `from soul.rules.reflexion import _reflexion_lesson`
4. `grep -c "def rule_set\|def rule_list\|def event_log_append\|def event_log_query\|def working_state_get\|def working_state_update\|def secret_scan" mcp_server_v2.py` returns 0 (definitions removed from monolith)
5. Same grep pattern for procedures and sessions tools returns 0 in `mcp_server_v2.py`
6. Each extracted module has a `register_tools(mcp)` function that registers its tools
7. `session_distill` with Ollama running returns a distilled summary
8. `session_distill` with `SOUL_OLLAMA_ENABLED=false` returns graceful degradation (raw session, no crash)
9. `reasoning_trace_search` returns similarity-ranked results for a known stored trace
10. All 61 existing tests pass
11. New module-specific tests exist and pass: `test_rules.py`, `test_procedures.py`, `test_sessions.py`, `test_peers.py`
12. `ada_boot_test.py` returns 10/10

**Pass criteria:** ALL 12 checks pass.

**Fail action:** If tool listing is wrong (check 2), the `register_tools` pattern is broken — fix the registration mechanism before extracting harder modules. If tests fail (check 10), a regression was introduced during extraction — diff the extracted code against the original monolith function, fix the divergence.

---

#### Gate 2: Medium Extraction Complete (Steps 9-12)
**Precondition:** Gate 1 passed. Easy-tier modules working independently.

**Verification steps:**
1. Tool count — 37 additional tools extracted:
   - `soul.identity`: `boot_context`, `soul_check`, `soul_snapshot`, `self_reflect`, `ocean_auto_calibrate`, `ocean_state_machine`, `inner_thoughts`, `active_recall`, `peer_model_update`, `peer_model_query` (10 tools)
   - `soul.instincts`: `instinct_create`, `instinct_list`, `instinct_search`, `instinct_activate`, `instinct_promote`, `instinct_evolve`, `instinct_consolidate`, `observation_analyze` (8 tools)
   - `soul.sleep`: `sleep_gate`, `sleep_gate_mood_retrieval`, `microcompact_text`, `microcompact_stats` (4 tools)
   - `soul.graph`: `connectome_build`, `connectome_status`, `connectome_ltp`, `soul_activate`, `soul_synthesize`, `connectome_bitemporal`, `connectome_bitemporal_query`, `connectome_causal`, `connectome_invalidate_edge`, `connectome_entity`, `connectome_entity_query`, `connectome_smart_route`, `temporal_graph_build`, `temporal_query` (14 tools) + `brain_health_report` if grouped here
2. `boot_context` for agent "ADA" returns complete context: OCEAN scores, identity text, recent memories, active instincts
3. `ada_boot_test.py` returns 10/10 ALMA CONECTADA (critical — boot_context is the most important tool)
4. `ocean_auto_calibrate` with boundary values does not exceed `OCEAN_SESSION_CAP` deltas
5. Instinct full lifecycle test: `instinct_create` -> `instinct_activate` -> `instinct_evolve` -> `instinct_promote` -> `instinct_consolidate` — all succeed without error
6. `instinct_activate` triggers `_reflexion_lesson` asynchronously (verify via event_log or memory store side effect)
7. `sleep_gate_mood_retrieval` uses `get_embedding()` from `soul.core.embeddings` (no direct `SentenceTransformer` import in function body): `grep -n "SentenceTransformer" src/soul/sleep/tools.py` returns 0
8. All 15 graph tools return `{"status": "unavailable", "reason": "..."}` when `SOUL_LITE_MODE=true` (no crash)
9. `connectome_build` creates edges in Neo4j when LITE_MODE is false
10. `soul_activate` performs spreading activation and returns weighted results
11. No hardcoded filesystem paths in extracted code: `grep -rn "/home/dadito" src/soul/` returns 0
12. Import dependency rule enforced: `soul.core` has zero imports from other `soul.*` modules
13. All 61 existing tests pass
14. New tests: `test_identity.py`, `test_instincts.py`, `test_sleep.py`, `test_graph.py` exist and pass

**Pass criteria:** ALL 14 checks pass.

**Fail action:** If check 2 or 3 fail (boot_context broken), this is a P0 blocker — boot_context is called at every agent startup. Revert to the pre-extraction state for identity module and debug. If check 8 fails (Soul Lite crash), add the Lite guard to the offending tool before proceeding. Do NOT start Phase 3 with a broken boot_context.

---

#### Gate 3: Hard Extraction Complete (Steps 13-15)
**Precondition:** Gate 2 passed. All modules except `memory/` and `dmem/` are extracted.

**Verification steps:**
1. Total tool count from `mcp.list_tools()` is 74+ (all tools minus `_reflexion_lesson`)
2. `mcp_server_v2.py` is now a shim: file is under 20 lines, contains only `from soul.server import main; main()`
3. `python -m soul.server` starts the MCP server and lists all tools
4. `python mcp_server_v2.py` starts the same server (backward compat)
5. `memory_store` with `imp=9` roundtrip test:
   - Store a memory with importance 9
   - Verify PG row exists
   - Verify Qdrant point exists (if not Lite mode)
   - Verify Neo4j node exists (if not Lite mode)
   - Verify connectome edges created
   - Verify auto-broadcast fired (if scope allows)
   - Verify instinct auto-activation fired
6. `memory_search` returns the stored memory ranked by semantic similarity
7. `memory_hybrid_search` returns combined vector + BM25 results
8. `memory_invalidate` cascades to Qdrant and Neo4j (verify point/node removed)
9. Soul Lite mode: `memory_store` + `memory_search` work with PG+pgvector only, skip Neo4j/Qdrant side effects
10. `dmem_gate` blocks duplicate/near-duplicate memories
11. `ace_curator` in `dry_run=true` returns analysis without side effects
12. `brain_health_report` reports status of all three databases (or marks unavailable in Lite mode)
13. Startup time: `time python -m soul.server --check` completes within 2x of original monolith baseline
14. All 61 existing tests pass
15. New tests: `test_memory.py`, `test_dmem.py` exist and pass
16. `ada_boot_test.py` returns 10/10

**Pass criteria:** ALL 16 checks pass.

**Fail action:** If check 2 fails (monolith not fully shimmed), there are tool definitions still in `mcp_server_v2.py` — extract them. If check 5 fails (memory_store side effects broken), the hook chain wiring in `server.py` is incorrect — debug the `on_store_hooks` registration. If checks 14/16 fail, regression during the hardest extraction — use `git bisect` between Step 13 commits to find the breaking change.

---

#### Gate 4: Product Infrastructure Complete (Steps 16-20)
**Precondition:** Gate 3 passed. All modules extracted, server.py is the sole entrypoint.

**Verification steps:**
1. Auth — valid API key:
   ```bash
   curl -H "Authorization: Bearer sk-test-valid-key" http://localhost:8000/mcp -d '{"method":"memory_search","params":{"query":"test"}}' 
   ```
   Returns 200 with results scoped to the key's tenant
2. Auth — missing key: same request without header returns 401
3. Auth — invalid key: request with `Bearer sk-invalid` returns 403 and event logged in `event_log`
4. Auth — key rotation: generate new key, old key still works for 24h, then returns 403
5. Auth — bypass: `SOUL_AUTH_ENABLED=false` allows all requests without auth header
6. Multi-tenant isolation (PG): Tenant A stores memory "Secret Alpha". Tenant B searches "Secret Alpha". Returns 0 results.
7. Multi-tenant isolation (Qdrant): same test via vector similarity search. Returns 0 results for Tenant B.
8. Multi-tenant isolation (Neo4j): Tenant B calls `connectome_status`. Edge count does NOT include Tenant A edges.
9. SQL injection: Tenant B sends `"'; SELECT * FROM memories WHERE tenant_id='alpha-id'--"` as search query. Returns 0 results, no traceback.
10. Default tenant: Team SEAL data has `tenant_id = '00000000-0000-0000-0000-000000000000'` and all tools work without explicit tenant config
11. Migration idempotency: `python migrations/apply.py` run twice produces no errors and no duplicate schema objects
12. All tables have `tenant_id` column: verify with `SELECT column_name FROM information_schema.columns WHERE table_name='memories' AND column_name='tenant_id'` (repeat for all tenant-scoped tables)
13. RLS is active: `SELECT polname FROM pg_policies WHERE tablename='memories'` returns at least one policy
14. Docker Compose Lite: `docker compose up -d` — all containers healthy within 5 minutes
15. Docker Compose Full: `docker compose --profile full up -d` — all containers healthy within 5 minutes
16. `boot_context` works from Docker deployment
17. `.env.example` has a comment for every `SOUL_*` variable
18. Missing `SOUL_PG_PASSWORD` with default `changeme` prints WARNING at startup (not crash)
19. Invalid `SOUL_PG_PORT=99999` raises clear error at startup
20. All 61 existing tests pass (run under default tenant)
21. `ada_boot_test.py` returns 10/10

**Pass criteria:** ALL 21 checks pass.

**Fail action:** If checks 6-9 fail (tenant isolation broken), this is a CRITICAL SECURITY BLOCKER. Stop all other work. Audit every database query for missing tenant_id filter. Run the negative test: disable RLS, verify cross-tenant query WOULD return results (proving RLS is the barrier). If check 14-15 fail (Docker broken), check container logs for missing env vars or port conflicts. If check 10 fails (Team SEAL broken), the default tenant migration failed — restore from backup and re-run migration.

---

#### Gate 5: Product Polish Complete (Steps 21-26)
**Precondition:** Gate 4 passed. Auth, multi-tenancy, Docker all working.

**Verification steps:**
1. Every tool has a docstring with: description, parameters, return value. Verify: `python scripts/generate_docs.py --check` exits 0 (flags missing docstrings)
2. `docs/api-reference.md` exists and lists all 74+ tools
3. `docs/quickstart.md` exists and the steps are executable (manual test: follow them on a clean machine)
4. `docs/deployment.md` covers Soul Lite, Soul Full, and Enterprise configurations
5. CI pipeline: push a PR with a lint error, verify CI catches it within 5 minutes
6. CI pipeline: push a clean PR, verify lint + type check + test all pass
7. `GET /health` returns JSON with status for PG, Qdrant (if not Lite), Neo4j (if not Lite), Embeddings
8. `GET /health` response time < 2 seconds
9. `GET /metrics` returns Prometheus-compatible text with at least these metrics: `soul_memory_store_total`, `soul_memory_search_total`, `soul_memory_search_latency_ms`, `soul_connectome_edges_total`, `soul_instinct_activations_total`, `soul_active_tenants`
10. Metrics increment: call `memory_store`, then `GET /metrics`, verify `soul_memory_store_total` increased by 1
11. `GET /ready` returns 200 when server is ready, 503 during startup
12. `soul-backup create --output /tmp/test-backup.tar.gz` produces archive with `manifest.json`
13. Wipe test databases, `soul-backup restore --input /tmp/test-backup.tar.gz`, verify memory count matches manifest
14. Per-tenant backup: `soul-backup create --tenant test-tenant --output /tmp/tenant-backup.tar.gz` only includes that tenant's data
15. Restore does not cross-contaminate: restore tenant A backup into environment where tenant B exists, verify tenant B data unchanged
16. HIPAA audit log: enable `SOUL_FEATURE_HIPAA_AUDIT=true`, call `memory_store`, verify audit_log row exists with tenant_id, agent_id, action, timestamp
17. Audit log immutability: attempt `UPDATE audit_log SET action='tampered'` with app DB role — verify it fails (permission denied)
18. PHI scanner: store memory containing "SSN: 123-45-6789", verify PHI detection logged
19. Field-level encryption (if enabled): inspect raw PG row, verify `content` column is ciphertext, verify `memory_search` returns decrypted plaintext
20. Code coverage: `poetry run pytest --cov=soul --cov-report=term` shows >= 60%
21. All 61 existing tests pass

**Pass criteria:** ALL 21 checks pass.

**Fail action:** If health/metrics endpoints fail (checks 7-11), fix before launch — monitoring is not optional for a product. If backup/restore fails (checks 12-15), the data integrity story is broken — debug the backup pipeline. If HIPAA features fail (checks 16-19), disable the feature flag and document as "coming in v1.1" rather than shipping broken HIPAA claims. Coverage below 60% (check 20) — prioritize tests for `memory_store`, `memory_search`, `boot_context`, and `auth/middleware.py`.

---

#### Gate 6: Launch Ready (Steps 27-30)
**Precondition:** Gate 5 passed. Product is polished and documented.

**Verification steps:**
1. `pip install soul-memory` from PyPI succeeds in a clean venv
2. `python -m soul.server` starts after pip install (no repo clone needed)
3. Package metadata on PyPI: description, homepage URL, classifiers are correct
4. `docker pull ghcr.io/sknaider/soul-memory:latest` succeeds
5. Docker image starts and `GET /health` returns healthy
6. Multi-arch: image runs on both amd64 and arm64
7. `README.md` renders correctly on GitHub (check rendered markdown)
8. Architecture diagram in README is readable
9. Quick start section in README matches actual deployment steps (execute them)
10. Feature comparison table (SOUL vs Mem0 vs Zep vs LangMem) is present and accurate
11. `LICENSE` file exists (Apache 2.0)
12. `LICENSE-COMMERCIAL` file exists (BSL or commercial)
13. `.github/ISSUE_TEMPLATE/bug_report.yml` renders correctly on GitHub
14. `.github/ISSUE_TEMPLATE/feature_request.yml` renders correctly on GitHub
15. `CONTRIBUTING.md` explains dev setup, testing, PR process
16. `CODEOWNERS` file routes PRs to William (`@sknaider`)
17. `CHANGELOG.md` has v1.0.0 entry with feature list
18. GitHub Actions CI runs on push to main and on PRs

**Pass criteria:** ALL 18 checks pass.

**Fail action:** If PyPI or Docker publishing fails (checks 1-6), fix the CI release pipeline — these are the primary distribution channels. If README is broken (checks 7-10), fix before public announcement — first impressions matter. License issues (checks 11-12) require legal review before launch.

---

## 2. Security Verification Rubric

Each test must be run after every phase gate and before any release. Automate as `tests/test_security.py`.

### 2.1 Multi-Tenant Isolation

| Test ID | Test Description | Method | Expected Result | Automated? |
|---------|-----------------|--------|-----------------|------------|
| SEC-01 | Tenant A cannot read Tenant B memories via `memory_search` | Store as A, search as B | 0 results | Yes |
| SEC-02 | Tenant A cannot read Tenant B memories via `memory_list` | List all as B | 0 memories from A | Yes |
| SEC-03 | Tenant A cannot read Tenant B memories via `memory_hybrid_search` | Hybrid search as B | 0 results from A | Yes |
| SEC-04 | Tenant A cannot read Tenant B instincts via `instinct_list` | List as B | 0 instincts from A | Yes |
| SEC-05 | Tenant A cannot read Tenant B sessions via `session_list` | List as B | 0 sessions from A | Yes |
| SEC-06 | Tenant A cannot read Tenant B connectome via `connectome_status` | Status as B | 0 edges from A | Yes |
| SEC-07 | Tenant A cannot read Tenant B broadcasts via `memory_broadcast_read` | Read as B | 0 broadcasts from A | Yes |
| SEC-08 | Tenant A cannot read Tenant B rules via `rule_list` | List as B | 0 rules from A | Yes |
| SEC-09 | Tenant A cannot read Tenant B reasoning traces via `reasoning_trace_search` | Search as B | 0 traces from A | Yes |
| SEC-10 | Tenant A cannot read Tenant B event log via `event_log_query` | Query as B | 0 events from A | Yes |

### 2.2 Auth Bypass

| Test ID | Test Description | Method | Expected Result | Automated? |
|---------|-----------------|--------|-----------------|------------|
| SEC-11 | Unauthenticated request rejected | No auth header, call any tool | 401 | Yes |
| SEC-12 | Invalid API key rejected | `Bearer sk-invalid`, call any tool | 403 + event logged | Yes |
| SEC-13 | Expired API key rejected | Use key past `expires_at` | 403 | Yes |
| SEC-14 | Readonly role cannot write | Readonly key, call `memory_store` | 403 | Yes |
| SEC-15 | Agent role cannot manage tenants | Agent key, call tenant management | 403 | Yes |
| SEC-16 | JWT with tampered signature rejected | Modify JWT payload, keep old signature | 403 | Yes |
| SEC-17 | JWT with expired `exp` claim rejected | Use expired JWT | 401 | Yes |

### 2.3 Secret Leakage

| Test ID | Test Description | Method | Expected Result | Automated? |
|---------|-----------------|--------|-----------------|------------|
| SEC-18 | No credentials in source code | `grep -rn "seal2026soul\|seal_memory_2026" src/ soul/ *.py` | 0 matches | Yes |
| SEC-19 | No credentials in git history | `git log -p --all -S "seal2026soul"` after cleanup | 0 matches (or only in removal commit) | Yes — CI |
| SEC-20 | `.env` is in `.gitignore` | `grep "^\.env$" .gitignore` | Match found | Yes |
| SEC-21 | `secret_scan` tool detects planted secrets | Store memory with "password=abc123" | Warning returned | Yes |
| SEC-22 | API keys hashed in DB | `SELECT key_hash FROM api_keys LIMIT 1` | bcrypt hash, not plaintext | Yes |
| SEC-23 | Error responses do not leak stack traces | Send malformed request | Error message without file paths or line numbers | Yes |

### 2.4 HIPAA Audit Log

| Test ID | Test Description | Method | Expected Result | Automated? |
|---------|-----------------|--------|-----------------|------------|
| SEC-24 | All tool calls logged to audit_log | Enable HIPAA, call 5 different tools | 5 audit_log rows | Yes |
| SEC-25 | Audit log contains required fields | Check any audit row | tenant_id, agent_id, tool_name, timestamp, status present | Yes |
| SEC-26 | Audit log is append-only | `UPDATE audit_log SET status='x'` with app role | Permission denied error | Yes |
| SEC-27 | Audit log DELETE blocked | `DELETE FROM audit_log` with app role | Permission denied error | Yes |
| SEC-28 | PHI detected and logged | Store memory with SSN pattern | PHI detection entry in audit_log | Yes |
| SEC-29 | Audit log survives backup/restore | Backup, restore, query audit_log | All entries preserved | Yes |

### 2.5 SQL Injection

| Test ID | Test Description | Method | Expected Result | Automated? |
|---------|-----------------|--------|-----------------|------------|
| SEC-30 | SQL injection in memory_search query | `"'; DROP TABLE memories;--"` | 0 results, table intact | Yes |
| SEC-31 | SQL injection in tenant_id parameter | `"'; SET LOCAL app.tenant_id='other'--"` | Request fails or returns 0 | Yes |
| SEC-32 | SQL injection in memory_store content | Store with SQL injection payload | Memory stored literally, no execution | Yes |
| SEC-33 | Cypher injection in Neo4j queries | `"' DETACH DELETE n//"` in search | No nodes deleted | Yes |

---

## 3. Performance Verification Rubric

Run after Phase 3 (all modules extracted) and before every release. Automate as `tests/test_performance.py` using `pytest-benchmark` or a custom harness.

### 3.1 Latency Targets

| Metric | Target | Measurement Method | Sample Size | Environment |
|--------|--------|--------------------|-------------|-------------|
| `memory_store` p95 latency | < 500ms | Time from request to response | 100 sequential stores | Single tenant, PG local |
| `memory_search` p95 latency (10K memories) | < 200ms | Time from request to response | 50 searches against 10K corpus | Single tenant, PG + pgvector |
| `memory_search` p95 latency (100K memories) | < 500ms | Time from request to response | 50 searches against 100K corpus | Single tenant, PG + pgvector |
| `memory_hybrid_search` p95 latency | < 400ms | Time from request to response | 50 searches | Single tenant, PG + Qdrant |
| `boot_context` latency | < 2s | Time from request to response | 10 boot calls | Agent with 1K memories, 50 instincts |
| `boot_context` latency (fresh agent) | < 500ms | Time from request to response | 10 boot calls | New agent, 0 memories |
| `connectome_build` for 1K memories | < 30s | Time from request to response | 3 builds | Single tenant, Neo4j local |
| `soul_activate` (spreading activation) | < 1s | Time from request to response | 20 activations | 10K edges in connectome |
| `instinct_search` p95 latency | < 200ms | Time from request to response | 50 searches | 500 instincts |
| `GET /health` latency | < 2s | HTTP response time | 100 requests | All services running |
| `GET /metrics` latency | < 100ms | HTTP response time | 100 requests | Accumulated metrics only, no DB query |

### 3.2 Throughput Targets

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Concurrent `memory_store` | 10 stores/sec sustained | 10 parallel clients, 100 stores each |
| Concurrent `memory_search` | 50 searches/sec sustained | 10 parallel clients, 100 searches each |
| Concurrent tenants | 10 tenants, 5 requests each simultaneously, 0 errors | Parallel test harness |
| Concurrent `boot_context` | 5 boots in parallel, all complete < 5s | Parallel test harness |

### 3.3 Resource Targets

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Server memory (idle) | < 1GB RSS | `ps aux` after startup, before any requests |
| Server memory (under load) | < 2GB RSS | During concurrent throughput test |
| Docker Compose total RAM (Lite) | < 2GB | `docker stats` during load test |
| Docker Compose total RAM (Full) | < 6GB | `docker stats` during load test |
| Startup time (server only) | < 15s | Time from `python -m soul.server` to first tool available |
| Startup time (Docker Compose Lite) | < 60s | Time from `docker compose up` to all healthy |
| Startup time (Docker Compose Full) | < 120s | Time from `docker compose --profile full up` to all healthy |
| Docker image size (slim) | < 500MB | `docker images` |
| Docker image size (with model) | < 2.5GB | `docker images` |

### 3.4 Performance Test Script Template

```bash
#!/bin/bash
# Run as: ./perf_test.sh <base_url> <api_key>
BASE=$1; KEY=$2

echo "=== memory_store latency (100 stores) ==="
for i in $(seq 1 100); do
  START=$(date +%s%N)
  curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $KEY" \
    -X POST "$BASE/mcp" -d "{\"method\":\"memory_store\",\"params\":{\"agent\":\"perf-test\",\"content\":\"Test memory $i\",\"category\":\"test\",\"importance\":5}}"
  END=$(date +%s%N)
  echo "$((($END-$START)/1000000))ms"
done | sort -n | tail -5  # Show p95-p100

echo "=== memory_search latency (50 searches) ==="
for i in $(seq 1 50); do
  START=$(date +%s%N)
  curl -s -o /dev/null -H "Authorization: Bearer $KEY" \
    -X POST "$BASE/mcp" -d "{\"method\":\"memory_search\",\"params\":{\"agent\":\"perf-test\",\"query\":\"test memory search\",\"limit\":10}}"
  END=$(date +%s%N)
  echo "$((($END-$START)/1000000))ms"
done | sort -n | tail -3  # Show p95-p100

echo "=== boot_context latency (10 boots) ==="
for i in $(seq 1 10); do
  START=$(date +%s%N)
  curl -s -o /dev/null -H "Authorization: Bearer $KEY" \
    -X POST "$BASE/mcp" -d "{\"method\":\"boot_context\",\"params\":{\"agent_name\":\"perf-test\"}}"
  END=$(date +%s%N)
  echo "$((($END-$START)/1000000))ms"
done | sort -n
```

---

## 4. Backward Compatibility Verification

Run after EVERY step, not just at phase gates. Automate as part of the CI `make test` target.

### 4.1 Test Suite Continuity

| Check | Command | Expected | When |
|-------|---------|----------|------|
| All 61 existing tests pass | `poetry run pytest tests/test_integration.py -q` | 61 passed, 0 failed | Every commit |
| No test removed or skipped | `grep -c "def test_" tests/test_integration.py` | >= 61 | Every commit |
| `ada_boot_test.py` 10/10 | `python ada_boot_test.py` | "10/10 ALMA CONECTADA" | Every phase gate |

### 4.2 Team SEAL Operational Continuity

| Check | Method | Expected | When |
|-------|--------|----------|------|
| `mcp_server_v2.py` launches server | `python mcp_server_v2.py` (check no crash for 5s) | Server starts, tools registered | Every step that modifies monolith |
| JARVIS can boot | Call `boot_context` with agent "JARVIS" | Returns OCEAN, identity, memories | Every phase gate |
| ADA can boot | Call `boot_context` with agent "ADA" | Returns OCEAN, identity, memories | Every phase gate |
| JARVIS-ADA messaging works | `memory_store` + `memory_broadcast_read` cross-agent | Broadcast received | Phase 3+ |
| DUM (Ollama agent) unaffected | Ollama-dependent tools work when Ollama running | Normal responses | Phase 2+ |

### 4.3 Data Integrity During Migration

| Check | Method | Expected | When |
|-------|--------|----------|------|
| Memory count unchanged | `SELECT COUNT(*) FROM memories` before/after migration | Same count | Step 17 (tenant migration) |
| Instinct count unchanged | `SELECT COUNT(*) FROM instincts` before/after | Same count | Step 17 |
| Session count unchanged | `SELECT COUNT(*) FROM sessions` before/after | Same count | Step 17 |
| Connectome edge count unchanged | `MATCH ()-[r]->() RETURN COUNT(r)` before/after | Same count | Step 17 |
| Qdrant point count unchanged | `client.count("soul_memories")` before/after | Same count | Step 17 |
| All existing data gets default tenant_id | `SELECT COUNT(*) FROM memories WHERE tenant_id = '00000000-0000-0000-0000-000000000000'` | Equals total count | Step 17 |
| No NULL tenant_ids | `SELECT COUNT(*) FROM memories WHERE tenant_id IS NULL` | 0 | Step 17 |
| OCEAN scores preserved | `SELECT * FROM ocean_scores WHERE agent='ADA'` before/after | Identical | Step 17 |

### 4.4 Import Shim Verification

| Check | Command | Expected | When |
|-------|---------|----------|------|
| `mcp_server_v2.py` shim works | `python -c "import mcp_server_v2"` | No ImportError | Steps 5-15 |
| `session_memory.py` shim works | `python -c "import session_memory"` | No ImportError | Step 7 |
| `embeddings.py` importable from old path | `python -c "from embeddings import get_embedding"` | No ImportError | Step 1 |
| `db.py` importable from old path | `python -c "from db import get_pool"` | No ImportError | Step 1 |

---

## 5. Deployment Verification

### 5.1 Docker Compose Smoke Tests

| Test | Command | Pass Criteria | Timeout |
|------|---------|---------------|---------|
| Lite starts | `docker compose up -d && docker compose ps` | All services "healthy" | 60s |
| Full starts | `docker compose --profile full up -d && docker compose ps` | All services "healthy" | 120s |
| Health check responds | `curl -f http://localhost:8000/health` | 200 OK with all components | 30s after healthy |
| Store + search roundtrip | Store a memory via MCP, search for it | Memory found | 60s after healthy |
| boot_context works | Call boot_context for a test agent | Returns context | 30s after healthy |
| Clean shutdown | `docker compose down` | All containers stopped, exit 0 | 30s |
| Data persists across restart | `docker compose down && docker compose up -d` | Previously stored memory still searchable | 120s |
| Volume cleanup | `docker compose down -v` | All volumes removed | 30s |

### 5.2 Soul Lite Mode Verification

| Test | Method | Pass Criteria |
|------|--------|---------------|
| Starts without Neo4j | `SOUL_LITE_MODE=true`, Neo4j not running | Server starts, no crash |
| Starts without Qdrant | `SOUL_LITE_MODE=true`, Qdrant not running | Server starts, no crash |
| memory_store works | Store a memory | PG row created, no Qdrant/Neo4j errors |
| memory_search works | Search for stored memory | Returns results via pgvector |
| boot_context works | Boot an agent | Returns context (no connectome data) |
| Graph tools degrade gracefully | Call `connectome_status` | Returns `{"status": "unavailable", "reason": "Soul Lite mode"}` |
| Instinct tools work | Create + activate instinct | Works (PG only path) |
| Session tools work | Save + recall session | Works |

### 5.3 Backup/Restore Cycle

| Step | Command | Verification |
|------|---------|--------------|
| 1. Seed data | Store 100 memories, 10 instincts, build connectome | Verify counts |
| 2. Create backup | `soul-backup create --output /tmp/test.tar.gz` | Archive exists, manifest.json inside |
| 3. Verify manifest | `tar xf /tmp/test.tar.gz manifest.json && cat manifest.json` | Counts match seeded data |
| 4. Wipe databases | Drop and recreate all databases | Verify 0 memories, 0 instincts |
| 5. Restore | `soul-backup restore --input /tmp/test.tar.gz` | Exit 0 |
| 6. Verify counts | Count memories, instincts, edges | Match manifest |
| 7. Verify searchable | `memory_search` for a known memory | Found |
| 8. Verify connectome | `connectome_status` | Edge count matches |

### 5.4 Environment Variable Override

| Variable | Override Value | Verification |
|----------|---------------|--------------|
| `SOUL_PG_PORT` | 5434 | Server connects to port 5434 |
| `SOUL_NEO4J_URI` | `bolt://custom:7688` | Server uses custom Neo4j URI |
| `SOUL_LITE_MODE` | true | Neo4j/Qdrant tools return unavailable |
| `SOUL_AUTH_ENABLED` | false | Requests work without auth |
| `SOUL_OLLAMA_ENABLED` | false | LLM-dependent tools degrade gracefully |
| `SOUL_LOG_LEVEL` | DEBUG | Verbose logs appear |
| `SOUL_EMBEDDING_MODEL` | custom-model | Embedding model name changes (may fail to load — verify error message is clear) |

---

## 6. MVP Launch Checklist

### Technical Readiness

- [ ] All 61 existing tests pass
- [ ] All new module tests pass (test_rules, test_procedures, test_sessions, test_identity, test_instincts, test_sleep, test_graph, test_memory, test_dmem)
- [ ] `test_auth.py` passes with valid/invalid/missing/expired key scenarios
- [ ] `test_tenant_isolation.py` passes all SEC-01 through SEC-10 tests
- [ ] `test_security.py` passes all SEC-11 through SEC-33 tests
- [ ] `test_performance.py` meets all latency targets (Section 3.1)
- [ ] Code coverage >= 60%
- [ ] `ruff check src/` returns 0 errors
- [ ] `mypy src/soul/` returns 0 errors (or known exceptions documented)
- [ ] Docker Compose Lite starts and passes smoke tests
- [ ] Docker Compose Full starts and passes smoke tests
- [ ] `pip install soul-memory` works from PyPI
- [ ] `docker pull ghcr.io/sknaider/soul-memory:latest` works
- [ ] Backup/restore cycle completes without data loss
- [ ] Soul Lite mode works end-to-end without Neo4j/Qdrant
- [ ] Team SEAL (JARVIS + ADA) boot and operate normally after all changes
- [ ] `ada_boot_test.py` returns 10/10

### Documentation Completeness

- [ ] `README.md` with architecture diagram, quick start, feature comparison
- [ ] `docs/quickstart.md` — tested by someone unfamiliar, completes in < 15 min
- [ ] `docs/deployment.md` — covers Lite, Full, Enterprise
- [ ] `docs/configuration.md` — all env vars documented
- [ ] `docs/api-reference.md` — all 74+ tools documented with params and examples
- [ ] `docs/upgrade.md` — migration path from raw `mcp_server_v2.py` to packaged SOUL
- [ ] `CHANGELOG.md` — v1.0.0 entry
- [ ] `CONTRIBUTING.md` — dev setup, testing, PR process
- [ ] Inline code comments for all complex logic (memory_store side effects, spreading activation, RLS setup)

### Security Audit

- [ ] `secret_scan` runs on startup and CI — blocks exposed credentials
- [ ] `grep -rn "seal2026soul\|seal_memory_2026\|password.*=.*['\"]" src/` returns 0 matches
- [ ] `git log -p --all -S "seal2026soul"` — only in removal commit (or use BFG to clean history)
- [ ] `.env` is in `.gitignore`
- [ ] API keys hashed with bcrypt in DB
- [ ] Error responses never contain stack traces or internal paths
- [ ] RLS policies active on all tenant-scoped tables
- [ ] SQL injection tests pass (SEC-30 through SEC-33)
- [ ] Auth bypass tests pass (SEC-11 through SEC-17)
- [ ] Multi-tenant isolation tests pass (SEC-01 through SEC-10)
- [ ] HIPAA audit log works when enabled (append-only, complete)

### Legal

- [ ] `LICENSE` — Apache 2.0 for Community tier
- [ ] `LICENSE-COMMERCIAL` — BSL or commercial for Team/Enterprise features
- [ ] Feature boundary is clear: README documents which tools are Community vs. paid
- [ ] BSL conversion clause: converts to Apache 2.0 after 3 years
- [ ] BAA template available for Enterprise/HIPAA customers (not signed — template only)
- [ ] No third-party license violations: check Neo4j GPL-3 (Community Edition) implications for commercial distribution
- [ ] Verify `soul-memory` name not trademarked or taken on PyPI

### Marketing Assets

- [ ] GitHub README is the primary landing page — polished, professional
- [ ] Architecture diagram (ASCII or image) in README
- [ ] Feature comparison table: SOUL vs Mem0 vs Zep vs LangMem vs MemoryScope
- [ ] "Quick start in 5 minutes" section with copy-paste commands
- [ ] Demo video or GIF showing boot_context + memory_store + memory_search
- [ ] Blog post: "How We Built a Brain for AI Agents" (architecture deep-dive)
- [ ] Blog post: "Beyond RAG: Why AI Agents Need Instincts" (instinct system)
- [ ] Hacker News submission draft ready ("Show HN: SOUL — Persistent Memory System for AI Agents")
- [ ] Discord server created for community support
- [ ] At least 1 integration example: SOUL + Claude Code (MCP native)

---

## 7. LLM-as-Judge Rubrics

For each milestone, an LLM judge evaluates completion by scoring 1-5 on each criterion. A milestone passes at average score >= 4.0 with no individual score below 3.

---

#### M1: Config Externalized (Gate 0, Steps 1-4)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | All hardcoded credentials removed from source | 5: `grep -rn "seal2026soul\|seal_memory_2026" src/ soul/ *.py` returns 0. 3: Found in comments only. 1: Found in active code. |
| 2 | `.env.example` exists with all required variables | 5: 15+ variables, each with description and example value. 3: Variables present but no descriptions. 1: Missing or incomplete. |
| 3 | Application starts with only environment variables | 5: `SOUL_PG_HOST=x SOUL_PG_PORT=y ... python -m soul.server` works. 3: Works but some defaults are SEAL-specific. 1: Crashes without `.env` file. |
| 4 | No secrets in git history | 5: `git log -p -S "seal2026soul" --all` shows only the removal commit. 3: Secrets in history but `.env` pattern used going forward. 1: Secrets still in recent commits. |
| 5 | Pydantic Settings validates config at startup | 5: Invalid port raises clear error before server starts. 3: Invalid config crashes with Pydantic traceback. 1: Invalid config silently ignored. |
| 6 | Backward compatibility maintained | 5: `python mcp_server_v2.py` works, `ada_boot_test.py` 10/10. 3: Server starts but some tools broken. 1: Server won't start. |

**Pass:** Average >= 4.0, no score below 3.

---

#### M2: Monolith Decomposed (Gate 3, Steps 5-15)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | `mcp_server_v2.py` is a thin shim | 5: Under 20 lines, only imports from `soul.server`. 3: Under 100 lines, some logic remains. 1: Still a monolith. |
| 2 | All 74+ tools registered via module `register_tools()` | 5: `len(mcp.list_tools()) >= 74` and each module has `register_tools`. 3: Most tools registered but some still in monolith. 1: Fewer than 50 tools registered. |
| 3 | Dependency rule enforced | 5: `soul.core` imports 0 other `soul.*` modules (verified by import linter or grep). 3: 1-2 violations documented as tech debt. 1: Circular imports exist. |
| 4 | Module-level tests exist | 5: Each of 9 modules has its own test file with >= 5 tests each. 3: Most modules have tests but < 5 each. 1: Tests only in `test_integration.py`. |
| 5 | Cross-module hooks use dependency injection | 5: `memory_store` uses `on_store_hooks` list, ACE uses injected callables. 3: Some direct imports across modules. 1: All cross-module calls are direct imports. |
| 6 | Soul Lite degradation correct | 5: All graph tools return `{"status": "unavailable"}` in Lite mode. 3: Most tools handle Lite mode. 1: Crashes in Lite mode. |
| 7 | Backward compatibility | 5: `ada_boot_test.py` 10/10, all 61 tests pass, `mcp_server_v2.py` shim works. 3: Tests pass but boot_test < 10/10. 1: Tests fail. |

**Pass:** Average >= 4.0, no score below 3.

---

#### M3: Multi-Tenant & Auth (Gate 4, Steps 16-20)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | Auth rejects unauthenticated requests | 5: 401 for missing key, 403 for invalid, event logged. 3: Rejects but no event logging. 1: Allows through. |
| 2 | Tenant isolation in PostgreSQL | 5: RLS active, cross-tenant query returns 0, negative test proves RLS is the barrier. 3: RLS active, cross-tenant returns 0, no negative test. 1: No RLS or leaks data. |
| 3 | Tenant isolation in Qdrant | 5: `is_tenant` payload index, cross-tenant vector search returns 0. 3: Filtered but not using `is_tenant` optimization. 1: No filtering. |
| 4 | Tenant isolation in Neo4j | 5: `tenant_id` property on all nodes/edges, filtered in all queries. 3: Most queries filtered. 1: No tenant filtering. |
| 5 | Default tenant backward compatibility | 5: Team SEAL works without any config change, data migrated to default UUID. 3: Works but requires setting a new env var. 1: Breaks Team SEAL. |
| 6 | Migration scripts idempotent | 5: `apply.py` tracks applied migrations, running twice is safe. 3: Migrations use `IF NOT EXISTS` but no tracking. 1: Running twice causes errors. |
| 7 | Docker Compose works | 5: `docker compose up` — all healthy < 60s, boot_context works. 3: Starts but takes > 60s or requires manual steps. 1: Doesn't start. |
| 8 | SQL injection blocked | 5: All 4 injection tests (SEC-30 to SEC-33) pass. 3: PG injection blocked, Neo4j untested. 1: Injection possible. |

**Pass:** Average >= 4.0, no score below 3. Scores on criteria 1-4 must be >= 4 (security-critical).

---

#### M4: Monitoring & Backup (Gate 5, Steps 21-26)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | `/health` endpoint complete | 5: Reports PG, Neo4j, Qdrant, Embeddings status, < 2s response. 3: Reports PG only. 1: No health endpoint. |
| 2 | `/metrics` Prometheus-compatible | 5: All 6 required metrics present, format parseable by Prometheus. 3: Some metrics present. 1: No metrics endpoint. |
| 3 | Backup creates consistent archive | 5: PG dump + Neo4j export + Qdrant snapshot in tar.gz with manifest. 3: PG dump only. 1: No backup tool. |
| 4 | Restore recovers all data | 5: Restore to clean env, counts match manifest, search works. 3: Restore works but some data lost. 1: Restore fails. |
| 5 | HIPAA audit log append-only | 5: INSERT works, UPDATE/DELETE blocked, PHI scanner logs detections. 3: Audit log exists but not append-only. 1: No audit log. |
| 6 | CI pipeline catches errors | 5: Lint + typecheck + test on every PR, release publishes to PyPI + GHCR. 3: Tests run but no lint/typecheck. 1: No CI. |
| 7 | API docs generated | 5: All 74+ tools documented with params, return values, examples. 3: Most tools documented. 1: No docs. |
| 8 | Code coverage >= 60% | 5: >= 70%. 4: 60-69%. 3: 50-59%. 2: 40-49%. 1: < 40%. |

**Pass:** Average >= 4.0, no score below 3.

---

#### M5: Deployment Ready (Gate 5-6, Steps 19-28)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | `pip install soul-memory` works | 5: Installs in clean venv, `python -m soul.server` starts. 3: Installs but requires manual config. 1: Package not on PyPI. |
| 2 | Docker image available | 5: `docker pull` works, image starts, health passes, multi-arch. 3: Image works but single arch. 1: No published image. |
| 3 | Quick start < 15 minutes | 5: Verified by unfamiliar user, < 10 min. 4: < 15 min. 3: 15-30 min. 2: 30-60 min. 1: > 60 min or broken. |
| 4 | Configuration documented | 5: Every env var in `.env.example` with description + `docs/configuration.md`. 3: `.env.example` exists but sparse. 1: No documentation. |
| 5 | Upgrade path documented | 5: `docs/upgrade.md` with step-by-step from raw monolith to packaged SOUL. 3: Mentioned in README. 1: No upgrade docs. |
| 6 | Data persists across restarts | 5: Docker volumes preserve data, verified. 3: Mentioned in docs but untested. 1: Data lost on restart. |

**Pass:** Average >= 4.0, no score below 3.

---

#### M6: Launch Ready (Gate 6, Steps 27-30)

Score 1-5 on each criterion:

| # | Criterion | Scoring Guide |
|---|-----------|---------------|
| 1 | README professional quality | 5: Architecture diagram, feature comparison, quick start, badges, no broken links. 3: Content present but rough. 1: Minimal or broken. |
| 2 | License clarity | 5: Apache 2.0 + BSL clearly delineated, feature boundary documented. 3: Licenses exist but boundary unclear. 1: No license. |
| 3 | Community infrastructure | 5: Issue templates, PR template, CONTRIBUTING.md, CODEOWNERS, Discord. 3: Issue templates only. 1: No community infrastructure. |
| 4 | Security posture | 5: All SEC-01 to SEC-33 tests pass, no secrets in history. 3: Most tests pass, some known issues documented. 1: Known vulnerabilities unfixed. |
| 5 | Team SEAL still works | 5: `ada_boot_test.py` 10/10, JARVIS and ADA boot normally, all messaging works. 3: Boots but some features degraded. 1: Broken. |
| 6 | Competitive positioning clear | 5: Feature comparison table accurate, "Mem0/Zep give a notepad, SOUL gives a brain" narrative supported by features. 3: Comparison exists but incomplete. 1: No positioning. |
| 7 | Marketing assets ready | 5: Blog posts written, HN draft ready, demo video exists, integration example. 3: README only. 1: No marketing materials. |

**Pass:** Average >= 4.0, no score below 3. Criteria 4 and 5 must be >= 4.

---

## Appendix A: Test Automation Summary

| Test Suite | File | Runs When | Est. Duration |
|-----------|------|-----------|---------------|
| Existing integration | `tests/test_integration.py` | Every commit | 30s |
| Module unit tests (9 files) | `tests/test_*.py` | Every commit | 60s |
| Security tests | `tests/test_security.py` | Every commit + pre-release | 45s |
| Tenant isolation | `tests/test_tenant_isolation.py` | Every commit + pre-release | 30s |
| Auth tests | `tests/test_auth.py` | Every commit + pre-release | 20s |
| Performance benchmarks | `tests/test_performance.py` | Weekly + pre-release | 10min |
| Docker smoke tests | `scripts/docker_smoke_test.sh` | Pre-release | 5min |
| Backup/restore cycle | `scripts/backup_restore_test.sh` | Pre-release | 3min |
| Boot test (Team SEAL) | `ada_boot_test.py` | Every phase gate | 10s |
| Secret scan | `secret_scan` MCP tool | Every startup + CI | 5s |

**Total CI time per commit:** ~3 minutes (excludes performance and Docker tests).
**Total pre-release validation:** ~20 minutes.

---

## Appendix B: Regression Trigger Matrix

When a specific file is changed, these tests MUST run (beyond the default suite):

| Changed File/Module | Additional Required Tests |
|---------------------|--------------------------|
| `soul/core/config.py` | ALL tests (config affects everything) |
| `soul/core/db.py` | `test_integration.py`, `test_tenant_isolation.py` |
| `soul/core/embeddings.py` | `test_memory.py`, `test_performance.py` (search latency) |
| `soul/auth/middleware.py` | `test_auth.py`, `test_security.py` SEC-11 to SEC-17 |
| `soul/tenant/rls.py` | `test_tenant_isolation.py`, `test_security.py` SEC-01 to SEC-10 |
| `soul/memory/store.py` | `test_memory.py`, `test_performance.py`, `test_tenant_isolation.py`, `ada_boot_test.py` |
| `soul/identity/boot.py` | `ada_boot_test.py`, `test_identity.py`, backward compat check |
| `soul/graph/*` | `test_graph.py`, Soul Lite degradation check |
| `migrations/*.sql` | `test_tenant_isolation.py`, data integrity checks (Section 4.3) |
| `Dockerfile` | Docker smoke tests |
| `docker-compose*.yml` | Docker smoke tests |
| `.github/workflows/*` | Trigger CI on a test branch, verify it runs |

---

*Generated by Claude Code (Opus 4.6) — QA Engineer — 2026-04-07*
*Companion to: decomposition-4.md, architecture-3.md, business-analysis-2c.md*
