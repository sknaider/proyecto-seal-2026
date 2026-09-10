"""Regression contract for the MCP surface recovered after the 2026-09-03 reset."""
from __future__ import annotations

import ast
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(os.environ.get("SEAL_MCP_RECOVERY_SERVER", ROOT / "memory/mcp_server_v4.py"))
CRON = Path(os.environ.get("SEAL_MCP_RECOVERY_CRON", ROOT / "memory/instinct_cron.py"))


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"missing function: {name}")


def _tool_surface(source: str) -> dict[str, list[str]]:
    surface: dict[str, list[str]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_tool = any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        )
        if is_tool:
            surface[node.name] = [arg.arg for arg in node.args.args]
    return surface


# La superficie EXACTA de herramientas del MCP, declarada por nombre.
#
# Por que un CONJUNTO y no un numero (JARVIS, 10-sep-2026): esta guarda nacio para
# detectar que la recuperacion no hubiera PERDIDO herramientas, y lo hacia con
# `len(surface) == 30`. Un conteo desnudo tiene dos defectos, los dos medidos hoy:
#
#   1. su mensaje de error es `assert 32 == 30`, que no dice QUE cambio. Hubo que
#      abrir el AST a mano para descubrir cuales eran las dos nuevas.
#   2. no distingue una PERDIDA de una ADICION, que es justo lo que importa:
#      perder una herramienta es la regresion que esta guarda persigue; agregar una
#      es trabajo legitimo que igual debe pasar por revision.
#
# El conjunto conserva la propiedad de fallar cerrado -toda alta o baja rompe el
# test y obliga a declararla aca- y ademas DICE cual. Las dos ultimas altas son
# closed_loop_review y closed_loop_effect, el piloto de revision autenticada de ADA.
SUPERFICIE_DECLARADA = frozenset({
    "active_recall", "agent_task", "announce_agent", "boot_context",
    "closed_loop_effect", "closed_loop_review", "connectome_gateway",
    "consent_grant", "emotional_diary", "goal_action_model",
    "governance_challenge", "index_repo", "memory_gateway",
    "memory_hybrid_search", "memory_indexer", "memory_store",
    "reflective_diagnosis", "seal_bench", "search_code", "self_reflect",
    "send_user_file", "soul_gateway", "soul_recall_router_tool", "soul_snapshot",
    "style_fingerprint", "system_gateway", "tokenjuice_compress", "web_search",
    "webchat_listen", "webchat_poll", "working_state_get", "working_state_update",
})


def test_unit_complete_tool_surface_and_restored_signatures() -> None:
    surface = _tool_surface(_source(SERVER))
    faltan = SUPERFICIE_DECLARADA - surface.keys()
    sobran = surface.keys() - SUPERFICIE_DECLARADA
    assert not faltan, f"herramientas PERDIDAS respecto de la superficie declarada: {sorted(faltan)}"
    assert not sobran, (
        f"herramientas NUEVAS sin declarar: {sorted(sobran)}. "
        "Si el alta es legitima, agregala a SUPERFICIE_DECLARADA en el mismo cambio "
        "que la introduce: esta guarda existe para que ninguna entre ni salga sin revision."
    )
    assert surface["agent_task"][-2:] == ["status", "description_append"]
    assert surface["send_user_file"][-1] == "channel"
    assert {"boot_context", "active_recall", "memory_store", "webchat_listen"} <= surface.keys()
    agent_task = _function(_source(SERVER), "agent_task")
    assert 'elif action == "update":' in agent_task
    assert "_append_agent_task_description" in agent_task


def test_positive_recovered_cognitive_and_integrity_contracts() -> None:
    source = _source(SERVER)
    assert source.count("await get_query_embedding(query)") == 11
    assert "_E5_COS_LO, _E5_COS_HI = 0.70, 0.90" in source
    assert 'SELECT soul_v3.ocean_signal_apply(' in source
    assert 'SELECT soul_v3.relationship_signal_apply(' in source
    assert "feedback_signal_id" in _function(source, "memory_feedback")
    assert "async with conn.transaction()" in _function(source, "memory_feedback")
    assert "trust_observed=" in _function(source, "boot_context")
    assert "ocean_observed" in _function(source, "identity_eval")


def test_negative_known_regressions_are_absent() -> None:
    source = _source(SERVER)
    hybrid = _function(source, "memory_hybrid_search")
    recall = _function(source, "active_recall")
    activate = _function(source, "instinct_activate")
    assert "await get_embedding(query)" not in source
    assert "max_sem =" not in hybrid
    assert "asyncio.create_task(_observe(" not in recall
    assert "activation_count = activation_count + 1" not in activate
    assert 'SEAL_IDENTITY_MODE", "OFF"' not in _function(source, "_seal_identity_mode")


def test_control_terminal_state_distinguishes_declared_errors() -> None:
    namespace: dict[str, object] = {}
    exec(_function(_source(SERVER), "_terminal_state"), namespace)
    terminal_state = namespace["_terminal_state"]
    assert terminal_state({"ok": False}) == ("declared_error", False)
    assert terminal_state({"error": "boom"}) == ("declared_error", False)
    assert terminal_state({}) == ("empty_dict", True)
    assert terminal_state([]) == ("empty_collection", True)


def test_instinct_paths_are_owner_scoped_atomic_and_incremental() -> None:
    server = _source(SERVER)
    activate = _function(server, "instinct_activate")
    decay = _function(server, "instinct_decay")
    assert "WHERE id = $1 AND agent = $2 AND invalid_at IS NULL" in activate
    assert "async with conn.transaction()" in activate
    assert "_instinct_decay_reference(" in decay
    assert "last_activated_at" in decay and "last_decayed_at" in decay

    spec = importlib.util.spec_from_file_location("instinct_cron_recovery_subject", CRON)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    activated = datetime(2026, 8, 29, tzinfo=timezone.utc)
    decayed = activated + timedelta(days=1)
    assert module.decay_reference(created, activated, decayed) == decayed
    first = module.decayed_strength(0.9, 1.0, 0)
    second = module.decayed_strength(first, 1.0, 0)
    assert second > module.decayed_strength(first, 151.0, 0)


def test_send_user_file_fails_closed_and_routes_explicit_channel() -> None:
    body = _function(_source(SERVER), "send_user_file")
    assert "identidad MCP verificada" in body
    assert "agent no coincide" in body
    # Binary upload and inline text delivery must both authenticate.
    assert body.count('"session_key": chat_token') == 2
    assert '"channel": channel' in body
    assert "/api/agents/upload" in body
