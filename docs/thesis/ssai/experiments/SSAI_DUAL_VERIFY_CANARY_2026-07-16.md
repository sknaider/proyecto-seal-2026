# SSAI ADA production canary — 2026-07-16

## Resultado

ADA inició `DUAL_VERIFY` en producción sin modificar `soul_v3.identity` y sin
activar enforcement prematuro.

- Autorización: William, DM `chat_111994`, “ok continua y llevalo a produccion”.
- Inicio: `2026-07-17T04:39:37.867777+00:00`.
- Elegibilidad técnica mínima: `2026-07-31T04:39:37.867777+00:00`.
- Assurance actual: `TOFU_UNANCHORED`.
- Alcance del canario: ADA. Las otras ocho identidades permanecen en `SHADOW`.

## Evidencia de activación

```text
ssai_registry_acceptance=ok registry=9 projections=9 rls=isolated
update=denied delete=denied truncate=denied gate_dates=immutable
backdate=normalized premature_enforce=denied

ADA mode=DUAL_VERIFY projection_match=true biv_pass=true status=PASS
seal-mcp-server old_pid=3914 new_pid=1527296 ActiveState=active
seal_core_guard status=GREEN
```

El temporizador `seal-ssai-dual-verify.timer` ejecuta cada minuto el verificador
con el rol runtime de mínimo privilegio. El MCP ejecuta el mismo chequeo antes de
cada arranque; durante `DUAL_VERIFY` un fallo queda registrado y visible, pero no
derriba el servicio.

## Barreras deliberadas

La base de datos rechaza `UPDATE`, `DELETE` y `TRUNCATE` sobre tablas inmutables,
aplica `FORCE ROW LEVEL SECURITY` y rechaza `ENFORCE` antes de completar 14 días.
Una prueba adversarial descubrió que la primera versión del trigger confiaba en
`enforce_eligible_after` provisto en el mismo `UPDATE`. ADA fue contenida de
inmediato en `DUAL_VERIFY`; el trigger ahora fija las fechas al entrar, las hace
inmutables y valida el tiempo transcurrido contra el estado anterior. La prueba
de regresión revierte siempre su transacción, incluso si una expectativa falla.
No se declara identidad anclada: faltan ceremonia real con custodia física,
rotación/revocación/recuperación, testigos externos independientes, vectores Go
y auditoría final de NEXUS.

## Rollback

El canario no reemplaza la ruta de identidad existente. Para contener una
divergencia se mantiene el MCP operativo, se detiene la promoción y se sigue
`docs/thesis/ssai/SSAI_INCIDENT_RUNBOOK.md`. Ningún rollback puede borrar o
reescribir evidencia append-only.
