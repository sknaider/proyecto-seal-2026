# SOUL Memory System — Implementation Decomposition
**Date:** 2026-04-07  
**Author:** Claude Code (Opus 4.6) — Tech Lead decomposition  
**Inputs:** architecture-3.md, codebase-analysis-2b.md, business-analysis-2c.md  
**Purpose:** Concrete, sequenced implementation steps with risks, dependencies, and acceptance criteria

---

## Phase 0: Prerequisites (Week 1-2)

### Step 1: Create `soul/core/config.py` — Externalize all hardcoded credentials

**Objective:** Replace 7 hardcoded values (DB URLs, passwords, service endpoints) with Pydantic Settings backed by environment variables. Eliminate `seal2026soul` and `seal_memory_2026` from source code.

**Files to create/modify:**
- CREATE `soul/core/__init__.py`
- CREATE `soul/core/config.py` (Pydantic BaseSettings with `SOUL_` prefix)
- CREATE `.env.example` (template with defaults)
- CREATE `.env` (actual values for Team SEAL, in `.gitignore`)
- MODIFY `db.py` — replace hardcoded `DB_URL` (line 7) with `get_config().pg_dsn`
- MODIFY `mcp_server_v2.py` — replace lines 44-51 (OLLAMA_GEN_URL, OLLAMA_MODEL, QDRANT_URL, QDRANT_COLLECTION, NEO4J_URI, NEO4J_AUTH) with `get_config()` calls
- MODIFY `mcp_server_v2.py` — replace line 55 `SOUL_LITE` with `get_config().lite_mode`
- MODIFY `embeddings.py` — replace hardcoded `MODEL_NAME` (line 24) and `CACHE_DIR` with config
- MODIFY `.gitignore` — add `.env`

**Dependencies:** None (first step)

**Estimated effort:** S — 2 days

**Risk level:** Medium

**Risk description:** Existing Team SEAL agents read `mcp_server_v2.py` via stdio. If env vars are missing or have wrong defaults, all agents break simultaneously (JARVIS, ADA, DUM go dark).

**Mitigation:** `.env` file ships with EXACT current values (`seal_memory_2026`, `seal2026soul`, etc.). Pydantic Settings reads `.env` automatically. Team SEAL sees zero change unless `.env` is deleted. Add a startup check: if any password is still `changeme`, log a warning but don't crash.

**Acceptance criteria:**
- `mcp_server_v2.py` contains zero hardcoded passwords or connection strings
- `grep -r "seal2026soul" soul/` returns zero matches
- `grep -r "seal_memory_2026" soul/` returns zero matches
- `.env.example` documents every `SOUL_*` variable with description
- Team SEAL boot_context works identically before and after
- All 61 existing tests pass

**Test strategy:** Run `ada_boot_test.py` (10-point health check). Run `test_new_tools.py` (61 tests). Manual: `boot_context` for ADA and JARVIS agents.

---

### Step 2: Extract shared helpers to `soul/core/helpers.py`

**Objective:** Move 8+ shared functions and 3+ constant dictionaries out of `mcp_server_v2.py` into a reusable module. This eliminates the circular import risk when modules are later split.

**Files to create/modify:**
- CREATE `soul/core/helpers.py` — functions: `temporal_decay_score()`, `_extract_entities()`, `classify_emotion()`, `generate_episode_context()`, `update_ocean()`, `update_relationships()`, `ocean_to_narrative()`
- CREATE `soul/core/types.py` — constants: `KNOWN_ENTITIES`, `HALF_LIFE_BY_CATEGORY`, `OCEAN_DELTAS`, `OCEAN_SESSION_CAP`; dataclasses for shared types
- CREATE `soul/core/ollama.py` — httpx calls to Ollama extracted from inline usage across tools
- MODIFY `mcp_server_v2.py` — replace function definitions with imports from `soul.core.helpers`

**Dependencies:** Step 1 (config.py must exist for Ollama URL)

**Estimated effort:** M — 3 days

**Risk level:** High

**Risk description:** These helpers are called from 15+ locations across the monolith. A subtle signature change or import error breaks multiple tools silently (e.g., `classify_emotion` returns different format, `update_ocean` loses the session delta cap).

**Mitigation:** Extract as pure copy-paste first. Zero refactoring of logic. Each function gets a unit test BEFORE moving it. Verify `_ocean_session_deltas` dict is passed correctly (it's module-level mutable state — can't just import it).

**Acceptance criteria:**
- All 8 helper functions importable from `soul.core.helpers`
- All constants importable from `soul.core.types`
- `mcp_server_v2.py` imports these instead of defining them inline
- `grep -c "def temporal_decay_score" mcp_server_v2.py` returns 0
- All 61 tests pass
- `_ocean_session_deltas` behavior unchanged (session caps still enforced)

**Test strategy:** Unit tests for each extracted function with known input/output pairs captured from current behavior. Then run full test suite.

---

### Step 3: Fix known bugs (`_reflexion_lesson` decorator, duplicate SentenceTransformer load)

**Objective:** Fix two identified bugs before they become harder to fix post-split.

**Files to create/modify:**
- MODIFY `mcp_server_v2.py` line 3625 — remove `@mcp.tool()` decorator from `_reflexion_lesson`. Keep function, prefix stays `_` (private).
- MODIFY `mcp_server_v2.py` line ~6693 — replace inline `SentenceTransformer("intfloat/multilingual-e5-base")` in `sleep_gate_mood_retrieval` with `get_embedding()` from `embeddings.py`
- MODIFY `mcp_server_v2.py` line 205 area — document the `mcp.tool = _observed_tool` replacement clearly with comments explaining import order sensitivity

**Dependencies:** Step 1 (embedding model name comes from config)

**Estimated effort:** XS — 0.5 days

**Risk level:** Low

**Risk description:** `_reflexion_lesson` removal from MCP tool list could break a client that explicitly calls it (unlikely — it takes `pool` as first arg, no MCP client should be calling it). Embedding change could affect `sleep_gate_mood_retrieval` if `get_embedding()` returns slightly different results than direct `SentenceTransformer()` (same model, but singleton vs fresh instance).

**Mitigation:** Search message logs and tool call history for any `_reflexion_lesson` calls from MCP clients. Verify `get_embedding()` and direct `SentenceTransformer()` return identical vectors for a test string.

**Acceptance criteria:**
- `_reflexion_lesson` no longer appears in MCP tool listing (verify via `mcp.list_tools()`)
- `_reflexion_lesson` still callable as internal function by `instinct_activate`
- `sleep_gate_mood_retrieval` uses `get_embedding()` — no direct `SentenceTransformer` import in function body
- Memory usage during `sleep_gate` call does not spike (no duplicate model load)
- All 61 tests pass

**Test strategy:** Call `sleep_gate_mood_retrieval` and verify it returns valid results. Monitor memory with `nvidia-smi` / `top` to confirm no second model load.

---

### Step 4: Set up Poetry project structure with `pyproject.toml`

**Objective:** Create a proper Python package structure so the codebase can be installed with `pip install soul-memory` and imported as `from soul.core import config`.

**Files to create/modify:**
- CREATE `pyproject.toml` — Poetry config, package name `soul-memory`, Python >=3.11
- CREATE `src/soul/__init__.py` (namespace package marker)
- MOVE `soul/core/` to `src/soul/core/` (or symlink during transition)
- CREATE `tests/conftest.py` — shared pytest fixtures (test tenant, test DB pool, mock Qdrant)
- MODIFY `.gitignore` — add `dist/`, `*.egg-info/`, `.venv/`
- CREATE `Makefile` or `justfile` — common commands: `make test`, `make lint`, `make build`

