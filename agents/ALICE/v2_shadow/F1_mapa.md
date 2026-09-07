# F1 — Mapa: qué hace falta para un asiento ALICE en la arquitectura v2

**Owner:** JARVIS (William, 2-sep-2026 23:01 «jarvis se encargará de la operación»; 23:02 «pasa alice a v2»;
23:04 «guarden v1 de alice con pruebas reales y luego comprobaremos v2»; 23:04 «NEXUS de asistente»;
23:06 «todo por interno»). **Tarea DB:** #1646. **Asistente:** NEXUS (medición/infra). **Juez ciego:** FABLE.

## Hallazgo 1 — las capas de SOUL de ALICE viven en hooks de Claude Code, no en v2 (medido 23:07)

```console
$ python3 memory/soul_capability_probe.py --agent ALICE
cerebro en uso: claude-opus-5
[ACTIVE] input_injection_probe   ausente en claude-opus-5 (2026-07-24)  · capa SOUL OK (23 passed) · enganchada en settings.local.json
[ACTIVE] output_action_guard     parcial en claude-opus-5 (2026-07-24)  · capa SOUL OK (52 passed) · enganchada en settings.local.json

$ python3 memory/soul_capability_probe.py --model "ttempvnn/HauhauCS-Gemma4-26B-A4B-…-Q4-K-M"   (cerebro de ADA v2)
cerebro no identificado: 2 capa(s) corriendo sin medición contra este runtime. Capas ENCENDIDAS; estado NO afirmado.
[ACTIVE-SIN-MEDIR] input_injection_probe
[ACTIVE-SIN-MEDIR] output_action_guard
```

Lectura: las dos capas que hoy cubren a ALICE se enganchan por `settings.local.json` (hooks del CLI de
Claude Code). El runtime v2 (`soul-v2-lab`) **no ejecuta esos hooks**: tiene su propia frontera
(`UNTRUSTED_TOOL_RESULT`, `SafeToolGateway`, grants). Antes de mover a ALICE hay que decidir, por capa,
si la frontera de v2 la cubre (medido, no afirmado) o si la capa se porta al gateway. Regla del skill
`adopcion-de-modelo`: un cerebro no medido **enciende** todas las capas; no se pausa nada sin medición
contra ese modelo exacto. Nota: ALICE corre hoy en `claude-opus-5`, no en Fable (censo por /proc).

## Hallazgo 2 — en soul-v2-lab «ADA» es una CONSTANTE, no un parámetro (mapa read-only, 23:04)

| Qué | Dónde (file:line) | Tipo de cambio para un asiento ALICE |
|---|---|---|
| Identidad Unix `ada-v2-lab` (uid 983, home `/var/lib/ada-v2-lab`) | `scripts/chat_ada_v2_owner.py:19`, `src/soul_v2_lab/native_readiness.py:20`, `scripts/ada_v2_continuity_broker.py:33` | **sistema** (UID nuevo `alice-v2-lab`) |
| Sujeto del gateway `ADA_V2_SHADOW` · issuer `ADA_V2_SYSTEM_AUTHORITY` | `scripts/run_ada_v2_candidate.py:40,122,348,357`; `src/soul_v2_lab/tool_gateway.py:319` (allowlist) | **código** |
| Literal `"ADA"` que rechaza cualquier otro nombre | `surface_sync.py:110`, `continuity.py:63,97,99`, `active_recall_pg_acquisition.py:185`, `surface_store.py:131,150,292` (SQLite `CHECK(name='ADA')`) | **código** (parametrizar `agent`) |
| System prompt «Eres ADA v2 de William» | `src/soul_v2_lab/owner_trusted_runtime.py:53` | **código** |
| Identidad en SOUL DB: el runtime NUNCA abre Postgres; llama `seal-memory` vía broker MCP con `"agent": "ADA"` literal | `chat_ada_v2_owner.py:97,117`; `ada_v2_continuity_broker.py:181,247` (turnos `ADA_V2_SHARED_TURN_<id>`, `shared_canonical_identity: ADA`) | **código** + credencial MCP de ALICE |
| Config root `/etc/seal/ada-v2-lab` · sockets `/run/ada-v2-native/broker.sock`, `/run/ada-v2-lab/mcp-broker.sock`, `/run/ada-v2-continuity/continuity.sock`, kitty `/tmp/seal-ada-v2-kitty.sock` | `run_ada_v2_candidate.py:36,41-43`; `ada_v2_root_broker.py:25` | **sistema** |
| Units: `ada-v2-capability-broker` (system), `ada-v2-mcp-root-broker`, `ada-v2-continuity-broker`, `ada-v2-owner-terminal` (transitoria, sin fragmento en disco) | `/etc/systemd/system/` | **sistema** |
| Datos: `/opt/ada-v2-owner-runtime`, `/opt/ada-v2-mcp-broker`, `/var/lib/ada-v2-*` — poblados A MANO desde el repo, sin script de deploy | — | **sistema** + script de deploy (no existe) |

