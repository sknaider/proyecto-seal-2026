#!/usr/bin/env python3
"""Export and visualize the SOUL Cognitive Graph.

Read-only exporter. It creates:
- graph.json: portable nodes/edges for tooling.
- viewer.html: local interactive Cytoscape.js viewer.
- Markdown notes: Obsidian-compatible graph/vault subset.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

try:
    from .seal_secrets import pg_dsn
    from .soul_cognitive_graph import extract_facets
    from .soul_map_exporter import (
        MemoryRow,
        fetch_memories,
        frontmatter,
        memory_note_ref,
        render_index,
        render_memory_note,
        slugify,
        write_text,
    )
except ImportError:  # CLI execution: python memory/soul_cognitive_graph_viewer.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seal_secrets import pg_dsn
    from soul_cognitive_graph import extract_facets
    from soul_map_exporter import (
        MemoryRow,
        fetch_memories,
        frontmatter,
        memory_note_ref,
        render_index,
        render_memory_note,
        slugify,
        write_text,
    )


DEFAULT_OUT = Path(__file__).parent / "diagnostic" / "soul_cognitive_graph_view"
NODE_COLORS = {
    "memory": "#8fb9ff",
    "agent": "#f4c76b",
    "layer": "#84d6a3",
    "category": "#d9a1ff",
    "facet": "#f28f8f",
}


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    type: str
    weight: float = 1.0
    soul_id: int | None = None
    agent: str | None = None
    layer: str | None = None
    category: str | None = None
    preview: str | None = None
    facet_kind: str | None = None


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source: str
    target: str
    relation: str
    weight: float = 1.0
    soul_id: int | None = None


def preview_text(content: str, *, max_chars: int = 220) -> str:
    text = " ".join(content.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def facet_label(facet: str) -> tuple[str, str]:
    kind, sep, value = facet.partition(":")
    if not sep:
        return "facet", facet
    return kind, value


def node_id_for_facet(facet: str) -> str:
    return f"facet:{slugify(facet, fallback='facet', max_len=96)}"


def add_node(nodes: dict[str, GraphNode], node: GraphNode) -> None:
    current = nodes.get(node.id)
    if current is None or node.weight > current.weight:
        nodes[node.id] = node


def edge_id(source: str, target: str, relation: str) -> str:
    return f"{slugify(source, max_len=80)}--{slugify(relation, max_len=32)}--{slugify(target, max_len=80)}"


def build_cognitive_graph_payload(rows: list[MemoryRow], *, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(UTC)
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    for row in rows:
        mem_id = f"memory:{row.id}"
        add_node(
            nodes,
            GraphNode(
                id=mem_id,
                label=f"#{row.id}",
                type="memory",
                weight=max(row.importance, 1),
                soul_id=row.id,
                agent=row.agent,
                layer=row.layer,
                category=row.category,
                preview=preview_text(row.content),
            ),
        )

        structural_targets = [
            (f"agent:{row.agent}", row.agent, "agent", "agent"),
            (f"layer:{row.layer}", row.layer, "layer", "layer"),
            (f"category:{slugify(row.category or 'none')}", row.category or "none", "category", "category"),
        ]
        for target_id, label, node_type, relation in structural_targets:
            add_node(nodes, GraphNode(id=target_id, label=label, type=node_type, weight=1.0))
            edges[edge_id(mem_id, target_id, relation)] = GraphEdge(
                id=edge_id(mem_id, target_id, relation),
                source=mem_id,
                target=target_id,
                relation=relation,
                weight=1.0,
                soul_id=row.id,
            )

        facets = extract_facets(row.content, category=row.category, layer=row.layer)
        for facet, weight in facets.items():
            kind, label = facet_label(facet)
            facet_id = node_id_for_facet(facet)
            add_node(
                nodes,
                GraphNode(
                    id=facet_id,
                    label=label,
                    type="facet",
                    weight=weight,
                    facet_kind=kind,
                ),
            )
            edges[edge_id(mem_id, facet_id, "facet")] = GraphEdge(
                id=edge_id(mem_id, facet_id, "facet"),
                source=mem_id,
                target=facet_id,
                relation="facet",
                weight=weight,
                soul_id=row.id,
            )

    by_type: dict[str, int] = {}
    for node in nodes.values():
        by_type[node.type] = by_type.get(node.type, 0) + 1
    return {
        "generated_at": generated_at.isoformat(),
        "schema": "soul_cognitive_graph_view.v1",
        "memory_count": len(rows),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes_by_type": dict(sorted(by_type.items())),
        "nodes": [asdict(node) for node in sorted(nodes.values(), key=lambda item: (item.type, item.id))],
        "edges": [asdict(edge) for edge in sorted(edges.values(), key=lambda item: item.id)],
    }


def cytoscape_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for node in payload["nodes"]:
        elements.append({"group": "nodes", "data": node})
    for edge in payload["edges"]:
        elements.append({"group": "edges", "data": edge})
    return elements


def render_viewer_html(payload: dict[str, Any]) -> str:
    data_json = json.dumps(cytoscape_elements(payload), ensure_ascii=False)
    stats_json = json.dumps(
        {
            "generated_at": payload["generated_at"],
            "memory_count": payload["memory_count"],
            "node_count": payload["node_count"],
            "edge_count": payload["edge_count"],
            "nodes_by_type": payload["nodes_by_type"],
        },
        ensure_ascii=False,
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SOUL Cognitive Graph</title>
  <script src="https://unpkg.com/cytoscape/dist/cytoscape.min.js"></script>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #111318;
      --panel: #1b2028;
      --line: #313946;
      --text: #e8edf4;
      --muted: #9ba7b6;
      --accent: #8fb9ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      overflow: hidden;
    }}
    .app {{
      display: grid;
      grid-template-columns: 320px 1fr 360px;
      height: 100vh;
      min-width: 960px;
    }}
    aside, .details {{
      background: var(--panel);
      border-right: 1px solid var(--line);
      padding: 16px;
      overflow: auto;
    }}
    .details {{ border-right: 0; border-left: 1px solid var(--line); }}
    h1 {{ font-size: 18px; margin: 0 0 14px; }}
    h2 {{ font-size: 13px; margin: 20px 0 8px; color: var(--muted); text-transform: uppercase; }}
    label {{ display: block; font-size: 12px; color: var(--muted); margin: 10px 0 6px; }}
    input, select, button {{
      width: 100%;
      background: #111821;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font-size: 13px;
    }}
    button {{ cursor: pointer; margin-top: 8px; }}
    button:hover {{ border-color: var(--accent); }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      margin-bottom: 8px;
      background: #151a22;
    }}
    .stat strong {{ display: block; font-size: 18px; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    #cy {{ width: 100%; height: 100%; background: #0f1217; }}
    .legend {{ display: grid; gap: 6px; }}
    .legend-item {{ display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }}
    .swatch {{ width: 12px; height: 12px; border-radius: 50%; }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #111821;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      color: var(--muted);
      font-size: 12px;
    }}
    .muted {{ color: var(--muted); font-size: 12px; }}
    @media (max-width: 1100px) {{
      body {{ overflow: auto; }}
      .app {{ grid-template-columns: 1fr; grid-template-rows: auto 70vh auto; min-width: 0; height: auto; }}
      aside, .details {{ border: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>SOUL Cognitive Graph</h1>
      <div class="row">
        <div class="stat"><strong id="statMem">0</strong><span>memories</span></div>
        <div class="stat"><strong id="statEdges">0</strong><span>edges</span></div>
      </div>
      <label for="search">Search</label>
      <input id="search" placeholder="William, Codex, dm, emotion">
      <label for="typeFilter">Node type</label>
      <select id="typeFilter">
        <option value="">All</option>
        <option value="memory">Memory</option>
        <option value="facet">Facet</option>
        <option value="agent">Agent</option>
        <option value="layer">Layer</option>
        <option value="category">Category</option>
      </select>
      <label for="layout">Layout</label>
      <select id="layout">
        <option value="cose">CoSE</option>
        <option value="breadthfirst">Breadthfirst</option>
        <option value="concentric">Concentric</option>
        <option value="grid">Grid</option>
      </select>
      <button id="apply">Apply Filters</button>
      <button id="fit">Fit Graph</button>
      <h2>Legend</h2>
      <div class="legend">
        <div class="legend-item"><span class="swatch" style="background:{NODE_COLORS['memory']}"></span>Memory</div>
        <div class="legend-item"><span class="swatch" style="background:{NODE_COLORS['facet']}"></span>Facet</div>
        <div class="legend-item"><span class="swatch" style="background:{NODE_COLORS['agent']}"></span>Agent</div>
        <div class="legend-item"><span class="swatch" style="background:{NODE_COLORS['layer']}"></span>Layer</div>
        <div class="legend-item"><span class="swatch" style="background:{NODE_COLORS['category']}"></span>Category</div>
      </div>
      <h2>Snapshot</h2>
      <p class="muted" id="generated"></p>
    </aside>
    <main id="cy"></main>
    <section class="details">
      <h1>Selected Node</h1>
      <pre id="details">Click a node to inspect it.</pre>
    </section>
  </div>
  <script>
    const elements = {data_json};
    const stats = {stats_json};
    const colors = {json.dumps(NODE_COLORS)};

    document.getElementById('statMem').textContent = stats.memory_count;
    document.getElementById('statEdges').textContent = stats.edge_count;
    document.getElementById('generated').textContent = 'Generated ' + stats.generated_at;

    function makeCy(filteredElements) {{
      if (!window.cytoscape) {{
        document.getElementById('details').textContent = 'Cytoscape.js did not load. Open graph.json or enable local network access for the CDN.';
        return null;
      }}
      return cytoscape({{
        container: document.getElementById('cy'),
        elements: filteredElements,
        style: [
          {{ selector: 'node', style: {{
            'label': 'data(label)',
            'background-color': ele => colors[ele.data('type')] || '#9ba7b6',
            'width': ele => Math.max(22, Math.min(64, 18 + Number(ele.data('weight') || 1) * 4)),
            'height': ele => Math.max(22, Math.min(64, 18 + Number(ele.data('weight') || 1) * 4)),
            'font-size': 10,
            'color': '#e8edf4',
            'text-outline-color': '#0f1217',
            'text-outline-width': 2
          }} }},
          {{ selector: 'edge', style: {{
            'width': ele => Math.max(1, Math.min(4, Number(ele.data('weight') || 1) * 2)),
            'line-color': '#465365',
            'target-arrow-color': '#465365',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'opacity': 0.72
          }} }},
          {{ selector: ':selected', style: {{ 'border-width': 3, 'border-color': '#ffffff', 'line-color': '#8fb9ff', 'target-arrow-color': '#8fb9ff' }} }}
        ],
        layout: {{ name: document.getElementById('layout').value, animate: false, fit: true, padding: 40 }}
      }});
    }}

    let cy = makeCy(elements);
    if (cy) {{
      cy.on('tap', 'node', evt => {{
        const data = evt.target.data();
        document.getElementById('details').textContent = JSON.stringify(data, null, 2);
      }});
    }}

    function filtered() {{
      const q = document.getElementById('search').value.trim().toLowerCase();
      const type = document.getElementById('typeFilter').value;
      const keep = new Set();
      for (const el of elements) {{
        if (el.group !== 'nodes') continue;
        const d = el.data;
        const blob = JSON.stringify(d).toLowerCase();
        if (type && d.type !== type) continue;
        if (q && !blob.includes(q)) continue;
        keep.add(d.id);
      }}
      const out = [];
      for (const el of elements) {{
        if (el.group === 'nodes' && keep.has(el.data.id)) out.push(el);
        if (el.group === 'edges' && keep.has(el.data.source) && keep.has(el.data.target)) out.push(el);
      }}
      return out;
    }}

    document.getElementById('apply').addEventListener('click', () => {{
      if (cy) cy.destroy();
      cy = makeCy(filtered());
      if (cy) cy.on('tap', 'node', evt => {{
        document.getElementById('details').textContent = JSON.stringify(evt.target.data(), null, 2);
      }});
    }});
    document.getElementById('fit').addEventListener('click', () => cy && cy.fit(null, 40));
  </script>
</body>
</html>
"""


