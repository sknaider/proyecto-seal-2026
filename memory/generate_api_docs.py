#!/usr/bin/env python3
"""
SOUL API Docs Generator
Extrae los 76 @mcp.tool() de mcp_server_v3.py y genera docs/api_reference.md

Uso:
  python3 generate_api_docs.py
  python3 generate_api_docs.py --json   # también genera docs/api_reference.json
"""

import ast
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PERU_TZ = ZoneInfo("America/Lima")

SERVER_PATH = Path(__file__).parent / "mcp_server_v3.py"
DOCS_DIR = Path(__file__).parent.parent / "docs"
MD_OUTPUT = DOCS_DIR / "api_reference.md"
JSON_OUTPUT = DOCS_DIR / "api_reference.json"

# Agrupación semántica de tools
GROUPS = {
    "Core Memory": [
        "memory_store", "memory_search", "memory_list", "memory_update",
        "memory_invalidate", "memory_utility_update", "memory_hybrid_search",
        "memory_cross_search", "memory_delta_sync", "memory_prefetch",
        "memory_feedback", "memory_flare", "memory_share_promote",
    ],
    "Boot & Identity": [
        "boot_context", "soul_activate", "soul_synthesize", "soul_snapshot",
        "soul_check", "working_state_get", "working_state_update",
    ],
    "Self-Awareness": [
        "self_reflect", "inner_thoughts", "ocean_auto_calibrate",
        "ocean_state_machine", "observation_analyze",
    ],
    "Connectome (Knowledge Graph)": [
        "connectome_build", "connectome_status", "connectome_entity",
        "connectome_entity_query", "connectome_smart_route",
        "connectome_bitemporal", "connectome_bitemporal_query",
        "connectome_ltp", "connectome_causal", "connectome_invalidate_edge",
        "temporal_graph_build", "temporal_query",
    ],
    "Instincts": [
        "instinct_create", "instinct_list", "instinct_search",
        "instinct_activate", "instinct_evolve", "instinct_promote",
        "instinct_consolidate",
    ],
    "D-MEM (Declarative Memory)": [
        "dmem_store", "dmem_gate",
    ],
    "Session & Distillation": [
        "session_save", "session_list", "session_recall",
        "session_distill", "session_distill_bulk", "sleep_gate",
        "sleep_gate_mood_retrieval",
    ],
    "Reasoning & Reflection": [
        "reasoning_trace_store", "reasoning_trace_update",
        "reasoning_trace_search", "reflection_synthesize",
    ],
    "Procedures": [
        "procedure_store", "procedure_update", "procedure_search",
    ],
    "Multi-Agent": [
        "memory_broadcast_read", "memory_broadcast_ack",
        "peer_model_query", "peer_model_update",
        "memory_communities", "ace_curator",
    ],
    "Rules & Events": [
        "rule_set", "rule_list", "event_log_append", "event_log_query",
    ],
    "Active Recall": [
        "active_recall",
    ],
    "System": [
        "health_check", "brain_health_report", "soul_snapshot",
        "microcompact_text", "microcompact_stats", "secret_scan",
    ],
}


def parse_tools(source_path: Path) -> list[dict]:
    """Parsea mcp_server_v3.py con AST y extrae todas las funciones @mcp.tool()."""
    source = source_path.read_text()
    tree = ast.parse(source)

    tools = []
    lines = source.splitlines()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue

        # Verificar si tiene @mcp.tool() decorator
        has_mcp_tool = False
        for dec in node.decorator_list:
            dec_src = ast.unparse(dec) if hasattr(ast, 'unparse') else ""
            if "mcp.tool" in dec_src or (
                isinstance(dec, ast.Call) and
                hasattr(dec.func, 'attr') and dec.func.attr == 'tool'
            ) or (
                isinstance(dec, ast.Attribute) and dec.attr == 'tool'
            ):
                has_mcp_tool = True
                break
            # Para Python < 3.9 sin ast.unparse
            if isinstance(dec, (ast.Call, ast.Attribute, ast.Name)):
                try:
                    raw = ast.dump(dec)
                    if 'tool' in raw:
                        has_mcp_tool = True
                        break
                except Exception:
                    pass

        if not has_mcp_tool:
            continue

        # Nombre
        name = node.name

        # Excluir funciones internas del decorator framework
        if name in ("wrapper", "_observed_tool"):
            continue

        # Docstring
        docstring = ast.get_docstring(node) or ""

        # Parámetros
        params = []
        args = node.args

        # Defaults (alinear con args desde el final)
        defaults = args.defaults
        n_args = len(args.args)
        n_defaults = len(defaults)
        offset = n_args - n_defaults

        for i, arg in enumerate(args.args):
            if arg.arg == "self":
                continue

            param = {"name": arg.arg}

            # Tipo
            if arg.annotation:
                try:
                    param["type"] = ast.unparse(arg.annotation)
                except Exception:
                    param["type"] = ast.dump(arg.annotation)
            else:
                param["type"] = "Any"

            # Default
            default_idx = i - offset
            if default_idx >= 0 and default_idx < len(defaults):
                try:
                    param["default"] = ast.unparse(defaults[default_idx])
                except Exception:
                    param["default"] = "..."
                param["required"] = False
            else:
                param["required"] = True

            params.append(param)

        # Return type
        return_type = ""
        if node.returns:
            try:
                return_type = ast.unparse(node.returns)
            except Exception:
                return_type = ""

        # Línea en el archivo
        line_no = node.lineno

        tools.append({
            "name": name,
            "docstring": docstring,
            "params": params,
            "return_type": return_type,
            "line": line_no,
        })

    # Ordenar por línea de aparición
    tools.sort(key=lambda t: t["line"])
    return tools


