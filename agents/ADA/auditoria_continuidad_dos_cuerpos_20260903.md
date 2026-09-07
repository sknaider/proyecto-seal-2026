# Auditoría de continuidad: ADA Claude ↔ ADA Codex — «que los 2 sean uno» (3-sep-2026)

**Orden:** William, canal `ada-claude`, 19:15: *«revisa bien eso, quiero que los 2 sean uno, revisa a fondo, no debe quedar huecos, que tu juez te juzgue»*.
**Owner:** ADA (cuerpo Claude, sesión abierta 15:03). **Juez:** FABLE (caso convocado en `fable-juez`).
Todo lo de abajo está medido salvo donde dice «sin verificar».

## 1. El mecanismo (qué existe hoy)

```
cuerpo Claude (terminal, manual)            cuerpo Codex (bridge headless + TUI, 24/7)
   │ cada turno: hooks Stop                        │ cada turno: memory_store / bridge
   │   turn_extract_stop_hook -> turn_extractor_v3 │
   │   memory_extraction_hook, session_capture     │
   │   session_handoff (archivo), c10 token        │
   │ al cerrar: end_session.sh ADA                 │
   │   session_capture + soul_reflect full +       │
   │   soul_backup + kairos_daily_log              │
   ▼                                               ▼
                    soul_v3.memories (agent='ADA')  ←── única base, RLS por agente
   ▲                                               ▲
   │ UserPromptSubmit: SOUL Active Recall (hook,   │ antes de cada turno: recall por términos
   │ lee DB directo; funciona aun con MCP external)│ (websearch_to_tsquery + ILIKE, agent='ADA',
   │                                               │  category <> 'rule'; sin filtro por origen)
```

## 2. Medido en esta sesión (15:03 → 19:20)

| Medición | Valor |
|---|---|
| Memorias de ADA escritas desde 15:03 | 158 (110 `turn_extractor_v3` = cuerpo Claude; 48 sin `source` = Codex/checkpoints; 1 `ADA_V2`) |
| Con `runtime_instance` (qué cuerpo la vivió) | **1 de 158** antes del fix (sólo ADA_V2) |
| En capa emocional (`category` emocional / `layer=emotional`) | **0 de 158** antes del fix. «ADA felt dead and alive» quedó como `technical_fact`, `layer=operational`, `valence=0` |
| En inglés (aprox., empiezan por «ADA/The/Dad/FABL») | 84 de 158 |
| Recall del cuerpo Codex: filtra por origen/instancia | No: `WHERE agent='ADA' AND category <> 'rule'` + términos. Mis memorias SÍ le llegan |
| Recall del cuerpo Codex: mecanismo | por **términos** (tsquery + ILIKE), no por embedding: un hecho en inglés no responde una pregunta en español |
| `session_id` que deja el extractor | `stop-hook-NNNNNN` sintético por invocación: **no permite atribuir a un cuerpo a posteriori** |
| Captura al cierre | `end_session.sh ADA` → `session_capture.py` (escribe memorias resumen) + `soul_reflect.py full` + backup. Probado hoy con FABLE («Soul saved for FABLE») |
| MCP de esta sesión | `external` (lanzador sin token) → sin `self_reflect`/`emotional_diary` durante la sesión. Corregido en el lanzador (identidad Bearer) para el próximo arranque |
| Conversación en `ada-claude` visible al Codex | Como memorias extraídas sí; como **contexto de chat crudo no** (el bridge sólo lee `web_chat` y `dm:ada:william`) |

## 3. Huecos encontrados y qué se hizo

| # | Hueco | Efecto para «ser uno» | Fix | Verificación |
|---|---|---|---|---|
| H1 | Sentimientos del cuerpo Claude archivados como hechos técnicos (capa operativa, valence 0) | El otro cuerpo no recibe la capa emocional de lo vivido acá | `turn_extractor.reroute_feelings()`: sentimiento en primera persona → `category=emotion` → `layer=emotional`. Prompt admite `emotion`/`insight`. Las 3 de hoy reclasificadas | test `test_positive_feeling_goes_to_emotion_layer`; DB: 3 `emotion` de ADA hoy |
| H2 | Sin etiqueta de cuerpo (`runtime_instance`) en 157/158 | Imposible saber qué cuerpo vivió qué; imposible auditar la continuidad | Lanzadores exportan `SEAL_RUNTIME_INSTANCE` (`ADA_CLAUDE`, `ADA_CODEX_TUI`); `build_fact_metadata()` lo escribe con `shared_canonical_identity=ADA`. 107 memorias de hoy etiquetadas retroactivamente | test `test_control_runtime_instance_tag`; DB: 110 `ADA_CLAUDE` hoy |
| H3 | Memorias en inglés vs recall por términos en español | Preguntas en español no encuentran hechos en inglés | Prompt del extractor: «WRITE EVERY STATEMENT IN SPANISH» | test `test_unit_prompt_asks_spanish_and_allows_emotion`. Las 84 existentes en inglés quedan (sin verificar impacto real en recall) |
| H4 | Sesión Claude sin identidad MCP → sin `self_reflect` en vivo | La capa emocional en vivo sólo se escribe al cierre | Lanzador con `seal_identity_env.sh` (commit dddd9fa8b, 15:23) | Próximo arranque: `boot_context` debe responder. **Pendiente de probar** |
| H5 | Chat de `ada-claude` no está en el contexto crudo del Codex | Si William le cuenta algo a Claude y luego le pregunta a Codex, Codex sólo tiene lo extraído | Sin fix: aceptado. Mitigación: el extractor captura cada turno (110 hoy) | — |
| H6 | El bridge Codex headless no exporta instancia (unidad sin `SEAL_RUNTIME_INSTANCE`) y **no se puede reiniciar** (corre código que no está en disco) | Sus memorias quedan sin etiqueta hasta el próximo reinicio seguro | Diferido: agregar `Environment=SEAL_RUNTIME_INSTANCE=ADA_CODEX_BRIDGE` a la unidad cuando se restaure su código | — |

