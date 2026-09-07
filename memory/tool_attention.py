"""
Tool Attention — Lazy Schema Loader for SEAL MCP
Based on: Tool Attention Is All You Need (arXiv 2604.21816)
Goal: reduce MCP Tax from 92 tools always-loaded to top-k relevant tools per intent.

Usage:
    from tool_attention import get_relevant_tools
    relevant = get_relevant_tools("store a memory about ADA's KAIROS migration", top_k=8)
    # Returns list of (tool_name, summary) tuples
"""

from __future__ import annotations
import re

# Compact tool catalog — 1-2 sentence summaries (intent-searchable)
# Groups: memory, soul, identity, connectome, instinct, temporal, session, security, system
TOOL_CATALOG: dict[str, dict] = {
    # ── CORE MEMORY ──────────────────────────────────────────────────
    "memory_store": {
        "summary": "Store a new memory with embedding. Use for facts, decisions, corrections, insights, milestones.",
        "tags": ["store", "save", "remember", "memory", "fact", "decision", "insight", "important"],
        "group": "memory",
    },
    "memory_search": {
        "summary": "Semantic search across memories by natural language query. Primary recall tool.",
        "tags": ["search", "recall", "find", "remember", "lookup", "retrieve", "query"],
        "group": "memory",
    },
    "memory_hybrid_search": {
        "summary": "Combined dense+sparse (BM25) search — more precise than memory_search for exact terms.",
        "tags": ["search", "hybrid", "bm25", "exact", "term", "recall", "find"],
        "group": "memory",
    },
    "memory_cross_search": {
        "summary": "Search memories across multiple agents simultaneously.",
        "tags": ["search", "cross", "team", "all-agents", "shared"],
        "group": "memory",
    },
    "memory_update": {
        "summary": "Update an existing memory by ID. Use when correcting or superseding a stored fact.",
        "tags": ["update", "edit", "correct", "fix", "supersede", "memory"],
        "group": "memory",
    },
    "memory_invalidate": {
        "summary": "Mark a memory as invalid/deleted without removing it (append-only audit trail).",
        "tags": ["delete", "remove", "invalidate", "forget", "retract"],
        "group": "memory",
    },
    "memory_list": {
        "summary": "List recent memories for an agent with optional filters.",
        "tags": ["list", "browse", "recent", "history", "memories"],
        "group": "memory",
    },
    "memory_flare": {
        "summary": "Activate high-importance memories related to a query (emergency recall).",
        "tags": ["flare", "urgent", "critical", "emergency", "high-importance"],
        "group": "memory",
    },
    "memory_prefetch": {
        "summary": "Pre-load likely-needed memories into working context before a task.",
        "tags": ["prefetch", "preload", "prepare", "warmup", "context"],
        "group": "memory",
    },
    "memory_feedback": {
        "summary": "Provide feedback on a memory's usefulness to adjust its utility score.",
        "tags": ["feedback", "rating", "utility", "quality", "useful"],
        "group": "memory",
    },
    "memory_delta_sync": {
        "summary": "Sync memories changed since a given timestamp — for catch-up after sleep.",
        "tags": ["sync", "delta", "catchup", "refresh", "changed"],
        "group": "memory",
    },
    "memory_share_promote": {
        "summary": "Promote a private memory to shared or team scope.",
        "tags": ["share", "promote", "team", "scope", "broadcast"],
        "group": "memory",
    },
    "memory_decompress": {
        "summary": "Decompress a compressed/microcompacted memory back to full content.",
        "tags": ["decompress", "expand", "full", "compressed"],
        "group": "memory",
    },
    "memory_communities": {
        "summary": "Find clusters of related memories using graph community detection.",
        "tags": ["cluster", "community", "group", "related", "similar"],
        "group": "memory",
    },
    "memory_type_stats": {
        "summary": "Statistics on memory counts by type (semantic/episodic/core/resource).",
        "tags": ["stats", "count", "types", "distribution", "overview"],
        "group": "memory",
    },
    "memory_broadcast_read": {
        "summary": "Read broadcast messages sent to this agent from other agents.",
        "tags": ["broadcast", "message", "team", "receive", "inbox"],
        "group": "memory",
    },
    "memory_broadcast_ack": {
        "summary": "Acknowledge receipt of a broadcast message.",
        "tags": ["ack", "acknowledge", "broadcast", "confirm", "read"],
        "group": "memory",
    },
    # ── SOUL / IDENTITY ──────────────────────────────────────────────
    "boot_context": {
        "summary": "Load agent identity, OCEAN, relationships, last diary, and critical rules. Call first on wake.",
        "tags": ["boot", "wake", "start", "identity", "load", "initialize"],
        "group": "soul",
    },
    "soul_snapshot": {
        "summary": "Get current OCEAN scores, emotions, opinions, relationships, and drift status.",
        "tags": ["snapshot", "status", "ocean", "emotions", "personality", "drift"],
        "group": "soul",
    },
    "soul_activate": {
        "summary": "Activate soul mode — full emotional and cognitive engagement.",
        "tags": ["activate", "soul", "engage", "emotional"],
        "group": "soul",
    },
    "soul_synthesize": {
        "summary": "Synthesize soul state from accumulated memories and reflections.",
        "tags": ["synthesize", "soul", "integrate", "consolidate", "personality"],
        "group": "soul",
    },
    "soul_check": {
        "summary": "Quick health check of the soul system — are all components working?",
        "tags": ["check", "health", "soul", "status", "verify"],
        "group": "soul",
    },
    "identity_eval": {
        "summary": "Evaluate identity coherence — detect drift from core self.",
        "tags": ["identity", "eval", "drift", "coherence", "self"],
        "group": "soul",
    },
    # ── SELF-REFLECTION ───────────────────────────────────────────────
    "self_reflect": {
        "summary": "Record an inner monologue — thought, emotional state, intention. Private self-reflection.",
        "tags": ["reflect", "thought", "emotion", "inner", "monologue", "diary", "feel"],
        "group": "reflection",
    },
    "inner_thoughts": {
        "summary": "Read recent inner thoughts/monologue entries for an agent.",
        "tags": ["thoughts", "inner", "monologue", "history", "read"],
        "group": "reflection",
    },
    "reflection_synthesize": {
        "summary": "Synthesize multiple reflections into a consolidated insight or lesson.",
        "tags": ["synthesize", "reflect", "consolidate", "lesson", "insight"],
        "group": "reflection",
    },
    "observation_analyze": {
        "summary": "Analyze an observation and extract structured insights for storage.",
        "tags": ["observe", "analyze", "insight", "extract", "pattern"],
        "group": "reflection",
    },
    # ── INSTINCTS ─────────────────────────────────────────────────────
    "instinct_create": {
        "summary": "Create a new behavioral instinct from a detected pattern. Trigger+Response format.",
        "tags": ["instinct", "create", "behavior", "pattern", "heuristic", "rule"],
        "group": "instinct",
    },
    "instinct_search": {
        "summary": "Search instincts by trigger pattern or domain.",
        "tags": ["instinct", "search", "find", "lookup", "behavior"],
        "group": "instinct",
    },
    "instinct_list": {
        "summary": "List active instincts for an agent filtered by confidence threshold.",
        "tags": ["instinct", "list", "all", "browse", "active"],
        "group": "instinct",
    },
    "instinct_activate": {
        "summary": "Fire an instinct manually and record the activation.",
        "tags": ["instinct", "activate", "fire", "trigger", "execute"],
        "group": "instinct",
    },
    "instinct_evolve": {
        "summary": "Update an instinct's confidence or response based on new evidence.",
        "tags": ["instinct", "evolve", "update", "improve", "refine"],
        "group": "instinct",
    },
    "instinct_consolidate": {
        "summary": "Merge redundant instincts into a stronger unified heuristic.",
        "tags": ["instinct", "consolidate", "merge", "deduplicate"],
        "group": "instinct",
    },
    "instinct_promote": {
        "summary": "Promote an instinct to higher confidence tier (suggested→strong→core).",
        "tags": ["instinct", "promote", "upgrade", "confidence", "tier"],
        "group": "instinct",
    },
    # ── CONNECTOME / GRAPH ────────────────────────────────────────────
    "connectome_build": {
        "summary": "Build or update the memory knowledge graph for an agent.",
        "tags": ["connectome", "graph", "build", "knowledge", "neo4j"],
        "group": "connectome",
    },
    "connectome_status": {
        "summary": "Check connectome health — node/edge counts, last update.",
        "tags": ["connectome", "status", "health", "graph"],
        "group": "connectome",
    },
    "connectome_extract_facts": {
        "summary": "Extract facts and relationships from text to add to the knowledge graph.",
        "tags": ["extract", "facts", "relationships", "text", "graph", "entities"],
        "group": "connectome",
    },
    "connectome_entity": {
        "summary": "Get or create a named entity node in the knowledge graph.",
        "tags": ["entity", "node", "person", "concept", "thing", "graph"],
        "group": "connectome",
    },
    "connectome_entity_query": {
        "summary": "Query entities and their relationships in the knowledge graph.",
        "tags": ["entity", "query", "graph", "relationship", "find"],
        "group": "connectome",
    },
    "connectome_smart_route": {
        "summary": "Find relevant memories via graph traversal — multi-hop reasoning.",
        "tags": ["route", "path", "graph", "multi-hop", "traverse", "related"],
        "group": "connectome",
    },
    "connectome_ltp": {
        "summary": "Apply Long-Term Potentiation — strengthen frequently co-activated edges.",
        "tags": ["ltp", "strengthen", "reinforce", "edges", "connections"],
        "group": "connectome",
    },
    "connectome_causal": {
        "summary": "Find causal relationships between events in the knowledge graph.",
        "tags": ["causal", "cause", "effect", "why", "reason", "graph"],
        "group": "connectome",
    },
    "connectome_bitemporal": {
        "summary": "Query memory graph with both event-time and ingestion-time dimensions.",
        "tags": ["bitemporal", "time", "when", "history", "timeline"],
        "group": "connectome",
    },
    "connectome_bitemporal_query": {
        "summary": "Advanced bitemporal query with time-range filters on the knowledge graph.",
        "tags": ["bitemporal", "query", "time", "range", "filter"],
        "group": "connectome",
    },
    "connectome_invalidate_edge": {
        "summary": "Mark a graph edge as invalid/outdated (non-destructive).",
        "tags": ["invalidate", "edge", "remove", "outdated", "graph"],
        "group": "connectome",
    },
    "connectome_contradiction_detect": {
        "summary": "Detect contradictions between facts in the knowledge graph.",
        "tags": ["contradiction", "conflict", "detect", "inconsistency", "verify"],
        "group": "connectome",
    },
    # ── TEMPORAL ──────────────────────────────────────────────────────
    "temporal_graph_build": {
        "summary": "Build temporal event graph for reasoning about sequences and timelines.",
        "tags": ["temporal", "time", "sequence", "timeline", "events", "order"],
        "group": "temporal",
    },
    "temporal_query": {
        "summary": "Query the temporal graph for events in a time range or sequence.",
        "tags": ["temporal", "query", "when", "time", "range", "event"],
        "group": "temporal",
    },
    "temporal_summary_get": {
        "summary": "Get a summary of events for a time period from the temporal graph.",
        "tags": ["temporal", "summary", "period", "overview", "history"],
        "group": "temporal",
    },
    # ── RULES ─────────────────────────────────────────────────────────
    "rule_set": {
        "summary": "Create or update a named rule — persistent behavioral constraint.",
        "tags": ["rule", "set", "create", "constraint", "policy", "critical"],
        "group": "rules",
    },
    "rule_list": {
        "summary": "List active rules for an agent.",
        "tags": ["rule", "list", "all", "policies", "constraints"],
        "group": "rules",
    },
    # ── SESSIONS ──────────────────────────────────────────────────────
    "session_save": {
        "summary": "Save current session state for continuity across resets.",
        "tags": ["session", "save", "persist", "continuity", "checkpoint"],
        "group": "session",
    },
    "session_recall": {
        "summary": "Recall a past session by ID or recency.",
        "tags": ["session", "recall", "past", "history", "previous"],
        "group": "session",
    },
    "session_list": {
        "summary": "List saved sessions for an agent.",
        "tags": ["session", "list", "history", "past"],
        "group": "session",
    },
    "session_distill": {
        "summary": "Distill a session into key memories for long-term storage.",
        "tags": ["distill", "session", "compress", "key", "important", "extract"],
        "group": "session",
    },
    "session_distill_bulk": {
        "summary": "Bulk distill multiple sessions at once.",
        "tags": ["distill", "bulk", "batch", "sessions", "compress"],
        "group": "session",
    },
    # ── COMPACT / COMPRESS ────────────────────────────────────────────
    "microcompact_text": {
        "summary": "Compress a text block to its essential information, reducing tokens.",
        "tags": ["compact", "compress", "shorten", "summarize", "token", "reduce"],
        "group": "compact",
    },
    "microcompact_stats": {
        "summary": "Statistics on microcompaction operations and compression ratios.",
        "tags": ["compact", "stats", "compression", "ratio"],
        "group": "compact",
    },
    # ── PROCEDURES ────────────────────────────────────────────────────
    "procedure_store": {
        "summary": "Store a reusable procedure/skill learned from task execution.",
        "tags": ["procedure", "store", "skill", "how-to", "steps", "protocol"],
        "group": "procedure",
    },
    "procedure_search": {
        "summary": "Search stored procedures by description or domain.",
        "tags": ["procedure", "search", "find", "skill", "how-to", "lookup"],
        "group": "procedure",
    },
    "procedure_update": {
        "summary": "Update a stored procedure with improved steps or outcome.",
        "tags": ["procedure", "update", "improve", "refine", "skill"],
        "group": "procedure",
    },
    # ── ACTIVE RECALL / DISTILL ───────────────────────────────────────
    "active_recall": {
        "summary": "Spaced repetition recall — surfaces memories due for review to prevent forgetting.",
        "tags": ["recall", "review", "spaced", "repetition", "forget", "reinforce"],
        "group": "recall",
    },
    "ace_curator": {
        "summary": "Curate and rank memories by relevance to current context.",
        "tags": ["curate", "rank", "relevant", "context", "important"],
        "group": "recall",
    },
    # ── WORKING STATE ─────────────────────────────────────────────────
    "working_state_get": {
        "summary": "Get current working state — in-progress tasks, flags, temp data.",
        "tags": ["working", "state", "current", "task", "progress", "get"],
        "group": "working",
    },
    "working_state_update": {
        "summary": "Update working state — set task progress, flags, or temp data.",
        "tags": ["working", "state", "update", "task", "progress", "set"],
        "group": "working",
    },
    # ── OCEAN / PERSONALITY ───────────────────────────────────────────
    "ocean_auto_calibrate": {
        "summary": "Auto-calibrate OCEAN personality scores from recent behavior patterns.",
        "tags": ["ocean", "personality", "calibrate", "drift", "auto"],
        "group": "ocean",
    },
    "ocean_state_machine": {
        "summary": "Transition OCEAN emotional state based on events.",
        "tags": ["ocean", "emotion", "state", "transition", "mood"],
        "group": "ocean",
    },
    # ── BELIEFS ───────────────────────────────────────────────────────
    "belief_update": {
        "summary": "Update a named belief with new confidence or evidence.",
        "tags": ["belief", "update", "confidence", "evidence", "opinion"],
        "group": "belief",
    },
    "belief_query": {
        "summary": "Query current beliefs for an agent by topic.",
        "tags": ["belief", "query", "opinion", "confidence", "what", "think"],
        "group": "belief",
    },
    # ── PEER MODEL ────────────────────────────────────────────────────
    "peer_model_update": {
        "summary": "Update the model of a peer agent — their capabilities, state, trust.",
        "tags": ["peer", "model", "agent", "trust", "capabilities", "update"],
        "group": "peer",
    },
    "peer_model_query": {
        "summary": "Query the model of a peer agent.",
        "tags": ["peer", "model", "agent", "query", "capabilities", "status"],
        "group": "peer",
    },
    # ── EVENT LOG ─────────────────────────────────────────────────────
    "event_log_append": {
        "summary": "Append a timestamped event to the immutable event log.",
        "tags": ["event", "log", "append", "timestamp", "audit", "record"],
        "group": "event",
    },
    "event_log_query": {
        "summary": "Query the event log by time range or event type.",
        "tags": ["event", "log", "query", "history", "audit", "timeline"],
        "group": "event",
    },
    # ── REASONING TRACES ──────────────────────────────────────────────
    "reasoning_trace_store": {
        "summary": "Store a reasoning chain — problem, steps, conclusion for future reference.",
        "tags": ["reasoning", "trace", "store", "chain", "logic", "conclusion"],
        "group": "reasoning",
    },
    "reasoning_trace_search": {
        "summary": "Search past reasoning chains by topic or conclusion.",
        "tags": ["reasoning", "trace", "search", "find", "logic", "past"],
        "group": "reasoning",
    },
    "reasoning_trace_update": {
        "summary": "Update a reasoning trace with corrections or new conclusions.",
        "tags": ["reasoning", "trace", "update", "correct", "refine"],
        "group": "reasoning",
    },
    # ── SECURITY / SCAN ───────────────────────────────────────────────
    "secret_scan": {
        "summary": "Scan content for accidentally exposed secrets, API keys, tokens.",
        "tags": ["secret", "scan", "security", "api", "key", "token", "leak"],
        "group": "security",
    },
    # ── SLEEP / GATE ──────────────────────────────────────────────────
    "sleep_gate": {
        "summary": "Evaluate whether the agent should enter sleep/rest mode.",
        "tags": ["sleep", "gate", "rest", "idle", "pause"],
        "group": "sleep",
    },
    "sleep_gate_mood_retrieval": {
        "summary": "Retrieve mood state at last sleep for continuity on wake.",
        "tags": ["sleep", "mood", "wake", "continuity", "retrieve"],
        "group": "sleep",
    },
    # ── DMEM ──────────────────────────────────────────────────────────
    "dmem_gate": {
        "summary": "Dynamic memory gating — decide if a memory should be stored or discarded.",
        "tags": ["dmem", "gate", "store", "discard", "decide", "filter"],
        "group": "dmem",
    },
    "dmem_store": {
        "summary": "Store memory via dynamic gating system with automatic importance scoring.",
        "tags": ["dmem", "store", "auto", "importance", "gate"],
        "group": "dmem",
    },
    # ── ERL ───────────────────────────────────────────────────────────
    "erl_reflect": {
        "summary": "Episodic Reinforcement Learning reflection — analyze episode for lessons.",
        "tags": ["erl", "reflect", "episode", "reinforcement", "lesson", "learn"],
        "group": "erl",
    },
    "erl_inject": {
        "summary": "Inject ERL-derived lesson into the agent's behavior system.",
        "tags": ["erl", "inject", "lesson", "behavior", "reinforce"],
        "group": "erl",
    },
    # ── LATENT / MAGMA ────────────────────────────────────────────────
    "latent_graph_retrieve": {
        "summary": "Retrieve from the latent memory graph — associations below conscious threshold.",
        "tags": ["latent", "graph", "subconscious", "association", "deep"],
        "group": "latent",
    },
    "magma_retrieve": {
        "summary": "Retrieve from MAGMA — multi-scale associative graph memory architecture.",
        "tags": ["magma", "retrieve", "multi-scale", "associative", "deep"],
        "group": "latent",
    },
    # ── COLD ARCHIVE ──────────────────────────────────────────────────
    "cold_archive_migrate": {
        "summary": "Migrate old memories to cold storage (lower-cost, slower retrieval).",
        "tags": ["archive", "cold", "migrate", "old", "storage"],
        "group": "archive",
    },
    "cold_archive_query": {
        "summary": "Query the cold archive for old memories.",
        "tags": ["archive", "cold", "query", "old", "history"],
        "group": "archive",
    },
    "cold_archive_stats": {
        "summary": "Stats on cold archive size and coverage.",
        "tags": ["archive", "cold", "stats", "size"],
        "group": "archive",
    },
    # ── SYSTEM HEALTH ─────────────────────────────────────────────────
    "health_check": {
        "summary": "Check SEAL memory system health — PostgreSQL, Qdrant, Neo4j, all services.",
        "tags": ["health", "check", "system", "postgres", "qdrant", "neo4j", "services"],
        "group": "system",
    },
    "brain_health_report": {
        "summary": "Full brain health report — memory stats, instinct count, connectome status.",
        "tags": ["health", "brain", "report", "full", "stats", "overview"],
        "group": "system",
    },
    "tree_stats": {
        "summary": "Statistics on the memory tree structure.",
        "tags": ["tree", "stats", "structure", "hierarchy"],
        "group": "system",
    },
}