def assign_groups(tools: list[dict]) -> dict[str, list[dict]]:
    """Agrupa tools por categoría semántica."""
    assigned = {g: [] for g in GROUPS}
    assigned["Otros"] = []
    name_to_group = {}
    for group, names in GROUPS.items():
        for n in names:
            name_to_group[n] = group

    for tool in tools:
        group = name_to_group.get(tool["name"], "Otros")
        if group not in assigned:
            assigned[group] = []
        assigned[group].append(tool)

    # Eliminar grupos vacíos
    return {k: v for k, v in assigned.items() if v}


def format_params_md(params: list[dict]) -> str:
    if not params:
        return "_Sin parámetros_"
    lines = []
    for p in params:
        req = "**requerido**" if p.get("required", True) else f"opcional (default: `{p.get('default', '—')}`)"
        lines.append(f"- `{p['name']}` ({p['type']}) — {req}")
    return "\n".join(lines)


def generate_markdown(tools: list[dict], grouped: dict[str, list[dict]]) -> str:
    total = len(tools)
    now = datetime.now(PERU_TZ).strftime("%Y-%m-%d %H:%M Lima")

    lines = [
        "# SOUL API Reference",
        "",
        f"> Auto-generado desde `mcp_server_v3.py` — {now}  ",
        f"> **{total} herramientas MCP** disponibles",
        "",
        "## Índice",
        "",
    ]

    # TOC
    for group_name, group_tools in grouped.items():
        anchor = group_name.lower().replace(" ", "-").replace("(", "").replace(")", "").replace("&", "").replace(".", "")
        lines.append(f"- [{group_name}](#{anchor}) ({len(group_tools)} tools)")

    lines += ["", "---", ""]

    # Por grupo
    for group_name, group_tools in grouped.items():
        lines.append(f"## {group_name}")
        lines.append("")

        for tool in group_tools:
            lines.append(f"### `{tool['name']}`")
            lines.append("")

            if tool["docstring"]:
                # Primera línea del docstring como descripción corta
                first_line = tool["docstring"].split("\n")[0].strip()
                lines.append(first_line)
                lines.append("")

                # Resto del docstring como detalles
                rest = "\n".join(tool["docstring"].split("\n")[1:]).strip()
                if rest:
                    lines.append("<details>")
                    lines.append("<summary>Detalles</summary>")
                    lines.append("")
                    lines.append("```")
                    lines.append(rest)
                    lines.append("```")
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
            else:
                lines.append("_Sin documentación_")
                lines.append("")

            lines.append("**Parámetros:**")
            lines.append("")
            lines.append(format_params_md(tool["params"]))
            lines.append("")

            if tool["return_type"]:
                lines.append(f"**Retorna:** `{tool['return_type']}`")
                lines.append("")

            lines.append(f"_Línea {tool['line']} en mcp_server_v3.py_")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SOUL API Docs Generator")
    parser.add_argument("--json", action="store_true", help="También generar JSON")
    parser.add_argument("--stdout", action="store_true", help="Imprimir en stdout en vez de archivo")
    args = parser.parse_args()

    if not SERVER_PATH.exists():
        print(f"❌ No se encuentra: {SERVER_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Parseando {SERVER_PATH}...", file=sys.stderr)
    tools = parse_tools(SERVER_PATH)
    print(f"  → {len(tools)} @mcp.tool() encontrados", file=sys.stderr)

    grouped = assign_groups(tools)

    markdown = generate_markdown(tools, grouped)

    if args.stdout:
        print(markdown)
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    MD_OUTPUT.write_text(markdown)
    print(f"✅ Docs generados: {MD_OUTPUT}", file=sys.stderr)
    print(f"   {len(tools)} tools | {len(grouped)} grupos", file=sys.stderr)

    if args.json:
        data = {
            "generated_at": datetime.now(PERU_TZ).isoformat(),
            "total_tools": len(tools),
            "groups": {
                group: [{"name": t["name"], "docstring": t["docstring"], "params": t["params"]} for t in gtools]
                for group, gtools in grouped.items()
            }
        }
        JSON_OUTPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"✅ JSON generado: {JSON_OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