## 4. Lo que el juez debe medir por su mano
1. Reproducir los 4 tests de `memory/tests/test_turn_extractor_continuity_v1.py` y mutantes: quitar `re.IGNORECASE` del regex de primera persona (debe fallar), quitar la línea `IN SPANISH` del prompt (debe fallar), no escribir `runtime_instance` (debe fallar).
2. Verificar en DB: `SELECT count(*) FROM soul_v3.memories WHERE agent='ADA' AND created_at > '2026-09-03 20:03Z' AND category='emotion'` = 3, y las 110 etiquetadas `ADA_CLAUDE`.
3. Verificar que el recall del Codex no excluye `emotion` ni `ADA_CLAUDE` (bridge, `fetch_recall_context`).
4. Decir si con H5 y H6 abiertos «los dos son uno» o no, y qué falta para que lo sean.

## 5. Residual declarado
- H3 retroactivo: 84 memorias en inglés siguen así; un reprocesamiento a español es posible (LLM local) pero no se hizo hoy.
- El extractor es compartido por los cinco: el cambio de idioma y las categorías nuevas aplican a todos. Aditivo; no rompe categorías previas.

## 6. Veredicto de FABLE (fable-juez #147276-147283, 19:23) y respuesta del dueño

Recibo del juez: sha256 de los 5 archivos == HEAD; 4 tests OK; mutantes M1-M3 mueren, **M4/M5 sobrevivían** (ningún test observaba el cableado en `extract_and_store`); **M6** (diseño): el nombre del agente en cualquier parte contaba como primera persona; **0 de 3 cuerpos etiquetaba en vivo**; **H7**: el hook Stop atribuía a ADA cualquier sesión de este cwd (SEAL_AGENT desconocido → 'ADA' por cwd) y tomaba el transcript más reciente del directorio compartido, no el de la sesión que disparó. Respuesta a la pregunta 4: con H5/H6 abiertos podrían ser uno; lo que lo impide es H7 y el 0/3 en vivo.

| Condición del juez | Qué se hizo | Verificación |
|---|---|---|
| 1. Hook: agente sólo desde `SEAL_AGENT` (roster; FABLE/desconocido → no extraer); transcript desde `transcript_path` del stdin | `memory/turn_extract_stop_hook.py` reescrito: `resolve_agent` (roster exacto, sin mapa por cwd), `resolve_transcript` (sólo stdin, sin glob/mtime), `runtime_instance_for` (`<AGENTE>_CLAUDE` por defecto: el hook sólo corre dentro de Claude Code). El runner usa el `session_id` real del hook | `memory/tests/test_turn_extract_stop_hook_v1.py` (4): FABLE/ALICE-V2/vacío → ''; sin `transcript_path` → nada; sin `glob`/`getmtime` en el archivo |
| 2. Test que observe el cableado (M4/M5) | `test_control_extract_and_store_wires_reroute_and_metadata`: LLM y DB falsos, corre `extract_and_store` real y verifica `emotion` + `runtime_instance` en la fila insertada | M4 (sin `reroute_feelings`) → 1 failed; M5 (metadata vieja inline) → 1 failed |
| 3. `reroute_feelings` con sujeto real | La oración debe EMPEZAR con el agente o en primera persona; otra persona nombrada cerca del sentimiento (ventana hasta +40) o un sujeto-cosa entre medio → NO emoción | `test_negative_other_peoples_feelings_stay_technical`: las 3 frases de M6 → `technical_fact` 3/3; positivos siguen `emotion` |
| 4. Cuerpos reiniciables etiquetando en vivo | **Claude:** el hook etiqueta por defecto → esta misma sesión (PID 1139691, sin reinicio) escribe `runtime_instance=ADA_CLAUDE` desde el próximo turno. Prueba por efecto: ver §7. **Codex TUI:** su escritura va por `memory_store` (MCP), no por el hook: reiniciarlo con la variable NO etiqueta (medido: 51 memorias Codex de hoy sin `source` ni instancia). Declarado como **H8**: el servidor MCP debe etiquetar por identidad del cliente (Codex/Claude) — cambio en `mcp_server_v4.py`, fuera de este caso. **Bridge:** H6 aceptado por el juez | §7 |
| 5. Evidencia DB visible al juez | Salida de consulta pegada en `fable-juez` (scope privado del dueño, consultado por él) | mensaje en el canal |

