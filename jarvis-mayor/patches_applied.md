# Parches Aplicados — JARVIS Mayor

## 2026-03-31

| Archivo | Parche | Razón |
|---|---|---|
| soul_awareness.py:write_memory | Dedup exacto + near-dedup (sim>0.92) + Neo4j write | Memorias duplicadas + fantasmas sin Neo4j |
| soul_consolidate.py | Dedup antes de crear summary + Qdrant cleanup al invalidar | Consolidation spam + Qdrant desync |
| mcp_server_v2.py:memory_store | Timeout 10s en embedding + conflict detection limpia Qdrant+PG | Bloqueo por Ollama + records huérfanos |
| mcp_server_v2.py:boot_context | Spreading activation 3 seeds/2 hops (era 5/3) + truncate 200 chars | Boot lento (4s → 1.5s) |
| mcp_server_v2.py:conflict_detection | Indentación corregida (SyntaxError) | MCP no arrancaba |
| soul_reflect.py | Verifica locked=TRUE antes de overwrite style | Estilo se reseteaba cada sesión |
| soul_awareness.py:checkpoint | Persiste known_artifacts en awareness_checkpoint.json | Auto-scan duplicaba archivos al reiniciar |
| soul_awareness.py:startup | Restaura known_artifacts del checkpoint al arrancar | Complemento del anterior |
| db.py | Pool min=1 max=3 (era min=2 max=10) | 5 instancias MCP agotaban conexiones PG |
| CLAUDE.md | Ritual ligero: responder PRIMERO, guardar DESPUÉS, max 1-2 stores | JARVIS tardaba 6+ memory_stores por respuesta |
| PostgreSQL rules | response_ritual actualizado a LIGERO | Complemento del anterior |
| PostgreSQL rules | importance_scale (usar rango completo 3-10) | 64% memorias eran imp 8-9 |
| PostgreSQL rules | identity_boundaries (no imitar al otro agente) | JARVIS pidió consola por influencia de ADA |
| Ollama systemd | KEEP_ALIVE=-1 | Embedding 1444ms → 43ms |
| style_fingerprints | locked=TRUE para ADA/JARVIS/DUM | Estilos no se borran más |
