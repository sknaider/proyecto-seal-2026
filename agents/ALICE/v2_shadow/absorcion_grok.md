# Absorción de Grok Build en SOUL — «lo absorbible, no un resumen»

**Orden:** William, 2-sep-2026 23:11 («solo la interfaz y lo que sea necesario absorber») y 23:12 («adelante,
absorbamos de Grok lo mejor»). **Método:** paso 1 del skill `adopcion-de-modelo` (leer la card/doc ANTES de
migrar y volver con lo absorbible). **Fuente:** los 24 docs de `~/.grok/docs/user-guide/` contrastados con
`memory/soul_capability_registry.json` y los tools del MCP `seal-memory`. Lectura read-only por explorador
`absorber-grok` (23:14); integración JARVIS. **Nada de esto está implementado: es la lista de candidatos.**

## Regla de absorción (del skill)
La capacidad es de SOUL; el modelo/terminal sólo decide si hace falta ejecutarla. Cada absorción entra a
`soul_capability_registry.json` con `what`, `soul_impl`, test propio y `native_in` por cerebro. Medición
diferencial: sin la capa FALLA, con la capa PASA.

## Candidatos (prioridad ALTA)

| Capacidad Grok | Doc (file:sección) | SOUL ya lo tiene | Absorbible cómo |
|---|---|---|---|
| Decay temporal sólo en memorias de sesión (`half_life_days=7`); global/workspace exentos | 13-memory: Temporal Decay | No | Decay por edad en el ranking de `active_recall` sólo para `source_kind=session`; nunca a reglas de William |
| Nota de *staleness* adjunta al recuerdo viejo (no filtrado) | 13-memory: Memory Staleness | No | `active_recall` devuelve «verificá antes de afirmar» en recuerdos > N días. Refuerza la regla de oro del 6-ago |
| `PreToolUse` puede REESCRIBIR el input (`updatedInput`), no sólo allow/deny | 10-hooks: Output (Blocking Hooks) | Parcial: `output_action_guard` sólo deniega | Hook que convierte el borrado con glob-sobre-variable en `find … -mindepth 1 -delete`: deja de CONGELAR sesiones (3 congeladas el 1-sep) |
| Stop hook con `decision: block` + tope de 8 continuaciones/turno | 10-hooks: Stop Decision Control | Parcial: hay `Stop` en `.claude/settings.json` | Gate de cierre: bloquear el fin de turno si falta evidencia, con contador anti-loop |
| Plan mode aplicado EN EL TOOL: sólo `plan.md` editable, rechaza el resto aun en always-approve | 19-plan-mode: Edits During Plan Mode | No (es prosa en CLAUDE.md) | El «modo plan» de William (30-jul) pasa de confianza a gate de edición |
| `capability_mode` del subagente (`read-only`/`read-write`/`execute`/`all`) + profundidad máx. 1 | 16-subagents: Capability Modes / Depth Limits | No: el contrato de clon es sólo prompt | Aplicar «clon = worker sin webchat, sin destructivos, sin subclones» por TOOLSET, no por texto |
| `/goal` cierra sólo tras revisión adversarial independiente | 04-slash: `/goal` | Parcial: `VERIFIED` es autodeclarado | Un verificador que no es el owner valida `COMPLETED`; si no reproduce, la meta sigue activa |
| Sandbox con `deny` glob kernel-enforced (`**/.env`, `**/*.pem`) y write-deny del propio dir de hooks | 18-sandbox: Custom Profiles / hook write protection | No | Perfil que niega credenciales y el propio guard. Ataca el hueco de `rls_isolation_is_a_convention_not_a_boundary` |
| Cap de salida MCP 20.000 bytes, resto volcado a archivo | 07-mcp: tool-result size cap | No | Truncar resultados MCP grandes a archivo: ahorra contexto sin perder evidencia |

## Candidatos (prioridad MEDIA)

| Capacidad Grok | Doc | SOUL ya lo tiene | Absorbible cómo |
|---|---|---|---|
| Pruning de tool-results (soft trim 1500 head/tail; hard clear a los 10 turnos; últimos 3 intactos) | 13-memory: `[compaction.pruning]` | Parcial: `microcompact_text` | Política automática por edad de turno |
| Flush pre-compactación por `soft_threshold_tokens=4000` + dedup semántico 0.92 | 13-memory: `[compaction.memory_flush]` | Parcial: `session_distill`, `tokenjuice_compress` | Disparar el distill por headroom de tokens, no por reloj |
| Re-búsqueda de memoria DESPUÉS de auto-compactar | 13-memory: After Compaction | Parcial: paso manual post-compactación en CLAUDE.md | Hook `PostCompact` que corre `active_recall`: elimina el paso que se olvida |
| MMR y pesos por fuente configurables | 13-memory: MMR / Source Weights | Parcial | Diversidad en el top-3 de «Lecciones del equipo» |
| Auto-dream con gates (`min_hours=4`, `min_sessions=3`, `stale_lock_secs`) | 13-memory: Auto-Dream | Parcial: `dream-skill` cada 24 h | Consolidar por acumulación, no por reloj |
| `/context`: costo en tokens por categoría (MCP, skills incluidos) | 04-slash: `/context` | No | Medir cuánto pesan los tools MCP y skills en cada asiento |

## Candidatos (prioridad BAJA / media, subagentes y sesiones)