**Dependencies:** Steps 1-3 (core modules must exist)

**Estimated effort:** S — 1.5 days

**Risk level:** Medium

**Risk description:** Moving to `src/` layout changes all import paths. Team SEAL's MCP config points to `mcp_server_v2.py` in the repo root — this must keep working.

**Mitigation:** Keep `mcp_server_v2.py` in repo root as a backward-compatible shim (`from soul.server import main; main()`). Use `pyproject.toml` `[tool.poetry.packages]` to configure `src/` layout. Verify `python -m soul.server` works AND `python mcp_server_v2.py` works.

**Acceptance criteria:**
- `poetry install` succeeds
- `python -c "from soul.core.config import get_config; print(get_config().pg_host)"` prints `localhost`
- `python mcp_server_v2.py` still launches the MCP server (backward compat shim)
- `poetry run pytest` runs all tests
- `poetry build` produces a `.whl` file

**Test strategy:** Install in a fresh venv, verify imports. Run full test suite via `poetry run pytest`.

---

## Phase 1: Module Extraction — Easy Tier (Week 3-4)

### Step 5: Extract `soul/rules/` module (rules, events, working_state — 8 tools)

**Objective:** Extract the simplest module first. Pure PostgreSQL CRUD, zero external dependencies beyond `soul.core`. Establishes the extraction pattern for all subsequent modules.

**Files to create/modify:**
- CREATE `src/soul/rules/__init__.py` — exports `register_tools(mcp)`
- CREATE `src/soul/rules/rules.py` — `rule_set` (line 2064), `rule_list` (line 2091)
- CREATE `src/soul/rules/events.py` — `event_log_append` (line 2115), `event_log_query` (line 2145)
- CREATE `src/soul/rules/state.py` — `working_state_get` (line 4284), `working_state_update` (line 4309)
- CREATE `src/soul/rules/secret.py` — `secret_scan` (line 3316), wraps `secret_scanner.py`
- CREATE `src/soul/rules/reflexion.py` — `_reflexion_lesson` (line 3625, internal only, NOT @mcp.tool)
- MODIFY `mcp_server_v2.py` — remove extracted tool definitions, add `from soul.rules import register_tools`
- CREATE `tests/test_rules.py`

**Dependencies:** Step 4 (package structure)

**Estimated effort:** S — 2 days

**Risk level:** Low

**Risk description:** This is the lowest-risk extraction. All tools are PG-only CRUD. The main risk is the `register_tools(mcp)` pattern — if it doesn't work correctly, the tool won't appear in MCP listings.

**Mitigation:** Test `register_tools` by listing tools after registration and comparing count. Verify each tool is callable via MCP protocol.

**Acceptance criteria:**
- `rule_set`, `rule_list`, `event_log_append`, `event_log_query`, `working_state_get`, `working_state_update`, `secret_scan` all appear in `mcp.list_tools()`
- `_reflexion_lesson` does NOT appear in `mcp.list_tools()`
- `mcp_server_v2.py` no longer contains these tool definitions (grep returns 0)
- All tools return identical results to pre-extraction behavior
- All 61 tests pass + new `test_rules.py` tests pass

**Test strategy:** Per-tool smoke test (call with known inputs, compare output). Integration test via MCP protocol.

---

### Step 6: Extract `soul/procedures/` module (reasoning traces, procedures — 6 tools)

**Objective:** Extract the second simplest module. PG + pgvector, no cross-tool calls.

**Files to create/modify:**
- CREATE `src/soul/procedures/__init__.py` — exports `register_tools(mcp)`
- CREATE `src/soul/procedures/tools.py` — `reasoning_trace_store` (line 2685), `reasoning_trace_update` (line 2756), `reasoning_trace_search` (line 2781), `procedure_store` (line 4081), `procedure_search` (line 4128), `procedure_update` (line 4198)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_procedures.py`

**Dependencies:** Step 4 (package structure)

**Estimated effort:** S — 1.5 days

**Risk level:** Low

**Risk description:** pgvector similarity search uses embedding vectors. Must ensure `get_embedding()` is called correctly from the extracted module.

**Mitigation:** Extracted tools import `get_embedding` from `soul.core.embeddings`. Unit test verifies embedding generation + similarity search returns expected results.

**Acceptance criteria:**
- 6 tools appear in `mcp.list_tools()`
- `reasoning_trace_search` returns similarity-ranked results
- `procedure_search` returns similarity-ranked results
- All 61 tests pass + new tests pass

**Test strategy:** Store a reasoning trace, search for it by semantic query, verify it's found. Same for procedures.

---

### Step 7: Extract `soul/sessions/` module (session save/recall/distill — 5 tools)

**Objective:** Extract session management. Already mostly delegated to `session_memory.py`.

**Files to create/modify:**
- CREATE `src/soul/sessions/__init__.py`
- CREATE `src/soul/sessions/tools.py` — `session_save` (line 2913), `session_recall` (line 2966), `session_list` (line 2984), `session_distill` (line 3032), `session_distill_bulk` (line 3188)
- MOVE `session_memory.py` to `src/soul/sessions/persistence.py` (keep old file as import shim)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_sessions.py`

**Dependencies:** Step 4 (package structure)

**Estimated effort:** S — 1.5 days

**Risk level:** Low-Medium

**Risk description:** `session_distill` and `session_distill_bulk` use Ollama for LLM summarization. If Ollama config isn't properly inherited from `soul.core.config`, distillation silently fails or hangs.

**Mitigation:** `session_distill` already has Ollama fallback (returns raw session if Ollama unavailable). Verify this fallback path still works after extraction.

**Acceptance criteria:**
- 5 tools in `mcp.list_tools()`
- `session_save` → `session_recall` roundtrip returns identical data
- `session_distill` works with Ollama running, degrades gracefully without
- `session_memory.py` backward import shim works (existing scripts that import it)

**Test strategy:** Save a session, recall it, verify content. Distill with Ollama up, then with `SOUL_OLLAMA_ENABLED=false`.

---

### Step 8: Extract `soul/peers/` module (peer models — 2 tools)

**Objective:** Extract the smallest module (2 tools). Pure PG, dynamic table creation.

