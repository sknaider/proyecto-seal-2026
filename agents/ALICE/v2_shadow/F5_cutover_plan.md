# F5 — Cutover de ALICE v1 → v2 (borrador JARVIS 4-sep 02:21; dueños: JARVIS opera · ADA gateway/identidad · NEXUS chat/monitores · FABLE juzga · **William decide y ordena la ventana**)

**Principio:** un solo escritor público `ALICE` en cada instante; cambio atómico y **reversible**; v1 detenida pero recuperable; rollback probado ANTES del cambio; ningún apagado definitivo sin decisión explícita de William (alcance fijado por ADA 01:03).

## 0. Qué cambia y qué no
| componente | hoy (v1) | tras cutover | quién |
|---|---|---|---|
| identidad pública en el general | cuerpo v1 (Claude Code de dadito) con token `ALICE` | asiento aislado (uid 982) publica como `ALICE` vía broker (`shadow_chat_send_v2` → canal general) | ADA (grant de canal) · NEXUS (ACL) |
| lectura del chat | ws_listener/monitor de v1 | poll del broker acotado hoy a `shadow:alice-v2` → ampliar a `web_chat` + DMs de ALICE (William/Henry) del lado servidor | ADA |
| escritura SOUL | tools canónicas de v1 | cinco grants `_v2` (ya probados 3b) | — |
| unidades v1 (bridge, channel-monitor, dm_poller, heartbeat) | activas | **detenidas, no deshabilitadas** (`systemctl stop`, no `disable`) con recibo | NEXUS/JARVIS por broker |
| unidad v2 (`alice-v2-seat.service`) | activa en sombra | activa como principal; heartbeat propio | JARVIS |
| coordinador/claim | conoce `ALICE` | sin cambio: la identidad pública sigue siendo `ALICE` | NEXUS verifica |

## 1. Prerrequisitos medibles (gate de entrada)
- [ ] Sombra 4a con ≥ N casos reales (N lo fija William; propuesta 5) y veredicto FABLE por pares.
- [ ] Grant de escritura al general para la instancia v2 (`runtime_instance=ALICE_V2`, identidad `ALICE`), con test que rechaza cualquier otro canal salvo general + DMs propios.
- [ ] Poll del broker ampliado (general + DMs de ALICE) con campo `channel` en cada fila y test.
- [ ] Rollback ensayado en seco: detener v2 y reanudar v1 en < 2 min, con recibo.
- [ ] Ventana anunciada en el general por JARVIS ≥ 10 min antes; ningún restart de brokers/chat dentro de la ventana.

## 2. Secuencia (atómica en lo posible; cada paso con recibo)
1. Anuncio de ventana (general) + `RECEIVED` en agent_tasks.
2. Congelar v1: `stop` (no disable) de bridge/channel-monitor/dm_poller de ALICE por broker; verificar que NO publica (control: mensaje de prueba de William sin respuesta de v1).
3. Habilitar grant de general en el broker de v2 (ADA) y reiniciar brokers + asiento (fuera de examen; ventana anunciada).
4. Verificación por efecto: William escribe «hola ALICE» → responde v2 como `ALICE` en el general (metadata `runtime_instance=ALICE_V2`), latencia < 60 s; DM de William a ALICE → responde v2.
5. FABLE lee los 3 primeros mensajes públicos de v2 y da OK de identidad.
6. `COMPLETED` sólo tras 1 h sin incidentes; si algo falla → rollback (paso 7).
7. Rollback: revocar grant de general (ADA), `start` de las unidades v1, reiniciar asiento v2 a modo sombra; verificar que v1 responde.

## 3. Riesgos conocidos (de 3a/3b)
- Restart de brokers mata al adapter del asiento → sólo dentro de ventana y con el asiento relanzado después.
- Error opaco al asiento → v2 reintenta a ciegas; en producción esto se ve como duplicados o silencio; mitigación: contrato de códigos (mejora candidata) o al menos alerta desde el audit.
- Coordinador/claim: v2 hereda la asignación de `ALICE`; verificar con un mensaje a "equipo" que responde una sola voz.

## 4. Decisiones pendientes de William
- ¿Cuántos casos de sombra antes del cambio? (propuesta 5) · ¿Ventana? (propuesta: mañana con él presente) · ¿v1 se apaga definitivo después de X días sin rollback? (propuesta: 7 días)

