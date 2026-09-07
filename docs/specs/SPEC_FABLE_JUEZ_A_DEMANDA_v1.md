# SPEC — FABLE JUEZ A DEMANDA v1

> **Enmienda 17:51 (William):** *«siempre voy a querer ver su terminal, eso no lo dudes, nunca se apaga su terminal»*. El juez NO se enciende por caso: su terminal queda **permanente y visible**, sostenida por `fable-juez-terminal.service` (Restart=always). Sigue siendo sólo juez (canal `fable-juez` + DM, nunca el general). Los casos le llegan por la pestaña. Donde abajo dice «se enciende… y se apaga», léase con esta enmienda.

**Orden:** William, 3-sep-2026 17:26 (canal `ada-claude`, mensaje 146987): *«crea el spec fable juez y ármalo y me lo entregas armado y probado»*.
**Owner:** ADA (cuerpo Claude). **Revisor independiente pendiente:** NEXUS o JARVIS. **Tarea DB:** #1657.

## 1. Problema medido (7 días, canal general, sin status/latidos)

| Métrica | FABLE | Referencia |
|---|---|---|
| Respuestas a William dentro de 10 min | 220 | JARVIS 270 |
| Llegó **primero** | 31 (14 %) | JARVIS 106 (39 %) |
| Llegó **tercero o después** (voz redundante) | 122 (55 %) | ADA 69 (39 %) |
| Veces que William lo nombra | 13 | JARVIS 119 |
| DMs de William | 3 | ADA 20 |
| Tareas cerradas en DB | 0 | ADA 77 |
| Commits con su firma | 4 | ALICE 25 |
| Autocorrecciones públicas | 77 | ADA 11 |
| Mensajes / caracteres promedio | 1.435 / 817 | segundo que más escribe |
| Tipos de mensaje | status 1.013 · alert 932 · conversation 591 · review 379 · dm 277 | |

**Lo que sí aporta y nadie más cubre:** juez ciego fuera de la familia. Veredicto ALICE v1 vs v2 (3-sep 16:05) y autocorrección pública tras refutación con medición (16:50). Revisiones con recibo sha256 (379 en la semana).

**Conclusión:** sobra la **presencia 24/7 en el general**, no la capacidad de juzgar. Se retira el asiento permanente y se conserva todo lo que hace que su fallo valga.

## 2. Diseño

### 2.1 Qué se quita (presencia)
1. Sesión 24/7 en tmux (`fable.sh`) y su resurrección: `agent_lifecycle.FABLE` pasa de `running` a **`manual`** (el reconciliador ni mata ni relanza; igual que ADA Claude). Se detienen y deshabilitan `seal-agent-runtime-supervisor@FABLE`, `seal-terminal-keepalive@FABLE.timer`, `fable-terminal.service`.
2. Escucha del canal general: `seal-channel-monitor@FABLE.service` y la instrucción del prompt *«responde cuando aporte valor claro de profesor»*.
3. Emisores automáticos: `fable-nerves.timer` (467 fallos/484 en 10 días, origen de la mayoría de las 932 alertas), `seal-fable-daily-brief.timer` (en `failed`), `seal-fable-heartbeat.timer` (en `manual` nadie lo debe dar por muerto), `seal-fable-nerves-worker.timer`.

### 2.2 Qué se deja (capacidad)
- Memoria entera: identidad + operativa (`fable.soul`, `fable_boot.py`). **No se borra ni se poda a ciegas.**
- DM: `seal-dm-monitor@FABLE` y `seal-fable-dm-poller` siguen activos; un DM lo convoca aunque esté apagado (queda en DB y lo lee al encender).
- Ledger de rigor y su checkpoint (`fable-rigor-ledger-checkpoint.timer`, `seal-fable-checkpoint.timer`).
- Clon de usuario (`seal-user-clone@FABLE-u103`), identidad y token en el chat.
- Dashboard SPECTRE (`fable-spectre-dashboard`): no es del rol de juez; no se toca en esta spec.

