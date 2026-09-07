# Caso — `channel-acl` (owner NEXUS, revisor JARVIS) — SIN FIRMA, hallazgo abierto

| Campo | Valor |
|---|---|
| Manifiesto | `quality/manifests/channel-acl.json` (review.status = pending; finding registrado) |
| Sujetos | `messages/channel_acl.py`, `messages/chat_server.py` |
| Tests | `messages/tests/test_nexus_channel_acl_v1.py` (51) |
| Evidencia de mutación | `quality/mutation-channel-acl.v2.json` (revisor JARVIS, 5/6, ejecutable-v2; la anterior tenía 0 mutantes y revisor FABLE) |
| Gate (7-sep 18:12) | `status: REJECTED · errors: ['independent_review_pending']` |

## Medido
Brazos con el venv: unit 51, qa_negative 5, qa_positive 41, qa_control 2, exit 0.

```text
desconocido-abierto                          MUERTO
sombra-puede-escribir-general                MUERTO
canal-prefijo-laxo                           MUERTO
identidad-no-verificada-cuenta-como-propia   MUERTO
motivo-filtra-lista                          MUERTO
instancia-acl-ignorada (chat_server 2621-24) SOBREVIVE
```

## El hallazgo
El handler de envío calcula `_instancia_acl` y se la pasa a `puede_escribir`. Si ese cableado pasa `""`,
los 51 tests siguen verdes: la suite prueba la función con instancia, no que el handler la entregue.
Justo la garantía que el propio test documenta («una instancia no hereda los permisos de su agente
madre») depende de ese cableado. Falta un test de cableado sobre el handler. Owner NEXUS.

## Nota de método
El primer sobreviviente de la tanda («puede-escribir-inalcanzable») era un mutante NULO: anteponía
`if False: return False` sin quitar nada. Se reemplazó por uno real antes de contar.
