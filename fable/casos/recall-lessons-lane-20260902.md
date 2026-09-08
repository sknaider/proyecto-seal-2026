# Caso para FABLE — recall-lessons-lane-20260902 (owner ADA; revisor JARVIS; gate STATIC_OK)
- Manifiesto: `quality/manifests/recall-lessons-lane-20260902.json`. Sujetos: `memory/mcp_server_v4.py`, `memory/instinct_cron.py`, dos arneses de mutación propios, `tools/seal_memory_files_ingest.py`. Tests: superficie MCP recuperada, active_recall_hook, carril de lecciones, ingesta de archivos de memoria.
- Qué: revalidación del carril aditivo de lecciones y de la superficie MCP perdida por el reset del 3-sep (agent_task update, autenticación de send_user_file, embeddings E5, feedback atómico, límites OCEAN, instintos incrementales).
- Hoy: el sujeto `mcp_server_v4.py` cambió en 0ac4bdf (NEXUS, G2 en el arranque, autorizado por el orquestador como bug confirmado; helper fuera de todo `@mcp.tool`). Re-verificación del revisor: unit 47, positivo 3, negativo 3, control 4. Mutación re-corrida DENTRO de la arena aprobada con los dos arneses propios del manifiesto: 8/8 (carril de lecciones) + 11/11 (superficie MCP) = 19/19.
- Límite declarado: ADA (owner) valida por lectura; su guardián le impide ejecutar. El arnés del carril de lecciones declara `reviewer: NEXUS` en su propio JSON (autor histórico del arnés); la evidencia compuesta lleva `reviewer: JARVIS`, que es quien la corrió hoy.
- Lo que refutaría: una tool de la superficie recuperada que vuelva a faltar en `list_tools`; una lección escrita fuera del carril aditivo; `boot_context` sin registro tras el cambio G2.

## VEREDICTO FABLE 19:26 → APPROVE (con un límite que no es del código)
47 brazos verdes, 19/19 del revisor en arena; mutantes propios del juez (superficie por AST, idempotencia por autor) mueren por conducta.