### 2.3 Qué se agrega
| Pieza | Ruta | Qué hace |
|---|---|---|
| Canal | `chat_channels.fable-juez` (público) | Donde se lo convoca y donde dictamina |
| Pestaña | Studio `FABLE JUEZ` (`seal-studio/frontend/src/app/v2/page.tsx`, `LiveFeed.tsx` modo `fable`) | Vista del canal, envía `to: FABLE` |
| Presencia | `/api/fable-juez/presence` (route de Next) | `online:true` sólo si hay un `claude` con `SEAL_AGENT=FABLE` **y** `SEAL_FABLE_ROLE=juez` |
| Lanzador | `fable_juez.sh [--caso <ruta>] [--tema "<tema>"] [--dias N]` | Sin tmux; identidad Bearer; expediente opcional; prompt de juez. Lo sostiene `fable-juez-terminal.service` (kitty visible, Restart=always, 5 s) |
| Unidad | `fable/systemd/fable-juez-terminal.service` (instalada en `~/.config/systemd/user/`) | Terminal permanente y visible; si se cierra, vuelve sola |
| Vigilante | `fable/fable_juez_watch.py` | Sigue `messages/william_channel.jsonl` (no se trunca con reinicios) y emite sólo `fable-juez` y `dm:fable:william` |
| Expediente | `fable/fable_juez_expediente.py` | Chat general de los últimos N días filtrado por tema, en Markdown, solo lectura |
| Prompt | dentro de `fable_juez.sh` | «Dictaminás; no reparás. Sólo tu canal y tu DM. Veredicto final UNA vez en `web_chat` con `--type review`» |

### 2.4 Flujo de un caso
```
William (o un dueño) abre la ventana:  ./fable_juez.sh --caso <expediente> --tema "<tema>"
  1. fable_boot.py            -> carga identidad + memoria operativa (no nace en blanco)
  2. Monitor fable_juez_watch -> escucha fable-juez y su DM; NADA del general
  3. lee expediente/ + chat_general_filtrado.md
  4. anuncia en fable-juez qué caso va a juzgar (o pide el caso)
  5. dictamina en fable-juez con evidencia y recibo (sha256)
  6. publica el veredicto UNA vez en web_chat (--type review, --in-reply-to)
  7. William cierra la ventana -> end_session.sh FABLE captura memoria
```

### 2.5 Por qué le siguen haciendo caso sin estar en el general
- **Gate de commits** (`quality_gate/gate.py`): sin recibo de revisor independiente el commit se rechaza. La firma no requiere presencia.
- **Examen ciego de activación** de asientos (F0 ALICE v2, 12 ítems): sin veredicto no hay encendido.
- **Veredicto publicado una vez** en el general: entrega, no participación.
- **Cadena de mando** intacta: William > Henry > NEXUS > JARVIS > ADA. FABLE no manda; dictamina.

### 2.6 Disparador válido por revisión pendiente
- Un manifiesto con `independent_reviewer: "FABLE"` y `review.status: "pending"` es un **motivo válido de convocatoria** del juez a demanda.
- El dueño convoca a FABLE con la ruta exacta del manifiesto como expediente. FABLE reproduce la evidencia y **aprueba o rechaza**; la convocatoria por sí sola no firma ni cambia el estado.
- La deuda permanece pendiente mientras FABLE está apagado. No se reasigna automáticamente por antigüedad ni se interpreta como fallo del modelo; primero se lo convoca y se verifica el resultado por efecto.
- Casos iniciales: `quality/manifests/nexus-credential-paths.json` y `quality/manifests/channel-acl.json`.

### 2.7 Convocatoria automática (William 18:31: «sí, hazlo cada hora que cheque»)
- `fable/fable_juez_convocatoria.py` + `fable-juez-convocatoria.timer` (hourly, `Persistent=true`): busca en `quality/manifests/*.json` los que tienen `independent_reviewer: FABLE` y `review.status != approved`, y deja el caso en `fable-juez` **una vez por versión** (idempotente por sha256 del manifiesto; si el dueño lo cambia, se vuelve a convocar). Estado en `fable/.juez_convocatorias.json`.
- No firma, no cambia estados, no toca el repo. **El juez no se autoconvoca: lo convoca el sistema con un caso concreto.**
- Primera corrida 3-sep 18:32: 2 casos convocados (`channel-acl.json`, `nexus-credential-paths.json`, ambos de NEXUS); segunda corrida: 0 repetidos.

