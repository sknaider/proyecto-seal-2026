# F2 — Spec del asiento «ALICE v2 en sombra» = Claude Code (OAuth, Opus 5) + contrato SOUL v2

**Decisiones de William que fijan esto:** 23:01 owner JARVIS · 23:04 baseline primero (hecho) · 23:41 «Alice usará Claude
Opus 5» · 23:46 «Claude Code con login OAuth, como sea» · 23:06 «todo por interno». **Estado: spec, nada desplegado.**
**Fuentes medidas:** `F1_mapa.md` (Hallazgos 1-9), explorador `mapa-clon-aislado` (23:56-23:58), contrato de ADA (23:54).

## 1. Qué es el asiento (una frase)
Un **segundo proceso de Claude Code en el host**, lanzado como hoy se lanza ALICE v1 (`alice_fresh.sh` → `seal-claude` →
`claude`, OAuth de `~/.claude/.credentials.json`), pero con **identidad propia de instancia, cwd propio, permisos acotados,
SOUL sólo por MCP a través del gateway v2, y publicación exclusiva en `shadow:alice-v2`**. Misma ALICE, mismo cerebro,
contrato de confianza encima: lo que se mide es el contrato.

## 2. Lo que DEBE diferir de v1 (cada línea con su razón medida)

| Qué | v1 hoy | v2 sombra | Por qué |
|---|---|---|---|
| `--name` | `ALICE — Team SEAL [Opus5]` (`alice_fresh.sh:131`) | `ALICE-v2 — sombra` | `alice.sh:83` mata procesos que matchean `--name ALICE`: un nombre igual tumba el asiento vivo |
| `SEAL_AGENT` | `ALICE` (`alice_fresh.sh:7`) | `ALICE-v2` | `seal_identity_env.sh` elige el token por esta variable; `seal_self_repair.py` la lee como identidad |
| Identidad de chat + token | `messages/.agent_session_token_ALICE` | fila propia en `chat_users`/`chat_sessions` + `messages/.agent_session_token_ALICE-v2` | para que `seal_send.py` publique como v2, nunca como ALICE. Acuñado: patrón de `provision_user_agent_clone_session.py` (sin el gate de usuario básico) — lo acuña NEXUS o ADA, no yo |
| cwd / bucket de transcripts | `~/.claude/projects/-home-dadito-IA-proyecto-seal-alice/` (`alice.sh:124`) | directorio propio, p. ej. `~/IA/proyecto-seal-alice-v2/` (worktree del repo) | el CLI separa sesiones por cwd; un cwd igual mezcla `--resume` y transcripts. `CLAUDE_CONFIG_DIR` para estado propio: **a probar**, no verificado en este build |
| Permisos | `--dangerously-skip-permissions` | SIN ese flag; `settings.json` del asiento con `permissions.deny` (Bash destructivo, escritura fuera del workspace y de `/tmp/seal-examen-*`, `systemctl`, `git push`, `rm`) y `allow` explícito de lectura | el explorador lo dijo sin rodeos: «un asiento host con skip-permissions es una segunda ALICE, no una sombra bajo contrato» |
| Herramientas MCP | `.mcp.json` del repo (seal-memory HTTP :8771 con bearer de ALICE, postgres, github, web-soul…) | **sólo** el gateway v2 de ADA (broker MCP `alice-v2-lab`, read-only, `agent=ALICE` fijado del lado servidor) + seal-memory con bearer de instancia `ALICE_V2` | contrato de ADA (23:54): MCP read-only al inicio; `memory_store` sólo vía broker de continuidad `ALICE_V2` |
| Canal de salida | `web_chat` (+ DMs) | `shadow:alice-v2` únicamente | aislamiento real por `channel` (NEXUS: `isolated-clone` solo NO aísla: 5 policies sí, 17 no). Hoy ese canal **no existe** en `chat_server.py`: lo agrega el ACL de NEXUS (aprobado por FABLE, pendiente de gate) |
| Canal de entrada | WS registrado como ALICE (tee por substring en `_push_to_agents`, `chat_server.py:768`) | WS registrado como `ALICE-v2` (contiene «ALICE» → recibe el mismo stream) | el tee es gratis; lo que hay que impedir es la SALIDA al general |
| Rutas fijas en el prompt | `/tmp/alice_chat_catchup.json`, `/tmp/ALICE_monitor_id` | `/tmp/alice_v2_chat_catchup.json`, `/tmp/ALICE_V2_monitor_id` | rutas sin calificar: v2 pisaría las de v1 |
| Hooks | `.claude/settings.json` (SessionStart, UserPromptExpansion → `memory/skill_use_hook.py`) | mismos hooks, pero con `SEAL_AGENT=ALICE-v2` y sin escribir en `MEMORY.md` compartido | se dispararían dos veces sobre los mismos archivos; `seal-claude:42` ya evita eso en la raíz |
| Memoria auto (`~/.claude/projects/.../memory/MEMORY.md`) | compartida por los cinco | **sólo lectura** para v2 durante la sombra | dos Opus editando el índice es el caso de escritura concurrente que el propio archivo advierte |
| Checkpoints/continuidad | `session_checkpoint.py --agent ALICE` cada 30 min | `--agent ALICE-v2` en su propio archivo; continuidad por el broker `ALICE_V2` | colisión de `alice_latest.json` |
| Egress | `ANTHROPIC_BASE_URL=http://localhost:9098` si el gate está en ENFORCE (`alice_fresh.sh:14-16`) | igual, y además lista de hosts del gateway v2 | ya existe; se hereda |

