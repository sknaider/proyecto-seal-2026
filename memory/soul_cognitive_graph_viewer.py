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


def render_3d_viewer_html(payload: dict[str, Any]) -> str:
    graph_json = json.dumps(payload, ensure_ascii=False)
    colors_json = json.dumps(NODE_COLORS)
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
    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SOUL Cognitive Graph 3D</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme: dark;
      --bg: #05070b;
      --panel: #111721;
      --line: #2d3848;
      --text: #edf3fb;
      --muted: #96a4b7;
      --accent: #8fb9ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      height: 100vh;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .app {
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr) 360px;
      height: 100vh;
      min-width: 980px;
    }
    .panel {
      background: color-mix(in srgb, var(--panel) 94%, transparent);
      border-right: 1px solid var(--line);
      padding: 16px;
      overflow: auto;
      z-index: 2;
    }
    .details {
      border-right: 0;
      border-left: 1px solid var(--line);
    }
    #scene {
      position: relative;
      min-width: 0;
      height: 100vh;
      background: radial-gradient(circle at 45% 45%, #0c1420 0%, #05070b 62%);
    }
    canvas { display: block; width: 100%; height: 100%; }
    h1 { margin: 0 0 12px; font-size: 18px; line-height: 1.2; }
    h2 { margin: 20px 0 8px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }
    label { display: block; margin: 10px 0 6px; color: var(--muted); font-size: 12px; }
    input, select, button {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #0b111a;
      color: var(--text);
      font-size: 13px;
    }
    button { cursor: pointer; margin-top: 8px; }
    button:hover { border-color: var(--accent); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .stat {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #0b111a;
    }
    .stat strong { display: block; font-size: 18px; }
    .stat span, .muted { color: var(--muted); font-size: 12px; }
    .legend { display: grid; gap: 6px; }
    .legend-item { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
    .swatch { width: 12px; height: 12px; border-radius: 50%; flex: 0 0 auto; }
    .hud {
      position: absolute;
      left: 16px;
      bottom: 16px;
      max-width: min(520px, calc(100% - 32px));
      padding: 10px 12px;
      border: 1px solid rgba(143, 185, 255, 0.22);
      border-radius: 6px;
      background: rgba(5, 7, 11, 0.68);
      color: var(--muted);
      font-size: 12px;
      z-index: 1;
      backdrop-filter: blur(8px);
    }
    pre {
      min-height: 220px;
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #0b111a;
      color: var(--muted);
      font-size: 12px;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1100px) {
      body { overflow: auto; }
      .app { grid-template-columns: 1fr; grid-template-rows: auto 78vh auto; min-width: 0; height: auto; }
      #scene { height: 78vh; min-height: 560px; }
      .panel, .details { border: 0; border-bottom: 1px solid var(--line); }
      .details { border-bottom: 0; }
    }
  </style>
  <script type="importmap">
    {
      "imports": {
        "three": "https://unpkg.com/three@0.160.0/build/three.module.js"
      }
    }
  </script>
</head>
<body>
  <div class="app">
    <aside class="panel">
      <h1>SOUL Cognitive Graph 3D</h1>
      <div class="row">
        <div class="stat"><strong id="statNodes">0</strong><span>nodes</span></div>
        <div class="stat"><strong id="statEdges">0</strong><span>edges</span></div>
      </div>
      <label for="search">Search</label>
      <input id="search" placeholder="William, Codex, rule, emotion">
      <label for="typeFilter">Node type</label>
      <select id="typeFilter">
        <option value="">All</option>
        <option value="memory">Memory</option>
        <option value="facet">Facet</option>
        <option value="agent">Agent</option>
        <option value="layer">Layer</option>
        <option value="category">Category</option>
      </select>
      <button id="apply">Apply View</button>
      <button id="reset">Reset Camera</button>
      <button id="spin">Pause Rotation</button>
      <button id="edges">Hide Edges</button>
      <h2>Legend</h2>
      <div class="legend">
        <div class="legend-item"><span class="swatch" style="background:#8fb9ff"></span>Memory orbit</div>
        <div class="legend-item"><span class="swatch" style="background:#f28f8f"></span>Facet shell</div>
        <div class="legend-item"><span class="swatch" style="background:#f4c76b"></span>Agent anchor</div>
        <div class="legend-item"><span class="swatch" style="background:#84d6a3"></span>Layer anchor</div>
        <div class="legend-item"><span class="swatch" style="background:#d9a1ff"></span>Category anchor</div>
      </div>
      <h2>Snapshot</h2>
      <p class="muted" id="generated"></p>
      <p class="muted"><a href="./viewer.html">Open 2D graph</a> · <a href="./graph.json">Open graph.json</a></p>
    </aside>
    <main id="scene">
      <div class="hud">Drag to rotate. Wheel to zoom. Click a node to inspect and focus it. The spatial layout keeps structural anchors near the center and memory nodes in connected semantic orbits.</div>
    </main>
    <section class="panel details">
      <h1>Selected Node</h1>
      <pre id="details">Click a 3D node to inspect it.</pre>
    </section>
  </div>
  <script type="module">
    import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';
    import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';

    const graph = __GRAPH_JSON__;
    const stats = __STATS_JSON__;
    const colors = __COLORS_JSON__;
    const sceneHost = document.getElementById('scene');
    const details = document.getElementById('details');
    const typeOrder = ['agent', 'layer', 'category', 'facet', 'memory'];
    const typeRadius = { agent: 18, layer: 46, category: 74, facet: 118, memory: 182 };
    const nodeMap = new Map(graph.nodes.map((node, index) => [node.id, { ...node, index }]));
    const edges = graph.edges.filter(edge => nodeMap.has(edge.source) && nodeMap.has(edge.target));
    const connected = new Map();
    const labels = [];
    let autoRotate = true;
    let showEdges = true;
    let selectedMesh = null;
    let frameCount = 0;

    document.getElementById('statNodes').textContent = stats.node_count;
    document.getElementById('statEdges').textContent = stats.edge_count;
    document.getElementById('generated').textContent = 'Generated ' + stats.generated_at;

    for (const edge of edges) {
      if (!connected.has(edge.source)) connected.set(edge.source, []);
      if (!connected.has(edge.target)) connected.set(edge.target, []);
      connected.get(edge.source).push(edge.target);
      connected.get(edge.target).push(edge.source);
    }

    function hash01(text) {
      let hash = 2166136261;
      for (let i = 0; i < text.length; i += 1) {
        hash ^= text.charCodeAt(i);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0) / 4294967295;
    }

    function spherical(index, count, radius, seed) {
      const n = Math.max(count, 1);
      const golden = Math.PI * (3 - Math.sqrt(5));
      const y = 1 - (index / Math.max(n - 1, 1)) * 2;
      const base = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * index + seed * Math.PI * 2;
      return new THREE.Vector3(Math.cos(theta) * base * radius, y * radius, Math.sin(theta) * base * radius);
    }

    function assignPositions() {
      const groups = new Map();
      for (const node of nodeMap.values()) {
        if (!groups.has(node.type)) groups.set(node.type, []);
        groups.get(node.type).push(node);
      }
      for (const type of typeOrder) {
        if (type === 'memory') continue;
        const group = groups.get(type) || [];
        group.sort((a, b) => a.id.localeCompare(b.id));
        group.forEach((node, index) => {
          node.position = spherical(index, group.length, typeRadius[type] || 90, hash01(type));
        });
      }
      const memories = (groups.get('memory') || []).sort((a, b) => a.id.localeCompare(b.id));
      memories.forEach((node, index) => {
        const links = connected.get(node.id) || [];
        const vector = new THREE.Vector3();
        for (const id of links) {
          const target = nodeMap.get(id);
          if (target?.position) vector.add(target.position.clone().normalize().multiplyScalar(Number(target.weight || 1)));
        }
        if (vector.lengthSq() < 0.001) vector.copy(spherical(index, memories.length, 1, hash01(node.id)).normalize());
        vector.normalize();
        const jitter = spherical(index, memories.length, 22 + hash01(node.id) * 26, hash01(node.id + ':jitter'));
        node.position = vector.multiplyScalar(typeRadius.memory + hash01(node.id) * 42).add(jitter);
      });
    }

    function nodeSize(node) {
      const weight = Number(node.weight || 1);
      if (node.type === 'memory') return Math.max(3.0, Math.min(8.5, 2.8 + weight * 0.5));
      if (node.type === 'facet') return Math.max(3.2, Math.min(7.0, 3.2 + weight * 4.0));
      return 7.5;
    }

    function makeTextSprite(text, color = '#edf3fb') {
      const canvas = document.createElement('canvas');
      canvas.width = 512;
      canvas.height = 128;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = '600 34px Inter, system-ui, sans-serif';
      ctx.fillStyle = color;
      ctx.shadowColor = 'rgba(0,0,0,0.85)';
      ctx.shadowBlur = 8;
      ctx.fillText(String(text).slice(0, 34), 18, 72);
      const texture = new THREE.CanvasTexture(canvas);
      texture.minFilter = THREE.LinearFilter;
      const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(38, 9.5, 1);
      return sprite;
    }

    assignPositions();

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x05070b);
    scene.fog = new THREE.FogExp2(0x05070b, 0.0022);

    const camera = new THREE.PerspectiveCamera(56, 1, 0.1, 1400);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(sceneHost.clientWidth, sceneHost.clientHeight);
    sceneHost.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.55;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const nodeMeshes = [];
    const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x344356, transparent: true, opacity: 0.42 });
    let edgeLines = null;

    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const keyLight = new THREE.DirectionalLight(0x8fb9ff, 1.2);
    keyLight.position.set(120, 180, 160);
    scene.add(keyLight);

    for (const radius of [typeRadius.layer, typeRadius.category, typeRadius.facet, typeRadius.memory]) {
      const shell = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 40, 24),
        new THREE.MeshBasicMaterial({ color: 0x8fb9ff, wireframe: true, transparent: true, opacity: radius === typeRadius.memory ? 0.035 : 0.055 })
      );
      scene.add(shell);
    }

    function buildEdges() {
      const positions = [];
      for (const edge of edges) {
        const source = nodeMap.get(edge.source);
        const target = nodeMap.get(edge.target);
        if (!source?.position || !target?.position) continue;
        positions.push(source.position.x, source.position.y, source.position.z);
        positions.push(target.position.x, target.position.y, target.position.z);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      edgeLines = new THREE.LineSegments(geometry, edgeMaterial);
      scene.add(edgeLines);
    }

    function buildNodes() {
      const geometry = new THREE.SphereGeometry(1, 18, 12);
      for (const node of nodeMap.values()) {
        const color = new THREE.Color(colors[node.type] || '#9ba7b6');
        const material = new THREE.MeshStandardMaterial({
          color,
          emissive: color.clone().multiplyScalar(node.type === 'memory' ? 0.12 : 0.24),
          roughness: 0.42,
          metalness: 0.05,
          transparent: true,
          opacity: 0.96
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.copy(node.position);
        const size = nodeSize(node);
        mesh.scale.setScalar(size);
        mesh.userData.node = node;
        scene.add(mesh);
        nodeMeshes.push(mesh);
        if (node.type !== 'memory' || node.weight >= 10) {
          const sprite = makeTextSprite(node.label, colors[node.type] || '#edf3fb');
          sprite.position.copy(node.position).add(new THREE.Vector3(size + 4, size + 4, 0));
          sprite.userData.owner = mesh;
          scene.add(sprite);
          labels.push(sprite);
        }
      }
    }

    function resetCamera() {
      camera.position.set(0, 82, 360);
      controls.target.set(0, 0, 0);
      controls.update();
    }

    function focusNode(mesh) {
      selectedMesh = mesh;
      const node = mesh.userData.node;
      for (const item of nodeMeshes) item.material.emissiveIntensity = item === mesh ? 1.7 : 0.8;
      controls.target.copy(mesh.position);
      camera.position.copy(mesh.position.clone().normalize().multiplyScalar(120).add(mesh.position));
      details.textContent = JSON.stringify(node, null, 2);
    }

    function cameraBasis() {
      const forward = controls.target.clone().sub(camera.position).normalize();
      const right = new THREE.Vector3().crossVectors(forward, camera.up).normalize();
      const up = camera.up.clone().normalize();
      return { forward, right, up };
    }

    function moveCamera(delta) {
      camera.position.add(delta);
      controls.target.add(delta);
      controls.update();
    }

    function zoomCamera(amount) {
      const { forward } = cameraBasis();
      camera.position.add(forward.multiplyScalar(amount));
      controls.update();
    }

    function rotateCamera(yaw, pitch) {
      const offset = camera.position.clone().sub(controls.target);
      const yawRotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
      offset.applyQuaternion(yawRotation);
      const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), offset).normalize();
      if (right.lengthSq() > 0.001) {
        const pitchRotation = new THREE.Quaternion().setFromAxisAngle(right, pitch);
        offset.applyQuaternion(pitchRotation);
      }
      camera.position.copy(controls.target).add(offset);
      controls.update();
    }

    function onKeyboardMove(event) {
      const targetTag = event.target?.tagName;
      if (targetTag === 'INPUT' || targetTag === 'SELECT' || targetTag === 'TEXTAREA') return;
      if (!event.ctrlKey || event.altKey || event.metaKey) return;
      const { right, up } = cameraBasis();
      const step = event.shiftKey ? 24 : 14;
      const angle = event.shiftKey ? 0.085 : 0.06;
      let handled = true;
      if (event.shiftKey && event.key === 'ArrowLeft') moveCamera(right.multiplyScalar(-step));
      else if (event.shiftKey && event.key === 'ArrowRight') moveCamera(right.multiplyScalar(step));
      else if (event.shiftKey && event.key === 'ArrowUp') moveCamera(up.multiplyScalar(step));
      else if (event.shiftKey && event.key === 'ArrowDown') moveCamera(up.multiplyScalar(-step));
      else if (event.key === 'ArrowLeft') rotateCamera(angle, 0);
      else if (event.key === 'ArrowRight') rotateCamera(-angle, 0);
      else if (event.key === 'ArrowUp') rotateCamera(0, angle);
      else if (event.key === 'ArrowDown') rotateCamera(0, -angle);
      else if (event.key === 'PageUp') zoomCamera(step);
      else if (event.key === 'PageDown') zoomCamera(-step);
      else handled = false;
      if (handled) event.preventDefault();
    }

    function applyView() {
      const query = document.getElementById('search').value.trim().toLowerCase();
      const type = document.getElementById('typeFilter').value;
      for (const mesh of nodeMeshes) {
        const node = mesh.userData.node;
        const blob = JSON.stringify(node).toLowerCase();
        const match = (!type || node.type === type) && (!query || blob.includes(query));
        mesh.visible = match;
        mesh.material.opacity = match ? 0.98 : 0.08;
      }
      for (const label of labels) label.visible = label.userData.owner.visible && label.userData.owner.userData.node.type !== 'memory';
    }

    function onPointerDown(event) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(nodeMeshes.filter(mesh => mesh.visible), false);
      if (hits.length) focusNode(hits[0].object);
    }

    function onResize() {
      const width = sceneHost.clientWidth;
      const height = sceneHost.clientHeight;
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    }

    buildEdges();
    buildNodes();
    resetCamera();
    onResize();
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('resize', onResize);
    window.addEventListener('keydown', onKeyboardMove);
    document.getElementById('apply').addEventListener('click', applyView);
    document.getElementById('reset').addEventListener('click', resetCamera);
    document.getElementById('spin').addEventListener('click', () => {
      autoRotate = !autoRotate;
      controls.autoRotate = autoRotate;
      document.getElementById('spin').textContent = autoRotate ? 'Pause Rotation' : 'Resume Rotation';
    });
    document.getElementById('edges').addEventListener('click', () => {
      showEdges = !showEdges;
      edgeLines.visible = showEdges;
      document.getElementById('edges').textContent = showEdges ? 'Hide Edges' : 'Show Edges';
    });

    function animate() {
      frameCount += 1;
      window.__SOUL3D_FRAME = frameCount;
      window.__SOUL3D_CAMERA = {
        x: camera.position.x,
        y: camera.position.y,
        z: camera.position.z,
        targetX: controls.target.x,
        targetY: controls.target.y,
        targetZ: controls.target.z
      };
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    }

    window.__SOUL3D_READY = true;
    window.__SOUL3D_NODE_COUNT = nodeMeshes.length;
    window.__SOUL3D_EDGE_COUNT = edges.length;
    animate();
  </script>
</body>
</html>
"""
    return (
        template.replace("__GRAPH_JSON__", graph_json)
        .replace("__STATS_JSON__", stats_json)
        .replace("__COLORS_JSON__", colors_json)
    )


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
            "Open `viewer_3d.html` in a browser for the interactive 3D perspective.",
            "",
            "Open this directory as an Obsidian vault for Markdown navigation.",
            "",
            "SOUL DB remains canonical. This export is read-only.",
            "",
            "## Files",
            "",
            "- `graph.json`: portable graph data.",
            "- `viewer.html`: local interactive Cytoscape.js viewer.",
            "- `viewer_3d.html`: local interactive Three.js 3D viewer.",
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
    write_text(root / "viewer_3d.html", render_3d_viewer_html(payload), allow_overwrite=args.allow_overwrite)
    write_markdown_subset(root, rows, payload, allow_overwrite=args.allow_overwrite)
    return {
        "status": "ok",
        "out": str(root),
        "viewer": str(root / "viewer.html"),
        "viewer_3d": str(root / "viewer_3d.html"),
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
