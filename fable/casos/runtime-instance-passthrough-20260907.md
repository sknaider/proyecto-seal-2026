# Caso — `runtime-instance-passthrough` (owner NEXUS, revisor independiente JARVIS, antes ADA)

| Campo | Valor |
|---|---|
| Manifiesto | `quality/manifests/runtime-instance-passthrough.json` |
| Sujetos | `messages/chat_server.py`, `quality/delivery_runtime_instance_passthrough.sh`, `quality/delivery_runtime_instance_arm3.py` |
| Tests | `tests/test_nexus_runtime_instance_passthrough_v1.py` (9) |
| Evidencia de mutación | `quality/mutation-runtime-instance-passthrough.v2.json` (revisor JARVIS, 5/5, ejecutable-v2, arena aprobada) |
| Spec | `quality/mutantes/runtime-instance-passthrough.spec.json` |
| Gate (7-sep 17:25) | `status: STATIC_OK · errors: None` |

## Por qué se re-firmó
`chat_server.py` cambió a las 14:49 (commit 6459704, «un canal desconocido nace cerrado»); la firma de
ADA quedó `independent_review_bytes_stale` y ADA no tiene ejecución. Recibo anterior conservado en
`review.historial`. Brazos con el venv: unit 9, qa_positive 1, qa_negative 1, qa_control 1, exit 0.

## Refutador
```text
copiar-el-metadata-entero-via-bucle     muerto por 4 tests (2 textuales + 2 conductuales: legacy_id/clave_extra no se pisan)
sin-recorte-ni-strip                    muerto por 2 (el cap [:40] y el isinstance se vigilan)
asignacion-presente-pero-inalcanzable   muerto por 2, incl. el conductual que lee la fila persistida
                                        <- conserva todos los literales; un test textual no lo vería
extraer-de-campo-equivocado             muerto por 2
sin-validacion-de-tipo                  muerto por 2
control sin mutar                       9 passed · sujeto restaurado tras cada mutante
```

## Lo que NO prueba
El brazo por efecto contra el servidor vivo (`delivery_*`) no se re-ejecutó en esta re-firma; la
evidencia es de suite y mutación, más el gate.
