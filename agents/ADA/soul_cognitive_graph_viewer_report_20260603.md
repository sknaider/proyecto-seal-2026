# SOUL Cognitive Graph Viewer - ADA Report - 2026-06-03

## Decision

Implementar visualizacion dual:

- `viewer.html`: visor local interactivo con Cytoscape.js.
- `graph.json`: grafo portable para herramientas.
- Markdown/Obsidian subset: notas de memorias y facetas para exploracion tipo vault.

## Why

Cytoscape.js encaja mejor que un lienzo propio porque ya soporta grafos,
layouts, estilos, seleccion, filtrado y serializacion JSON. Obsidian sigue
siendo util para navegacion manual, pero no reemplaza una vista interactiva
con filtros por tipo/faceta.

## Output

Generated directory:

`memory/diagnostic/soul_cognitive_graph_view/`

Files:

- `viewer.html`
- `graph.json`
- `README.md`
- `SOUL Memory Map.md`
- `Memories/*.md`
- `Facets/*.md`

Latest export:

- memories: 350
- nodes: 423
- edges: 3134
- files: 408

## Safety

- Read-only against SOUL DB.
- Does not write to DB.
- Does not call MCP tools.
- Viewer data is local export.
- Telemetry queries are not used.

## Use

Open:

`memory/diagnostic/soul_cognitive_graph_view/viewer.html`

Or open the folder as an Obsidian vault:

`memory/diagnostic/soul_cognitive_graph_view/`