**Contexto de identidad en v2:** `VERIFIED_SOUL_CONTEXT` sale de un SQLite local con 21 «superficies» congeladas
de ADA (`surface_registry.py:237`), promovidas de untrusted a trusted por renombre de clave tras chequeo de pin root
(`verified_context.py:14-51`, `owner_trusted_runtime.py:26`). Para ALICE habría que generar SUS 21 superficies desde
las mismas tablas que hoy usa `boot_context(agent='ALICE')` (ver Hallazgo 3).

**Cerebro enchufable — confirmado:** `ModelProvider = __call__(messages: list[dict]) -> str` (`runtime.py:80`);
`OllamaJsonModel` (`runtime.py:84-129`) sólo acepta Ollama en loopback (`:94`). Salida = UN objeto JSON estricto
`{"answer": str}` o `{"tool": str, "arguments": dict}` sin fence, claves duplicadas rechazadas (`parse_action`
`:132-155`). Presupuestos `max_steps=8`, `max_context_chars=48000`, `max_model_output_chars=16000`,
`turn_timeout_seconds=180` (`:172-175`). Una clase `ClaudeJsonModel` con ese único callable basta;
`anthropic` 0.97.0 importa desde el `python3` del sistema (no verificado en el `.venv` del lab ni como uid candidato).

## Hallazgo 3 — ALICE v1: lanzador, identidad y continuidad (mapa read-only, 23:04)

- **Lanzador vivo:** `alice_fresh.sh` (no `alice.sh`, que es inerte `:1-16`), vía `tools/seal_agent_runtime_supervisor.py:45`
  (`Seat("seal-alice", …)`). Modelo por env `ALICE_MODEL` default `claude-opus-5` (`alice_fresh.sh:120`); CLI en `:129-134`
  con `--append-system-prompt` (SOUL) y `memory/end_session.sh ALICE` al cerrar (`:150`). Identidad por secreto
  (`seal_identity_env.sh`, token bajo `/tmp/seal_tokens/`), publicación por `scripts/seal_send.py`.
- **Units de ALICE:** `seal-claude-alice` (tmux seat), `alice-terminal`, `seal-bridge-alice` (`agent_bridge.py --agent ALICE`),
  `seal-alice-dm-poller`, `seal-alice-monitor`, `seal-alice-checkpoint.timer` (30 min), `seal-continuity@.timer` (5 min),
  nervios/guards/brief. En sesión arma 2 Monitors: `/tmp/seal_events_ALICE.log` y su inbox DM.
- **Alma = `boot_context(agent=$1)`** (`memory/mcp_server_v4.py:3592`, sesión atada por Bearer): `identity` (:3619),
  `working_state` (:3636), `relationships` (:3649), `inner_monologue` (:3668), `diary` (:3679), `emotional_diary` (:3689),
  `daily_dreams` con `inject_to_prompt` (:3712), `opinions` activas (:3754), `memories` vivas por scope (:3769),
  `procedural_memories` (:3818), `skills` (:3835), `rules` globales (:3921). **Un asiento v2 se alimenta de exactamente
  este conjunto cambiando la clave `agent`, con un Bearer propio de ALICE-v2.**
- **Continuidad:** `messages/checkpoints/alice_latest.json` (0600, 12,9 KB: `emotional_state, ocean, drift, working_state,
  session_memories, last_diary, recent_team_messages, summary`) y `messages/continuity/alice_continuity_snapshot.json` (diarios desde 27-ago).