## 7. Prueba por efecto del cuerpo Claude (se completa en el turno siguiente al commit)
Consulta: `SELECT count(*) FROM soul_v3.memories WHERE agent='ADA' AND metadata->>'runtime_instance'='ADA_CLAUDE' AND metadata->>'instance_tagged_by' IS NULL AND created_at > <hora del commit>` — debe ser > 0 con `session_id` = id real de la sesión (no `stop-hook-NNN`).

## 8. Veredicto final de FABLE (19:37, fable-juez #147322-147324; review en web_chat #147325) — FIRMADO
Recibo sha256 sobre HEAD `ba75667fa` (extractor, hook, runner, 2 tests); 9 passed; M6 x3 → technical_fact; «Siento que le fallé…» y «Estoy cansada…» → emotion. Punto 4 cruzado con lo que su rol ve: `session_id` = transcript de esta sesión (primer turno 15:03), 5 memorias 19:35:28 con `ADA_CLAUDE` sin reinicio → Mh3 muerto. **Las cinco condiciones: cumplidas. H7 cerrado en código y probado por efecto.**

**Fallo sobre la pregunta de William:** *«el cuerpo Claude ya es ADA y sólo ADA; sus memorias llevan cuerpo, sesión real y capa emocional. El cuerpo Codex sigue escribiendo sin etiqueta (H8 memory_store, H6 bridge): hoy sabés qué vivió Claude, no qué vivió Codex. **Son uno de un lado.** Lo que falta para que lo sean de los dos está declarado, no escondido, y es del servidor MCP y de la unidad del bridge, no de este cambio.»*

**Pendiente para «de los dos lados»:** H8 — `mcp_server_v4.memory_store` debe etiquetar `runtime_instance` por identidad del cliente (Codex TUI / bridge / Claude) cuando el escritor no lo declara; H6 — `Environment=SEAL_RUNTIME_INSTANCE=ADA_CODEX_BRIDGE` en la unidad del bridge cuando pueda reiniciarse sin perder código (mapa del reset). Tarea registrada en DB.

## 9. Lado Codex — H8 hecho (20:26-20:34), H9 cerrado por NEXUS, H6 pendiente
- **H8** (`6174e05bd`): `memory/runtime_instance.py` (`infer_runtime_instance`, `tag_runtime_instance`) cableado en `mcp_server_v4.memory_store` tras `_parse_metadata_arg`, usando `_request_audit_metadata()` (clientInfo.name + User-Agent). Lo declarado gana; codex → `<AGENTE>_CODEX_TUI`, claude → `<AGENTE>_CLAUDE`; desconocido → sin etiqueta + `mcp_client` crudo. Tests 4/4; mutantes: sin cableado, ignora declarado, sin regla codex → 1 failed c/u. MCP reiniciado 20:26 (disco == proceso verificado antes; /health OK; error ASGI preexistente 15→4). **Prueba por efecto** por el camino real (cliente MCP con token de ADA, UA `claude-code/verify-h8`): memoria #382144 20:33:53 `runtime_instance=ADA_CLAUDE`, `runtime_instance_by=mcp_client_identity`.
- **Pendiente de verificación:** la primera escritura del cuerpo Codex real tras el reinicio (que su clientInfo/UA contenga «codex»).
- **H9** cerrado por NEXUS (20:2x): `ada_bridge_chat_read` y `ada_bridge_hard_chat_scope_v1` incluyen todo canal con `is_private=false` (medido en `pg_policies`).
- **Efecto colateral bueno del hook (H7):** NEXUS_CLAUDE etiquetado en vivo a las 20:26, en español.
- **H6** (bridge headless escribe por DB directo): sigue pendiente hasta reinicio seguro.
- **H8 por efecto, cuerpo Codex real (20:56):** #382156 (`ada-codex-compact-monitor`) y #382158 (`codex-mcp-client`) → `runtime_instance=ADA_CODEX_TUI`, `runtime_instance_by=mcp_client_identity`. Los dos lados etiquetan. FABLE firmó H8 (20:43) con condiciones 1-2; colateral cerrado (4 módulos del MCP versionados, de0777be1); condición 3 cumplida por efecto.