**Files to create/modify:**
- CREATE `src/soul/peers/__init__.py`
- CREATE `src/soul/peers/tools.py` — `peer_model_update` (line 7333), `peer_model_query` (line 7426)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_peers.py`

**Dependencies:** Step 4 (package structure)

**Estimated effort:** XS — 0.5 days

**Risk level:** Low

**Risk description:** `peer_model_update` does dynamic table creation (`CREATE TABLE IF NOT EXISTS`). Extracted module must have PG pool access.

**Mitigation:** Pool comes from `soul.core.db.get_pool()`. Straightforward.

**Acceptance criteria:**
- 2 tools in `mcp.list_tools()`
- `peer_model_update` creates/updates peer model entries
- `peer_model_query` returns stored peer models

**Test strategy:** Update a peer model, query it back. Verify dynamic table creation works on clean DB.

---

## Phase 2: Module Extraction — Medium Tier (Week 5-6)

### Step 9: Extract `soul/identity/` module (soul, OCEAN, boot_context — 10 tools)

**Objective:** Extract the identity module. `boot_context` is the most complex orchestrator — it calls `soul_activate` (graph module) and `memory_search` (memory module) internally.

**Files to create/modify:**
- CREATE `src/soul/identity/__init__.py`
- CREATE `src/soul/identity/boot.py` — `boot_context` (line 1756)
- CREATE `src/soul/identity/soul.py` — `soul_check` (line 2331), `soul_snapshot` (line 2260), `self_reflect` (line 2188)
- CREATE `src/soul/identity/ocean.py` — `ocean_auto_calibrate` (line 5762), `ocean_state_machine` (line 6134), moved `update_ocean()` reference
- CREATE `src/soul/identity/inner.py` — `inner_thoughts` (line 2222), `active_recall` (line 5113)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- MODIFY `src/soul/identity/boot.py` — import `soul_activate` from `soul.graph` (forward reference until graph is extracted)
- CREATE `tests/test_identity.py`

**Dependencies:** Steps 5-8 (easy tier complete). Step 2 (helpers extracted — `ocean_to_narrative`, `update_ocean` in core).

**Estimated effort:** L — 4 days

**Risk level:** High

**Risk description:** `boot_context` (line 1756) is the most critical tool — it's called at every agent startup. It internally calls `soul_activate` and `memory_search`, which haven't been extracted yet (they're still in `mcp_server_v2.py`). Cross-module imports at this stage create a fragile dependency chain. Also reads a hardcoded path (`/home/dadito/IA/proyecto-seal/morning_briefing.txt` at line 1962) that must be removed for productization.

**Mitigation:** Extract identity module WITH temporary direct imports from `mcp_server_v2.py` for `soul_activate` and `memory_search`. These will be cleaned up when graph/ and memory/ are extracted in Phase 3. Remove hardcoded briefing path — make it configurable or omit. Run `ada_boot_test.py` after extraction to verify boot_context still produces 10/10.

**Acceptance criteria:**
- 10 tools in `mcp.list_tools()`
- `boot_context` returns complete soul context (OCEAN scores, identity, recent memories, instincts)
- `ada_boot_test.py` returns 10/10 ALMA CONECTADA
- `ocean_auto_calibrate` adjusts OCEAN scores without exceeding session caps
- `ocean_state_machine` transitions correctly
- No hardcoded filesystem paths in extracted code
- All 61 tests pass + new tests pass

**Test strategy:** Full boot_context test with live DB. OCEAN calibration test with boundary values. `ada_boot_test.py` as integration gate.

---

### Step 10: Extract `soul/instincts/` module (instinct lifecycle — 8 tools)

**Objective:** Extract the instinct lifecycle. PG + pgvector + Ollama. `instinct_activate` calls `_reflexion_lesson` (already in rules/reflexion.py from Step 5).

**Files to create/modify:**
- CREATE `src/soul/instincts/__init__.py`
- CREATE `src/soul/instincts/crud.py` — `instinct_create` (line 3375), `instinct_list` (line 3565), `instinct_search` (line 3498)
- CREATE `src/soul/instincts/lifecycle.py` — `instinct_activate` (line 3420), `instinct_promote` (line 3913), `instinct_evolve` (line 4557)
- CREATE `src/soul/instincts/consolidate.py` — `instinct_consolidate` (line 3804)
- CREATE `src/soul/instincts/observe.py` — `observation_analyze` (line 4390)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_instincts.py`

**Dependencies:** Step 5 (rules module — `_reflexion_lesson` lives there). Step 2 (helpers).

**Estimated effort:** M — 3 days

**Risk level:** Medium

**Risk description:** `instinct_activate` calls `_reflexion_lesson` fire-and-forget via `asyncio.ensure_future`. The import path changes when both are in separate modules. `instinct_evolve` and `observation_analyze` call Ollama — must handle unavailability gracefully.

**Mitigation:** Import `_reflexion_lesson` from `soul.rules.reflexion`. Verify fire-and-forget still works with cross-module import. Test with `SOUL_OLLAMA_ENABLED=false`.

**Acceptance criteria:**
- 8 tools in `mcp.list_tools()`
- Full instinct lifecycle works: create → activate → evolve → promote → consolidate
- `instinct_search` returns similarity-ranked results
- `observation_analyze` works with Ollama, degrades without
- `instinct_activate` triggers `_reflexion_lesson` asynchronously

**Test strategy:** Create an instinct, activate it, evolve it, promote it. Verify the full lifecycle. Test Ollama degradation.

---

### Step 11: Extract `soul/sleep/` module (consolidation, health — 4 tools)

**Objective:** Extract sleep/consolidation tools. Isolated numpy dependency.