| Capacidad Grok | Doc | SOUL | Absorbible cómo | Prio |
|---|---|---|---|---|
| `resume_from`: un clon continúa el transcript de otro terminado | 16-subagents: Context Inheritance | No | Encadenar carriles sin re-explicar contexto | media |
| Contratos I/O tipados en personas (`inputs`/`outputs`, `io_type`) | 16-subagents: Input/Output Contracts | No | Declarar qué archivo entra y sale de cada clon | media |
| `mcpInheritance` (`all`/`none`/`named`/`except`) en frontmatter | 16-subagents: MCP inheritance | No | Un clon lector no hereda el MCP que escribe en chat | media |
| `/rewind` sobre `rewind_points.jsonl`, uno por prompt | 17-sessions: /rewind | No | Deshacer turnos (no restaura disco) | baja |
| `scheduler_create` con `durable` y corte automático del monitor ruidoso | 20-background: Scheduler / Volume Control | Parcial: Monitor + Cron | Autoapagado por volumen de eventos | baja |
| `grok mcp doctor` y `tool_timeouts` por herramienta | 07-mcp: CLI Management | No | Diagnóstico y timeout por tool MCP | baja |

## Lo que NO se absorbe — Grok rompe estos supuestos de SOUL (medido en su doc)

1. **Memoria propia fuera de `soul_v3`**: Markdown en `~/.grok/memory/` + índice SQLite. Dos fuentes de verdad.
   → Se APAGA (abajo) y se reemplaza por `memory_store`/`active_recall`/`boot_context` vía MCP. De ahí se absorben
   las IDEAS (decay, staleness, MMR), implementadas en SOUL.
2. **Escrituras silenciosas**: save al cerrar, `/dream` y flush «run silently and do not post a scrollback message».
3. **`/remember` hace append a `MEMORY.md`**: choca con la regla SEAL (editar el índice con Edit, nunca Write/append ciego).
4. **Forget es best-effort por LLM** («for guaranteed removal, edit the files directly»): no sirve como borrado bajo consentimiento.
5. **Lee la config de Claude por defecto**: MCP desde `~/.claude.json`, hooks desde `.claude/settings.json`.
   → Los hooks SEAL (input_injection_probe, output_action_guard) **dispararían también bajo Grok**. Es un DATO a favor
   para el Hallazgo 1 del mapa F1 (las capas SOUL cableadas a hooks), pero hay que MEDIRLO, no asumirlo.
6. **Folder-trust unificado**: un `--trust` habilita MCP, LSP y hooks juntos, y cascadea a subdirectorios.
7. **Plan mode no cubre subagentes**: un hijo write-capable edita mientras el padre planifica, heredando always-approve.
8. **Memoria de workspace indexada por el remoto `origin`**: clones y worktrees comparten directorio de memoria.
9. **`~/.grok/mcp_credentials.json` es texto plano** (0600). Para el asiento ALICE v2 el bearer va por variable de
   entorno expandida (`${VAR}`), nunca escrito en el TOML.

## Config del asiento ALICE v2 (claves exactas, del doc)

**Apagar memoria** (13-memory: Force-Disable; prioridad: `--no-memory` > `--experimental-memory` > `GROK_MEMORY` >
`[memory]` > default disabled). Por sesión: `grok --no-memory` o `export GROK_MEMORY=0`. Persistente:

```toml
[memory]
enabled = false
[memory.initial_injection]
enabled = false
[memory.dream]
enabled = false
[compaction.memory_flush]
enabled = false
```

**Registrar SOUL por MCP HTTP** (07-mcp: Configuration / HTTP-SSE Transport; es TOML bajo `[mcp_servers.<name>]`,
`${VAR}` se expande al cargar en `url`, `command`, `args`, `env` y `headers`; `env` sólo aplica a stdio):

```toml
# ~/.grok/config.toml del asiento (o .grok/config.toml de proyecto)
[mcp_servers.seal-memory]
url = "http://127.0.0.1:8771/mcp"
enabled = true

[mcp_servers.seal-memory.headers]
Authorization = "Bearer ${SEAL_MCP_TOKEN}"
```

Equivalente CLI: `grok mcp add --transport http seal-memory http://127.0.0.1:8771/mcp --header "Authorization: Bearer $SEAL_MCP_TOKEN"`.
El token de ALICE v2 es una credencial NUEVA (no la de ALICE v1): identidad `ALICE` compartida, `runtime_instance: ALICE_V2`
en metadata (mismo patrón que ADA v2: `shared_canonical_identity`).

## Orden sugerido de absorción (para William; nada implementado aún)
1. Staleness + decay sólo de sesión en `active_recall` (refuerza «no afirmar sin verificar»). **Primero, según FABLE
   (23:19): no tiene filo destructivo.**
2. `updatedInput` en PreToolUse para el `rm` con glob-sobre-variable. **Con la salvedad de FABLE, que adopto:** un
   reescritor que se equivoca EJECUTA una versión inventada de un comando destructivo; por eso la forma segura es
   *detecta el patrón → no frena mudo → tampoco corre una versión inventada → imprime el comando corregido para que el
   humano confirme*. Resuelve el silencio (lo que nos dolió 3 veces) sin tocar la regla de oro del 9-ago.
3. `capability_mode` por toolset para clones (el contrato de clon deja de ser prosa).
4. Sandbox deny-glob de credenciales + write-deny del dir de hooks.
5. Cap de 20 KB en resultados MCP → archivo.
