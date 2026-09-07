# SPEC — SEAL Autonomy: cero errores silenciosos/repetidos v1

Estado: implementado
Owner: ADA
Autoridad: William, webchat `120903`, 2026-07-30

## Objetivo

Permitir autocuración reversible dentro del asiento propio y bloquear cierres
basados en evidencia vacía o del sujeto equivocado.

La meta no es prometer cero errores. Es:

1. cero errores silenciosos;
2. cero repetición del mismo error sin un test de regresión;
3. recuperación automática o escalamiento con evidencia.

## Límites de autoridad

- Identidad operativa: `SEAL_AGENT`, sembrada por el launcher.
- Cada agente elige una **acción simbólica** de una tabla cerrada.
- La custodia de esa tabla vive fuera del broker: el Stability Guard parsea
  `AGENT_ACTIONS` y `CONTROL_UNITS` y las compara con una copia exacta en su
  ruta core protegida. Ampliar autoridad exige modificar también el guard
  protegido, ejecutar sus tests y reiniciarlo; una edición aislada queda
  `YELLOW/drift`.
- El agente no puede pasar nombres libres de unidades, PIDs ni comandos.
- Sólo se permiten servicios propios y reversibles.
- Cross-agent, `DELETE`, `DROP`, `rm -rf`, compromisos externos y cambios de
  objetivo quedan fuera de v1.
- `SEAL_AGENT` es una frontera contra errores y deriva, no aislamiento
  criptográfico: los agentes comparten el mismo usuario Unix. Separar por UID es
  una mejora posterior si William requiere aislamiento hostil.

## Contrato de ejecución

Entrada:

```text
SEAL_AGENT=<AGENTE> scripts/seal_self_repair.py restart <acción> --reason <motivo>
```

Salida: un recibo JSON `seal.autonomy.repair-receipt.v1` en la ruta durable
`$XDG_DATA_HOME/seal/autonomy_receipts/<AGENTE>/` o, si no se define XDG,
`~/.local/share/seal/autonomy_receipts/<AGENTE>/`.

El CLI no acepta una ruta de salida libre. `/tmp` queda excluido porque su
limpieza por reboot/edad borraría la evidencia que autoriza declarar una
reparación terminada. Directorios y recibos usan modos `0700` y `0600`.

El recibo debe contener:

- sujeto exacto: agente, acción y unidad resuelta por política;
- tiempo de observación antes/después;
- oportunidad real: comando invocado;
- esperado y observado;
- control positivo: objetivo activo;
- prueba por efecto: nueva invocación o PID;
- control negativo: un servicio de otro agente no cambió;
- código de salida y resultado final.

Sólo `result=verified` permite declarar la reparación terminada.

El broker no puede impedir que alguien invoque `systemctl` directamente bajo el
usuario Unix compartido. El Stability Guard cubre esa segunda puerta: mantiene
una línea base durable de `InvocationID` para las 13 unidades permitidas y exige
una cadena de recibos `verified` que conecte la invocación aceptada con la
observada. Un cambio sin recibo queda `YELLOW/drift` después de una gracia de 30
segundos. El primer despliegue y cada reboot del host establecen línea base para
no acusar retrospectivamente arranques del sistema.

## Contrato de cierre no vacío

`memory/task_lifecycle.py` mantiene compatibilidad con contratos previos. Cuando
un contrato declara cualquiera de estos campos en `expected_evidence`, el evento
`verified` debe proporcionarlo:

```text
subject observed_at opportunity expected actual
positive_control negative_control by_effect
```

Así, “0 errores” sin casos ejercidos no permite cerrar.

Los campos fuertes se validan por contenido, no sólo por presencia:

- `opportunity`: `exercised=true` y operación concreta;
- controles positivo/negativo: valores `expected` y `observed` más
  `passed=true`;
- `by_effect`: valores `before` y `after` diferentes y `changed=true`;
- `observed_at`: timestamp ISO válido.

Etiquetas plausibles como `"ok"`, `"sí"` o un booleano no permiten cerrar.

## Acciones v1

- ADA: bridge, channel_monitor, stream_relay, visible_poller, visible_terminal.
- ALICE: bridge, channel_monitor.
- FABLE: channel_monitor.
- JARVIS: bridge, channel_monitor.
- NEXUS: bridge, channel_monitor, visible_terminal.

DUM queda fuera hasta tener unidades supervisadas propias. También quedan fuera
los `oneshot`: no ofrecen un PID vivo para verificar y algunos esperan 40 s o
conservan procesos gráficos en su cgroup. Los relanzamientos del proceso
cognitivo completo deben diseñarse con checkpoint y handoff porque matar al
propio runtime sin sucesor comprobado puede perder el turno.

## Aceptación

1. Identidad ausente: fail-closed.
2. Unidad arbitraria: denegada.
3. Unidad de otro agente: inalcanzable desde la política propia.
4. Reinicio real de un servicio ADA: activo, invocación/PID nuevo.
5. Control negativo: el servicio JARVIS medido conserva invocación/PID.
6. Recibo durable fuera de `/tmp`, con árbol `0700` y archivo `0600`.
7. Lifecycle rechaza evidencia sin oportunidad/controles cuando el contrato los
   exige y acepta un bundle completo.
8. El Stability Guard detecta si desaparece el broker o cualquiera de sus
   controles obligatorios.
9. El Stability Guard detecta cualquier ampliación o cambio de la tabla de
   acciones/controles que no haya pasado por su ruta core protegida.
10. El Stability Guard detecta por `InvocationID` un reinicio directo de
    cualquiera de las 13 unidades permitidas que no tenga cadena de recibos
    `verified`; acepta el mismo cambio cuando el broker produjo evidencia válida.
11. Un bypass confirmado no se lava con un recibo posterior. Para volver a
    `GREEN`, el owner debe adjudicar exactamente los `InvocationID` accepted y
    observed mediante `--ack-out-of-band`, con razón durable. El ACK queda
    separado en `~/.local/share/seal/autonomy_acknowledgements/<AGENTE>/`
    (`0700/0600`) y un bypass posterior vuelve a disparar el guard. Durante la
    gracia el estado es `pending`, nunca `healthy`.
12. Un healthcheck puede poner en cuarentena un marcador de ruta obsoleto, pero
    eso no autoriza reiniciar servicios que continúan activos. Toda reparación
    real de los cuatro servicios ADA supervisados pasa por el broker; el poller
    visible tiene la acción cerrada `visible_poller`.

## Operación y rollback

El Stability Guard sí se modifica: tras el cambio se debe ejecutar/reiniciar
`seal-agent-stability-guard.service` y comprobar reporte verde. El broker se
ejecuta bajo demanda. Rollback: retirar su chequeo del guard, retirar
`scripts/seal_self_repair.py` y volver a los contratos de evidencia mínimos. No
hay migración ni cambio destructivo.
