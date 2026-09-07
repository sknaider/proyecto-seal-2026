# SOUL Cognitive Graph Viewer - ADA Report - 2026-06-03

## Decision

Implementar visualizacion dual:

- `viewer.html`: visor local interactivo con Cytoscape.js.
- `viewer_3d.html`: visor local interactivo con Three.js/WebGL.
- `graph.json`: grafo portable para herramientas.
- Markdown/Obsidian subset: notas de memorias y facetas para exploracion tipo vault.

## Why

Cytoscape.js encaja mejor que un lienzo propio porque ya soporta grafos,
layouts, estilos, seleccion, filtrado y serializacion JSON. Obsidian sigue
siendo util para navegacion manual, pero no reemplaza una vista interactiva
con filtros por tipo/faceta.

La vista 3D agrega perspectiva espacial: anclas estructurales cerca del centro
`agent/layer/category/facet`, y memorias orbitando cerca de sus conexiones.
Esto permite observar densidad, clusters y profundidad cognitiva sin cambiar el
schema portable `soul_cognitive_graph_view.v1`.

## Output

Generated directory:

`memory/diagnostic/soul_cognitive_graph_view/`

Files:

- `viewer.html`
- `viewer_3d.html`
- `graph.json`
- `README.md`
- `SOUL Memory Map.md`
- `Memories/*.md`
- `Facets/*.md`

Latest export:

- memories: 350
- nodes: 423
- edges: 3138
- files: 410

3D controls:

- mouse drag: orbit
- wheel: zoom
- click node: inspect/focus
- `Ctrl + ArrowLeft/ArrowRight/ArrowUp/ArrowDown`: rotate camera
- `Ctrl + Shift + ArrowLeft/ArrowRight/ArrowUp/ArrowDown`: pan camera
- `Ctrl + PageUp/PageDown`: zoom camera

## Safety

- Read-only against SOUL DB.
- Does not write to DB.
- Does not call MCP tools.
- Viewer data is local export.
- Telemetry queries are not used.
- `viewer_3d.html` uses CDN Three.js module imports; `graph.json` and
  Obsidian files remain usable without WebGL.

## Validation

- `python3 -m py_compile memory/soul_cognitive_graph_viewer.py`: OK.
- `pytest -q memory/tests/test_soul_cognitive_graph_viewer.py`: 5 passed.
- Full graph suite: 36 passed.
- `scripts/seal_core_guard.py --health`: GREEN.
- HTTP server: `http://100.75.201.110:8799/viewer_3d.html` returns HTTP 200.
- Playwright desktop/mobile canvas validation: nonblank WebGL canvas,
  423 nodes, 3138 edges, no console/page errors.
- Playwright keyboard validation: `Ctrl+ArrowRight` changed camera
  `rotateDelta=23.316`; `Ctrl+Shift+ArrowLeft` changed camera
  `panDelta=36.242`.

## Use

Open:

`memory/diagnostic/soul_cognitive_graph_view/viewer.html`

3D:

`memory/diagnostic/soul_cognitive_graph_view/viewer_3d.html`

Or open the folder as an Obsidian vault:

`memory/diagnostic/soul_cognitive_graph_view/`