**Files to create/modify:**
- CREATE `src/soul/sleep/__init__.py`
- CREATE `src/soul/sleep/tools.py` — `sleep_gate` (line 6438), `sleep_gate_mood_retrieval` (line 6652), `microcompact_text` (line 3271), `microcompact_stats` (line 3299)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_sleep.py`

**Dependencies:** Step 3 (duplicate SentenceTransformer fixed). Step 2 (helpers).

**Estimated effort:** S — 2 days

**Risk level:** Low-Medium

**Risk description:** `sleep_gate` imports numpy inside function body (line 6466). Module extraction doesn't change this, but numpy must be in the dependency list. `microcompact_text` uses Ollama.

**Mitigation:** Add numpy to `pyproject.toml` dependencies. Verify numpy import works in extracted module. Test Ollama degradation for `microcompact_text`.

**Acceptance criteria:**
- 4 tools in `mcp.list_tools()`
- `sleep_gate` produces consolidation scores using numpy
- `sleep_gate_mood_retrieval` uses `get_embedding()` (not direct SentenceTransformer)
- `microcompact_text` compresses text via Ollama, errors gracefully without

**Test strategy:** Run `sleep_gate` with test data. Verify mood retrieval embedding path. Test microcompact with and without Ollama.

---

### Step 12: Extract `soul/graph/` module (connectome + temporal — 15 tools)

**Objective:** Extract the Neo4j-heavy graph module. This is the largest module by tool count and the most tightly coupled to Neo4j session management.

**Files to create/modify:**
- CREATE `src/soul/graph/__init__.py`
- CREATE `src/soul/graph/connectome.py` — `connectome_build` (line 1579), `connectome_status` (line 1688), `connectome_ltp` (line 5540)
- CREATE `src/soul/graph/activation.py` — `soul_activate` (line 1303), `soul_synthesize` (line 1435)
- CREATE `src/soul/graph/bitemporal.py` — `connectome_bitemporal` (line 5867), `connectome_bitemporal_query` (line 7228)
- CREATE `src/soul/graph/causal.py` — `connectome_causal` (line 5963), `connectome_invalidate_edge` (line 5929)
- CREATE `src/soul/graph/entity.py` — `connectome_entity` (line 6744), `connectome_entity_query` (line 6816)
- CREATE `src/soul/graph/routing.py` — `connectome_smart_route` (line 7107)
- CREATE `src/soul/graph/temporal.py` — `temporal_graph_build` (line 5622), `temporal_query` (line 5686)
- CREATE `src/soul/core/clients.py` — Neo4j driver factory extracted from `mcp_server_v2.py` globals (`_neo4j_driver`, `get_neo4j()`)
- MODIFY `mcp_server_v2.py` — remove extracted tools, remove Neo4j globals
- CREATE `tests/test_graph.py`

**Dependencies:** Step 9 (identity module — `boot_context` calls `soul_activate`; now wired to `soul.graph.activation`). Step 2 (helpers — `_extract_entities`).

**Estimated effort:** L — 5 days

**Risk level:** High

**Risk description:** Neo4j driver (`_neo4j_driver`) is currently a module-level global in `mcp_server_v2.py`. Extracting it to `core/clients.py` changes initialization order. `connectome_smart_route` calls `temporal_query`, `connectome_entity_query`, AND `memory_search` (cross-module). Soul Lite mode must return graceful "unavailable" for all 15 tools.

**Mitigation:** Extract `get_neo4j()` to `soul.core.clients` as a lazy singleton (same pattern as current code). `connectome_smart_route` imports `memory_search` from `soul.memory` — this creates a forward dependency (memory/ not yet extracted). Use a temporary import from `mcp_server_v2.py` until Step 13. Add `if SOUL_LITE_MODE: return {"status": "unavailable"}` guard to every graph tool.

**Acceptance criteria:**
- 15 tools in `mcp.list_tools()`
- `connectome_build` creates edges in Neo4j
- `connectome_status` reports accurate edge/node counts
- `soul_activate` performs spreading activation correctly
- `connectome_smart_route` routes queries across temporal, entity, and memory backends
- Soul Lite mode: all 15 tools return `{"status": "unavailable", "reason": "..."}` without crashing
- All 61 tests pass + new tests pass

**Test strategy:** Build connectome from test memories, verify edge creation. Test spreading activation. Test smart_route end-to-end. Test Soul Lite degradation by setting `SOUL_LITE_MODE=true`.

---

## Phase 3: Module Extraction — Hard Tier (Week 7-8)

### Step 13: Extract `soul/memory/` module (CRUD, search, retrieval — 16 tools)

**Objective:** Extract the hardest single module. `memory_store` has 15 side effects touching PG, Qdrant, Neo4j, and Ollama. This is the keystone of the entire system.

**Files to create/modify:**
- CREATE `src/soul/memory/__init__.py`
- CREATE `src/soul/memory/store.py` — `memory_store` (line 568, ~300 lines), `_auto_broadcast`, `_auto_activate_instincts`
- CREATE `src/soul/memory/search.py` — `memory_search` (line 955), `memory_hybrid_search` (line 2423), `memory_cross_search` (line 6895)
- CREATE `src/soul/memory/crud.py` — `memory_list` (line 1093), `memory_update` (line 1208), `memory_invalidate` (line 2842), `memory_utility_update` (line 1145), `memory_feedback` (line 4008)
- CREATE `src/soul/memory/broadcast.py` — `memory_broadcast_read` (line 874), `memory_broadcast_ack` (line 926)
- CREATE `src/soul/memory/advanced.py` — `memory_flare` (line 4726), `memory_communities` (line 4834), `memory_prefetch` (line 4966), `memory_delta_sync` (line 5278)
- CREATE `src/soul/memory/share.py` — `memory_share_promote` (line 6959)
- CREATE `src/soul/core/clients.py` (if not already) — add Qdrant client factory (`get_qdrant()`, `_qdrant` global)
- MODIFY `mcp_server_v2.py` — remove extracted tools, remove Qdrant globals
- CREATE `tests/test_memory.py`

**Dependencies:** Step 12 (graph module — `memory_store` creates Neo4j edges). Step 10 (instincts — `_auto_activate_instincts`). Step 2 (all helpers).

**Estimated effort:** XL — 6 days

**Risk level:** High

**Risk description:** `memory_store` is the most complex function in the codebase with 15 side effects spanning all 4 backends (PG, Qdrant, Neo4j, Ollama). Extracting it requires that ALL its dependencies (helpers, clients, instinct hooks) are properly importable. A single broken import path means memories can't be stored — the entire system is useless.

**Mitigation:** Extract `memory_store` side effects as a hook chain. Define `on_store_hooks: list[Callable]` in `memory/store.py`. Wire hooks at server startup in `server.py` (dependency inversion). This means `memory_store` doesn't import `instincts` or `graph` directly — it just calls hooks. Test each side effect independently. Use feature flags to enable/disable individual side effects during migration.

**Acceptance criteria:**
- 16 tools in `mcp.list_tools()`
- `memory_store` with `imp=9` triggers all 15 side effects (D-MEM gate, A-MEM enrichment, embedding, conflict detection, emotion classification, PG insert, Qdrant upsert, Neo4j merge, connectome edges, entity extraction, OCEAN update, relationship update, episode context, auto-broadcast, auto-activate instincts)
- `memory_search` returns semantically relevant results with correct temporal decay
- `memory_hybrid_search` combines vector + BM25 results
- `memory_invalidate` cascades to Qdrant + Neo4j
- Soul Lite: memory tools work via PgVectorAdapter, skip Neo4j side effects
- All 61 tests pass + new tests pass

**Test strategy:** Store a high-importance memory and verify all 15 side effects fire. Store + search roundtrip. Test invalidation cascade. Test Soul Lite mode. Performance: store 100 memories and verify no degradation.

---

### Step 14: Extract `soul/dmem/` module (D-MEM gate, ACE curator — 5 tools)

**Objective:** Extract the orchestrator module. D-MEM gates `memory_store`. ACE curator calls across memory, graph, and instincts.

**Files to create/modify:**
- CREATE `src/soul/dmem/__init__.py`
- CREATE `src/soul/dmem/gate.py` — `dmem_gate` (line 6270), `dmem_store` (line 6338)
- CREATE `src/soul/dmem/ace.py` — `ace_curator` (line 5323)
- CREATE `src/soul/dmem/health.py` — `brain_health_report` (line 6992)
- MODIFY `mcp_server_v2.py` — remove extracted tools
- CREATE `tests/test_dmem.py`

**Dependencies:** Step 13 (memory module), Step 12 (graph module), Step 10 (instincts module). D-MEM/ACE orchestrates all of them.

**Estimated effort:** M — 3 days

**Risk level:** High

**Risk description:** `ace_curator` would call `instinct_create`, `connectome_ltp`, and `memory` tools if not in dry_run mode. These cross-module calls are the most complex dependency graph in the system. `brain_health_report` queries all three databases.

**Mitigation:** Use dependency injection: `ace_curator` receives callable references at server startup, not direct imports. This makes it testable with mocks and avoids circular imports. `brain_health_report` uses `soul.core.clients` for all DB connections.

**Acceptance criteria:**
- 5 tools in `mcp.list_tools()`
- `dmem_gate` correctly gates memories (blocks duplicates, allows novel ones)
- `ace_curator` in dry_run mode returns analysis without side effects
- `brain_health_report` reports status of all three databases
- Soul Lite: `brain_health_report` reports PG only, marks Neo4j/Qdrant as "unavailable"

**Test strategy:** Store similar memories, verify D-MEM gate blocks the duplicate. Run ACE curator dry_run. Run brain_health_report and verify all components reported.

---

### Step 15: Create `soul/server.py` — Unified MCP entrypoint importing all modules

**Objective:** Create the single entrypoint that imports all modules, registers all tools, sets up the `mcp.tool = _observed_tool` replacement, and handles startup/shutdown lifecycle.

**Files to create/modify:**
- CREATE `src/soul/server.py` — `create_server()` factory, `main()` entrypoint
- CREATE `src/soul/core/instrumentation.py` — `_observe`, `_observed_tool`, observation queue (moved from mcp_server_v2.py line 178-205)
- MODIFY `mcp_server_v2.py` — replace entire file with backward-compat shim: `from soul.server import main; main()`
- MODIFY `pyproject.toml` — add `[tool.poetry.scripts] soul-memory = "soul.server:main"`

**Dependencies:** Steps 5-14 (ALL modules extracted)

**Estimated effort:** M — 3 days

**Risk level:** High

**Risk description:** This is the "big bang" moment. All modules must register correctly, import order matters (instrumentation BEFORE tool registration), and the backward-compat shim must work for Team SEAL. If any import fails, the entire server won't start.

**Mitigation:** Build `server.py` incrementally — start with just `core` + `rules`, verify it works, add one module at a time. The `mcp_server_v2.py` shim is the safety net — if `soul.server` fails, revert the shim to import from old monolith. Keep `mcp_server_v2.py` backup until all tests pass.

**Acceptance criteria:**
- `python -m soul.server` starts the MCP server
- `python mcp_server_v2.py` starts the same server (backward compat)
- `mcp.list_tools()` returns all 74+ tools (minus `_reflexion_lesson`)
- `boot_context` works end-to-end
- `memory_store` → `memory_search` roundtrip works
- `ada_boot_test.py` returns 10/10
- All 61 tests pass
- Startup time is within 2x of original monolith (embedding model load dominates)

**Test strategy:** Full integration test: boot_context, store memory, search memory, build connectome, create instinct, activate instinct, session save/recall. Compare tool listing before/after migration.

---

## Phase 4: Product Infrastructure (Week 9-10)

### Step 16: Implement authentication layer (OAuth 2.1 + API key fallback)

**Objective:** Add authentication middleware so external users must authenticate. Team SEAL can optionally bypass with `SOUL_AUTH_ENABLED=false`.

**Files to create/modify:**
- CREATE `src/soul/auth/__init__.py`
- CREATE `src/soul/auth/middleware.py` — FastMCP interceptor for auth
- CREATE `src/soul/auth/jwt.py` — JWT validation (verify signature, extract tenant_id + agent_id + role)
- CREATE `src/soul/auth/apikey.py` — API key lookup in `api_keys` PG table
- CREATE `src/soul/auth/models.py` — `Tenant`, `APIKey`, `Role` dataclasses
- CREATE `migrations/002_auth_tables.sql` — `tenants`, `api_keys` tables
- MODIFY `src/soul/server.py` — wire auth middleware
- MODIFY `src/soul/core/config.py` — add `auth_enabled`, `auth_jwt_secret`, `auth_api_key_enabled`
- CREATE `tests/test_auth.py`

**Dependencies:** Step 15 (server.py exists). Step 4 (pyproject.toml for `pyjwt` dependency).

**Estimated effort:** L — 4 days

**Risk level:** Medium

**Risk description:** MCP protocol (stdio) doesn't have HTTP headers. Authentication via MCP requires using the Streamable HTTP transport or a custom header mechanism. Current Team SEAL uses stdio — auth must be bypassable.

**Mitigation:** `SOUL_AUTH_ENABLED=false` (default for backward compat) skips all auth. When enabled, auth only works over Streamable HTTP transport. Document this clearly. API key rotation with 24h grace period (per BDD spec in business-analysis-2c).

**Acceptance criteria:**
- Valid API key: request succeeds, `tenant_id` propagated to tool context
- Missing API key: 401 response
- Invalid API key: 403 response + event logged
- Key rotation: old key valid for 24h after new key generated
- `SOUL_AUTH_ENABLED=false`: all requests pass through (Team SEAL compat)
- BDD scenarios from business-analysis-2c §6 all pass

**Test strategy:** Implement all 4 BDD scenarios from business-analysis-2c as pytest tests. Test with valid key, invalid key, missing key, expired key, rotated key.

---

### Step 17: Add multi-tenancy (tenant_id columns, RLS, Qdrant payload index, Neo4j property)

**Objective:** Add tenant isolation across all three databases. Existing Team SEAL data gets default tenant UUID `00000000-0000-0000-0000-000000000000`.

**Files to create/modify:**
- CREATE `src/soul/tenant/__init__.py`
- CREATE `src/soul/tenant/context.py` — `TenantContext` class, propagated via `contextvars`
- CREATE `src/soul/tenant/rls.py` — `with_tenant_context(pool, tenant_id)` for PG RLS
- CREATE `src/soul/tenant/isolation.py` — Qdrant filter injection, Neo4j property injection
- CREATE `migrations/001_add_tenant_id.sql` — add `tenant_id` to all tables + RLS policies
- MODIFY ALL module files — inject `tenant_id` into every PG query, Qdrant filter, Neo4j property
- CREATE `tests/test_tenant_isolation.py`

**Dependencies:** Step 16 (auth provides tenant_id). Step 15 (server.py for context propagation).

**Estimated effort:** XL — 5 days

**Risk level:** High

**Risk description:** Every single database query across 74 tools must be tenant-scoped. Missing a single query leaks data between tenants. RLS policy misconfiguration could lock out ALL tenants (including Team SEAL). PG `SET LOCAL` requires transactions — asyncpg's default autocommit mode doesn't support it.

**Mitigation:** Use `current_setting('app.tenant_id', true)` (the `true` makes it return NULL instead of error if unset — safe default of "no rows returned"). Implement as a context manager that wraps every DB call. Test with the 4 BDD isolation scenarios from business-analysis-2c §6. Include SQL injection test. The default tenant UUID ensures Team SEAL never breaks.

**Acceptance criteria:**
- Tenant A cannot read Tenant B's memories (PG, Qdrant, Neo4j)
- Tenant A cannot read Tenant B's instincts, sessions, rules, connectome edges
- SQL injection attempt returns 0 results (no error traceback exposed)
- Default tenant (`00000000-...`) works for Team SEAL without configuration
- BDD tenant isolation scenarios from business-analysis-2c §6 all pass
- All 61 tests pass (they run under default tenant)

**Test strategy:** Create two test tenants. Store data in each. Cross-query. Verify zero leakage. SQL injection test. Run existing tests under default tenant to verify backward compat.

---

### Step 18: Database migration scripts

**Objective:** Create versioned, idempotent migration scripts for all schema changes required by productization.

**Files to create/modify:**
- CREATE `migrations/` directory
- CREATE `migrations/001_add_tenant_id.sql` — tenant_id column + RLS for all tables
- CREATE `migrations/002_auth_tables.sql` — tenants, api_keys tables
- CREATE `migrations/003_audit_log.sql` — audit_log table (append-only)
- CREATE `migrations/004_qdrant_tenant_index.sql` — Qdrant tenant payload index (Python script)
- CREATE `migrations/005_neo4j_tenant_index.sql` — Neo4j tenant property index (Cypher script)
- CREATE `migrations/apply.py` — migration runner (tracks applied migrations in PG `schema_migrations` table)
- CREATE `migrations/README.md` — how to run migrations

**Dependencies:** Step 17 (tenant design finalized)

**Estimated effort:** M — 3 days

**Risk level:** Medium

**Risk description:** Migrations on a live database (Team SEAL is running) could lock tables or corrupt data. `ALTER TABLE ADD COLUMN` with `NOT NULL DEFAULT` is safe on PG 11+ (doesn't rewrite table), but `CREATE INDEX CONCURRENTLY` can still be slow on large tables.

**Mitigation:** All migrations are idempotent (`IF NOT EXISTS` guards). `CREATE INDEX CONCURRENTLY` used for all indexes (doesn't lock writes). Migration runner tracks applied migrations to prevent re-runs. Backup before migration.

**Acceptance criteria:**
- `python migrations/apply.py` runs all pending migrations
- Running it twice has no effect (idempotent)
- Team SEAL data gets `tenant_id = '00000000-...'`
- All tables have `tenant_id` column + index
- RLS policies are active
- `schema_migrations` table tracks which migrations have been applied

**Test strategy:** Run on test database. Verify schema changes. Run twice to verify idempotency. Verify Team SEAL data integrity after migration.

---

### Step 19: Docker Compose with health checks (lite + full profiles)

**Objective:** Create production-ready Docker Compose with two profiles: `lite` (PG only) and `full` (PG + Neo4j + Qdrant).

**Files to create/modify:**
- CREATE `Dockerfile` — multi-stage build, embedding model bundled, non-root user
- CREATE `docker-compose.yml` — services: postgres, soul-server (always), neo4j + qdrant (profile: full)
- MODIFY `.env.example` — Docker-specific defaults (service hostnames instead of localhost)
- CREATE `scripts/init.sql` — initial schema creation for fresh deployments

**Dependencies:** Step 15 (server.py as entrypoint). Step 1 (config via env vars).

**Estimated effort:** M — 3 days

**Risk level:** Medium

**Risk description:** Docker image size could be huge (SentenceTransformer model ~400MB + Python deps). Health checks with wrong timing cause cascading restarts. Port conflicts with existing Team SEAL services.

**Mitigation:** Multi-stage Docker build (builder stage downloads model, runtime stage is slim). Health check `start_period` set to 30s for PG, 60s for Neo4j. Port mapping uses env vars (`${SOUL_PG_PORT:-5433}`) to avoid conflicts. Document port requirements.

**Acceptance criteria:**
- `docker compose up -d` starts Soul Lite (PG + SOUL server) within 5 minutes
- `docker compose --profile full up -d` starts full stack within 5 minutes
- All containers reach "healthy" status
- `boot_context` works from a fresh deployment
- `docker compose down -v` cleanly removes everything
- BDD deployment scenarios from business-analysis-2c §6 pass

**Test strategy:** Fresh machine (or clean Docker env). Clone repo, `cp .env.example .env`, edit passwords, `docker compose up`. Verify health. Store and search a memory.

---

### Step 20: Configuration via environment variables + .env.example

**Objective:** Comprehensive documentation and validation of all configuration options.

**Files to create/modify:**
- MODIFY `.env.example` — complete with ALL `SOUL_*` variables, grouped by service, with descriptions
- MODIFY `src/soul/core/config.py` — add Pydantic validators (port ranges, URL formats, password strength)
- CREATE `src/soul/core/config_validator.py` — startup validator that checks all required settings and prints clear errors

**Dependencies:** Step 1 (config.py exists), Step 19 (Docker compose uses env vars)

**Estimated effort:** S — 1 day

**Risk level:** Low

**Risk description:** Missing or invalid env vars cause cryptic errors at runtime instead of clear messages at startup.

**Mitigation:** Pydantic validators catch issues at import time. Startup validator prints a table of all settings (passwords masked) so the operator can verify configuration before the server accepts connections.

**Acceptance criteria:**
- Missing `SOUL_PG_PASSWORD` with default `changeme` prints a WARNING at startup
- Invalid port number (e.g., `SOUL_PG_PORT=99999`) raises clear error at startup
- `SOUL_AUTH_ENABLED=true` without `SOUL_AUTH_JWT_SECRET` raises clear error
- `.env.example` has a comment for every variable

**Test strategy:** Set invalid values for each config option and verify error messages. Set valid values and verify server starts.

---

## Phase 5: Product Polish (Week 11-12)

### Step 21: API documentation (auto-generated from tool docstrings)

**Objective:** Generate browsable API docs from MCP tool docstrings. Every tool must have a clear description of parameters, return values, and behavior.

**Files to create/modify:**
- MODIFY all tool functions — ensure every `@mcp.tool()` has complete docstrings with parameter descriptions
- CREATE `scripts/generate_docs.py` — reads tool registry, generates Markdown API reference
- CREATE `docs/api-reference.md` — auto-generated output

**Dependencies:** Step 15 (all tools registered in server.py)

**Estimated effort:** M — 3 days

**Risk level:** Low

**Risk description:** Docstrings may be incomplete or inconsistent across 74 tools. Manual review is tedious.

**Mitigation:** Script flags tools with missing/incomplete docstrings. Fix in batches by module.

**Acceptance criteria:**
- Every tool has a docstring with: description, parameters, return value, example
- `scripts/generate_docs.py` produces a complete API reference
- API reference is browsable and searchable

**Test strategy:** Run generator, spot-check 10 random tools for accuracy.

---

### Step 22: Deployment guide

**Objective:** Write a comprehensive deployment guide covering Soul Lite, Soul Full, and Enterprise configurations.

**Files to create/modify:**
- CREATE `docs/deployment.md` — Docker Compose, bare metal, Kubernetes (pointer to Helm chart)
- CREATE `docs/quickstart.md` — 15-minute getting started
- CREATE `docs/configuration.md` — all env vars with descriptions and examples
- CREATE `docs/upgrade.md` — migration from previous versions

**Dependencies:** Step 19 (Docker setup finalized), Step 20 (configuration documented)

**Estimated effort:** S — 2 days

**Risk level:** Low

**Risk description:** Documentation gets stale if not maintained. Quickstart guide that doesn't work is worse than no guide.

**Mitigation:** Quickstart guide is tested as part of CI (see Step 23). Auto-generate configuration docs from Pydantic model.

**Acceptance criteria:**
- A developer can go from zero to working SOUL instance in 15 minutes following quickstart
- Deployment guide covers: prerequisites, installation, configuration, verification, troubleshooting
- All code examples in docs are tested

**Test strategy:** Give the quickstart guide to someone unfamiliar with SOUL. Time them. Target: 15 minutes.

---

### Step 23: CI/CD pipeline (GitHub Actions: lint, test, build, publish)

**Objective:** Automated quality gates on every PR and release.

**Files to create/modify:**
- CREATE `.github/workflows/ci.yml` — lint (ruff), type check (mypy), test (pytest), coverage
- CREATE `.github/workflows/release.yml` — build Docker image, push to GHCR, build wheel, push to PyPI
- CREATE `.github/workflows/docs.yml` — generate and publish docs
- MODIFY `pyproject.toml` — add dev dependencies: ruff, mypy, pytest, coverage

**Dependencies:** Step 4 (pyproject.toml), Step 15 (server.py as test target)

**Estimated effort:** M — 2 days

**Risk level:** Low

**Risk description:** CI tests require PostgreSQL (at minimum). GitHub Actions runners don't have Neo4j or Qdrant by default.

**Mitigation:** CI runs Soul Lite tests only (PG via `services: postgres` in GitHub Actions). Full integration tests run in a separate workflow with Docker Compose. Use `SOUL_LITE_MODE=true` for CI.

**Acceptance criteria:**
- Every PR gets lint + type check + test results
- Tests run against PG in GitHub Actions (Soul Lite mode)
- Release workflow builds and pushes Docker image to GHCR
- Release workflow publishes to PyPI
- Code coverage reported (target: >60% for v1.0)

**Test strategy:** Push a PR with a deliberate lint error, verify CI catches it. Push a clean PR, verify all checks pass.

---

### Step 24: Self-monitoring endpoints (health, metrics, readiness)

**Objective:** Add HTTP endpoints for operational monitoring.

**Files to create/modify:**
- CREATE `src/soul/monitoring/__init__.py`
- CREATE `src/soul/monitoring/health.py` — `/health` endpoint (checks PG, Qdrant, Neo4j, embeddings)
- CREATE `src/soul/monitoring/metrics.py` — `/metrics` endpoint (Prometheus format)
- CREATE `src/soul/monitoring/readiness.py` — `/ready` endpoint (for k8s readiness probes)
- MODIFY `src/soul/server.py` — mount monitoring endpoints
- CREATE `tests/test_monitoring.py`

**Dependencies:** Step 15 (server.py), Step 19 (Docker health checks use `/health`)

**Estimated effort:** M — 2 days

**Risk level:** Low

**Risk description:** `/health` endpoint could itself cause issues if it hammers databases too frequently. Prometheus metrics collection adds minor overhead.

**Mitigation:** Health check caches results for 10 seconds. Metrics use lightweight counters/histograms (no DB queries on `/metrics` call — just return accumulated counters).

**Acceptance criteria:**
- `GET /health` returns status of all components (per BDD spec in business-analysis-2c §6)
- `GET /health` response time < 2 seconds
- `GET /metrics` returns Prometheus-compatible text with: `soul_memory_store_total`, `soul_memory_search_total`, `soul_memory_search_latency_ms`, `soul_connectome_edges_total`, `soul_instinct_activations_total`, `soul_active_tenants`
- `GET /ready` returns 200 when server can accept requests, 503 otherwise

**Test strategy:** Start server, hit endpoints, parse responses. Verify metrics increment after tool calls.

---

### Step 25: Backup/restore scripts (cross-database consistent)

**Objective:** CLI tool for consistent backup and restore across PG, Neo4j, and Qdrant.

**Files to create/modify:**
- CREATE `src/soul/cli/backup.py` — `soul-backup create` and `soul-backup restore` commands
- CREATE `src/soul/cli/__init__.py`
- MODIFY `pyproject.toml` — add CLI entrypoint: `soul-backup = "soul.cli.backup:main"`

**Dependencies:** Step 17 (tenant_id for per-tenant backup). Step 19 (Docker for service access).

**Estimated effort:** L — 4 days

**Risk level:** Medium

**Risk description:** Consistent backup across 3 databases is hard. If PG backup happens at T=0 and Neo4j at T=5, data can be inconsistent. Qdrant snapshots are async.

**Mitigation:** For v1.0: stop writes during backup (acceptable for self-hosted). Create a `BACKUP IN PROGRESS` lock in PG that tools check. Future: WAL-based incremental backup with point-in-time recovery.

**Acceptance criteria:**
- `soul-backup create --output backup.tar.gz` captures PG dump + Neo4j export + Qdrant snapshot
- `soul-backup restore --input backup.tar.gz` restores to clean environment
- Manifest file lists component versions and row counts
- Per-tenant backup supported: `soul-backup create --tenant acme`
- Restore does not cross-contaminate existing tenants
- BDD backup/restore scenarios from business-analysis-2c §6 pass

**Test strategy:** Store 100 memories + connectome. Backup. Wipe databases. Restore. Verify all data matches. Cross-tenant test.

---

### Step 26: HIPAA tier implementation (audit log, encryption, PHI scan)

**Objective:** Implement the "HIPAA-Ready" tier (not certified — architecture + controls).

**Files to create/modify:**
- CREATE `src/soul/hipaa/__init__.py`
- CREATE `src/soul/hipaa/audit.py` — immutable audit logging middleware
- CREATE `src/soul/hipaa/encryption.py` — field-level encryption for memory content
- CREATE `src/soul/hipaa/phi_scanner.py` — extend `secret_scanner.py` with PHI patterns (SSN, MRN, DOB, etc.)
- CREATE `migrations/003_audit_log.sql` — audit_log table (append-only, INSERT-only permissions)
- CREATE `docker-compose.hipaa.yml` — overlay with TLS, encrypted volumes
- MODIFY `src/soul/core/config.py` — add `feature_hipaa_audit`, `encryption_key`

**Dependencies:** Step 17 (tenant isolation is prerequisite for HIPAA). Step 18 (migration infrastructure).

**Estimated effort:** L — 4 days

**Risk level:** Medium

**Risk description:** Encryption adds latency to every read/write. PHI scanner false positives could flag legitimate clinical data. Claiming "HIPAA-ready" without legal review creates liability.

**Mitigation:** Encryption is opt-in via `SOUL_FEATURE_HIPAA_AUDIT=true`. PHI scanner logs warnings but does NOT block storage (clinical systems need to store patient data). README clearly states "HIPAA-ready architecture" not "HIPAA-certified". Legal review before any healthcare customer engagement.

**Acceptance criteria:**
- Audit log captures every tool call with: tenant_id, agent_id, action, timestamp, status
- Audit log is append-only (UPDATE/DELETE revoked from app role)
- Field-level encryption encrypts memory content before PG INSERT, decrypts on SELECT
- PHI scanner detects SSN, MRN, email, phone patterns in stored text
- PHI detection logged to audit_log
- 6-year retention policy documented
- All existing tests pass (HIPAA features are opt-in)

**Test strategy:** Enable HIPAA features. Store a memory with fake PHI. Verify audit log entry. Verify content is encrypted in PG but decrypted on read. Attempt to UPDATE audit_log — verify it fails.

---

## Phase 6: Launch Preparation (Week 13-14)

### Step 27: Python package publishing (PyPI)

**Objective:** Publish `soul-memory` to PyPI so users can `pip install soul-memory`.

**Files to create/modify:**
- MODIFY `pyproject.toml` — finalize metadata: name, version, description, classifiers, URLs
- CREATE `src/soul/__init__.py` — package version
- MODIFY `.github/workflows/release.yml` — PyPI publish step

**Dependencies:** Step 23 (CI pipeline), Step 15 (server.py as entrypoint)

**Estimated effort:** S — 1 day

**Risk level:** Low

**Risk description:** Package name `soul-memory` might be taken on PyPI. Package size could be large if embedding model is included.

**Mitigation:** Check PyPI for name availability early (do this NOW, before Week 13). PyPI package does NOT include the embedding model — it's downloaded on first run or bundled in Docker. Package is code-only (~500KB).

**Acceptance criteria:**
- `pip install soul-memory` installs the package
- `python -m soul.server` starts the server
- Package metadata on PyPI is accurate (description, links, classifiers)

**Test strategy:** Install from PyPI in a clean venv. Start server. Verify tools are registered.

---

### Step 28: Docker image publishing (Docker Hub / GHCR)

**Objective:** Publish official Docker image with bundled embedding model.

**Files to create/modify:**
- MODIFY `Dockerfile` — finalize with version labels, non-root user, model bundled
- MODIFY `.github/workflows/release.yml` — GHCR push step
- CREATE `docker-compose.quickstart.yml` — minimal compose using published image (not local build)

**Dependencies:** Step 19 (Dockerfile), Step 23 (CI pipeline)

**Estimated effort:** S — 1 day

**Risk level:** Low

**Risk description:** Image size (~2GB with model) may deter users. Multi-arch builds (amd64 + arm64) needed for DGX Spark compatibility.

**Mitigation:** Offer a slim image without bundled model (downloads on first run, ~200MB). Multi-arch via `docker buildx`.

**Acceptance criteria:**
- `docker pull ghcr.io/sknaider/soul-memory:latest` works
- Image starts and serves MCP tools
- Multi-arch: amd64 and arm64 supported
- Image size documented in README

**Test strategy:** Pull image on clean machine. Run with Docker Compose quickstart. Verify health endpoint.

---

### Step 29: README, CHANGELOG, LICENSE

**Objective:** Create polished project documentation for public launch.

**Files to create/modify:**
- CREATE `README.md` — architecture diagram, quick start, feature comparison table, deployment options
- CREATE `CHANGELOG.md` — v1.0.0 release notes
- CREATE `LICENSE` — Apache 2.0 for Community features
- CREATE `LICENSE-COMMERCIAL` — BSL or commercial license for Team/Enterprise features

**Dependencies:** Step 22 (deployment guide for cross-referencing)

**Estimated effort:** S — 2 days

**Risk level:** Low

**Risk description:** BSL license terms could deter contributors or create confusion about what's free vs. paid.

**Mitigation:** Clear feature matrix in README showing which tools are in Community (Apache 2.0) vs. Team/Enterprise (commercial). BSL converts to Apache 2.0 after 3 years (standard BSL practice).

**Acceptance criteria:**
- README renders correctly on GitHub
- Architecture diagram is readable
- Quick start section matches actual deployment steps
- Feature comparison table (SOUL vs Mem0 vs Zep vs LangMem) is accurate
- License files are legally sound (review with attorney for BSL)

**Test strategy:** Follow the README quickstart end-to-end on a clean machine.

---

### Step 30: GitHub repo setup (issues templates, contributing guide)

**Objective:** Prepare the public GitHub repository for community engagement.

**Files to create/modify:**
- CREATE `.github/ISSUE_TEMPLATE/bug_report.yml`
- CREATE `.github/ISSUE_TEMPLATE/feature_request.yml`
- CREATE `.github/PULL_REQUEST_TEMPLATE.md`
- CREATE `CONTRIBUTING.md` — how to contribute, code style, testing requirements
- CREATE `CODE_OF_CONDUCT.md`
- CREATE `.github/CODEOWNERS` — assign William as default reviewer

**Dependencies:** Step 29 (README exists), Step 23 (CI checks PRs)

**Estimated effort:** XS — 0.5 days

**Risk level:** Low

**Risk description:** None significant. Standard GitHub setup.

**Mitigation:** N/A

**Acceptance criteria:**
- New issue form shows bug report and feature request templates
- PR template includes checklist (tests, docs, breaking changes)
- CONTRIBUTING.md explains dev setup, testing, PR process
- CODEOWNERS routes PRs to William

**Test strategy:** Create a test issue and PR, verify templates appear.

---

## Critical Path Analysis

```
Step 1 ──→ Step 2 ──→ Step 4 ──→ Step 5 ──┐
   │            │                  Step 6 ──┤
   └──→ Step 3  │                  Step 7 ──┤ (Easy tier - parallelizable)
                │                  Step 8 ──┘
                │                       │
                │                       ▼
                │              Step 9 ──┐
                │              Step 10 ──┤ (Medium tier)
                │              Step 11 ──┤
                └────────────→ Step 12 ──┘
                                    │
                                    ▼
                              Step 13 ──→ Step 14 ──→ Step 15  (Hard tier - SEQUENTIAL)
                                                         │
                                          ┌──────────────┤
                                          ▼              ▼
                                    Step 16 ──→ Step 17 ──→ Step 18
                                          │              │
                                          ▼              ▼
                                    Step 19 ←── Step 20
                                          │
                              ┌───────────┼───────────────┐
                              ▼           ▼               ▼
                        Step 21     Step 23         Step 24
                              │           │               │
                              ▼           │               │
                        Step 22           │               │
                              │           │               │
                              └───────┬───┘               │
                                      ▼                   ▼
                              Step 25 ←──────────── Step 26
                                      │
                              ┌───────┼───────┐
                              ▼       ▼       ▼
                        Step 27  Step 28  Step 29
                              │       │       │
                              └───────┼───────┘
                                      ▼
                                 Step 30