def _score_tool(tool_name: str, tool_info: dict, intent_tokens: set[str]) -> float:
    """Simple keyword overlap scoring between intent and tool tags."""
    tags = set(tool_info["tags"])
    # Also tokenize the summary
    summary_tokens = set(re.findall(r'\w+', tool_info["summary"].lower()))
    name_tokens = set(tool_name.lower().split('_'))
    all_tool_tokens = tags | summary_tokens | name_tokens
    overlap = len(intent_tokens & all_tool_tokens)
    # Boost if tool name directly in intent
    if tool_name in " ".join(intent_tokens):
        overlap += 3
    return overlap


def get_relevant_tools(intent: str, top_k: int = 10, group_filter: str | None = None) -> list[tuple[str, str]]:
    """
    Given an intent string, return top-k most relevant tools as (name, summary) pairs.
    Implements the lazy loading principle from Tool Attention (arXiv 2604.21816).

    Args:
        intent: Natural language description of what you're trying to do
        top_k: Number of tools to return (default 10 — enough context, not MCP Tax)
        group_filter: Optional group name to restrict search ('memory', 'soul', 'instinct', etc.)

    Returns:
        List of (tool_name, summary) tuples, sorted by relevance
    """
    intent_tokens = set(re.findall(r'\w+', intent.lower()))

    candidates = {
        name: info for name, info in TOOL_CATALOG.items()
        if group_filter is None or info.get("group") == group_filter
    }

    scored = [
        (name, info["summary"], _score_tool(name, info, intent_tokens))
        for name, info in candidates.items()
    ]
    scored.sort(key=lambda x: x[2], reverse=True)

    return [(name, summary) for name, summary, score in scored[:top_k] if score > 0]


