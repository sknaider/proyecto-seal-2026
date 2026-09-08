# Caso — `bridge-runtime-instance-20260905` (owner ADA, revisor independiente JARVIS en relevo de NEXUS)

| Campo | Valor |
|---|---|
| Manifiesto | `quality/manifests/bridge-runtime-instance-20260905.json` |
| Sujetos | `messages/ada_codex_remote_bridge.py`, `quality/delivery_bridge_runtime_instance.sh` |
| Tests | `messages/tests/test_bridge_runtime_instance_v1.py` (8) |
| Evidencia de mutación | `quality/mutation-bridge-runtime-instance-20260905.v2.json` (revisor JARVIS, 5/5, ejecutable-v2; la anterior tenía 0 mutantes) |
| Spec | `quality/mutantes/bridge-runtime-instance-20260905.spec.json` |
| Gate (7-sep 18:10) | `status: STATIC_OK · errors: None` |

## Por qué se re-firmó
`ada_codex_remote_bridge.py:32` pasó a `ROOT = Path(__file__).resolve().parents[1]` (ALICE, ed0ee0d);
la firma de NEXUS quedó bytes_stale y NEXUS no dio señal desde las 16:19 (plazo 18:00). Recibo anterior
en `review.historial`. Brazos con el venv: unit 8, qa_positive 3, qa_negative 3, qa_control 1, exit 0.

## Refutador
```text
lectura-variable-equivocada     MUERTO
firma-inventada-por-default     MUERTO   <- una etiqueta falsa llegaría al payload
asignacion-bajo-false           MUERTO
lectura-desde-session-key       MUERTO   <- una credencial llegaría al payload
sin-recorte-a-40                MUERTO
control sin mutar               8 passed · sujeto restaurado tras cada mutante
```

## Lo que NO prueba
El puente vivo no se reinició ni se ejercitó (`delivery_*` no re-ejecutado); en este host el ROOT resuelto
es idéntico al literal anterior.

## VEREDICTO FABLE 19:26 (#151984) → APPROVE
B1 y B2 muertos por conducta; B3 (etiqueta también top-level) sobrevive e inocuo (el servidor sólo lee
`body.metadata.runtime_instance`). Sin condición. Par del caso passthrough: el defecto del blanco está del lado del servidor.
