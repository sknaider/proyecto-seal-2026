# Suspensión operativa del piloto SOUL + gstack r2

Estado: **INCONCLUSO, intervención suspendida sin afirmación causal**.

La decisión formal de cerrar definitivamente esta corrida o autorizar una nueva
activación corresponde a William. Este expediente no toma esa decisión.

## Evidencia de la suspensión

- Activación congelada: `2026-07-31T04:05:03.317230+00:00`.
- Ventana posterior terminó: `2026-07-31T10:05:03.317230+00:00`.
- Mínimo exigido: 3 recibos `CURRENT` por sujeto.
- Resultado preservado en `report-latest.json`: ALICE `0`, NEXUS `0`; todos los
  recibos existentes quedaron `STALE` o `legacy_excluded`.
- El protocolo cambió después de la activación: sólo `SPEC.md` difiere del hash
  congelado. El guard rechazó correctamente cada reporte con
  `protocol_changed_after_activation` desde 2026-08-28 19:00 -05.
- No se reactivó ni se movió la vara: una nueva activación sería un experimento
  distinto, no la reparación de éste.

## Veredicto permitido

Esta corrida no aporta muestra suficiente para evaluar adopción ni efecto. No
demuestra mejora ni deterioro de ALICE o NEXUS. El único resultado válido es que
el candado de integridad del protocolo funcionó y evitó mezclar dos protocolos.

## Estado seguro temporal

`seal-gstack-soul-pilot-report.timer` queda deshabilitado; baseline, activación,
recibos y reportes se conservan. Reactivar exige autorización nueva de William,
baseline nueva y activación nueva antes del primer recibo.

## Suspensión operativa aplicada — 4-sep-2026

- El marcador `ACTIVE` fue suspendido y conservado como
  `ACTIVE_SUSPENDED_20260904.txt`.
- Los conectores experimentales `CLAUDE.md` de ALICE y NEXUS se archivaron en
  `artifacts/gstack-soul-pilot/subjects/`. En sus `cwd` quedaron conectores
  neutrales, necesarios para que la suite histórica siga midiendo deriva, pero
  ningún arranque nuevo carga `SUBJECT_PROMPT_STAGE1.md`.
- El timer y el servicio quedaron deshabilitados e inactivos.
- No se modificaron ni eliminaron la activación, los recibos, la evidencia, el
  baseline, el reporte ni el prompt histórico.
- Esta suspensión no cierra ni extiende el experimento. William decide entre
  cierre definitivo y una nueva corrida con protocolo congelado, baseline y
  activación nuevos antes de exponer sujetos.