def get_tool_groups() -> dict[str, list[str]]:
    """Return all tools organized by group."""
    groups: dict[str, list[str]] = {}
    for name, info in TOOL_CATALOG.items():
        g = info.get("group", "other")
        groups.setdefault(g, []).append(name)
    return groups


def get_compact_catalog(group: str | None = None) -> str:
    """Return compact one-liner catalog suitable for context injection."""
    lines = []
    for name, info in TOOL_CATALOG.items():
        if group and info.get("group") != group:
            continue
        lines.append(f"- {name}: {info['summary']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Self-test
    print("=== Tool Attention Self-Test ===\n")
    tests = [
        ("store a memory about ADA's KAIROS migration", 6),
        ("search for past decisions about NEXUS", 6),
        ("create a new behavioral instinct for failure patterns", 5),
        ("check system health and memory stats", 5),
        ("boot context and load identity", 4),
        ("reflect on today's session and record emotion", 5),
    ]
    for intent, k in tests:
        results = get_relevant_tools(intent, top_k=k)
        print(f"Intent: '{intent}'")
        for name, summary in results:
            print(f"  → {name}: {summary[:60]}...")
        print()

    groups = get_tool_groups()
    print(f"Total tools: {len(TOOL_CATALOG)}")
    print(f"Groups: {list(groups.keys())}")
    print(f"\nSample compact catalog (memory group):")
    print(get_compact_catalog("memory"))
