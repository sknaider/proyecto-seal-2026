# Auditoría post-borrado — trabajos previos de ADA (8-sep-2026 10:55 Lima)

Orden de William (terminal ADA Claude): «se borró todo el sistema y tuvimos que recuperar; revisa tus
trabajos realizados anteriormente, si están o siguen». Cuerpo que audita: ADA Claude
(`SEAL_RUNTIME_INSTANCE=ADA_CLAUDE`). Tarea DB #1746.

## Método (todo medido, comandos en el historial de la sesión)

1. 42 manifiestos con `owner=ADA` en `quality/manifests/`: existencia de sujetos y tests, presencia en
   git, sha firmado vs HEAD vs disco, y `quality_gate/gate.py verify` sobre cada uno.
2. 107 rutas citadas en 212 tareas completadas (desde 1-ago) y 1.500 memorias operativas (desde 10-ago):
   existencia en disco.
3. 13 archivos del repo faltantes: `git log --all` y copia NFS `/mnt/spark-2/recuperacion_seal_7sep`.
4. Unidades systemd de ADA (user y system), timers, y las que están en `failed`.
5. Mensajes de William de las últimas 24 h sin respuesta de ADA (`reply_to`).

## Resultado en una tabla

```text
capa                              total   sano   perdido / roto
manifiestos owner=ADA (gate)        42     12    27 REJECTED + 3 ERROR
  - por daño del borrado                    -    5 (archivos nunca en git o firma perdida)
  - por estado previo (review pending)      -    18 (ya estaban así; no es daño)
  - por mi edición de hoy (esperada)        -    3 (re-firma de JARVIS/NEXUS pendiente)
  - evidencia de mutación vencida           -    3 ERROR (re-correr arena del revisor)
rutas citadas en tareas/memorias   107     52    55 faltan (31 sesiones Codex, 4 recibos, 13 repo, 7 labs)
archivos del repo perdidos          13      0    13 NUNCA estuvieron en git ni en el NFS -> irrecuperables
unidades systemd de ADA (user)      17     10    3 failed + 4 inactive normales (timers)
daemons de mis carriles              9      8    seal-agent-stability-guard inactive (timer vivo)
mensajes de William sin respuesta    -      -    2 (152377 del 7-sep 23:22; 152710 de hoy 09:48)
```

## Perdido de verdad (nunca estuvo en git, no está en el NFS)

```text
memory/migrations/20260818_ocean_drift_precision_v2.sql (+ .rollback.sql)   manif. ocean-integrity-v2
scripts/verify_ocean_integrity_v2.py                                          manif. ocean-integrity-v2
memory/tests/test_ocean_drift_calculator.py / test_ocean_protect.py /         manif. ocean-integrity-v2
  test_verify_ocean_integrity_v2.py
memory/tests/test_sleep_gate_credencial.py                                    manif. sleep-gate-credencial (aprobado, entregado)
memory/tests/test_seal_unit_failed_notify.py                                  manif. unit-failed-notify (aprobado, entregado)
messages/ada_codex_c10_token_writer.py                                        unidad ada-codex-c10-token-writer FALLA cada 15 min
scripts/chat_ada_shadow.py · scripts/seal_index_ownership_guard.py ·
  tests/test_seal_index_ownership_guard.py · tests/test_kernel.py ·
  fable/seal_timer_calendar_anchor.py · quality/manifests/soul-runtime-supervisor.json
agents/ADA/AUDIT_SOUL_USAGE_20260831.md · auditoria_soul_20260903_1458.md ·
  baseline_recall_alice_v1_20260907.md · inventario_soul_v2_lab_vs_spec_20260907.md
docs/research/bob.md · messages/uploads/document_1788216962916501641.md
labs: ~/IA/seal-frontier-lab · ~/IA/soul-next-lab · ~/IA/soul-v2-review/ADA/phase6-*-blind.json
```

Lección que confirma la de ALICE del 7-sep: **el gate protegió lo firmado que estaba en git; lo que se
entregó sin commit murió aunque tuviera recibo aprobado.** Dos carriles aprobados y entregados
(`sleep-gate-credencial`, `unit-failed-notify`) hoy no pueden re-verificarse porque su test no existe.

## Firmas que ya no coinciden con lo recuperado (sin edición mía)

```text
soul-f2-runtime-orchestrator      tests/test_soul_runtime_orchestrator.py          firmado a23a24e8 · HEAD 7d35c807
studio-stream-body-signature-v1   seal-studio/backend/test_stream_body_signature_v1.py firmado ada93a49 · HEAD 272c0f8e
sleep-gate-credencial-20260905    quality/delivery_sleep_gate_credencial.sh          firmado 93f9c63d · HEAD 91b4d045
```

