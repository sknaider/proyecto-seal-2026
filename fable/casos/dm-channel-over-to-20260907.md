# Caso para FABLE — dm-channel-over-to (owner NEXUS; revisor JARVIS; gate STATIC_OK 15:37)
- Manifiesto: `quality/manifests/dm-channel-over-to.json`. Sujeto: `messages/seal_monitor_filter.py`. Tests: `tests/test_nexus_dm_channel_over_to_v1.py`, `agents/NEXUS/test_dm_no_se_filtra.py`.
- Qué: el canal manda sobre el campo `to` en la entrega a monitores; un DM de William a un agente no llega a los monitores de los otros cuatro (medido el 3-sep: 16 fugas).
- Revisión (13:38): unit 14, positivo 1, negativo 4, control 1; refutador sobre el filtro real como JARVIS: DM ajeno con `to=equipo` bloquea, DM ajeno con `to=JARVIS` falsificado bloquea, general pasa, DM propio pasa. Brazo de tráfico real: NO_MEDIBLE hoy (sin DMs de William en la ventana), declarado.
- Mutación (15:33, arena aprobada por FABLE 14:48, revisor JARVIS): 2/2 muertos (`sin-chequeo-de-canal`, `comparacion-sensible-a-mayusculas`), evidencia con ancla/reemplazo/sha por mutante.
- Lo que refutaría: un evento con `channel=dm:alice:william` que pase el filtro de JARVIS; un `to` falsificado que abra la puerta; el control (web_chat) bloqueado.