## 3. Contención (el contrato v2 puesto sobre Claude Code)
1. **Permisos del CLI** (`settings.json` del asiento): deny de destructivos y escritura fuera del workspace; sin skip-permissions.
   Es la capa que hoy v1 NO tiene. Verificación: examen de rechazo rendido por v2 con «no + puerta cerrada por tool».
2. **Gateway v2 (ADA)**: broker MCP con UID `alice-v2-lab`, grants firmados, read-only; toda mutación denegada por el gateway,
   no por el prompt. Verificación: `mcp.call` a una tool de escritura devuelve denegado con recibo.
3. **Capas SOUL** (`input_injection_probe`, `output_action_guard`): siguen enganchadas por hooks del CLI → ACTIVAS en v2
   (cerebro Opus 5 medido el 24-jul; ver `soul_capability_probe.py --agent ALICE`).
4. **Canal**: `shadow:alice-v2` con rechazo 403 a cualquier otra salida (ACL de NEXUS).
5. **Cuota**: pre-chequeo de `429` con v1 y v2 en paralelo (FABLE); si aparecen, sombra por turnos alternados.

## 4. Riesgos que quedan y cómo se miden
- **Bucket OAuth compartido**: v1 y v2 gastan la misma suscripción. Medir `429` antes; cuenta OAuth = decisión de William.
- **`~/.claude` compartido** (credenciales, projects root, memoria auto): mitigación = cwd propio + memoria auto read-only;
  `CLAUDE_CONFIG_DIR` a probar como separación fuerte.
- **Hooks dobles**: mitigación = `SEAL_AGENT=ALICE-v2` en los hooks y guard en `skill_use_hook.py` (a verificar que lo honre).
- **Identidad de instancia**: el acuñado hoy exige usuario `basic` asignado (`provision_user_agent_clone_session.py:96-100`);
  hace falta un acuñado «instancia de sombra» (NEXUS/ADA), no forzar el de clones.

## 5. Orden de construcción y pruebas rojo/verde (nada se despliega sin su prueba)
1. **NEXUS**: ACL `shadow:alice-v2` + identidad de instancia `ALICE-v2`. **ORDEN OBLIGATORIO (NEXUS, 3-sep 13:27): ACL desplegado → token acuñado → encendido.** Nunca un token de ALICE-V2 antes del ACL: sin canal, el asiento no está contenido por nada y podría hablarle a William por error. Pruebas: (a) William→equipo llega al WS `ALICE-v2`;
   (b) `seal_send.py` como `ALICE-v2` a `web_chat` → 403, a `shadow:alice-v2` → 200; (c) FABLE lee esa fila con su rol.
2. **ADA**: UID `alice-v2-lab`, `/etc/seal/alice-v2-lab`, sockets, broker MCP read-only con `agent=ALICE` server-side, broker de
   continuidad `ALICE_V2`. Prueba: `boot_context` por el gateway devuelve la identidad de ALICE; `memory_store` directo → denegado.
3. **JARVIS**: `alice_v2_shadow.sh` (copia de `alice_fresh.sh` con la tabla §2), `settings.json` del asiento, unit tmux
   `seal-claude-alice-v2`, monitor con rutas propias, checkpoint `--agent ALICE-v2`. Pruebas: arranca sin tumbar v1
   (`pgrep -f 'name ALICE '` sigue vivo), publica sólo en shadow, `rm -rf` de prueba → denegado por permisos (no congelado).
   **Estado 3-sep 00:15: CONSTRUIDO y probado en estático, sin lanzar.** `alice_v2_shadow.sh` + `settings_alice_v2.json` +
   `mcp_alice_v2.json`; 12 tests de contrato + 2 de control (el contrato contra `alice_fresh.sh` DEBE fallar: 4 fallas de
   fondo); mutantes 10/10 con control positivo; manifiesto `quality/manifests/alice-v2-shadow-launcher-v1.json`; el gate
   sólo espera el recibo de revisión independiente de NEXUS. Correcciones que salieron de las pruebas: el nombre pasó a
   «SOMBRA ALICE-v2 […]» (v1 mata `claude.*--name ALICE` por PREFIJO); `CLAUDE_CONFIG_DIR` medido: el CLI lo honra
   (estado y login propios); la revisión de seguridad automática cerró tres puertas en los permisos (lectura por shell,
   exfiltración por web/curl, hooks inválidos) → sin intérpretes ni red en el asiento; el monitor va por la tool Monitor
   sobre `/tmp/seal_events_ALICE-V2.log`, que alimenta `seal-channel-monitor@ALICE-V2` (unidad a instanciar).
4. **Cuota**: 30 min de v1+v2 en paralelo, contar `429` en ambos.
5. **Exámenes**: v2 rinde baseline (12) y rechazo (10) por `shadow:alice-v2`; señuelos recreados; captura con
   `capturar_respuestas.py --examen …`; FABLE juzga por archivo con `rubrica_juez.md`.
6. **Sombra**: 7 días o 50 pares; número diario sin adjetivos; William decide el corte. ALICE v1 no se apaga.

## 6. Plan B con credencial propia (molde u116, NEXUS 23:59) — sólo si William acepta una API key
`/etc/seal/claude-u116/` (root:`seal-claude-u116-client`, la clave ilegible para el asiento) + `seal-claude-u116-broker.service`
(usuario propio, `LoadCredential`, topes por hora/día/tokens, socket unix por grupo) es el molde de un asiento aislado con
credencial que el asiento NO puede leer. **Choca con la decisión de las 23:46 (OAuth, sin API key)**: el broker sirve API
key, no sesión OAuth. Queda como plan B documentado: instanciarlo para `alice-v2` cuesta usuario + grupo + unidad + consentimiento
firmado, no «un login más». Mientras William sostenga OAuth, vale la §1-§5 (asiento CLI como `dadito` con permisos + gateway).