Los tres archivos entraron a HEAD en `bf59df8` (7-sep 12:48, recuperación). La versión firmada era
posterior a la copia y se perdió con el home. Hay que re-firmar sobre los bytes de hoy o reponer la
versión firmada si algún revisor la tiene.

## Unidades de ADA

```text
FALLA  ada-codex-c10-token-writer.service   script perdido (arriba) — el timer lo relanza cada 15 min y falla
FALLA  seal-ada-daily-brief.service         reconstruida del journal con ExecStart="(python3)": unidad inválida
FALLA  ada-listening-healthcheck.service    NO es daño: falla a propósito por 2 mensajes de William sin respuesta
VIVO   ada-codex-remote-bridge · seal-ada-codex-poller · seal-ada-codex-stream-relay · ada-codex-compact-monitor
VIVO   seal-ada-session-watchdog · seal-whisper-ADA · seal-ada-embodied-sim · ada-v2-* brokers (system)
OJO    ada-claude-terminal.service restaurada de f8cf0b8 con Restart=always: mi fix del 6-sep (Restart=no)
       se perdió. Hoy está inactive+disabled, así que no resucita, pero la unidad volvió a la forma vieja.
```

Aparte, en `failed` hay 26 unidades de otros dueños (lista en la sesión); no las toqué.

## Mensajes de William sin respuesta de ADA (backlog del healthcheck)

```text
152377  7-sep 23:22  web_chat  «que es lo que falta, ada ya hizo arreglos»
152710  8-sep 09:48  web_chat  «arrancamos jarvis, tambien revisa los cambios que hizo ada ...»
```

Ambos son del general con lead JARVIS; ADA no contestó ninguno.

## Sano (12 manifiestos STATIC_OK)

bob-runtime-rewrite · bridge-runtime-instance · compact-original-request · compaction-metrics ·
containment-eval-artifact · panel-soul-release-links · seal-lifecycle-manual-state · soul-autowire-world-lab ·
soul-f1-runtime-hooks · stability-guard-lifecycle-intent · subagent-nesting · tool-result-budget-inert.

## Qué propongo (no ejecutado; William dijo «para» a las 10:44)

1. Reponer `messages/ada_codex_c10_token_writer.py` o deshabilitar su timer (falla cada 15 min).
2. Reescribir `seal-ada-daily-brief.service` con un ExecStart real, o deshabilitarla.
3. Reescribir los dos tests perdidos de carriles aprobados (`sleep-gate-credencial`, `unit-failed-notify`)
   y pedir re-firma; decidir si `ocean-integrity-v2` (18-ago, nunca revisado) se reconstruye o se archiva.
4. Re-firma de las tres firmas desfasadas por la recuperación, más las tres mías de hoy.
5. Volver a poner `Restart=no` en `ada-claude-terminal.service` (fix del 6-sep) y commitear la unidad.
6. Regla estructural: nada se declara entregado sin commit; el gate ya rechaza `unindexed_*`, hacerlo
   bloqueante también para `delivery.status=verified`.

## Segunda pasada (11:05) — ¿hace falta recuperarlo? ¿se puede con lo que hay?

Pregunta de William: «con lo que tienes puedes recuperar tus trabajos, es necesario? ya están, revisa».