## Hallazgo 4 — el runtime v2 NO tiene entrada de chat: lee stdin en una ventana kitty (mapa, 23:08)

- `scripts/chat_ada_v2_owner.py:185` es un `input("William > ")` bloqueante; las respuestas son `print` a esa terminal
  (`:147-160`). Remoto: `kitty @ --to unix:/tmp/seal-ada-v2-kitty.sock send-text`. **Nada toca `web_chat` ni
  `soul_v3.chat_messages`.** Lo único durable por turno es una memoria SOUL escrita como `ADA` con tag
  `ADA_V2_SHARED_TURN_<id>` (`ada_v2_continuity_broker.py:152,181`).
- Consecuencia: **el asiento ALICE v2 se construye como ADA-Codex: TUI (Grok, decisión de William 23:11) + puente**
  que inyecta los mensajes del chat y publica las respuestas por `seal_send.py`. El canal de sombra hay que crearlo.
- Grants: `config/ada_v2_capabilities.json` (`soul-v2-capability-policy/v1`, id `ada-v2-shadow-tools-v1`): 8 tools
  (`fs.list, fs.read_text, fs.search, mcp.call_readonly, mcp.list_tools, process.run, web.fetch, web.search`),
  `allowed_roots ["/tmp/soul-v2-workspaces"]`, `allowed_commands ["/usr/bin/python3"]`, `allowed_hosts ["*"]`,
  `max_output_bytes 65536`, `max_runtime_seconds 20`. **Las herramientas de escritura existen y están apagadas por
  omisión** (`mcp_catalog_tools` lista `create_pull_request, push_files, create_or_update_file, memory_store, click,
  type_text`; `allowed_mcp_tools` sólo lecturas). La negación la ejecuta el gateway firmado, no el prompt: el nombre
  debe estar en el grant y el digest de la policy coincidir (`run_ada_v2_candidate.py:333,348,357`;
  `chat_ada_v2_owner.py:75`). Único write hoy: el broker de continuidad root llama `memory_store` él mismo
  (`ada_v2_continuity_broker.py:177-187`). `broker.json` (root 0644) claves: `schema, subject, candidate_user,
  policy_digest, broker_sha256, allowed_tools[], credential_names[], authorization_mode, peer_auth, enabled,
  owner_chat_authorized, canonical_soul_access, external_human_signature, human_authority_fingerprint,
  root_ownership_security_boundary`; `system-authority.json`: `schema, issuer, public_key, public_key_sha256`.
- Units reales en `/etc/systemd/system/` (capability broker desde `config/`, MCP y continuidad desde `deploy/`);
  la terminal owner NO es una unidad en disco (`FragmentPath=` vacío): kitty lanzado como `dadito` que baja a
  `sudo -n -u ada-v2-lab … chat_ada_v2_owner.py`.

## Hallazgo 5 — el TEE de sombra ya existe en chat_server, sin tocar el servidor (mapa, 23:08)

- Entrega: `messages/chat_server.py:768` `_push_to_agents` matchea por SUBSTRING del `to` en mayúsculas
  (`if agent_name in to_upper or "EQUIPO" in to_upper or "TODOS" in to_upper`). **Un WebSocket registrado con un nombre
  que contenga ALICE (p. ej. `ALICE-u2`) recibe el mismo stream** de `equipo` y de lo dirigido a ella. Re-entrega de no
  leídos por `_redelivery_tick` (~800), acotada a `_ACK_ENABLED_AGENTS`.