def render_readme(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            frontmatter(
                {
                    "generated_by": "memory/soul_cognitive_graph_viewer.py",
                    "generated_at": payload["generated_at"],
                    "memory_count": payload["memory_count"],
                    "node_count": payload["node_count"],
                    "edge_count": payload["edge_count"],
                }
            ),
            "# SOUL Cognitive Graph View",
            "",
            "Open `viewer.html` in a browser for the interactive graph.",
            "",
            "Open this directory as an Obsidian vault for Markdown navigation.",
            "",
            "SOUL DB remains canonical. This export is read-only.",
            "",
            "## Files",
            "",
            "- `graph.json`: portable graph data.",
            "- `viewer.html`: local interactive Cytoscape.js viewer.",
            "- `Memories/`: generated memory notes.",
            "- `Facets/`: generated facet notes.",
            "",
        ]
    )


def render_facet_note(node: GraphNode, incoming: list[GraphEdge]) -> str:
    links = [f"[[{edge.source.replace('memory:', 'Memories/Memory-')}]]" for edge in incoming[:50]]
    return "\n".join(
        [
            frontmatter(
                {
                    "generated_by": "memory/soul_cognitive_graph_viewer.py",
                    "node_id": node.id,
                    "facet_kind": node.facet_kind or "",
                    "memory_links": len(incoming),
                }
            ),
            f"# {node.label}",
            "",
            f"Facet kind: `{node.facet_kind or 'facet'}`",
            "",
            "## Connected Memories",
            "",
            *[f"- {link}" for link in links],
            "",
        ]
    )