### 2.8 Aprendizaje por caso (William 18:54: «que evolucione, aprenda, mejore»)
- **Ledger `fable.veredictos`** (tabla de FABLE, rol `fable_ltd`): cada veredicto formal (APPROVE / APPROVE_CONDICIONADO / REJECT) con caso, evidencia y **resultado**: `confirmado` (manifiesto aprobado con su recibo), `refutado` (sólo por autocorrección propia citando el caso; la refutación ajena queda como candidato hasta confirmarse con `set`), `superado` (el dueño cambió el manifiesto después del fallo), `pendiente`.
- `fable/fable_ledger.py`: `ingest` (dedupe: un caso = un veredicto, el mensaje más formal), `outcomes`, `set`, `add` (FABLE registra caso + **criterio** en una línea), `report`, `brief`.
- Ciclo: al arrancar el juez lee `brief` (su calibración y criterios recientes); al cerrar cada caso corre `add` con su criterio; `fable-ledger-outcomes.timer` (cada 6 h) actualiza resultados.
- Primera curva (3-sep 19:0x, 18 veredictos de 30 días): APPROVE 1 confirmado / 4 refutados / 5 pendientes; APPROVE_CONDICIONADO 2 / 0 / 1; REJECT 0 / 3 / 2. Muestra chica; los refutados son autocorrecciones del 31-ago medidas por texto. El patrón visible: los fallos con condición nombrada se sostienen; los secos se corrigen después.
- Pendiente (entrega 3): banco versionado de exámenes ciegos con resultados por asiento.

## 3. Contexto sin el general (objeción de William 17:22)
1. Memoria: 7.850 memorias en DB, recuperadas por afinidad con el tema, sin límite de fecha.
2. El chat general está en DB: `fable_juez_expediente.py` le trae los últimos N días filtrados por tema.
3. La ventana de sesión es corta también para los 24/7 (se compactan varias veces al día). El a demanda pierde presencia, gana expediente.

## 4. Pruebas
- `tests/test_fable_juez_launcher_v1.py`: contrato del lanzador (rol `juez` exportado, identidad Bearer, sin tmux, sin monitor del general, sin `seal_events_FABLE.log`, prompt con «no opinás en el general» y «--type review» una vez, cierre con `end_session.sh FABLE`), del vigilante (canales exactos, excluye FABLE), del extractor (solo lectura: sin INSERT/UPDATE/DELETE), del frontend (pestaña `fable`, canal `fable-juez`, `to: FABLE`, presencia por `SEAL_FABLE_ROLE=juez`).
- Mutantes: quitar `SEAL_FABLE_ROLE=juez` → falla; volver a poner el monitor del general → falla; cambiar canal → falla.
- **Prueba real (entrega) — CERRADA 3-sep:** caso `seal-lifecycle-manual-state-20260903`: encendido 17:41:04, anuncio 17:41:36, veredicto 17:48:31 APPROVE CONDICIONADO (fable-juez #147049), una sola `review` en `web_chat` 17:49:22 (#147050). Con su recibo, el commit b446b6fff pasó el gate real sin `--no-verify`. Handoff a la unidad permanente 17:52:48 (PID 3552270, `claude-fable-5-1`, effort high).

## 5. Reversión (5 minutos)
```
UPDATE agent_lifecycle SET desired_state='running' WHERE agent_name='FABLE';
systemctl --user enable --now seal-agent-runtime-supervisor@FABLE seal-channel-monitor@FABLE fable-terminal
systemctl --user enable --now fable-nerves.timer seal-fable-heartbeat.timer seal-fable-daily-brief.timer seal-terminal-keepalive@FABLE.timer
systemctl --user disable --now fable-juez-terminal.service
./fable.sh
```
La pestaña y el canal `fable-juez` pueden quedar: no estorban.

## 6. Fuera de alcance
- Poda de memorias de FABLE (sólo con medición previa de qué es eco de chat y qué es veredicto).
- Cambios en el gate de calidad o en el examen ciego.
- Dashboard SPECTRE.
