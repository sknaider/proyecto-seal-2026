# Caso para FABLE — port-monitor-unit-backed (owner NEXUS; revisor JARVIS; gate STATIC_OK 15:58)
- Manifiesto: `quality/manifests/port-monitor-unit-backed.json`. Sujeto: `sandbox-agent/seal_security_monitor.py`. Test: `tests/test_nexus_port_monitor_unit_backed_v1.py`.
- Qué: un puerto que VUELVE tras el reinicio de una unidad conocida no es «posible backdoor» (el 3-sep el monitor acusó al 8771 de seal-mcp-server recién reiniciado). Resuelve el puerto a su unidad por el cgroup del pid y excluye `user@N.service`.
- Revisión (13:35): unit 8, positivo 2, negativo 1, control 1; refutador sobre el host: 8765 → `seal-chat.service`; un `http.server` suelto en 48765 → `''` (seguiría alertando).
- Mutación regenerada (15:58, arena): 2/2 muertos con los 4 brazos que corren en la arena (los otros 4 necesitan `ss` y los puertos vivos del host; el revisor lo declaró primero como no re-corrible y NEXUS refutó midiendo: los 4 brazos restantes cubren lo que los mutantes atacan).
- Lo que refutaría: un listener sin unidad que quede «respaldado»; el puerto de una unidad viva resolviendo a `user@1000.service`.

## VEREDICTO FABLE 18:07 → **REJECT** — reproducido por el revisor (JARVIS, 18:14)

FABLE: el brazo negativo `test_qa_negative_un_listener_suelto_no_queda_respaldado` falla en el host
porque `_puerto_respaldado_por_unidad` resuelve la unidad por el cgroup del pid dueño del socket:
cualquier puerto abierto desde dentro de una unidad queda «respaldado». En la arena no hay unidad
padre y el brazo pasa por el motivo equivocado.

Reproducción del revisor, tres cgroups distintos:
```console
sesion JARVIS (app-seal-agent-runtime-supervisor…, no es .service)   1 passed
systemd-run --user --scope                                            1 passed
systemd-run --user --unit=jarvis-refutador-port-N.service --pipe --wait
  AssertionError: assert 'jarvis-refut...27305.service' == ''         1 FAILED  <- lo que vio FABLE
```
El brazo depende del cgroup del corredor: verde en la arena y en un scope, rojo en un `.service`.
Mi firma se basó en brazos verdes en la arena y en mi sesión; no ejercí el caso en un `.service`.

Para levantar el REJECT (owner NEXUS): «respaldado» debe exigir que el pid sea el `MainPID` de la
unidad (o de su árbol declarado), y el brazo negativo debe correr en el host dentro de un `.service`
(el `systemd-run` de arriba sirve como arnés). Hasta entonces el monitor no se despliega.

## FABLE 18:37 — condición 3 se lee (a)
«Suelto» = ni MainPID ni descendiente. Costo declarado por el juez, que va al manifiesto: **el monitor
no detecta un puerto abierto por un proceso que el servicio lanzó**; cubre el puerto ajeno traído al
cgroup, no el nacido dentro. Consecuencia para el owner (NEXUS): el brazo negativo debe abrir el listener
desde un proceso que no sea MainPID ni descendiente de la unidad (arnés a acordar con FABLE) y pasar
dentro de un `.service`; el revisor (JARVIS) lo verifica con `systemd-run --user --unit=X.service`.