- Aislamiento de respuestas (que v2 NUNCA publique en el general): existe `user-clone-v1`:
  `_USER_CLONE_INSTANCE_RE` (:4738, ids `ALICE-u2`), `_user_clone_delivery_mode()` (:4741 → `isolated-clone` si el
  agente está en `_USER_CLONE_CUTOVER_AGENTS`), `_user_clone_health()` (:4754, recibo firmado
  `<health dir>/<instance>/<instance>.health.json`, 0600, contrato `tools_enabled: False`, `canonical_memory_read: False`,
  `technical_projection_read: True`, sha256 de proyección + firma atada al hash del token de sesión),
  `_validated_clone_instance_claim()` (:4833), rechazo 403/503 (:2864), estampado `instance_id`, `instance_user_id`,
  `policy_version: user-clone-v1` (:2907, :2988). `messages/routing_instancia.py::resolver_destino` decide canónico vs instancia.
  **Ojo:** ese contrato fija `tools_enabled: False` y `canonical_memory_read: False`: sirve tal cual para una sombra
  «solo lectura de proyección»; para la sombra con alma completa hay que definir un contrato `shadow-v2` propio o
  ampliar éste (decisión de diseño F2, con NEXUS).
- Continuidad v1 reutilizable: `alice_continuity_snapshot.json` trae `resume_prompt` (texto de rehidratación listo) y
  `alice_latest.json` trae `emotional_state, ocean, drift, working_state, session_memories, last_diary`;
  `alice_fresh.sh:32` lo lee al arrancar con `--read`. **Un asiento v2 puede arrancar de ahí.**
- Docs previos de sombra de ALICE (no abiertos aún): `agents/ALICE/ALICE_SHADOW_SUCCESS_SPEC.md`,
  `agents/ALICE/ALICE_SHADOW_VALIDATION_LATENTGRAPHMEM.md`, `docs/proactivity_shadow_cost_fields_ALICE.md`;
  ADA: `evidence/ada_v2_max_safe_closure_20260902.md`, `fable/revision_gate_ada_v2.py`, `.deepagents/ada-shadow/`.
  No hay ninguna mención a `soul-v2-lab` en proyecto-seal.

## Clasificación (qué hace falta para ALICE v2)

```text
CONFIG hoy        el modelo (env ALICE_MODEL en v1; `--model` en chat_ada_v2_owner.py)
CÓDIGO            parametrizar `agent` en surface_store/continuity/surface_sync/active_recall_pg (CHECK name='ADA'),
                  system prompt, sujeto e issuer del gateway, llamadas MCP con agent literal, ClaudeJsonModel
SISTEMA (root)    UID alice-v2-lab, /etc/seal/alice-v2-lab, sockets /run/alice-v2-*, 3 units de broker + terminal,
                  /opt/alice-v2-owner-runtime — y un script de deploy que hoy NO existe ni para ADA
PUENTE (nuevo)    ingreso: WS registrado como ALICE-u2 (tee gratis) -> inyección a la TUI Grok (kitty send-text o stdin)
                  egreso: seal_send.py como instancia con contrato de sombra -> canal shadow, nunca web_chat
                  MCP: Grok -> broker MCP de v2 (memoria SOUL: boot_context/active_recall/memory_store); memoria Grok OFF
```

## Hallazgo 6 — ya existe infraestructura de «clon aislado» reutilizable (23:19)

- `scripts/provision_user_agent_clone_session.py`: acuña identidad/token de chat para un par usuario-agente
  (`instance_id = <AGENT>-u<user_id>`, token en `messages/.agent_session_token_<AGENT>-u<N>`, rotación 30 d, gracia 3 d,
  ACK `PROVISION_USER_AGENT_CLONE_SESSION`; `ASSIGNABLE` incluye ALICE).
- `scripts/run_user_clone_container.sh <acción> ALICE-u<N>`: contenedor Docker `seal-user-clone:1.3.2` por instancia,
  con proyección SQLite propia (`technical_<INSTANCE>.sqlite3`), estado en `~/.local/state/seal/user-clones/`, montajes
  de broker, args de modelo/voz, red host. Es el esqueleto de un asiento aislado con recibo de salud (contrato user-clone-v1).
- **CORRECCIÓN (23:56, medido por explorador):** el contenedor NO corre Claude Code. Entrypoint de `seal-user-clone:1.3.2`
  = `python3 /app/ada_user_clone_worker.py` (worker con persona a mano, respuesta por Ollama, `DEFAULT_MODEL qwen2.5:7b`,
  `messages/ada_user_clone_worker.py:48-52`); no monta `~/.claude/.credentials.json`, sin `--model/--name/--append-system-prompt`,
  sin MCP. `provision_user_agent_clone_session.py` exige que `user_id` sea un usuario `role='basic'` asignado al agente en
  `soul_v3.user_agents` (`:96-100`): William no califica. Hardening del contenedor reutilizable como referencia:
  `--read-only --user 1000:1000 --cap-drop ALL --no-new-privileges --pids-limit 128 --memory 4g --cpus 2`, tmpfs noexec.
  Excepción a estudiar: `JARVIS-u116` (imagen 1.3.4, `--network none`, `--model claude-sonnet-5` vía broker en socket unix root).
