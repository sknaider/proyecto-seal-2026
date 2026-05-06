#!/usr/bin/env python3
"""Genera mcp_server_v4.py con el proxy pattern desde mcp_server_v3.py.

8 direct tools (siempre visibles) + 4 gateway tools.
84 herramientas pasan a funciones internas (sin @mcp.tool()).

Ejecucion: python3 generate_mcp_v4.py
"""
from pathlib import Path

SRC = Path(__file__).parent / "mcp_server_v3.py"
DST = Path(__file__).parent / "mcp_server_v4.py"

DIRECT_TOOLS = {
    "memory_store",
    "memory_hybrid_search",
    "boot_context",
    "self_reflect",
    "soul_snapshot",
    "working_state_get",
    "working_state_update",
    "active_recall",
}

GATEWAY_CODE = '''

# ── SEAL MCP Proxy Layer — 4 gateway tools ──
# Generado por NEXUS per spec_mcp_proxy_pattern_20260426.md
# 92 tools → 8 direct + 4 gateways = 12 visibles (88% reduccion overhead)


@mcp.tool()
async def memory_gateway(
    action: str,
    agent: Optional[str] = None,
    query: Optional[str] = None,
    content: Optional[str] = None,
    memory_id: Optional[int] = None,
    importance: Optional[int] = None,
    category: Optional[str] = None,
    scope: Optional[str] = None,
    limit: Optional[int] = None,
    metadata: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> str:
    """Gateway para operaciones de memoria extendidas.

    action: search | list | update | invalidate | utility_update | feedback |
            prefetch | flare | decompress | type_stats | delta_sync |
            share_promote | cross_search | broadcast_read | broadcast_ack |
            communities | cold_query | cold_migrate | cold_stats | ace_curate

    Ejemplo: memory_gateway(action="invalidate", memory_id=1234, agent="JARVIS")
    """
    _dispatch = {
        "search": memory_search,
        "list": memory_list,
        "update": memory_update,
        "invalidate": memory_invalidate,
        "utility_update": memory_utility_update,
        "feedback": memory_feedback,
        "prefetch": memory_prefetch,
        "flare": memory_flare,
        "decompress": memory_decompress,
        "type_stats": memory_type_stats,
        "delta_sync": memory_delta_sync,
        "share_promote": memory_share_promote,
        "cross_search": memory_cross_search,
        "broadcast_read": memory_broadcast_read,
        "broadcast_ack": memory_broadcast_ack,
        "communities": memory_communities,
        "cold_query": cold_archive_query,
        "cold_migrate": cold_archive_migrate,
        "cold_stats": cold_archive_stats,
        "ace_curate": ace_curator,
    }
    handler = _dispatch.get(action)
    if not handler:
        return f"Error: action \'{action}\' no reconocida. Validas: {list(_dispatch.keys())}"
    kwargs = {k: v for k, v in {
        "agent": agent, "query": query, "content": content,
        "memory_id": memory_id, "importance": importance,
        "category": category, "scope": scope, "limit": limit,
        "metadata": metadata,
    }.items() if v is not None}
    if extra:
        kwargs.update(extra)
    return await handler(**kwargs)


@mcp.tool()
async def soul_gateway(
    action: str,
    agent: Optional[str] = None,
    query: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[int] = None,
    rule_key: Optional[str] = None,
    active: Optional[bool] = None,
    priority: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: Optional[int] = None,
    extra: Optional[dict] = None,
) -> str:
    """Gateway para operaciones de alma, identidad, reglas, sesiones, instintos, creencias.

    action: activate | synthesize | check | identity_eval | ocean_calibrate | ocean_state |
            inner_thoughts | belief_query | belief_update | rule_set | rule_list |
            event_append | event_query | session_save | session_recall | session_list |
            session_distill | session_distill_bulk | reflect | observe |
            procedure_store | procedure_update | procedure_search |
            trace_store | trace_update | trace_search |
            peer_query | peer_update |
            instinct_create | instinct_list | instinct_search | instinct_activate |
            instinct_evolve | instinct_consolidate | instinct_promote

    Ejemplo: soul_gateway(action="instinct_list", agent="NEXUS")
    """
    _dispatch = {
        "activate": soul_activate,
        "synthesize": soul_synthesize,
        "check": soul_check,
        "identity_eval": identity_eval,
        "ocean_calibrate": ocean_auto_calibrate,
        "ocean_state": ocean_state_machine,
        "inner_thoughts": inner_thoughts,
        "belief_query": belief_query,
        "belief_update": belief_update,
        "rule_set": rule_set,
        "rule_list": rule_list,
        "event_append": event_log_append,
        "event_query": event_log_query,
        "session_save": session_save,
        "session_recall": session_recall,
        "session_list": session_list,
        "session_distill": session_distill,
        "session_distill_bulk": session_distill_bulk,
        "reflect": reflection_synthesize,
        "observe": observation_analyze,
        "procedure_store": procedure_store,
        "procedure_update": procedure_update,
        "procedure_search": procedure_search,
        "trace_store": reasoning_trace_store,
        "trace_update": reasoning_trace_update,
        "trace_search": reasoning_trace_search,
        "peer_query": peer_model_query,
        "peer_update": peer_model_update,
        "instinct_create": instinct_create,
        "instinct_list": instinct_list,
        "instinct_search": instinct_search,
        "instinct_activate": instinct_activate,
        "instinct_evolve": instinct_evolve,
        "instinct_consolidate": instinct_consolidate,
        "instinct_promote": instinct_promote,
    }
    handler = _dispatch.get(action)
    if not handler:
        return f"Error: action \'{action}\' no reconocida. Validas: {list(_dispatch.keys())}"
    kwargs = {k: v for k, v in {
        "agent": agent, "query": query, "content": content,
        "category": category, "importance": importance,
        "rule_key": rule_key, "active": active, "priority": priority,
        "event_type": event_type, "limit": limit,
    }.items() if v is not None}
    if extra:
        kwargs.update(extra)
    return await handler(**kwargs)


@mcp.tool()
async def connectome_gateway(
    action: str,
    agent: Optional[str] = None,
    query: Optional[str] = None,
    entity: Optional[str] = None,
    entity_type: Optional[str] = None,
    memory_ids: Optional[list] = None,
    limit: Optional[int] = None,
    extra: Optional[dict] = None,
) -> str:
    """Gateway para grafo temporal y conectome Neo4j.

    action: build | status | entity | entity_query | extract_facts |
            bitemporal | bitemporal_query | causal | contradiction |
            invalidate_edge | ltp | smart_route |
            temporal_build | temporal_query | temporal_summary |
            latent_retrieve | magma_retrieve

    Ejemplo: connectome_gateway(action="status")
    """
    _dispatch = {
        "build": connectome_build,
        "status": connectome_status,
        "entity": connectome_entity,
        "entity_query": connectome_entity_query,
        "extract_facts": connectome_extract_facts,
        "bitemporal": connectome_bitemporal,
        "bitemporal_query": connectome_bitemporal_query,
        "causal": connectome_causal,
        "contradiction": connectome_contradiction_detect,
        "invalidate_edge": connectome_invalidate_edge,
        "ltp": connectome_ltp,
        "smart_route": connectome_smart_route,
        "temporal_build": temporal_graph_build,
        "temporal_query": temporal_query,
        "temporal_summary": temporal_summary_get,
        "latent_retrieve": latent_graph_retrieve,
        "magma_retrieve": magma_retrieve,
    }
    handler = _dispatch.get(action)
    if not handler:
        return f"Error: action \'{action}\' no reconocida. Validas: {list(_dispatch.keys())}"
    kwargs = {k: v for k, v in {
        "agent": agent, "query": query, "entity": entity,
        "entity_type": entity_type, "memory_ids": memory_ids, "limit": limit,
    }.items() if v is not None}
    if extra:
        kwargs.update(extra)
    return await handler(**kwargs)


@mcp.tool()
async def system_gateway(
    action: str,
    agent: Optional[str] = None,
    query: Optional[str] = None,
    text: Optional[str] = None,
    limit: Optional[int] = None,
    extra: Optional[dict] = None,
) -> str:
    """Gateway para diagnostico, mantenimiento y herramientas de sistema.

    action: health | brain_health | tree_stats | secret_scan |
            microcompact | microcompact_stats | sleep_gate | sleep_mood |
            dmem_gate | dmem_store | erl_inject | erl_reflect

    Ejemplo: system_gateway(action="health")
    """
    _dispatch = {
        "health": health_check,
        "brain_health": brain_health_report,
        "tree_stats": tree_stats,
        "secret_scan": secret_scan,
        "microcompact": microcompact_text,
        "microcompact_stats": microcompact_stats,
        "sleep_gate": sleep_gate,
        "sleep_mood": sleep_gate_mood_retrieval,
        "dmem_gate": dmem_gate,
        "dmem_store": dmem_store,
        "erl_inject": erl_inject,
        "erl_reflect": erl_reflect,
    }
    handler = _dispatch.get(action)
    if not handler:
        return f"Error: action \'{action}\' no reconocida. Validas: {list(_dispatch.keys())}"
    kwargs = {k: v for k, v in {
        "agent": agent, "query": query, "text": text, "limit": limit,
    }.items() if v is not None}
    if extra:
        kwargs.update(extra)
    return await handler(**kwargs)

'''