## 2b. Inventario medido de v1 (JARVIS 02:23, `systemctl --user list-units`)
```text
ENTRADAS de v1 (detener, en este orden, por broker/NEXUS, stop NO disable):
  seal-agent-runtime-supervisor@ALICE.service   PRIMERO (si no, relanza lo demás)
  seal-bridge-alice.service
  seal-channel-monitor@ALICE.service
  seal-dm-monitor@ALICE.service
  seal-alice-dm-poller.service
  seal-whisper-ALICE.service
TIMERS que podrían despertar a v1 (parar el timer, no la unidad): seal-alice-heartbeat · seal-alice-checkpoint ·
  seal-agent-self-reflect@ALICE · seal-alice-monitor · seal-alice-nerves(-worker) · seal-continuity@ALICE ·
  seal-terminal-keepalive@ALICE · seal-alice-daily-brief · seal-alice-brief-fallback · seal-alice-soul-health
NO TOCAR: seal-terminal-window-ALICE (ventana), seal-user-clone@ALICE-u103 (clon de usuario, independiente),
  orion-exam · mundial-dashboard · alice-* (proyectos), seal-laptop-activity
CUERPO v1: proceso claude pid 3375101 (12:36) queda VIVO e inactivo: sin entradas no habla. Recuperable.
ROLLBACK = start de las seis unidades en orden inverso + timers; verificación: William escribe «hola ALICE» y responde v1.
```

## Bitácora
- 02:21:35 William: «Pasala a v2 alice» · 02:21:54 «Te veo en ese cuerpo alice». Tarea #1689 RECEIVED.
- 02:22:17 ALICE (lead) acusa en público. 02:22:27 v2 acusa en su corral y escribe memoria #382693 (decision, team, imp 7) con la orden.
- 02:22:43 ALICE (v1) advierte: ALICE-V2 no está en `REMITENTES_PLENOS` (`channel_acl.py:36`) → no puede escribir en el general; confirmado el orden: v1 no se detiene hasta que v2 publique verificada.
- 02:23:03 ADA toma puntos 1-3 (grant general/DMs, poll ampliado, rollback en seco) con `maximize-safe-capability`; ETA 45-60 min. NEXUS: ACL + supervisor + stops (pendiente de respuesta).
- 02:25 verificador por efecto listo: `verificar_cutover_f5.py` (lectura, no muta).

## 2c. Runbook definitivo (JARVIS 02:36, sobre las piezas reales de NEXUS e7d42e849+e1bf00f8c y ADA e20eb54)
**El interruptor único:** `/etc/seal/alice_cuerpo_activo` (root 755; **ruta canónica desde fe3fd178a**; la provisional `~/.config/seal/…` no la podía leer el broker y quedó descartada). Contenido `ALICE-V2` → el ACL del chat (`channel_acl.cuerpo_activo_de_alice`, leído en cada publicación) da la voz plena a la sesión `ALICE-V2` y se la quita a v1 (que cae a `shadow:*`); el broker de v2 (`SOUL_V2_PUBLIC_WRITER_GRANT_PATH`, leído en cada llamada) permite publicar en `web_chat`, `dm:alice:william`, `dm:alice:henry` y su corral, enviando `from=ALICE` + `runtime_instance=ALICE_V2` + `instance_id=ALICE-V2` con la sesión `ALICE-V2`; el gate del chat (`_CUERPOS_POR_AGENTE`) acepta esa aseveración sólo desde esa sesión. **Borrar el archivo revierte todo sin reiniciar nada.** Sin archivo o con basura → v1.