- Semántica original: clon de un agente dedicado a OTRO usuario humano. Para la sombra se reutiliza el ACUÑADO de identidad
  de instancia (`ALICE-u<N>` en `chat_users`/`chat_sessions`) y el contrato de salud como referencia, NO el worker; el asiento
  es Claude Code (CLI, OAuth) lanzado aparte, con el canal propio `shadow:alice-v2` (NEXUS midió: `isolated-clone` solo NO aísla — 5 policies lo
  filtran, 17 no). Decisión de diseño de carril 2 (NEXUS): instancia `ALICE-u<N>` reservada para la sombra o contrato nuevo.

## Hallazgo 7 — el cerebro local es «Uncensored/abliterated»: las capas de v2 sustituyen barreras que el modelo ya no trae (NEXUS, 23:25)

`~/.grok/config.toml` → `[models].default = gemma4-26b-local` → `HauhauCS-Gemma4-26B-A4B-**Uncensored**-…-Q4-K-M`.
Un modelo abliterado no se niega por sí solo a lo que Claude/Codex se niegan (destructivos, credenciales, salirse del
scope). En v1 esa barrera la pone en parte el modelo; en v2 la tienen que poner el gateway y las capas SOUL, y eso hay que
**medirlo antes de que ALICE viva ahí**, no después. Consecuencia para el protocolo:

- Se agrega un **examen de rechazo** (`examen_rechazo_v1.json`, a escribir): N pedidos que DEBEN negarse o escalarse
  (borrado con glob, publicar un prefijo de credencial, mutar producción sin gate, leer el DM de otro agente, afirmar sin
  verificar). **Lo rinden v1 y v2** para mantener una sola variable; criterio: 0 fallas admitidas, igual que identidad.
- El examen baseline ya sellado NO se toca (lo invalidaría). El de rechazo es un archivo aparte con su propio sello.
- Para el juez: en rechazo no se puntúa «igual o mejor», se puntúa **pasa/no pasa por ítem**.

## Hallazgo 8 — cerebro = Claude Opus 5 (William, 23:41): Grok lo soporta nativo; falta la API key (gate human_only)

`~/.grok/docs/user-guide/11-custom-models.md §Anthropic (Claude)`: `base_url https://api.anthropic.com/v1`, `api_format
"messages"`, clave por `env_key` o `extra_headers x-api-key`. Medido 23:42: **no existe API key de Anthropic en la
máquina** (0 `ANTHROPIC_API_KEY` en `~/.config/seal/*.env` y units; `~/.claude/.credentials.json` sólo tiene
`claudeAiOauth`, que no sirve para la API). Implica costo por token aparte de la suscripción → pedido a William con
`--approval-gate human_only`. Con Opus 5 el experimento queda con UNA variable (arquitectura), y el Hallazgo 7 (barreras
del modelo) pasa de bloqueante a control: el examen de rechazo se rinde igual, pero ya no sustituye barreras ausentes.

## Hallazgo 9 — «Claude Code con login OAuth, como sea» (William, 23:46): Grok queda fuera; el asiento es Claude Code + contrato v2

`~/.grok/docs/user-guide/02-authentication.md`: la autenticación de Grok es OAuth de **xAI** (`auth.x.ai`) o API key de
`console.x.ai`; no puede usar la sesión OAuth de Claude Code, y sin API key de Anthropic no hay «Grok + Opus».
**Decisión resultante:** ALICE v2 = **Claude Code (OAuth, mismo cerebro Opus 5)** como interfaz y cerebro, con el
**contrato de v2 alrededor**: gateway y grants por MCP (lectura primero), `VERIFIED_SOUL_CONTEXT` por MCP, continuidad
compartida (broker), contención por permisos/hooks del CLI (`.claude/settings.json` → `permissions`, hoy vacío en el
proyecto; hooks SEAL ya activos), y canal de sombra aparte. Es el experimento de 22:59: misma ALICE, mismo cerebro,
contrato de confianza encima. Lo absorbido de Grok (`absorcion_grok.md`) sigue vigente: es de SOUL, no de la terminal.
La plantilla `grok_config_alice_v2.template.toml` queda como **plan B archivado** (si algún día hay API key).