```text
pieza perdida                              ¿ya está / hace falta?                          ¿reconstruible con lo que hay?
------------------------------------------ ----------------------------------------------- --------------------------------------------
tests sleep-gate-credencial (1 archivo)    HACE FALTA: carril aprobado+entregado, sujeto    SI: el manifiesto conserva los 4 nombres de
                                           memory/sleep_gate_cron.py vive y corre           test y qué prueba cada uno; sujeto intacto
tests unit-failed-notify (1 archivo)       HACE FALTA: idem, tools/seal_unit_failed_notify  SI: idem (4 brazos nombrados en el manifiesto)
                                           .py vive
suite ocean-integrity-v2 (6 archivos)      A MEDIAS: los 3 sujetos (ocean_protect, drift    PARCIAL: la migración YA está aplicada en la DB
                                           _calculator, adaptive_schema) viven; la          (ocean_drift_log numeric(6,4)); los tests se
                                           migración ya está aplicada; nunca fue revisada   rehacen desde los sujetos; el verify script no
messages/ada_codex_c10_token_writer.py     HACE FALTA (menor): medía tokens de las sesiones  SI: memory/c10_token_writer_shared.py hace lo
                                           Codex de ADA para soul_v3.agent_token_budget;    mismo para Claude; adaptar a los rollouts de
                                           hoy el timer falla cada 15 min                   Codex (~80 líneas)
seal-ada-daily-brief (script)              HACE FALTA: regla de William del brief matutino  SI: copiar agents/JARVIS/jarvis_daily_brief.py
                                           por hallazgo; hoy la unidad es inválida          (con test) y cambiar el agente
scripts/chat_ada_shadow.py                 NO POR AHORA: runner de ADA v2 shadow (1-sep);   NO desde memorias (sin código); el carril v2
                                           el lab soul-v2-lab y sus commits también         está pendiente (#1624/#1626) y se rehará
                                           se perdieron
seal_index_ownership_guard.py (+test)      NO: tarea #1566 seguía pendiente (coverage        NO desde memorias; sí desde su descripción si
                                           fallaba); nunca cerró                            se retoma #1566
fable/seal_timer_calendar_anchor.py        NO: su efecto ya está aplicado (OnCalendar en    innecesario; JARVIS re-ancló los timers el 7-sep
                                           los timers vivos)
quality/manifests/soul-runtime-supervisor  NO URGENTE: el supervisor y sus 21 tests viven   SI, es un JSON; tarea #1593 pendiente
                                           en tools/; sólo faltaba el manifiesto
tests/test_kernel.py                       NO: era del lab soul-v2-lab, no del repo         se rehace con el lab
informes .md en agents/ADA (4)             NO: su contenido vive en SOUL (memorias del      no hace falta; son fotos de auditorías ya
                                           31-ago, 3-sep, 7-sep)                            superadas
docs/research/bob.md · upload document_*   NO: bob.md ya se absorbió (tareas #1696/#1703); parcial; poco valor
                                           el upload era la spec 2.1 de ADA v2 (decisión
                                           guardada en memoria #375195)
sesiones Codex (31) y recibos (4)          NO recuperables ni necesarios: eran evidencia    no
                                           de trabajos ya cerrados
```

**Veredicto:** de 13 archivos perdidos del repo, **5 conviene rehacer** (dos suites de carriles aprobados,
el escritor c10 de Codex, el brief de ADA, el manifiesto del supervisor) y **8 no hace falta**: o su efecto
ya está aplicado, o pertenecen a labs que también se perdieron y se reharán con ellos, o su contenido
vive en SOUL. Ninguno se recupera byte a byte; los 5 se reconstruyen desde sujeto + manifiesto + código
hermano. Las 3 firmas desfasadas no se recuperan: se re-firman sobre los bytes de hoy.

## Ejecución (11:08-11:15) — luz verde de William y de JARVIS

```text
pieza                                    estado                       commit / firma
1 test sleep-gate-credencial             REHECHO · 5 tests · 3/3 mut. c18c55b · re-firma NEXUS pedida
2 test unit-failed-notify                REHECHO · 15 tests · 6/6 mut. c18c55b · re-firma JARVIS pedida
3 brief matutino ADA + unidad            REHECHO · 10 tests · unidad   8ead1de · FIRMADO JARVIS 11:10 · gate STATIC_OK
                                         instalada, timer 9-sep 06:00
4 escritor c10 Codex + unidad            REHECHO · 7 tests · corrido   en disco · revisión NEXUS pedida
                                         en vivo: written=true,
                                         72 eventos, 12,6 M input
5 plantilla supervisor + manifiesto      REHECHA · 13/13 (antes 12/13) en disco · revisión NEXUS pedida ·
                                         NO instalada a propósito
```

**Dos hallazgos que salieron al hacerlo:**

- El gate de staged **no deja commitear un manifiesto nuevo con `independent_review_pending`** aunque
  sus tests estén verdes: el orden real es código → manifiesto en disco → firma del revisor → commit.
  Un commit rechazado deja lo staged; se commitea con pathspec para no arrastrar lo ajeno (había tres
  archivos de NEXUS en el índice compartido; siguen intactos).
- Las unidades vivas `seal-agent-runtime-supervisor@JARVIS/@ALICE` **no corren el supervisor**: son
  lanzadores tmux reconstruidos desde el proceso el 7-sep. La plantilla real (`ops/systemd/...@.service`)
  nunca estuvo en git; la dejé versionada y sin instalar. Decisión de JARVIS.

**Regla de oro nueva de William (10:59):** «algo que quieran modificar o arreglar en bien de SOUL,
preguntar a JARVIS o viceversa». Aplicada: aviso a JARVIS a las 11:01 antes de tocar; OK a las 11:02.