def write_markdown_subset(root: Path, rows: list[MemoryRow], payload: dict[str, Any], *, allow_overwrite: bool) -> None:
    write_text(root / "README.md", render_readme(payload), allow_overwrite=allow_overwrite)
    write_text(root / "SOUL Memory Map.md", render_index(rows, datetime.fromisoformat(payload["generated_at"])), allow_overwrite=allow_overwrite)
    for row in rows:
        note_path = root / "Memories" / f"Memory-{row.id}.md"
        write_text(note_path, render_memory_note(row), allow_overwrite=allow_overwrite)

    nodes = {node["id"]: GraphNode(**node) for node in payload["nodes"]}
    edges = [GraphEdge(**edge) for edge in payload["edges"]]
    for node in nodes.values():
        if node.type != "facet":
            continue
        incoming = [edge for edge in edges if edge.target == node.id and edge.relation == "facet"]
        note_path = root / "Facets" / f"{slugify(node.facet_kind or 'facet')}-{slugify(node.label)}.md"
        write_text(note_path, render_facet_note(node, incoming), allow_overwrite=allow_overwrite)


async def export_view(args: argparse.Namespace) -> dict[str, Any]:
    root = args.out.resolve()
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows = await fetch_memories(
            conn,
            agents=args.agent,
            limit=args.limit,
            min_importance=args.min_importance,
        )
    finally:
        await conn.close()

    payload = build_cognitive_graph_payload(rows)
    if args.dry_run:
        return {
            "status": "dry-run",
            "out": str(root),
            "memory_count": payload["memory_count"],
            "node_count": payload["node_count"],
            "edge_count": payload["edge_count"],
            "nodes_by_type": payload["nodes_by_type"],
        }

    root.mkdir(parents=True, exist_ok=True)
    write_text(root / "graph.json", json.dumps(payload, indent=2, ensure_ascii=False) + "\n", allow_overwrite=args.allow_overwrite)
    write_text(root / "viewer.html", render_viewer_html(payload), allow_overwrite=args.allow_overwrite)
    write_markdown_subset(root, rows, payload, allow_overwrite=args.allow_overwrite)
    return {
        "status": "ok",
        "out": str(root),
        "viewer": str(root / "viewer.html"),
        "graph": str(root / "graph.json"),
        "memory_count": payload["memory_count"],
        "node_count": payload["node_count"],
        "edge_count": payload["edge_count"],
        "file_count": sum(1 for path in root.rglob("*") if path.is_file()),
        "nodes_by_type": payload["nodes_by_type"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a read-only SOUL Cognitive Graph viewer.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory.")
    parser.add_argument("--agent", action="append", default=["ADA"], help="Agent filter; can be repeated.")
    parser.add_argument("--limit", type=int, default=350, help="Maximum memories to export.")
    parser.add_argument("--min-importance", type=int, default=8, help="Minimum memory importance.")
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting generated files.")
    parser.add_argument("--dry-run", action="store_true", help="Only print summary; do not write files.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(export_view(args))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