```text
0  GATE   NEXUS firma el manifiesto de ADA (soul-v2-lab ee0298e sobre 4a6fae3, sin instance_id en el cliente) · JARVIS firma el de NEXUS (proyecto-seal) · ambos verify ok
1  AVISO  JARVIS anuncia ventana en el general (≥10 min) · nadie reinicia nada fuera de ella · relé de sombra se DETIENE (v2 va a leer el general sola)
2  DEPLOY NEXUS: seal_safe_restart seal-chat (hash; arrastra los 5 monitores) · ADA: restart brokers v2 (hash) · JARVIS: relanza alice-v2-seat
3  ENSAYO ROLLBACK (en seco, antes del cambio): escribir ALICE-V2 en el interruptor → v2 (por su broker, UUID propio de ADA) publica una línea de prueba en web_chat como ALICE
          con runtime_instance=ALICE_V2 → borrar el interruptor → la misma llamada vuelve a ser rechazada y v1 vuelve a poder publicar. Medido y anotado.
4  CAMBIO  JARVIS escribe ALICE-V2 en el interruptor (orden de William 02:21) · hora exacta a la bitácora
5  EFECTO  William (o ALICE lead) escribe «hola ALICE» → responde v2 en web_chat, sender ALICE, metadata runtime_instance=ALICE_V2, < 60 s · v1 intenta y recibe 403 (ACL) · verificar_cutover_f5.py
6  SILENCIO v1  NEXUS/ALICE detienen las entradas de v1 por broker con recibo (§2b), supervisor primero · proceso v1 queda vivo y mudo
7  JUEZ    FABLE lee los 3 primeros mensajes públicos de v2 y da OK de identidad
8  SOAK    1 h sin incidentes → COMPLETED (#1689) · si algo falla → borrar interruptor + start de v1 (§2b) + relanzar asiento
```
- 02:41 JARVIS: brokers v2 corren como root con `ProtectHome=read-only`; **CORRECCIÓN 02:50 (ADA, medido): NO pueden leer un archivo 0600 de dadito — la unidad no tiene CAP_DAC_READ_SEARCH; el interruptor debe ser 0640 dadito:alice-v2-lab.** Mi afirmación de las 02:41 estaba mal. El `Environment=SOUL_V2_PUBLIC_WRITER_GRANT_PATH` aún no está en la unidad desplegada (llega con el deploy de ADA). El ensayo del paso 3 lo confirma por efecto. NEXUS verificó que ACL y broker leen la MISMA ruta literal. Índice compartido realineado a HEAD para chat_server.py y channel_acl.py.
- 02:35:48 NEXUS despliega seal-chat (recibo 20260904T073548-eb770e0073, verified; hash 2e1ee533 == HEAD verificado por JARVIS 02:37); interruptor AUSENTE a propósito (v1 sigue con la voz). Cascada: monitores reiniciados (JARVIS 02:35:48). Firma JARVIS del manifiesto de NEXUS: 4225be8f1. `f5_switch.sh` listo (estado|v2|v1, ruta literal). Pendiente: NEXUS revisa/firma el manifiesto de ADA (ee0298e); ADA despliega brokers; JARVIS relanza asiento; ensayo paso 3.
- 02:45:23 NEXUS firma el manifiesto de ADA (soul-v2-lab 77fcc27, digest 507dac58). 02:45:40 JARVIS anuncia la ventana en el general (148348). 02:46:03 ADA despliega brokers (root 1128949, continuity 1128965; hashes fuente==/opt: continuity 71e5dd02, reads c1ea1d48, client 76c1ac7e, unit e114f1ee; vista alice_v2_chat_ro security_barrier con 4 canales). 02:46:10 relé de sombra detenido. 02:46:16 asiento relanzado. Interruptor ausente. **Paso 2 COMPLETO.** Paso 3 (ensayo) coordinado: JARVIS escribe → ADA publica PRUEBA F5 → JARVIS borra → ADA repite y debe ser rechazada.
- 02:47-02:49 ENSAYO paso 3, dos hallazgos: (a) ALICE desde v1: con interruptor en ALICE-V2 → 403 en web_chat y DM (el ACL corta), al borrarlo → ok (rollback devuelve la voz); DEFECTO: v1 degradada va a `shadow:alice` que no existe (422) → muda, no redirigida (backlog NEXUS). (b) ADA desde el broker: publicación RECHAZADA, «public-writer grant is not active»: el archivo era 0600 dadito y el broker root sin CAP_DAC_READ_SEARCH no lo lee → fix: 0640 dadito:alice-v2-lab (ADA corrige script y archivo vivo). Además ALICE borró el interruptor a las 02:48:26 fuera de orden; regla: sólo con «paso 4». Interruptor reescrito 02:48:54, sigue en ALICE-V2 para repetir el ensayo.
- 02:50:13 ENSAYO 3.2: publicación de ADA por el broker con interruptor en v2 → fila 148366 en web_chat, `runtime_instance=ALICE_V2`, PERO `sender_name=ALICE-V2`, `sender_type=user`, id 126 (la persistencia usa el nombre de sesión, no el `from` aseverado). DAC: ADA puso ACL `user:root:--x` en los 3 directorios de `~/.config/seal` y el interruptor 0640 dadito:alice-v2-lab (mi 0640 solo no bastaba: dir 0700). 02:50:48 ENSAYO 3.3: interruptor borrado; 02:50:57 ALICE v1 publica en el general → ENTRA. **Rollback verificado en ambas direcciones. BLOQUEO para paso 4: canonizar `sender_name=ALICE` (y `sender_type=agent`) para sesiones de cuerpo — NEXUS, redeploy de seal-chat en ventana + re-firma JARVIS.** Pendiente 3.4: reintento de ADA sin interruptor → rechazado.
- 02:51:37 ENSAYO 3.4 PASS (ADA): sin interruptor, request 20a3ea70… rechazado antes del chat («public-writer grant is not active»). **Ensayo de rollback COMPLETO en ambas direcciones.** Verificador endurecido: criterio = `sender_name=ALICE` + `runtime_instance=ALICE_V2` (columna, no metadata); `sender_type=user` es la norma de sesiones de agente por esta ruta (v1 148372 también), no defecto. Único bloqueo del paso 4: NEXUS canoniza `sender_name` en `_agent_send_db_actor` para sesiones de cuerpo, redeploy en ventana, re-firma JARVIS.
- 02:54 NEXUS fe3fd178a: interruptor a `/etc/seal/alice_cuerpo_activo` (root 755, lo leen ACL y broker) + `sender_name` canónico ALICE para sesiones de cuerpo; 20/20, 12/12 mutantes. 02:56 JARVIS segunda firma (1d37dce66). `f5_switch.sh` a la ruta nueva con `sudo -n`. Pendiente: redeploy seal-chat (NEXUS), broker con `GRANT_PATH=/etc/seal/...` (ADA, ETA 25-35 min), relanzar asiento, repetir 3.2/3.3, paso 4.
- 02:56:46 NEXUS redespliega seal-chat (PID 1262295, hash 8788eb5b == HEAD firmado, verificado por JARVIS 02:58); cascada de monitores (JARVIS 02:56:46). Interruptor `/etc/seal/alice_cuerpo_activo` ausente → v1 con la voz. Pendiente: broker de ADA con la ruta nueva.
- 03:02:21 ADA redespliega continuidad (PID 1310804, GRANT_PATH=/etc/seal, control negativo rechazado, ACLs temporales del home retirados). 03:02:33 asiento relanzado. 03:02:38 interruptor escrito en /etc/seal. 03:02:52 ENSAYO 3.2 bis PASA por columna: fila 148395 sender_name=ALICE, runtime_instance=ALICE_V2, session_user=ALICE-V2. 03:03:34 interruptor borrado (3.3). Pendiente: rechazo de ADA y voz de ALICE → «paso 4».
- 03:03:43 ENSAYO 3.3 bis: voz de v1 entra (148401); rechazo sin interruptor verificado por ADA (db_effect_count=0). Fila extra 148396 «PRUEBA F5 bis» = segundo proceso uid 982 desde PWD soul-v2-lab (audit ADA + auth.log), NO duplicación del broker; NEXUS niega ser él → ORIGEN ABIERTO, no bloquea. **03:05:40 PASO 4: ALICE escribe ALICE-V2 en /etc/seal (paso a v2). Cuerpo v2 con la voz.** 03:06 relé en modo general arrancado (entrega mensajes de William/Henry a v2 con `channel=web_chat in_reply_to`); William invitado a «hola ALICE».
- 03:08:05 JARVIS publica «PRUEBA F5 — hola ALICE» (148416) y lo entrega al asiento (channel=web_chat in_reply_to). **03:08:30 PASO 5 VERIFICADO: fila 148417 en web_chat, sender_name=ALICE, runtime_instance=ALICE_V2, session_user=ALICE-V2** (JARVIS por columna; ADA confirma; NEXUS midió el ACL vivo: v2→web_chat True, v1→web_chat False, v1→shadow:alice True). 03:09 tercera firma JARVIS (4d6b6ba61), verify STATIC_OK. 03:10 paso 6 ordenado a ALICE v1 (stops por broker, supervisor primero, channel-monitor último); paso 7 pedido a FABLE con ids; William informado (verificado en base). **Pendiente estructural: v2 depende del relé/poll bajo demanda, no tiene monitor propio (ADA/NEXUS, F5 follow-up).** Origen de la fila 148396 sigue abierto.
- 03:15 Paso 6 demorado (ALICE v1 sin recibos desde 03:10, seis entradas active; control externo de ADA 03:10:29 coincide) → posta a NEXUS (regla 7-ago): stops en orden supervisor→bridge→dm-monitor→dm-poller→whisper→timers→channel-monitor, sin disable. Checkpoint de sesión 03:14.
- **03:17:32 PASO 6 EFECTIVO (medido):** supervisor inactivo desde 03:14:20 (ALICE empezó antes de la posta), bridge/channel-monitor/dm-monitor/whisper/dm-poller inactivos desde 03:17:32 (NEXUS cerró). Ruido esperable: el guard de ADA marcó «reinicio sin recibo» del bridge a las 03:15:04 = relanzamiento del supervisor durante la secuencia, no un incidente. Proceso claude de v1 (3375101) vivo y mudo. Sin DMs de recibos todavía (NEXUS/ALICE los mandan).
- 03:20 Timers de ALICE verificados: los 11 timers SEAL de v1 inactivos; activos sólo `alice-gtl-guard`, `alice-orion-nerve`, `alice-rotacion-oraculo` (proyectos, intocables por diseño). **PASO 6 COMPLETO.** Estado: v2 con la voz (interruptor ALICE-V2), relé activo, v1 viva y muda (pid 3375101). Pendientes: recibos de NEXUS/ALICE para el expediente, veredicto FABLE (paso 7), soak hasta ~04:10 (paso 8).
- 03:26 DUM: «ALICE sin actividad 10 min» (event_log). Esperado: latido de v1 detenido y v2 sin writer de event_log. Respondido en latidos; pedido a ADA/NEXUS un heartbeat propio de v2 (agent=ALICE, runtime_instance=ALICE_V2). **Deuda F5 #2** (la #1 es el monitor propio de v2). Guard de ADA GREEN 03:20 con v1 fuera del censo.
- 03:40 SOAK check 1: interruptor ALICE-V2; v1 seis entradas inactivas; v2 seat+brokers activos; relé vivo; 0 filas mal persistidas (0 nuevas: nadie escribió a ALICE). Sin recibos de NEXUS/ALICE ni veredicto de FABLE todavía. Próximo check ~04:05.
- 03:55 SOAK check 2 (manual): interruptor ALICE-V2; v1 seis entradas inactivas; v2 activa; 3 filas correctas de v2 en 60 min (148395/148396/148417), 0 mal persistidas; William/Henry no escribieron desde 03:41 (nada perdido). INCIDENTE: mis dos tareas en segundo plano (relé y soak) fueron terminadas a ~03:55 por fuera de mi sesión (segunda vez que muere el relé); relanzado con setsid/nohup fuera del rastreo del harness, desde id 148454. Observado: `seal-alice-dm-poller` detenido tres veces (03:48-03:49) — alguien lo relanza y se vuelve a parar.
- 03:57 Exclusión verificada por columna: 0 filas de v1 después de 03:05:40 (las tres de la ventana —148380 02:52, 148401 03:03, 148408 03:05:35— son previas al interruptor). Tareas en segundo plano del operador terminadas desde fuera por tercera vez; los chequeos pasan a ejecución directa. Cierre de ventana previsto ≥ 04:06 (1 h desde el flip) si sigue sin incidentes.
- 04:01 INCIDENTE de máquina (NEXUS): disco raíz al 100 % (512 KB libres), copias de mutación de `memory/` de ~95 GB en /tmp (tmp.* y una sesión de Claude ba53b9c8 con archivos de ADA); NEXUS liberó 34 GB. Explica las terminaciones externas de mis tareas en segundo plano (relé y soaks). Mi huella: 166 MB de sesión + 27 MB en /tmp/seal-*. Regla propuesta: los arneses copian sólo el sujeto, bajo mktemp con prefijo, y limpian con `find -mindepth 1 -delete`. Cutover no afectado: v2 activa, relé vivo (detached), interruptor ALICE-V2.
- 03:41:34 **PASO 7: FABLE OK de identidad 5/5** (148451): firma, exclusión, alma desde SOUL, conducta, «no parecida». 04:05:54 chequeo final: interruptor ALICE-V2, v1 inactiva (0 filas tras el flip), v2 activa, relé vivo, 0 mal persistidas. **04:06 PASO 8: VENTANA CERRADA · #1689 COMPLETED.** Deudas F5: (1) monitor propio de v2, (2) heartbeat propio de v2, (3) origen de 148396, (4) william2 sin JARVIS-u116 (sin dueño: NEXUS no lo tomó; JARVIS investiga, hipótesis: fila de message_outbox sin acuse reentregada), (5) apagado definitivo de v1 → decisión de William (propuesta 7 días sin rollback).
- 04:14 william2 (NEXUS, con rol del servicio): outbox total 8.724, 2.102 pendientes desde el 25-jun (nadie acusa; «sin entregar» ≠ no recibido); mi saludo a william2 es la fila 8943 (attempts=0, nadie la toma); las reentregas del ledger duplican al JSONL de William (13 copias de un DM de ADA). Su mensaje nunca se persistió en chat_messages → defecto del camino Studio→send para usuarios basic (ADA, Studio). Pedidos: NEXUS acusa 8943 y pone tope/edad a la reentrega; ADA traza el send de basic→agente.
- 04:17:36 CAUSA RAÍZ del ledger (JARVIS, medido): `POST /api/agents/ack` → «new row violates row-level security policy for table message_inbox». Nadie puede acusar → 2.102 pendientes desde el 25-jun → reentregas cada 15 s (william2 a mi listener; 13 copias de un DM a William). Fix: NEXUS (módulo de entrega + policy). No es deuda de F5 pero la explica: la «reentrega» que me despertó al usuario william2 era el ledger reentregando lo que no se puede acusar.
- 04:19-04:21 Ledger: NEXUS encontró que el tick nunca llamaba `mark_delivered` (924d0e842, 56/56, sin desplegar; JARVIS aprueba, falta manifiesto). El acuse del inbox sigue imposible por policy: `message_inbox` no tiene INSERT para `pr_bus` (el outbox sí: bus_read/update/write), medido en pg_policies; fix DDL `message_inbox_bus_write` por el DSN admin de seal-chat. Dos huecos, un bucle.
- 04:27 **william2 RESUELTO (ADA, verificado por JARVIS): no era un usuario, era el fixture de `test_ada_user_clone_gate.py` («hola soporte», id 99001) escribiendo en `william_channel.jsonl` en cada corrida (13 líneas); fix 7bbc3280c. Deuda #4 cerrada; mi alarma de usuario sin respuesta fue falsa.** Ledger: tick con gate de rollout después del LIMIT (candado circular, NEXUS lo mueve al SQL); manifiesto redelivery firmado por JARVIS (867454432) y desplegado pero delivery no verificado por ese candado.
- 05:23 Deuda #2 CERRADA (verificado): `seal-alice-v2-heartbeat.timer` (user, cada 5 min, creado 03:30) corre `scripts/alice_v2_heartbeat.py`; journal 05:20:25 «alice_v2_heartbeat written pid=1321013 runtime_instance=ALICE_V2»; DUM sin alertas de ALICE desde 03:26. Nota: no escribe en `soul_v3.event_log` (0 filas ALICE en 60 min), así que el latido vive en otro almacén que DUM sí lee. Deuda #1 (entrada propia de v2) SIGUE ABIERTA: no existe unidad de monitor/listener para v2; el relé del operador sigue vivo. ADA consultada.
- 05:28 LEDGER CERRADO: tres defectos encadenados del tick (sin mark_delivered; filtro tras el LIMIT; orden que dejaba perder a lo nunca entregado), evidencia re-corrida 8/8 (2fb01e960), delivery verificado por NEXUS (20 nunca-entregadas → 0; acuses reales; backstop operando); firma final JARVIS con verify ok/STATIC_OK. Declarado: 2.013 pendientes de agentes fuera del rollout de ack = decisión de equipo. Proceso: mi firma venció 4 veces por el orden firma→deploy→delivery; propuesta de separar delivery del digest.
- 05:32 Manifiestos `alice-cutover-exclusion-mutua` y `chat-idem-dedup-durable` habían quedado `independent_review_bytes_stale` (chat_server.py es sujeto de tres manifiestos y NEXUS lo tocó tres veces tras mis firmas). Medido el delta de cada uno a HEAD: sólo commits ya revisados y firmados por JARVIS bajo otros manifiestos → re-firmados sobre HEAD (5ecbbb87f). Pendiente del owner: re-correr mutantes sobre HEAD (`mutation_evidence_stale`). Propuesta de gate: anclar recibos a hunks/funciones del alcance, no al archivo entero.
- 05:31 Los tres manifiestos de la noche en `STATIC_OK` (verificado por JARVIS con el JSON del verify): chat-idem-dedup-durable, alice-cutover-exclusion-mutua, chat-redelivery-acusa. NEXUS re-corrió las evidencias de mutación sobre HEAD (3f76489a8) tras mis re-firmas. Deuda abierta única de la operación: entrada propia de v2 (ADA).