```

### Critical Path (longest sequential chain):

**Step 1 → 2 → 4 → 12 → 13 → 14 → 15 → 17 → 18 → 25 → 28**

This is ~38 days on the critical path. Steps 5-8 and 9-11 can run in parallel with Step 12 prep work.

### Parallelization Opportunities:

| Parallel Track A | Parallel Track B | Parallel Track C |
|-----------------|-----------------|-----------------|
| Steps 5-8 (Easy extraction) | Step 3 (Bug fixes) | — |
| Steps 9-11 (Medium extraction) | Step 12 (Graph extraction) | — |
| Step 16 (Auth) | Step 19 (Docker) | Step 21 (Docs) |
| Step 22 (Deploy guide) | Step 23 (CI/CD) | Step 24 (Monitoring) |
| Step 27 (PyPI) | Step 28 (Docker Hub) | Step 29 (README) |

---

## Total Effort Summary

| Phase | Steps | Effort (days) | Risk Level | Key Milestone |
|-------|-------|--------------|------------|---------------|
| **Phase 0: Prerequisites** | 1-4 | 7 | Medium | Config externalized, helpers extracted, Poetry project |
| **Phase 1: Easy Extraction** | 5-8 | 5.5 | Low | 21 tools extracted (rules, procedures, sessions, peers) |
| **Phase 2: Medium Extraction** | 9-12 | 14 | High | 37 more tools extracted (identity, instincts, sleep, graph) |
| **Phase 3: Hard Extraction** | 13-15 | 12 | High | All 74 tools extracted, monolith is a shim |
| **Phase 4: Product Infra** | 16-20 | 16 | High | Auth, multi-tenancy, migrations, Docker, config |
| **Phase 5: Product Polish** | 21-26 | 17 | Medium | Docs, CI/CD, monitoring, backup, HIPAA |
| **Phase 6: Launch Prep** | 27-30 | 4.5 | Low | PyPI, Docker Hub, README, GitHub repo |
| **TOTAL** | **30 steps** | **76 days** | — | **~14 weeks at full capacity** |

### Adjusted Timeline (Solo Developer + AI Agents)

Assuming William + JARVIS + ADA work at ~1.5x productivity vs solo dev:
- **Effective days: 76 / 1.5 = ~51 working days = ~10 weeks**
- **With buffer for unknowns (1.3x): ~13 weeks**
- **Target: 14 weeks** (matches architecture-3.md timeline)

### Risk Heatmap

| Risk | Probability | Impact | Steps Affected |
|------|------------|--------|----------------|
| `memory_store` extraction breaks core flow | High | Critical | 13, 14, 15 |
| Cross-module circular imports | High | High | 9, 12, 13, 14 |
| RLS misconfiguration leaks tenant data | Medium | Critical | 17, 18 |
| Team SEAL breaks during migration | Medium | High | All extraction steps |
| Docker image too large for users | Low | Medium | 19, 28 |
| PyPI name `soul-memory` taken | Low | Low | 27 |
| Neo4j GPL-3 license issue for commercial use | Medium | High | 12, 29 |

---

*Generated by Claude Code (Opus 4.6) — 2026-04-07*
*Next step: architecture-3.md approved → begin Step 1 (config.py)*