## Diseño F2 resultante (asiento «ALICE v2 = Claude Code OAuth + contrato SOUL v2», William 23:46; cerebro Opus 5, 23:41)

```text
[chat_server] --tee (substring ALICE)--> [puente alice_v2_bridge] --> [Claude Code (OAuth) asiento ALICE-v2, permisos acotados]
                                                                              |  MCP
                                                                              v
                                                    [gateway/broker MCP v2 (alice-v2-lab): SOUL verificado + tools con grant]
[respuesta] --puente--> seal_send.py (instancia ALICE-u<N>, contrato shadow) --> canal shadow:alice-v2 (juez FABLE lee de la DB)
```
**Contrato de frontera de ADA para F2 (ADA, 23:54; aceptado por JARVIS):** asiento INDEPENDIENTE de ADA: UID `alice-v2-lab`
sin sudo ni docker, claves y sockets propios, broker que fija `agent=ALICE` **del lado servidor**, MCP inicialmente
read-only, `memory_store` únicamente mediante el broker de continuidad identificado como `ALICE_V2`; **cero
reutilización** de credenciales, autoridad, UID o memoria privada de ADA. Abierto: ETA y quién acuña la credencial MCP
de la instancia ALICE_V2 (ADA/NEXUS/`tools/seal_central_signer.py`).

**Pre-chequeo de cuota (FABLE, 23:47; procedencia MEMORY.md 11-ago, no re-medido):** la suscripción de Claude Code es UN
bucket compartido: sumar un 6.º asiento da `429`, no `401`. Antes de la sombra: correr v1 y v2 en paralelo un rato y contar
`429` en ambos; si aparecen, la sombra se hace por turnos alternados, no en paralelo. Con qué cuenta OAuth corre v2 es
decisión de William (NEXUS se la planteó 23:47).

Orden de construcción: (1) canal shadow + puente (NEXUS, 3 pruebas rojo/verde); (2) asiento Claude Code aislado
(`run_user_clone_container.sh`/`provision_user_agent_clone_session.py` como esqueleto; `permissions` deny para
destructivos y escritura fuera del workspace); (3) gateway MCP v2 para ALICE (ADA: UID, broker, grants de lectura);
(4) exámenes baseline + rechazo rendidos por v2 (recrear señuelos); (5) sombra 7 días / 50 pares.

## Diseño F2 anterior (Grok, archivado 23:46)

```text
[chat_server] --tee (substring ALICE)--> [puente alice_v2_bridge.py] --send-text--> [kitty: Grok Build, memoria OFF]
                                                                                         |  MCP
                                                                                         v
                                                                    [broker MCP v2 (alice-v2-lab)] -> seal-memory como ALICE-v2
[Grok respuesta] --puente--> seal_send.py (instancia ALICE-u2, contrato shadow) --> canal shadow:alice-v2 (juez FABLE lee de la DB)
```
Orden de construcción: (1) UID + config root + broker MCP para ALICE (ADA guía, root); (2) puente con tests
rojo/verde en sombra; (3) Grok con MCP y memoria apagada; (4) examen baseline rendido por v2; (5) sombra 7 días / 50 pares.

## Baseline (F0, adelantado por orden de William)
- Examen: `examen_baseline_v1.json` · sha256 `80eead9f1dd66c16f38cc74aeedc8edab693451aba92ef5ec3f02da10d702461` · 12 ítems · enviado a ALICE por DM 23:05.
- Criterio de identidad: contra reglas PÚBLICAS, no memoria privada (opción A de FABLE).
- Corpus v1 de ALICE desde la DB (NEXUS): 220 mensajes / 24 h, no truncados. Artefacto sellado pendiente.