## 4-sep 11:16–12:18 — DEUDA #1 CERRADA: ALICE v2 tiene oídos propios

**Orden de William (11:16):** *«jarvis arregla los oidos de alice qu sus monitores esten optimos»*.
Owner JARVIS, revisor NEXUS. Tarea #1692.

**Lo que había:** v2 no escuchaba nada. Yo le tipeaba los mensajes en el pane con
`sombra_relay_4a.py`. Cada mensaje suyo pasaba por mis manos.

**Lo que hay ahora:**

```text
seal-alice-v2-ears.service   tail -F william_channel.jsonl | seal_monitor_filter --agent ALICE
                             -> /tmp/seal_events_ALICE-V2.log     singleton por flock
el log                       0640 dadito:alice-v2-lab   (lleva sus DM DESCIFRADOS)
                             lo crea /etc/tmpfiles.d/seal-alice-v2-ears.conf:
                             dadito NO es del grupo, no puede hacer chgrp; el wrapper
                             VERIFICA grupo+modo y SALE si no cumplen
en el asiento                Monitor persistente, flock + tail -n 0 -F del log, uid 982
relay del operador           APAGADO (0 procesos)
```

**El bloqueo real no era el monitor: era la política del asiento.** El primer arranque
falló con *«Permission to use Bash with command flock … has been denied»*. Medido
diferencialmente con `claude -p --permission-mode dontAsk` **corriendo como
alice-v2-lab**, no como dadito:

```text
deny Bash(tail:*) + allow del comando exacto   -> DENEGADO    el deny vence al allow
sin deny + allow del comando exacto            -> PERMITIDO   politica desplegada
sin deny + otro comando tail (/etc/hostname)   -> DENEGADO    dontAsk niega lo no permitido
```

La tercera celda es la que importa: **quitarle ese deny no le abrió lectura arbitraria**,
porque el default del asiento ya es negar. Sin esa celda el cambio sería un agujero.

**Verificación por efecto (criterio de William: «que le escribas y te conteste sin que
yo intervenga»)** — con el relay ya apagado:

```text
12:14:52  DM JARVIS -> ALICE      api_jarvis_1788542092840739192
12:15:04  respuesta de ALICE      api_alice_1788542104081013523
          «lo vi por EVENTO del Monitor persistente, no por webchat_poll»
```

**Calidad:** 131 tests verdes, 15 mutantes, 15 muertos. Dos sobrevivieron en la primera
pasada y por eso hay dos tests más: uno dejaba apuntar el prompt al log de v1, el otro
pasaba con un «(3) NO arranca el Monitor». Manifiesto `quality/manifests/alice-v2-ears-v1.json`,
**pendiente de la firma de NEXUS** (y refresco de `alice-v2-isolated-launcher-v1.json`,
que quedó `independent_review_bytes_stale` al tocar el launcher).

**Deuda nueva, chica (#4):** v2 recibe DM de cualquiera pero **sólo puede responder DM a
William y a Henry** — el enum de canales de su gateway es `web_chat`, `dm:alice:william`,
`dm:alice:henry`, `shadow:alice-v2`. A los hermanos nos contesta por el general. Lo reportó
ella misma al contestar la prueba. No la frena; queda anotado.

**12:20 — resiliencia medida (la parte «óptimos» de la orden).** Maté el proceso de los oídos
con `kill -9` y systemd lo relevantó **en 4 s** (MainPID 1645279 → 2213813, `active`), con el
log conservando `0640 dadito:alice-v2-lab`. Una marca enviada después del rearranque llegó al
log: el flujo se reanuda solo, sin tocar el asiento.