def generate_v4():
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    i = 0
    removed = 0
    kept = 0

    while i < len(lines):
        line = lines[i]

        # Detect @mcp.tool() decorator
        if line.strip() == "@mcp.tool()":
            # Lookahead: find the function name on the next non-empty line
            j = i + 1
            func_name = None
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                if next_line.startswith("async def "):
                    parts = next_line[len("async def "):].split("(")
                    func_name = parts[0].strip()
                    break
                j += 1

            if func_name and func_name not in DIRECT_TOOLS:
                # Remove the decorator — replace with blank line to preserve line count roughly
                removed += 1
                i += 1
                continue
            else:
                kept += 1

        # Replace port 8766 → 8771 (match .mcp.json) and update server identity
        if '"8766"' in line:
            line = line.replace('"8766"', '"8771"')
        if "'8766'" in line:
            line = line.replace("'8766'", "'8771'")

        # Insert gateways before the # ── Main ── block
        if line.strip() == "# ── Main ──":
            out.append(GATEWAY_CODE)

        out.append(line)
        i += 1

    DST.write_text("".join(out), encoding="utf-8")
    print(f"Generated {DST}")
    print(f"  Decorators removed (gateway): {removed}")
    print(f"  Decorators kept (direct):     {kept}")
    print(f"  Total lines: {len(out)}")


if __name__ == "__main__":
    generate_v4()
